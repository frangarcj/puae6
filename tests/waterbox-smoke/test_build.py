"""Offline tests for the Waterbox ELF build gate."""

import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TOOL = ROOT / "tools" / "build_waterbox.py"
sys.path.insert(0, str(TOOL.parent))
import build_waterbox  # noqa: E402


def elf64(loads, machine=183, elf_class=2, elf_type=2):
    """Make a minimal ELF64 little-endian executable with PT_LOAD headers."""
    ident = b"\x7fELF" + bytes((elf_class, 1, 1, 0)) + bytes(8)
    header = struct.pack("<16sHHIQQQIHHHHHH", ident, elf_type, machine, 1,
                          0, 64, 0, 0, 64, 56, len(loads), 0, 0, 0)
    phdrs = b"".join(struct.pack("<IIQQQQQQ", 1, flags, off, va, va,
                                    filesz, memsz, 0x4000)
                      for off, va, filesz, memsz, flags in loads)
    size = max([64 + len(phdrs)] + [off + filesz for off, _, filesz, _, _ in loads])
    data = bytearray(size)
    data[:64] = header
    data[64:64 + len(phdrs)] = phdrs
    return data


class BuildElfTests(unittest.TestCase):
    def check_bad(self, data):
        with tempfile.NamedTemporaryFile() as artifact:
            artifact.write(data)
            artifact.flush()
            with self.assertRaises(build_waterbox.BuildError):
                build_waterbox.validate_elf(artifact.name)

    def test_accepts_arm64_load_segments(self):
        with tempfile.NamedTemporaryFile() as artifact:
            artifact.write(elf64([(0x4000, 0x4000, 0x3000, 0x4000, 5),
                                  (0x8000, 0x8000, 0x2000, 0x4000, 6)]))
            artifact.flush()
            result = build_waterbox.validate_elf(artifact.name)
            self.assertIsInstance(result, dict)

    def test_rejects_non_arm64_and_non_elf64(self):
        self.check_bad(elf64([(0x4000, 0x4000, 0x4000, 0x4000, 5)], machine=62))
        self.check_bad(elf64([(0x4000, 0x4000, 0x4000, 0x4000, 5)], elf_class=1))

    def test_rejects_missing_load_and_misaligned_load(self):
        self.check_bad(elf64([]))
        self.check_bad(elf64([(0x4001, 0x4000, 0x4000, 0x4000, 5)]))
        self.check_bad(elf64([(0x4000, 0x4001, 0x4000, 0x4000, 5)]))

    def test_rejects_invalid_or_truncated_load_range(self):
        bad_size = elf64([(0x4000, 0x4000, 0x4001, 0x4000, 5)])
        self.check_bad(bad_size)
        complete = elf64([(0x4000, 0x4000, 0x4000, 0x4000, 5)])
        self.check_bad(complete[:24])
        truncated = complete[:-1]
        self.check_bad(truncated)

    def test_rejects_rwx_and_mixed_permissions_in_host_page(self):
        self.check_bad(elf64([(0x4000, 0x4000, 0x4000, 0x4000, 7)]))
        self.check_bad(elf64([(0x4000, 0x4000, 0x4000, 0x4000, 5),
                              (0x8000, 0x4000, 0x4000, 0x4000, 6)]))

    def test_cli_missing_artifact_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            missing = Path(directory) / "missing.wbx"
            result = subprocess.run([sys.executable, str(TOOL), "--check-artifact",
                                     str(missing)], capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("missing", (result.stdout + result.stderr).lower())


if __name__ == "__main__":
    unittest.main()
