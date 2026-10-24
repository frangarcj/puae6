"""Complete CIA compilation and isolated emulated TOD/timer execution."""
from pathlib import Path
import platform
import re
import subprocess
import tempfile
import unittest

from tests.host import ensure_generated

ROOT = Path(__file__).resolve().parents[2]


class CIATests(unittest.TestCase):
    def test_a500_object_does_not_reference_excluded_hardware(self):
        ensure_generated()
        tree = ROOT / "build/upstream"
        pristine = (ROOT / "upstream/winuae/cia.cpp").read_text(errors="replace")
        excluded = ("akiko_mute", "parallel_port_scsi", "parallel_port_scsi_read",
                    "cfgfile_resolve_path_out_load", "inprec_recorddebug_cia",
                    "inprec_playdebug_cia", "input_play", "input_record", "inputrecord_debug")
        for name in excluded:  # the test cannot pass vacuously
            self.assertIn(name, pristine)
        with tempfile.TemporaryDirectory(prefix="p6-cia-imports-") as tmp:
            obj = Path(tmp) / "cia.o"
            for optimization in ("-O0", "-O2"):
                subprocess.run(["clang++", "-std=c++17", optimization,
                                "-I" + str(ROOT / "ports/portable"),
                                "-I" + str(ROOT / "build/generated"),
                                "-I" + str(tree / "include"), "-I" + str(tree),
                                "-c", str(tree / "cia.cpp"), "-o", str(obj)],
                               check=True, timeout=60)
                imports = subprocess.run(["nm", "-u", "-C", str(obj)], capture_output=True,
                                         text=True, check=True).stdout
                names = {re.sub(r"\(.*", "", line.strip()).lstrip("_")
                         for line in imports.splitlines()}
                self.assertFalse(names & set(excluded), (optimization, names & set(excluded)))

    def test_portable_tod_and_timers(self):
        ensure_generated()
        tree = ROOT / "build/upstream"
        common = ["clang++", "-std=c++17", "-UNDEBUG", "-Werror=format",
                  "-I" + str(ROOT / "ports/portable"), "-I" + str(ROOT / "build/generated"),
                  "-I" + str(tree / "include"), "-I" + str(tree)]
        with tempfile.TemporaryDirectory(prefix="p6-cia-") as tmp:
            obj, exe = Path(tmp) / "cia.o", Path(tmp) / "probe"
            subprocess.run([*common, "-O0", "-c", str(tree / "cia.cpp"), "-o", str(obj)],
                           check=True, timeout=30)
            imports = subprocess.run(["nm", "-u", str(obj)], capture_output=True,
                                     text=True, check=True).stdout
            for forbidden in ("gettimeofday", "clock_gettime", "pthread", "sleep"):
                self.assertNotIn(forbidden, imports)
            # Includes RTC callers as well as TOD: no hidden civil host clock.
            clock_imports = {line.split()[-1].lstrip("_") for line in imports.splitlines()
                             if line.split()[-1].lstrip("_") in {"time", "localtime", "gmtime", "mktime"}}
            self.assertFalse(clock_imports)
            # Syntax proof of the retained parallel branch, not host device support.
            subprocess.run([*common, "-DPARALLEL_PORT", "-fsyntax-only", str(tree / "cia.cpp")],
                           check=True, timeout=30)
            for optimization in ("-O0", "-O2"):
                strip = "-Wl,-dead_strip" if platform.system() == "Darwin" else "-Wl,--gc-sections"
                # Whole unit included; only the probe uses selective linkage.
                subprocess.run([*common, optimization, "-ffunction-sections", "-fdata-sections",
                                "-fsanitize=address,undefined", "-fno-sanitize-recover=all", strip,
                                str(ROOT / "tests/host/cia_probe.cpp"), "-o", str(exe)],
                               check=True, timeout=60)
                subprocess.run([str(exe)], check=True, timeout=30)


if __name__ == "__main__":
    unittest.main()
