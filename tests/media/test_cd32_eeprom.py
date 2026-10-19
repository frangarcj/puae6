"""Real WinUAE 24C08, with the existing portable VFS/diagnostics/LED services."""
from pathlib import Path
import platform
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
from materialize_upstream import materialize


class Cd32EepromTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        (ROOT / "build").mkdir(exist_ok=True)
        cls.temp = tempfile.TemporaryDirectory(prefix="cd32-eeprom-", dir=ROOT / "build")
        cls.addClassCleanup(cls.temp.cleanup)
        tmp = Path(cls.temp.name)
        _, tree = materialize(ROOT, str((tmp / "upstream").relative_to(ROOT)))
        flags = ["-O1", "-g", "-UNDEBUG", "-fsanitize=address,undefined",
                 "-fno-sanitize-recover=all", "-ffunction-sections", "-fdata-sections"]
        blob = tmp / "blob.o"
        subprocess.run(["clang", "-std=c11", *flags, "-c", str(ROOT / "ports/portable/blob_io.c"),
                        "-o", str(blob)], check=True, timeout=30)
        strip = "-Wl,-dead_strip" if platform.system() == "Darwin" else "-Wl,--gc-sections"
        cls.exe = tmp / "probe"
        sources = [ROOT / "tests/media/cd32_eeprom_probe.cpp", tree / "flashrom.cpp", tree / "zfile.cpp",
                   ROOT / "ports/portable/gui.cpp", ROOT / "ports/portable/diagnostics.cpp"]
        subprocess.run(["clang++", "-std=c++17", *flags, "-Werror=format", strip,
                        "-I" + str(ROOT / "ports/portable"), "-I" + str(tree / "include"),
                        "-I" + str(tree), *map(str, sources), str(blob), "-o", str(cls.exe)],
                       check=True, timeout=60)

    def run_probe(self, case):
        subprocess.run([str(self.exe), case], check=True, timeout=30)

    def test_protocol_page_and_address_wrap(self):
        self.run_probe("protocol")

    def test_restore_between_every_pin_transition(self):
        self.run_probe("restore")

    def test_invalid_states_are_transactional(self):
        self.run_probe("reject")
