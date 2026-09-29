import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

HERE = Path(__file__).parent / "microrom"
spec = importlib.util.spec_from_file_location("microrom_builder", HERE / "build_rom.py")
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)

SOURCE = (HERE / "boot.s").read_text(encoding="utf-8")
CODE = bytes.fromhex("46fc2700" "4ff80400" "13fc000300bfe201" "13fc000200bfe001" "5280" "60fc")


def sha256(data):
    return hashlib.sha256(data).hexdigest()


class MicroRomTests(unittest.TestCase):
    def test_encodings_are_the_documented_opcodes(self):
        self.assertEqual(builder.assemble(SOURCE), CODE)
        self.assertEqual(builder.assemble("MOVE.W #$2700,SR"), bytes.fromhex("46fc2700"))
        self.assertEqual(builder.assemble("LEA $400.W,A7"), bytes.fromhex("4ff80400"))  # A7 field 7 << 9
        self.assertEqual(builder.assemble("ADDQ.L #8,D3"), bytes.fromhex("5083"))       # 8 encodes as 0
        self.assertEqual(builder.assemble("ADDQ.L #1,D0"), bytes.fromhex("5280"))
        self.assertEqual(builder.assemble("l:\nNOP\nBRA.S l"), bytes.fromhex("4e7160fc"))

    def test_image_is_kickstart_shaped_with_a_valid_checksum(self):
        image = builder.build(SOURCE)
        self.assertEqual(len(image), 256 * 1024)
        self.assertEqual(image[:8], bytes.fromhex("11114ef900f800d2"))
        self.assertEqual(image[0xD2:0xD2 + len(CODE)], CODE)
        self.assertEqual(int.from_bytes(image[-20:-16], "big"), len(image))
        total = sum(int.from_bytes(image[i:i + 4], "big") for i in range(0, len(image), 4))
        while total >> 32:
            total = (total & 0xFFFFFFFF) + (total >> 32)
        self.assertEqual(total, 0xFFFFFFFF)

    def test_manifest_matches_the_files(self):
        manifest = json.loads((HERE / "manifest.json").read_text(encoding="utf-8"))
        image = builder.build(SOURCE)
        self.assertEqual(manifest["logical_id"], "microrom-boot-v1")
        self.assertEqual(manifest["source_sha256"], sha256(SOURCE.encode()))
        self.assertEqual(manifest["builder_sha256"], sha256((HERE / "build_rom.py").read_bytes()))
        self.assertEqual(manifest["output"]["sha256"], sha256(image))
        self.assertEqual(manifest["output"]["size_bytes"], len(image))
        self.assertEqual(manifest["source_license"], "MIT")

    def test_errors_and_cli(self):
        with self.assertRaisesRegex(ValueError, "unsupported syntax"):
            builder.assemble("RTS")
        with self.assertRaisesRegex(ValueError, "undefined label"):
            builder.assemble("BRA.S nowhere")
        with self.assertRaisesRegex(ValueError, "out of range: 0"):
            builder.assemble("BRA.S next\nnext: NOP")
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "boot.rom"
            subprocess.run([sys.executable, str(HERE / "build_rom.py"), str(HERE / "boot.s"),
                            "--output", str(output)], check=True)
            self.assertEqual(sha256(output.read_bytes()), sha256(builder.build(SOURCE)))
            self.assertEqual(list(Path(directory).iterdir()), [output])


if __name__ == "__main__":
    unittest.main()
