"""PCM transport and real stereo mixer; not DMA, filter or frame accuracy."""
from pathlib import Path
import platform
import subprocess
import tempfile
import unittest

from tests.host import ensure_generated

ROOT = Path(__file__).resolve().parents[2]


class PCMTests(unittest.TestCase):
    def test_bounded_stereo_output(self):
        ensure_generated()
        tree = ROOT / "build/upstream"
        common = ["clang++", "-std=c++17", "-UNDEBUG", "-Werror=format",
                  "-I" + str(ROOT / "ports/portable"), "-I" + str(ROOT / "build/generated"),
                  "-I" + str(tree / "include"), "-I" + str(tree)]
        deps = subprocess.run([*common, "-M", str(tree / "audio.cpp")],
                              text=True, capture_output=True, check=True).stdout
        self.assertIn("ports/portable/sounddep/sound.h", deps)
        self.assertNotIn("od-win32/sounddep", deps)
        self.assertNotIn("threaddep", deps)
        with tempfile.TemporaryDirectory(prefix="p6-pcm-") as tmp:
            obj, exe = Path(tmp) / "audio.o", Path(tmp) / "probe"
            for optimization in ("-O0", "-O2"):
                subprocess.run([*common, optimization, "-c", str(tree / "audio.cpp"), "-o", str(obj)],
                               check=True, timeout=60)
                imports = subprocess.run(["nm", "-u", str(obj)], text=True,
                                         capture_output=True, check=True).stdout
                self.assertNotIn("ripper", imports)
                self.assertNotIn("zfile_fopen", imports)
                strip = "-Wl,-dead_strip" if platform.system() == "Darwin" else "-Wl,--gc-sections"
                for probe in ("pcm_probe.cpp", "audio_scale_probe.cpp", "sound_lifecycle_probe.cpp"):
                    extra = []
                    if probe == "sound_lifecycle_probe.cpp":
                        extra = [str(tree / "events.cpp"),
                                 str(ROOT / "ports/portable/sound_lifecycle.cpp"),
                                 str(ROOT / "ports/portable/diagnostics.cpp")]
                    subprocess.run([*common, optimization, "-ffunction-sections", "-fdata-sections", strip,
                                    "-fsanitize=address,undefined", "-fno-sanitize-recover=all",
                                    str(ROOT / "tests/host" / probe),
                                    str(ROOT / "ports/portable/pcm_output.cpp"), *extra, "-o", str(exe)],
                                   check=True, timeout=60)
                    subprocess.run([str(exe)], check=True, timeout=30)


if __name__ == "__main__":
    unittest.main()
