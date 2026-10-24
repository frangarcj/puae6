"""Unit-level scheduler execution, not CPU/chipset/frame equivalence."""
import hashlib
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
from materialize_upstream import materialize


class EventTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        key = hashlib.sha256((ROOT / "patches/winuae/series.json").read_bytes()).hexdigest()
        _, cls.tree = materialize(ROOT, "build/t06-events/" + key)

    def test_complete_event_unit_and_host_free_imports(self):
        with tempfile.TemporaryDirectory(prefix="p6-events-") as tmp:
            obj, exe = Path(tmp) / "events.o", Path(tmp) / "probe"
            common = ["clang++", "-std=c++17", "-I" + str(ROOT / "ports/portable"),
                      "-I" + str(self.tree / "include"), "-I" + str(self.tree)]
            for optimization in ("-O0", "-O2"):
                flags = [optimization, "-fsanitize=address,undefined", "-fno-sanitize-recover=all"]
                subprocess.run([*common, *flags, "-c", str(self.tree / "events.cpp"),
                                "-o", str(obj)], check=True)
                subprocess.run([*common, *flags, str(ROOT / "tests/host/events_probe.cpp"),
                                str(obj), "-o", str(exe)], check=True)
                subprocess.run([str(exe)], check=True, timeout=20)
            # No dead stripping: every reference in the complete unit is checked.
            subprocess.run([*common, "-O0", "-c", str(self.tree / "events.cpp"),
                            "-o", str(obj)], check=True)
            result = subprocess.run(["nm", "-u", str(obj)], capture_output=True, text=True, check=True)
            imports = {line.split()[-1].lstrip("_") for line in result.stdout.splitlines() if line.strip()}
            self.assertEqual(imports, {
                "Z11gui_messagePKcz", "Z13do_cycles_ccki", "Z24event_doint_delay_do_extj",
                "Z9write_logPKcz", "currprefs", "eventtab", "eventtab2", "pissoff"})


if __name__ == "__main__":
    unittest.main()
