"""RTG-B6: real engine closure without exposing the unfinished public option."""
import json
from pathlib import Path
import shutil
import subprocess
import unittest

from tests.build_support import build_targets
from tests.host.test_bridge_lifecycle import run_probe


ROOT = Path(__file__).resolve().parents[2]
TOOLS = all(shutil.which(tool) for tool in ("cmake", "ninja", "clang", "clang++", "nm"))


@unittest.skipUnless(TOOLS, "toolchain unavailable")
class RtgLinkTests(unittest.TestCase):
    def test_fixed_profile_and_materialized_hooks(self):
        manifest = json.loads((ROOT / "manifests/source-manifest.json").read_text(encoding="utf-8"))
        self.assertIn("PICASSO96", manifest["selection"]["features_off"])

        cmake = (ROOT / "CMakeLists.txt").read_text(encoding="utf-8")
        for source in (
            "ports/portable/rtg_device.cpp",
            "ports/portable/rtg_memory.cpp",
            "ports/portable/rtg_uaegfx.cpp",
            "ports/portable/rtg_uaegfx_install.cpp",
        ):
            self.assertEqual(cmake.count(source), 1)
        self.assertIn("target_compile_definitions(p6_engine PRIVATE P6_STATE_BODIES P6_PORTABLE_RTG)", cmake)
        self.assertIn("target_compile_definitions(p6_portable PRIVATE P6_PORTABLE_RTG)", cmake)

        autoconf = (ROOT / "build/upstream/autoconf.cpp").read_text(encoding="utf-8")
        filesys = (ROOT / "build/upstream/filesys.cpp").read_text(encoding="utf-8")
        self.assertEqual(autoconf.count("P6_PORTABLE_RTG"), 2)
        self.assertEqual(filesys.count("P6_PORTABLE_RTG"), 2)
        self.assertIn("p6_uaegfx_install_code(rtarea_base + RTAREA_RTG)", autoconf)
        self.assertIn("Portable uaegfx resident exceeds its fixed RTAREA slot", autoconf)
        self.assertIn("p6_uaegfx_alloc(ctx)", filesys)
        self.assertIn("#elif defined(P6_PORTABLE_RTG)", autoconf)
        self.assertIn("#elif defined(P6_PORTABLE_RTG)", filesys)

    def test_real_headless_link_and_imports(self):
        executable = build_targets(
            ROOT / "build/link-audit", ["p6_headless"], sanitizers=False
        )["p6_headless"]
        identity = subprocess.run(
            [str(executable), "--identity"], capture_output=True, text=True, timeout=30
        )
        self.assertEqual(identity.returncode, 0, identity.stdout + identity.stderr)
        self.assertIn("profile: deterministic-a500", identity.stdout)
        self.assertIn("video: up to 760x576", identity.stdout)

        symbols = subprocess.run(
            ["nm", str(executable)], check=True, capture_output=True, text=True, timeout=30
        ).stdout.lower()
        self.assertIn("p6_uaegfx_install_code", symbols)
        for forbidden in (
            "d3d11createdevice",
            "getwritewatch",
            "resetwritewatch",
            "picasso96_alloc",
            "uaegfx_install_code",  # The upstream/PICASSO96 implementation has no p6_ prefix.
        ):
            if forbidden == "uaegfx_install_code":
                stripped = symbols.replace("p6_uaegfx_install_code", "")
                self.assertNotIn(forbidden, stripped)
            else:
                self.assertNotIn(forbidden, symbols)

        if shutil.which("otool"):
            dependencies = subprocess.run(
                ["otool", "-L", str(executable)], check=True, capture_output=True,
                text=True, timeout=30
            ).stdout.lower()
            for forbidden in ("appkit", "opengl", "metal.framework", "sdl", "qt"):
                self.assertNotIn(forbidden, dependencies)

        commands = json.loads((ROOT / "build/link-audit/compile_commands.json").read_text(encoding="utf-8"))
        by_file = {Path(entry["file"]).name: entry["command"] for entry in commands}
        for source in ("autoconf.cpp", "filesys.cpp", "expansion.cpp", "rtg_device.cpp",
                       "rtg_memory.cpp", "rtg_uaegfx.cpp", "rtg_uaegfx_install.cpp"):
            self.assertIn(source, by_file)
            self.assertIn("P6_PORTABLE_RTG", by_file[source])
        for excluded in ("picasso96.cpp", "gfxboard.cpp", "win32gfx.cpp"):
            self.assertNotIn(excluded, by_file)

    def test_default_profile_and_public_rtg_rejection(self):
        executable = build_targets(
            ROOT / "build/link-audit", ["p6_media_probe"], sanitizers=False
        )["p6_media_probe"]
        result = run_probe(executable)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("media ok", result.stdout)


if __name__ == "__main__":
    unittest.main()
