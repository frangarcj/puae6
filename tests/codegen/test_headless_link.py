"""The headless runner links without host UI/audio/plugin libraries and reports its identity.

It does not boot a machine: `boot: not implemented` is part of the asserted output."""
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
        # Capability bits describe implemented behaviour: nothing is implemented yet.
        self.assertEqual(info["caps"], "0x0")
        self.assertEqual(info["boot"], "not implemented")

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
