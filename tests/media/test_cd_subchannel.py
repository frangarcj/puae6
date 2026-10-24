"""Synthetic P/Q vs Python's independent CRC primitive; no engine or host I/O."""
import binascii
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]

def oracle(number, relative, absolute, pause, control):
    def bcd(value):
        return int(f"{value:02d}", 16)
    def msf(frames):
        minutes, rest = divmod(frames, 4500)
        seconds, fraction = divmod(rest, 75)
        return bytes(map(bcd, (minutes, seconds, fraction)))
    q = bytes((control * 16 + 1, bcd(number), 0 if relative < 0 else 1)) + msf(abs(relative)) + b"\0" + msf(absolute)
    q += (binascii.crc_hqx(q, 0) ^ 0xffff).to_bytes(2, "big")
    return bytes([255 if pause else 0]) * 12 + q + bytes(72)

@unittest.skipUnless(shutil.which("clang++"), "clang++ required")
class CdSubchannelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        (ROOT / "build").mkdir(exist_ok=True)
        cls.temp = tempfile.TemporaryDirectory(prefix="cd-sub-", dir=ROOT / "build")
        cls.addClassCleanup(cls.temp.cleanup)
        cls.exe = Path(cls.temp.name) / "probe"
        subprocess.run(["clang++", "-std=c++17", "-O1", "-g", "-Wall", "-Wextra", "-Werror",
                        "-fsanitize=address,undefined", "-fno-sanitize-recover=all", "-fno-exceptions", "-fno-rtti",
                        "-I" + str(ROOT / "ports/portable/media"), str(ROOT / "ports/portable/media/cd_subchannel.cpp"),
                        str(ROOT / "tests/media/cd_subchannel_probe.cpp"), "-o", str(cls.exe)], check=True, timeout=60)

    def check_cases(self, cases):
        result = subprocess.run([str(self.exe)], input="".join(" ".join(map(str, case)) + "\n" for case, _, _ in cases),
                                capture_output=True, text=True, check=True, timeout=30)
        self.assertEqual(len(cases), len(result.stdout.splitlines()))
        for (case, status, expected), line in zip(cases, result.stdout.splitlines()):
            with self.subTest(case=case):
                code, data = line.split()
                self.assertEqual(status, int(code))
                self.assertEqual(b"\xcc" + expected + b"\xcc", bytes.fromhex(data))

    def test_positions_pause_effective_control_and_msf_boundaries(self):
        cases = []
        for stored in (0, 1):
            for lba in (0, 74, 75, 149, 150, 151, 4499, 4500, 9999):
                for number, control in ((1, 4), (9, 1), (10, 3), (99, 0)):
                    for pause in (0, 1):  # Includes INDEX01/postgap pause; never inferred from index.
                        case = (0, 150, 10000, number, lba, pause, control, 1, stored, 96, 0)
                        cases.append((case, 0, oracle(number, lba - 150, lba + 150, pause, control)))
        # Effective control differs from this data track without changing track/index/range.
        cases.append(((0, 300, 1000, 2, 0, 1, 0, 1, 0, 96, 0), 0, oracle(2, -300, 150, True, 0)))
        cases.append(((0, 150, 1000, 2, 999, 1, 4, 1, 0, 96, 0), 0, oracle(2, 849, 1149, True, 4)))  # Explicit postgap pause.
        cases.append(((0, 449999, 450000, 99, 0, 1, 4, 1, 0, 96, 0), 0, oracle(99, -449999, 150, True, 4)))
        cases.append(((0, 0, 449850, 99, 449849, 1, 15, 1, 1, 97, 0), 0, oracle(99, 449849, 449999, True, 15)))
        self.assertEqual("410101000000000002002832", oracle(1, 0, 150, False, 4)[12:24].hex())
        self.check_cases(cases)

    def test_invalid_ranges_overflow_and_small_buffers_preserve_output(self):
        base = [0, 150, 1000, 1, 150, 0, 4, 1, 0, 96, 0]
        cases = []
        for field, value, status in ((0, 151, 1), (1, 1000, 1), (2, 0, 1), (3, 0, 1), (3, 100, 1),
                                      (4, 1000, 3), (4, 0xffffffff, 3), (6, 16, 1), (7, 0, 1), (7, 2, 1),
                                      (9, 0, 1), (9, 95, 1), (10, 1, 1)):
            case = base.copy(); case[field] = value
            cases.append((case, status, b"\xcc" * 96))
        for case, status in (((200, 300, 400, 1, 199, 0, 4, 1, 0, 96, 0), 3),
                             ((0, 0, 0xffffffff, 1, 449850, 0, 4, 1, 0, 96, 0), 5),
                             ((0, 0xfffffffe, 0xffffffff, 1, 0, 0, 4, 1, 0, 96, 0), 5)):
            cases.append((case, status, b"\xcc" * 96))
        self.check_cases(cases)
