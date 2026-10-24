"""Execute set_audio with real PCM, math and scheduler, without host pacing."""
from pathlib import Path
import platform
import subprocess
import tempfile
import unittest

from tests.host.test_audio_math import function

from tests.host import ensure_generated

ROOT = Path(__file__).resolve().parents[2]


class AudioConfigTests(unittest.TestCase):
    def test_set_audio(self):
        ensure_generated()
        tree = ROOT / "build/upstream"
        common = ["clang++", "-std=c++17", "-UNDEBUG", "-fno-fast-math",
                  "-I" + str(ROOT / "ports/portable"), "-I" + str(ROOT / "build/generated"),
                  "-I" + str(tree / "include"), "-I" + str(tree)]
        sources = [tree / "events.cpp", tree / "softfloat/softfloat.cpp",
                   tree / "softfloat/softfloat_fpsp.cpp"]
        sources += [ROOT / "ports/portable" / name for name in (
            "audio_math.cpp", "pcm_output.cpp", "sound_lifecycle.cpp", "drive_sound.cpp",
            "diagnostics.cpp", "gui.cpp")]
        with tempfile.TemporaryDirectory(prefix="p6-audio-config-") as tmp:
            tmp = Path(tmp)
            # This notification has no host dependency. Execute its real body;
            # compiling all cfgfile.cpp remains a separate, visible gate.
            cfg = (ROOT / "upstream/winuae/cfgfile.cpp").read_text()
            (tmp / "upstream_audio_config.h").write_text(function(cfg, "void set_config_changed("))
            obj, exe = tmp / "audio.o", tmp / "probe"
            for optimization in ("-O0", "-O2"):
                subprocess.run([*common, optimization, "-c", str(tree / "audio.cpp"), "-o", str(obj)],
                               check=True, timeout=60)
                imports = subprocess.run(["nm", "-u", str(obj)], text=True,
                                         capture_output=True, check=True).stdout
                for forbidden in ("compute_vsynctime", "sound_volume", "read_processor_time",
                                  "ripper", "pthread"):
                    self.assertNotIn(forbidden, imports)
                self.assertIn("p6_sound_validate_config", imports)
                strip = "-Wl,-dead_strip" if platform.system() == "Darwin" else "-Wl,--gc-sections"
                subprocess.run([*common, optimization, "-I" + str(tmp),
                                "-ffunction-sections", "-fdata-sections", strip,
                                "-fsanitize=address,undefined", "-fno-sanitize-recover=all",
                                str(ROOT / "tests/host/audio_config_probe.cpp"),
                                *map(str, sources), "-o", str(exe)], check=True, timeout=60)
                subprocess.run([str(exe)], check=True, timeout=30)


if __name__ == "__main__":
    unittest.main()
