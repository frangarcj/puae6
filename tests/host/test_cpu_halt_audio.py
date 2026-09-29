"""Halted CPU must leave Paula running on the shared emulated timeline."""
from pathlib import Path
import platform
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]


class CPUHaltAudioTests(unittest.TestCase):
    def test_halt_keeps_audio_state_and_time(self):
        subprocess.run(["python3", "tools/generate_cpu.py"], cwd=ROOT, check=True,
                       stdout=subprocess.PIPE, timeout=120)
        tree = ROOT / "build/upstream"
        common = ["clang++", "-std=c++17", "-UNDEBUG", "-fno-fast-math",
                  "-I" + str(ROOT / "ports/portable"), "-I" + str(ROOT / "build/generated"),
                  "-I" + str(tree / "include"), "-I" + str(tree)]
        sources = [ROOT / "tests/host/cpu_halt_audio_probe.cpp", tree / "events.cpp"]
        sources += [ROOT / "ports/portable" / name for name in (
            "atomic.cpp", "pcm_output.cpp", "sound_lifecycle.cpp", "diagnostics.cpp", "gui.cpp")]
        strip = "-Wl,-dead_strip" if platform.system() == "Darwin" else "-Wl,--gc-sections"
        with tempfile.TemporaryDirectory(prefix="p6-halt-audio-") as tmp:
            exe, obj = Path(tmp) / "probe", Path(tmp) / "cpu.o"
            for optimization in ("-O0", "-O2"):
                subprocess.run([*common, optimization, "-ffunction-sections", "-fdata-sections", strip,
                                "-fsanitize=address,undefined", "-fno-sanitize-recover=all",
                                *map(str, sources), "-o", str(exe)], check=True, timeout=60)
                subprocess.run([str(exe)], check=True, timeout=30)
                subprocess.run([*common, optimization, "-c", str(tree / "newcpu.cpp"), "-o", str(obj)],
                               check=True, timeout=60)
                imports = subprocess.run(["nm", "-u", str(obj)], text=True,
                                         capture_output=True, check=True).stdout
                self.assertNotIn("audio_deactivate", imports)
                self.assertNotIn("pause_sound", imports)
                self.assertNotIn("cpu_halt", imports)
                symbols = subprocess.run(["nm", "-g", str(obj)], text=True,
                                         capture_output=True, check=True).stdout
                self.assertRegex(symbols, r" T \S*cpu_halt\S*")
                self.assertRegex(symbols, r" T \S*cpu_inreset\S*")


if __name__ == "__main__":
    unittest.main()
