"""RTC byte handlers on the real scheduler, not a full-machine state proof."""
import os
from pathlib import Path
import platform
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]


class RTCTests(unittest.TestCase):
    def test_registers_clock_and_restore(self):
        subprocess.run(["python3", "tools/generate_cpu.py"], cwd=ROOT, check=True,
                       stdout=subprocess.PIPE, timeout=120)
        tree = ROOT / "build/upstream"
        common = ["clang++", "-std=c++17", "-UNDEBUG", "-Werror=format",
                  "-I" + str(ROOT / "ports/portable"), "-I" + str(ROOT / "build/generated"),
                  "-I" + str(tree / "include"), "-I" + str(tree)]
        with tempfile.TemporaryDirectory(prefix="p6-rtc-") as tmp:
            obj, exe = Path(tmp) / "unit.o", Path(tmp) / "probe"
            for source in (tree / "cia.cpp", tree / "rtc.cpp", ROOT / "ports/portable/rtc_time.cpp"):
                subprocess.run([*common, "-O0", "-c", str(source), "-o", str(obj)], check=True)
                imports = subprocess.run(["nm", "-u", str(obj)], capture_output=True,
                                         text=True, check=True).stdout
                symbols = {line.split()[-1].lstrip("_") for line in imports.splitlines()}
                self.assertFalse(symbols & {"time", "localtime", "gmtime", "mktime", "gettimeofday", "clock_gettime"})
            for optimization in ("-O0", "-O2"):
                flags = [optimization, "-fsanitize=address,undefined", "-fno-sanitize-recover=all",
                         "-ffunction-sections", "-fdata-sections"]
                objects = []
                for name in ("civil_time", "blob_io"):
                    target = Path(tmp) / (name + ".o")
                    subprocess.run(["clang", "-std=c11", *flags, "-c",
                                    str(ROOT / ("ports/portable/" + name + ".c")), "-o", str(target)], check=True)
                    objects.append(str(target))
                strip = "-Wl,-dead_strip" if platform.system() == "Darwin" else "-Wl,--gc-sections"
                sources = [ROOT / "tests/host/rtc_probe.cpp", tree / "rtc.cpp", tree / "events.cpp",
                           tree / "zfile.cpp", ROOT / "ports/portable/rtc_time.cpp", ROOT / "ports/portable/diagnostics.cpp"]
                subprocess.run([*common, *flags, strip, *map(str, sources), *objects, "-o", str(exe)],
                               check=True, timeout=60)
                for tz in ("UTC", "Europe/Madrid", "Pacific/Honolulu"):
                    subprocess.run([str(exe)], env=dict(os.environ, TZ=tz), check=True, timeout=30)


if __name__ == "__main__":
    unittest.main()
