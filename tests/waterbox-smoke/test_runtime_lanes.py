"""T34a: the two lanes of libwaterboxhost.

The local copy of the runtime (built from ../MacHawk's pinned sources, never touching them) is a RELEASE build by default, the one BizHawk ships: entering and leaving the sandbox
costs ~0.0004 ms per unit, against ~3.1 ms in a Debug build (T33/T34). The Debug runtime is a lane of its own for what only it has: the runtime's assertions
(`debug_assertions`) and the eviction of the sandbox at every call, which it forces on (`wbx_set_always_evict_blocks` is a no-op there) to catch dangling pointers. It is chosen with

    P6_WATERBOX_RUNTIME=debug python3 tools/build_waterbox.py            (the whole build, receipt in build/waterbox/receipt-debug.json)
    python3 tools/build_waterbox.py --runtime-lane debug                   (only the runtime, from the receipt of the default build)

and every Python entry point (build_waterbox.run_puae6/run_ffi/build_core, the suites of WB02) follows it. What this module shows, and which suites run in the Debug lane and why:

  * the profile is Release in plan/toolchains.json and the receipt of the default build says so;
  * the library is reproducible in form: no path of the tree it was compiled in, and a fixed install name (the sha256 recorded in manifests/waterbox-memory.json is therefore the
    same wherever the repository or a worktree lives; a test rebuilds the Release library from a copy of the sources in another directory and compares it byte for byte);
  * Release (eviction off, what BizHawk sets), Release with the runtime's own eviction on (P6_EVICT=1) and the Debug lane give the same machine unit by unit, so the speed is the
    only thing that changes;
  * the ABI and identity gate (tests/ffi/test_ffi.py: ABI01, WB15, canaries around what the guest is handed, damaged guests) and the host-callback, dirty-page and restore tests of the guest
    (tests/waterbox-smoke/test_puae6_wb.py: WB13-WB15) run in the Debug lane too. Those are the suites that hand pointers across the boundary and that damaged inputs would turn into a
    runtime assertion; the runtime's own cargo tests (cargo test, Debug, `debug_assertions` on) run in every build() as before.

The Debug lane is built from the same sources when it is missing (about half a minute). Skips without the guest (BLOCKED in the gate):
    python3 -m unittest discover -s tests/waterbox-smoke -p test_runtime_lanes.py
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_waterbox  # noqa: E402
from test_puae6 import runtime_available  # noqa: E402

BOOT_ROM = build_waterbox.BUILD / "boot.rom"
UNITS = 20


class LaneSelectionTests(unittest.TestCase):
    """No runtime needed: how the profile is chosen."""

    def test_the_default_profile_is_release_and_the_environment_picks_the_other_lane(self):
        self.assertEqual(json.loads(build_waterbox.LOCK.read_text())["runtime"]["profile"], "release")
        with mock.patch.dict(os.environ):
            os.environ.pop(build_waterbox.RUNTIME_ENV, None)
            self.assertEqual(build_waterbox.runtime_profile(), "release")
            self.assertEqual(build_waterbox.runtime_receipt_path(), build_waterbox.BUILD / "receipt.json")
            os.environ[build_waterbox.RUNTIME_ENV] = "debug"
            self.assertEqual(build_waterbox.runtime_profile(), "debug")
            self.assertEqual(build_waterbox.runtime_receipt_path(), build_waterbox.BUILD / "receipt-debug.json")
            os.environ[build_waterbox.RUNTIME_ENV] = "fast"
            with self.assertRaises(build_waterbox.BuildError):
                build_waterbox.runtime_profile()
        self.assertEqual(build_waterbox.runtime_receipt_path("release"), build_waterbox.BUILD / "receipt.json")
        self.assertEqual(build_waterbox.runtime_receipt_path("debug"), build_waterbox.BUILD / "receipt-debug.json")

    def test_a_lane_that_does_not_exist_is_refused(self):
        with self.assertRaises(build_waterbox.BuildError):
            build_waterbox.ensure_runtime_lane("profile")


@unittest.skipUnless(runtime_available(), "no Waterbox runtime and guest (python3 tools/build_waterbox.py && python3 tools/build_waterbox.py --puae6)")
class RuntimeLaneTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not BOOT_ROM.is_file():
            subprocess.run([sys.executable, str(ROOT / "tests/fixtures/microrom/build_rom.py"), str(ROOT / "tests/fixtures/microrom/boot.s"), "--output", str(BOOT_ROM)], check=True)
        cls.release_receipt = build_waterbox.ensure_runtime_lane("release")
        cls.debug_receipt = build_waterbox.ensure_runtime_lane("debug")

    def run_lane(self, lane, **extra_env):
        with mock.patch.dict(os.environ, {build_waterbox.RUNTIME_ENV: lane, **extra_env}):
            return build_waterbox.run_puae6(BOOT_ROM, UNITS)

    def test_the_default_build_is_release_and_the_debug_lane_is_another_library(self):
        release, debug = (json.loads(path.read_text()) for path in (self.release_receipt, self.debug_receipt))
        self.assertEqual((release["runtime_profile"], debug["runtime_profile"]), ("release", "debug"))
        self.assertIn("/release/", release["runtime_dylib"]["path"])
        self.assertIn("/debug/", debug["runtime_dylib"]["path"])
        self.assertNotEqual(release["runtime_dylib"]["sha256"], debug["runtime_dylib"]["sha256"])
        self.assertEqual(release["guest"], debug["guest"], "both lanes run the same guest")
        for receipt in (release, debug):
            self.assertEqual(build_waterbox.sha256(Path(receipt["runtime_dylib"]["path"])), receipt["runtime_dylib"]["sha256"])
        self.assertEqual(release["cargo_tests"], {"passed": 8, "failed": 0, "ignored": 0}, "the runtime's own tests ran (Debug profile, assertions on)")

    def test_the_release_library_carries_no_path_of_the_tree_it_was_built_in(self):
        """Only the Release library: the Debug lane keeps its debug info (the paths of the objects), it is never recorded anywhere."""
        dylib = Path(json.loads(self.release_receipt.read_text())["runtime_dylib"]["path"])
        data = dylib.read_bytes()
        for leak in (str(ROOT), str(Path.home())):
            self.assertNotIn(leak.encode(), data, f"{dylib.name} still names {leak}")
        install_name = subprocess.run(["otool", "-D", str(dylib)], check=True, text=True, stdout=subprocess.PIPE).stdout.splitlines()[-1]
        self.assertEqual(install_name, "@rpath/libwaterboxhost.dylib")

    def test_the_release_library_built_from_another_directory_is_the_same_file(self):
        recorded = json.loads(self.release_receipt.read_text())["runtime_dylib"]["sha256"]
        tc = build_waterbox.toolchain()
        with tempfile.TemporaryDirectory(prefix="p6-lane-") as temp:
            root = Path(temp).resolve()
            build = root / "build/waterbox"
            build.mkdir(parents=True)
            for name in ("runtime-src", "cargo-home"):
                shutil.copytree(build_waterbox.BUILD / name, build / name, symlinks=True)
            env = {**tc["env"], "RUSTFLAGS": build_waterbox.runtime_rustflags(root, build), "CARGO_HOME": str(build / "cargo-home"), "CARGO_TARGET_DIR": str(build / "cargo")}
            subprocess.run([str(tc["paths"]["cargo"]), "build", "--offline", "--locked", "--release", "--target", tc["lock"]["runtime"]["target"], "--manifest-path",
                            str(build / "runtime-src/Cargo.toml")], env=env, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            rebuilt = build / "cargo" / tc["lock"]["runtime"]["target"] / "release/libwaterboxhost.dylib"
            self.assertEqual(build_waterbox.sha256(rebuilt), recorded)

    def test_release_with_and_without_eviction_and_the_debug_lane_run_the_same_machine(self):
        release, release_result = self.run_lane("release", P6_EVICT="0")
        evicting, evicting_result = self.run_lane("release", P6_EVICT="1")
        debug, debug_result = self.run_lane("debug")
        self.assertEqual(len(release), UNITS)
        self.assertEqual(release, evicting, "eviction on or off is not visible to the machine")
        self.assertEqual(release, debug, "the Debug runtime gives the same units as the Release one")
        self.assertEqual(release_result["after_last_unit"], debug_result["after_last_unit"])
        self.assertEqual(release_result["snapshot_bytes"], debug_result["snapshot_bytes"])
        self.assertEqual(release_result["runtime_sha256"], evicting_result["runtime_sha256"])
        self.assertNotEqual(release_result["runtime_sha256"], debug_result["runtime_sha256"])

    def test_the_suites_that_hand_pointers_across_the_boundary_pass_in_the_debug_lane(self):
        env = {**os.environ, build_waterbox.RUNTIME_ENV: "debug"}
        for directory, pattern in (("tests/ffi", "test_ffi.py"), ("tests/waterbox-smoke", "test_puae6_wb.py")):
            result = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", directory, "-p", pattern], cwd=ROOT, env=env, text=True,
                                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=3600)
            tail = "\n".join(result.stdout.splitlines()[-30:])
            self.assertEqual(result.returncode, 0, f"{pattern} in the Debug lane:\n{tail}")
            self.assertNotIn("skipped", tail.splitlines()[-1], f"{pattern} skipped tests in the Debug lane:\n{tail}")


if __name__ == "__main__":
    unittest.main()
