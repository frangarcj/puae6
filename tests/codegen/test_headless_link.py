"""The headless runner links without host UI/audio/plugin libraries and reports its identity.

It runs micro-ROM programs (T07/T08) and says so: no Kickstart compatibility is claimed."""
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
BUILD = ROOT / "build/link-audit"
FORBIDDEN_LIBRARIES = ("SDL", "Qt", "X11", "libGL", "OpenGL", "Cocoa", "AppKit", "AudioToolbox",
                       "CoreAudio", "AudioUnit", "IOKit", "Foundation", "CoreGraphics", "libretro",
                       "pulse", "alsa", "vulkan", "Metal")
ALLOWED_PREFIXES = ("/usr/lib/libSystem.B.dylib", "/usr/lib/libc++.1.dylib", "/usr/lib/libc++abi.dylib")


@unittest.skipUnless(all(shutil.which(t) for t in ("cmake", "ninja", "clang++")), "toolchain unavailable")
class HeadlessLinkTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        subprocess.run(["cmake", "-S", str(ROOT), "-B", str(BUILD), "-G", "Ninja",
                        "-DCMAKE_C_COMPILER=clang", "-DCMAKE_CXX_COMPILER=clang++",
                        "-DCMAKE_BUILD_TYPE=Debug", "-DP6_SANITIZERS=OFF"],
                       check=True, capture_output=True, text=True, timeout=120)
        subprocess.run(["cmake", "--build", str(BUILD), "--target", "p6_headless"],
                       check=True, capture_output=True, text=True, timeout=1200)
        cls.exe = BUILD / "p6_headless"

    def identity(self):
        run = subprocess.run([str(self.exe), "--identity"], capture_output=True, text=True, timeout=30)
        self.assertEqual(run.returncode, 0, run.stderr)
        return dict(line.split(": ", 1) for line in run.stdout.splitlines())

    def test_identity_matches_the_manifests(self):
        info = self.identity()
        manifest = json.loads((ROOT / "manifests/source-manifest.json").read_text())
        series = ROOT / "patches/winuae/series.json"
        count = len(json.loads(series.read_text())["patches"])
        digest = hashlib.sha256(series.read_bytes()).hexdigest()
        header = (ROOT / "bridge/include/p6.h").read_text()
        abi = re.search(r"#define P6_ABI_VERSION\s+UINT32_C\((\d+)\)", header)[1]
        self.assertEqual(info["core"], "puae6-headless")
        self.assertEqual(info["profile"], manifest["profile"])
        self.assertEqual(info["machines"], "A500")
        self.assertEqual(info["abi"], abi)
        self.assertEqual(info["upstream"], f"WinUAE {manifest['commit']}")
        self.assertEqual(info["patches"], f"{count} ({digest})")
        # Capability bits describe implemented behaviour: only the lag flag exists so far.
        self.assertEqual(info["caps"], "0xe")            # P6_CAP_LAG_FLAG | P6_CAP_MEDIA_EVENTS | P6_CAP_MEMORY_REGIONS
        self.assertEqual(info["boot"], "micro-ROM programs only; Kickstart compatibility is not proven")
        self.assertEqual(info["video"], "up to 760x576")

    def test_identity_is_reproducible_and_free_of_paths_and_dates(self):
        first = subprocess.run([str(self.exe)], capture_output=True, text=True, timeout=30).stdout
        second = subprocess.run([str(self.exe), "--identity"], capture_output=True, text=True, timeout=30).stdout
        self.assertEqual(first, second)
        self.assertNotIn(str(ROOT), first)
        self.assertNotRegex(first, r"\b20\d\d-\d\d-\d\d\b")

    def test_unknown_arguments_are_rejected(self):
        for argument in ("--boot", "--identity=1", "run"):
            run = subprocess.run([str(self.exe), argument], capture_output=True, text=True, timeout=30)
            self.assertEqual(run.returncode, 64, argument)
            self.assertIn("usage:", run.stderr)

    def run_headless(self, *args):
        return subprocess.run([str(self.exe), *args], capture_output=True, text=True, timeout=300)

    def test_running_a_rom_prints_each_unit_and_matches_the_recorded_picture(self):
        import struct
        import tempfile
        sys.path.insert(0, str(ROOT))
        from tests.scenarios.a500.display import Display, rom_source
        from tests.step.test_step import builder
        baselines = json.loads((ROOT / "tests/scenarios/a500/display-baselines.json").read_text())
        display = Display("lores-bars", mode="lores", bg=0x008, bars=((64, 0x00F), (128, 0xF00), (192, 0x0FF)))
        with tempfile.TemporaryDirectory(prefix="p6-headless-") as directory:
            rom = Path(directory) / "lores-bars.rom"
            rom.write_bytes(builder.build(rom_source(display)))
            dump = Path(directory) / "frames"
            dump.mkdir()
            run = self.run_headless("--run", str(rom), "--units", "6", "--dump", str(dump))
            self.assertEqual(run.returncode, 0, run.stderr)
            lines = run.stdout.splitlines()
            self.assertEqual(lines[-1], "done units=6")
            units = [re.fullmatch(r"unit (\d+) ticks=(\d+) video=1 active=756x574 parity=0 aspect=26/25 "
                                  r"cadence=3546895/70824 hash=([0-9a-f]{16}) audio=(\d+)", line) for line in lines[:-1]]
            self.assertTrue(all(units), lines)
            self.assertEqual([int(u[1]) for u in units], [1, 2, 3, 4, 5, 6])
            ticks = [int(u[2]) for u in units]
            self.assertTrue(all(b > a for a, b in zip(ticks, ticks[1:])))
            # The hash printed for a unit is FNV-1a of the active pixels; the dump is those pixels.
            data = (dump / "frame-006.bin").read_bytes()
            width, height, _, _ = struct.unpack("<4I", data[:16])
            self.assertEqual((width, height, len(data)), (756, 574, 16 + 756 * 574 * 4))
            fnv = 1469598103934665603
            for byte in data[16:]:
                fnv = ((fnv ^ byte) * 1099511628211) & 0xFFFFFFFFFFFFFFFF
            self.assertEqual(units[-1][3], "%016x" % fnv)
            # The same picture the STEP01 probe recorded as the PAL lores baseline (SHA-256 of the same bytes).
            self.assertEqual(hashlib.sha256(data[16:]).hexdigest(), baselines["pictures"]["lores-bars-pal"])
            # NTSC is a different machine clock and raster.
            ntsc = self.run_headless("--run", str(rom), "--units", "2", "--ntsc")
            self.assertEqual(ntsc.returncode, 0, ntsc.stderr)
            self.assertIn("active=756x484 parity=0 aspect=43/50 cadence=715909/11921", ntsc.stdout)

    def test_run_errors_are_reported_and_exit_nonzero(self):
        import tempfile
        with tempfile.TemporaryDirectory(prefix="p6-headless-err-") as directory:
            missing = self.run_headless("--run", str(Path(directory) / "absent.rom"))
            self.assertEqual(missing.returncode, 1)
            self.assertIn("absent.rom", missing.stderr)
            junk = Path(directory) / "junk.rom"
            junk.write_bytes(b"\0" * 1000)
            bad = self.run_headless("--run", str(junk))
            self.assertEqual(bad.returncode, 1)
            self.assertRegex(bad.stderr, r"p6_load failed \(6\): firmware is not an accepted Kickstart image")
        for arguments in (["--run"], ["--run", "x.rom", "--units", "0"], ["--run", "x.rom", "--bogus"]):
            with self.subTest(arguments=arguments):
                self.assertEqual(self.run_headless(*arguments).returncode, 64)

    @unittest.skipUnless(sys.platform == "darwin", "otool")
    def test_only_the_c_and_cpp_runtimes_are_linked(self):
        out = subprocess.run(["otool", "-L", str(self.exe)], check=True, capture_output=True, text=True).stdout
        libraries = [line.split(" (")[0].strip() for line in out.splitlines()[1:]]
        self.assertTrue(libraries)
        for library in libraries:
            self.assertTrue(library.startswith(ALLOWED_PREFIXES), library)
            self.assertFalse(any(f in library for f in FORBIDDEN_LIBRARIES), library)

    def test_the_build_graph_never_downloads(self):
        text = "\n".join(p.read_text() for p in [ROOT / "CMakeLists.txt", *(ROOT / "cmake").glob("*.cmake")])
        for forbidden in ("FetchContent", "ExternalProject", "file(DOWNLOAD", "git clone", "curl ", "wget "):
            self.assertNotIn(forbidden, text)


if __name__ == "__main__":
    unittest.main()
