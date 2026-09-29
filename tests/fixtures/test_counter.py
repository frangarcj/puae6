import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


FIXTURE = Path(__file__).parent / "counter68k"
ASSEMBLER = FIXTURE / "assemble.py"
SOURCE = FIXTURE / "program.s"
MANIFEST = FIXTURE / "manifest.json"
EXPECTED_SHA256 = "f3804fbf95198daeda8ad2d1ca3eb72a8bfeca342d22685f1b80fbc88d6a6a62"

spec = importlib.util.spec_from_file_location("counter68k_assembler", ASSEMBLER)
assembler = importlib.util.module_from_spec(spec)
spec.loader.exec_module(assembler)


def sha256(data):
    return hashlib.sha256(data).hexdigest()


class CounterFixtureTests(unittest.TestCase):
    def setUp(self):
        self.manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        self.source = SOURCE.read_text(encoding="utf-8")

    def test_rebuild_and_manifest_integrity(self):
        output = assembler.compile(self.source)
        self.assertEqual(output, bytes.fromhex("70 00 52 80 60 fc"))
        self.assertEqual(len(output), self.manifest["output"]["size_bytes"])
        self.assertEqual(sha256(output), EXPECTED_SHA256)
        self.assertEqual(sha256(output), self.manifest["output"]["sha256"])
        self.assertEqual(sha256(self.source.encode()), self.manifest["source_sha256"])
        assembler_text = ASSEMBLER.read_text(encoding="utf-8")
        self.assertEqual(sha256(assembler_text.encode("utf-8")), self.manifest["assembler_sha256"])
        self.assertEqual(self.manifest["logical_id"], "counter68k-v1")
        self.assertEqual(self.manifest["kind"], "raw_program")
        self.assertEqual(self.manifest["load_address"], self.manifest["entry_address"])
        self.assertFalse(self.manifest["firmware_required"])

    def test_cli_writes_only_the_requested_output(self):
        with tempfile.TemporaryDirectory() as directory:
            output_path = Path(directory) / "counter.bin"
            subprocess.run([sys.executable, str(ASSEMBLER), "--output", str(output_path)],
                           check=True)
            self.assertEqual(output_path.read_bytes(), bytes.fromhex("70 00 52 80 60 fc"))

    def test_rejects_unknown_syntax_and_missing_label(self):
        with self.assertRaisesRegex(ValueError, "unsupported syntax"):
            assembler.compile("NOP")
        with self.assertRaisesRegex(ValueError, "undefined label"):
            assembler.compile("BRA.S missing")

    def test_rejects_zero_out_of_range_and_misaligned_branch(self):
        with self.assertRaisesRegex(ValueError, "out of range: 0"):
            assembler.compile("BRA.S next\nnext: ADDQ.L #1,D0")
        far_program = "BRA.S far\n" + "MOVEQ #0,D0\n" * 64 + "far: MOVEQ #0,D0"
        with self.assertRaisesRegex(ValueError, "out of range"):
            assembler.compile(far_program)
        with self.assertRaisesRegex(ValueError, "even 32-bit address"):
            assembler.compile(self.source, origin=0x1001)

    def test_rejects_empty_program_invalid_origin_and_address_overflow(self):
        with self.assertRaisesRegex(ValueError, "at least one instruction"):
            assembler.compile("; comments and labels alone are not a program\nempty:")
        for origin in (True, 4096.0, "4096"):
            with self.subTest(origin=origin), self.assertRaisesRegex(ValueError, "even 32-bit address"):
                assembler.compile(self.source, origin=origin)
        with self.assertRaisesRegex(ValueError, "past the 32-bit address space"):
            assembler.compile(self.source, origin=0xFFFFFFFE)
        self.assertEqual(len(assembler.compile(self.source, origin=0xFFFFFFFA)), 6)

    def test_source_mutation_changes_source_and_output_hashes(self):
        changed = self.source.replace("ADDQ.L #1,D0", "ADDQ.L #1,D0\n    ADDQ.L #1,D0")
        self.assertNotEqual(sha256(changed.encode()), self.manifest["source_sha256"])
        self.assertNotEqual(sha256(assembler.compile(changed)), self.manifest["output"]["sha256"])


if __name__ == "__main__":
    unittest.main()
