import hashlib
import importlib.util
import json
from pathlib import Path
import re
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
HEADER = "; Original PUAE6 micro-ROM, licensed under MIT; see LICENSE."
SCENARIO_NAMES = {"vsync", "copper_irq", "long_ops", "halt", "stop", "lof_stuck", "long_frame", "input_all", "input_rotate"}


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def scenario(name):
    """(manifest entry, source text, image) of a scenario ROM built with its recorded default defines."""
    entry = json.loads((HERE / "manifest.json").read_text(encoding="utf-8"))["scenarios"][name]
    source = (HERE / entry["program_source"]).read_text(encoding="utf-8")
    return entry, source, builder.build(source, entry["defines"])


def checksum_total(image):
    total = sum(int.from_bytes(image[i:i + 4], "big") for i in range(0, len(image), 4))
    while total >> 32:
        total = (total & 0xFFFFFFFF) + (total >> 32)
    return total


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

    def test_scenario_encodings_are_the_documented_opcodes(self):
        # Expected words come from the 68000 programmer's reference, not from the assembler.
        for text, expected in [
            ("MOVE.W #$C020,$DFF09A.L", "33fc" "c020" "00dff09a"),
            ("MOVE.W #$0020,$DFF09C.L", "33fc" "0020" "00dff09c"),
            ("MOVE.L #$12345678,$6C.L", "23fc" "12345678" "0000006c"),
            ("MOVE.B #-128,$10.L", "13fc" "0080" "00000010"),
            ("MOVE.B #255,$10.L", "13fc" "00ff" "00000010"),
            ("MOVE.L D0,(A0)+", "20c0"),
            ("MOVE.L D7,(A3)+", "26c7"),
            ("MOVE.L D1,(SP)+", "2ec1"),
            ("MOVEA.L #$1001,A7", "2e7c" "00001001"),
            ("MOVEA.L #0,A0", "207c" "00000000"),
            ("MOVEA.L #$1001,sp", "2e7c" "00001001"),
            ("MOVE.L #7,D2", "243c" "00000007"),
            ("MOVE.L #$12345678,D0", "203c" "12345678"),
            ("MOVE.L #-1,D7", "2e3c" "ffffffff"),
            ("LEA $1000.W,A0", "41f8" "1000"),
            ("LEA $10.W,A3", "47f8" "0010"),
            ("LEA $10.W,SP", "4ff8" "0010"),
            ("DIVU.W D2,D3", "86c2"),
            ("DIVU.W D0,D0", "80c0"),
            ("DIVU.W D7,D6", "8cc7"),
            ("NOP", "4e71"),
            ("RTE", "4e73"),
            ("STOP #$2000", "4e72" "2000"),
            ("TRAP #0", "4e40"),
            ("TRAP #15", "4e4f"),
            ("MOVEM.L D0-D7/A0-A6,-(SP)", "48e7" "fffe"),
            ("MOVEM.L (SP)+,D0-D7/A0-A6", "4cdf" "7fff"),
            ("MOVEM.L D0-D3/A0/A2-A4,-(A7)", "48e7" "f0b8"),
            ("MOVEM.L (A1)+,D0-D3/A0/A2-A4", "4cd9" "1d0f"),
            ("MOVEM.L D0,-(sp)", "48e7" "8000"),
            ("MOVEM.L A7,-(SP)", "48e7" "0001"),
            ("MOVEM.L (SP)+,A7", "4cdf" "8000"),
            ("MOVEM.L (SP)+,D0", "4cdf" "0001"),
            ("MOVEM.L A0-A7,-(A2)", "48e2" "00ff"),  # predecrement mirrors the mask: A0 is bit 7
        ]:
            with self.subTest(text=text):
                self.assertEqual(builder.assemble(text), bytes.fromhex(expected))

    def test_wide_branch_and_branch_ranges(self):
        # BRA.W measures its 16-bit displacement from the address of the displacement word.
        self.assertEqual(builder.assemble("BRA.W l\nNOP\nl: NOP"), bytes.fromhex("6000" "0004" "4e71" "4e71"))
        self.assertEqual(builder.assemble("l: NOP\nBRA.W l"), bytes.fromhex("4e71" "6000" "fffc"))
        self.assertEqual(len(builder.assemble("BRA.W l\n.nops 16382\nl: NOP")), 4 + 2 * 16382 + 2)  # +32766
        self.assertEqual(len(builder.assemble("l: .nops 16383\nBRA.W l")), 2 * 16383 + 4)  # -32768
        with self.assertRaisesRegex(ValueError, "out of range: 32768"):
            builder.assemble("BRA.W l\n.nops 16383\nl: NOP")
        with self.assertRaisesRegex(ValueError, "out of range: -32770"):
            builder.assemble("l: .nops 16384\nBRA.W l")
        self.assertEqual(builder.assemble("BRA.W l\nl: NOP"), bytes.fromhex("6000" "0002" "4e71"))  # next instruction
        # The short form keeps its limits: +126 and -128.
        self.assertEqual(builder.assemble("BRA.S l\n.nops 63\nl: NOP")[:2], bytes.fromhex("607e"))
        self.assertEqual(builder.assemble("l: .nops 63\nBRA.S l")[-2:], bytes.fromhex("6080"))
        with self.assertRaisesRegex(ValueError, "out of range: 128"):
            builder.assemble("BRA.S l\n.nops 64\nl: NOP")
        with self.assertRaisesRegex(ValueError, "undefined label"):
            builder.assemble("BRA.W nowhere")

    def test_labels_and_expressions_in_values(self):
        # A label is its absolute ROM address: base 0xF80000 + entry 0xD2 + offset.
        self.assertEqual(builder.assemble("MOVE.L #handler,$6C.L\nhandler: NOP"),
                         bytes.fromhex("23fc" "00f800dc" "0000006c" "4e71"))
        self.assertEqual(builder.assemble("Handler: NOP\nMOVE.L #HANDLER,D0"), bytes.fromhex("4e71" "203c" "00f800d2"))
        self.assertEqual(builder.assemble("NOP\nx: NOP\n.long x, x+2"), bytes.fromhex("4e714e71" "00f800d4" "00f800d6"))
        self.assertEqual(builder.assemble("MOVE.L #x,x.L\nx: NOP"), bytes.fromhex("23fc" "00f800dc" "00f800dc" "4e71"))
        self.assertEqual(builder.assemble(".word 1+2*3, (1+2)*3, 8-3-2, 1<<2+1, $10>>2|1, $F0&$3C, -1, -(2)"),
                         bytes.fromhex("0007" "0009" "0003" "0008" "0005" "0030" "ffff" "fffe"))
        self.assertEqual(builder.assemble("MOVE.L #((($04<<8)|(2<<1)|1)<<16)|$FFFE,$2000.L"),
                         bytes.fromhex("23fc" "0405fffe" "00002000"))
        self.assertEqual(builder.assemble(".long $12345678,0,4294967295,-1"),
                         bytes.fromhex("12345678" "00000000" "ffffffff" "ffffffff"))

    def test_value_ranges_and_bad_expressions(self):
        for text, message in [
            (".word 65536", "out of range: 65536"),
            (".word -32769", "out of range: -32769"),
            (".long 4294967296", "out of range: 4294967296"),
            ("MOVE.B #256,$10.L", "out of range: 256"),
            ("MOVE.B #-129,$10.L", "out of range: -129"),
            ("MOVE.W #65536,SR", "out of range: 65536"),
            ("STOP #$10000", "out of range: 65536"),
            ("MOVE.L #1,-4.L", "absolute address out of range: -4"),
            ("LEA $8000.W,A0", "LEA absolute word out of range: 32768"),
            ("LEA x.W,A0\nx: NOP", "LEA absolute word out of range"),
            ("TRAP #16", "TRAP vector out of range: 16"),
            (".nops 131073", r"\.nops count out of range"),
            (".word missing", "undefined label missing"),
            (".word 1<<33", "shift count out of range: 33"),
            (".word 1/2", "unsupported syntax"),
            (".word (1+2", "unsupported syntax"),
            (".word 1+2)", "unsupported syntax"),
            (".word 1,,2", "unsupported syntax"),
            (".word 1 2", "unsupported syntax"),
            (".word", "unsupported syntax"),
            ("MOVE.L #@X@,D0", "unsupported syntax"),  # assemble() never substitutes
        ]:
            with self.subTest(text=text):
                with self.assertRaisesRegex(ValueError, message):
                    builder.assemble(text)
        with self.assertRaisesRegex(ValueError, "line 3: undefined label"):
            builder.assemble("NOP\nNOP\nMOVE.L #nowhere,D0")
        with self.assertRaisesRegex(ValueError, "duplicate label"):
            builder.assemble("a: NOP\nA: NOP")

    def test_everything_outside_the_subset_is_rejected(self):
        for text in [
            "RTS", "MOVE.L D0,D1", "MOVE.W #1,D0", "MOVE.B #1,D0", "MOVEA.W #1,A0", "MOVEA.L #1,D0", "MOVE.L D0,(A8)+",
            "MOVE.L A0,(A1)+", "MOVE.W D0,D1", "MOVE.L #1,$10.W", "DIVU D2,D3", "DIVU.W #1,D0", "DIVU.W D0,A1",
            "DIVS.W D2,D3", "ADDQ.L #9,D0", "ADDQ.L #0,D0", "ADDQ.W #1,D0", "STOP D0", "STOP", "TRAP 0", "TRAP #-1",
            "BRA label", "BRA.L label", "BRA.S 5", "BNE.S l", "MOVEM.W D0,-(SP)", "MOVEM.L D0,(SP)", "MOVEM.L (SP),D0",
            "MOVEM.L -(SP),D0", "MOVEM.L D0-A3,-(SP)", "MOVEM.L D3-D0,-(SP)", "MOVEM.L D0-D8,-(SP)", "MOVEM.L D0/,-(SP)",
            "MOVEM.L (A9)+,D0", "MOVEM.L (SP)+,", ".align 3", ".align", ".nops", ".nops -1", ".nops l", ".byte 1",
            "LEA 4.L,A0", "LEA (A0),A1", "lea 4.w a0", "NOP NOP",
        ]:
            with self.subTest(text=text):
                with self.assertRaisesRegex(ValueError, "unsupported syntax"):
                    builder.assemble(text)

    def test_nops_align_and_data_directives(self):
        self.assertEqual(builder.assemble(".nops 3"), bytes.fromhex("4e71" * 3))
        self.assertEqual(builder.assemble(".NOPS $2"), bytes.fromhex("4e71" * 2))
        self.assertEqual(builder.assemble(".nops 0"), b"")
        self.assertEqual(builder.assemble("l: .nops 2\nBRA.S l"), bytes.fromhex("4e71" "4e71" "60fa"))
        self.assertEqual(builder.assemble("NOP\n.align 2\nNOP"), bytes.fromhex("4e714e71"))
        self.assertEqual(builder.assemble(".word $1234\n.WORD 1, 2 ; trailing comment"), bytes.fromhex("1234" "0001" "0002"))

    def test_templates(self):
        self.assertEqual(builder.substitute("a @HP@ b @HP@", {"HP": 2}), "a 2 b 2")
        self.assertEqual(builder.substitute("MOVE.W #@V@,SR", {"V": "$2700"}), "MOVE.W #$2700,SR")
        self.assertEqual(builder.substitute("nothing to do"), "nothing to do")
        self.assertEqual(builder.substitute("nothing to do", {}), "nothing to do")
        self.assertEqual(builder.build(SOURCE), builder.build(SOURCE, {}))
        with self.assertRaisesRegex(ValueError, "undefined placeholder @HP@"):
            builder.substitute("a @HP@", {"PAD": 1})
        with self.assertRaisesRegex(ValueError, "undefined placeholder @HP@"):
            builder.build("a @HP@")
        with self.assertRaisesRegex(ValueError, "unused define: B, C"):
            builder.substitute("@A@", {"A": 1, "B": 2, "C": 3})
        with self.assertRaisesRegex(ValueError, "bad define name"):
            builder.substitute("x", {"1X": 1})
        with self.assertRaisesRegex(ValueError, "bad value"):
            builder.substitute("@A@", {"A": "1\nNOP"})
        with self.assertRaisesRegex(ValueError, "bad value"):
            builder.substitute("@A@", {"A": ""})
        # A placeholder is replaced once: a value that looks like a placeholder is not expanded again.
        self.assertEqual(builder.substitute("@A@", {"A": "@B@"}), "@B@")
        # The parametrised ROMs change only where their parameter goes.
        copper = (HERE / "copper_irq.s").read_text(encoding="utf-8")
        for hp, wait in [(0, "0401"), (2, "0405"), (3, "0407"), (127, "04ff")]:
            with self.subTest(hp=hp):
                self.assertIn(bytes.fromhex("23fc" + wait + "fffe" + "00002000"), builder.assemble(builder.substitute(copper, {"HP": hp})))
        long_ops = (HERE / "long_ops.s").read_text(encoding="utf-8")
        lengths = [len(builder.assemble(builder.substitute(long_ops, {"PAD": pad}))) for pad in range(6)]
        self.assertEqual(lengths, [lengths[0] + 2 * pad for pad in range(6)])
        with self.assertRaises(ValueError):
            builder.build(copper)  # the template needs its parameter
        with self.assertRaisesRegex(ValueError, "unused define"):
            builder.build(SOURCE, {"HP": 2})

    def test_every_scenario_is_a_valid_rom_and_matches_the_manifest(self):
        manifest = json.loads((HERE / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(set(manifest["scenarios"]), SCENARIO_NAMES)
        self.assertEqual(manifest["builder_sha256"], sha256((HERE / "build_rom.py").read_bytes()))
        self.assertEqual({"vsync": "S2", "copper_irq": "S3", "long_ops": "S4", "halt": "S5", "stop": "S7", "lof_stuck": "V1", "long_frame": "V2", "input_all": "I1", "input_rotate": "I2"},
                         {name: entry["scenario"] for name, entry in manifest["scenarios"].items()})
        self.assertEqual({name: entry["defines"] for name, entry in manifest["scenarios"].items()},
                         {"vsync": {}, "copper_irq": {"HP": "2"}, "long_ops": {"PAD": "3"}, "halt": {}, "stop": {},
                          "lof_stuck": {}, "long_frame": {}, "input_all": {}, "input_rotate": {}})
        for name in sorted(SCENARIO_NAMES):
            with self.subTest(scenario=name):
                entry, source, image = scenario(name)
                self.assertEqual(entry["program_source"], name + ".s")
                self.assertEqual(entry["logical_id"], "microrom-%s-v1" % name.replace("_", "-"))
                self.assertEqual(entry["source_sha256"], sha256(source.encode()))
                self.assertEqual(entry["output"], {"size_bytes": 256 * 1024, "sha256": sha256(image)})
                self.assertEqual(source.splitlines()[0], HEADER)
                self.assertEqual(len(image), 256 * 1024)
                self.assertEqual(image[:8], bytes.fromhex("11114ef900f800d2"))  # entry offset intact
                self.assertEqual(int.from_bytes(image[12:14], "big"), 34)
                self.assertEqual(int.from_bytes(image[-20:-16], "big"), len(image))
                self.assertEqual(checksum_total(image), 0xFFFFFFFF)
                code = builder.assemble(builder.substitute(source, entry["defines"]))
                self.assertEqual(image[0xD2:0xD2 + len(code)], code)
                self.assertEqual(set(image[0xD2 + len(code):-24]), {0})  # nothing but the code and the trailer
                # Every scenario starts with the prologue of boot.s: SR, stack, then the CIA-A overlay switch.
                self.assertEqual(code[:len(CODE) - 4], CODE[:-4])

    def test_register_to_absolute_moves_and_exchange_are_the_documented_opcodes(self):
        self.assertEqual(builder.assemble("MOVE.W D2,$DFF100.L"), bytes.fromhex("33c2" "00dff100"))
        self.assertEqual(builder.assemble("MOVE.L D7,$DFF080.L"), bytes.fromhex("23c7" "00dff080"))
        self.assertEqual(builder.assemble("EXG D2,D3"), bytes.fromhex("c543"))
        self.assertEqual(builder.assemble("EXG D0,D7"), bytes.fromhex("c147"))
        with self.assertRaises(ValueError):
            builder.assemble("MOVE.B D0,$10.L")

    def test_input_log_forms_are_the_documented_opcodes(self):
        self.assertEqual(builder.assemble("MOVE.W $DFF00A.L,(A0)+"), bytes.fromhex("30f9" "00dff00a"))
        self.assertEqual(builder.assemble("MOVE.W $DFF00C.L,(A1)+"), bytes.fromhex("32f9" "00dff00c"))
        self.assertEqual(builder.assemble("MOVE.W #$AA55,(A0)+"), bytes.fromhex("30fc" "aa55"))
        self.assertEqual(builder.assemble("MOVE.W D2,(A0)+"), bytes.fromhex("30c2"))
        self.assertEqual(builder.assemble("MOVE.B $BFE001.L,D2"), bytes.fromhex("1439" "00bfe001"))
        self.assertEqual(builder.assemble("MOVEQ #0,D3"), bytes.fromhex("7600"))
        self.assertEqual(builder.assemble("MOVEQ #-1,D7"), bytes.fromhex("7eff"))
        with self.assertRaises(ValueError):
            builder.assemble("MOVEQ #128,D0")

    def test_the_boot_source_did_not_move_and_the_image_carries_the_iack_vectors(self):
        # boot.s is the contract of the earlier tasks. Its image changed once, when the interrupt-acknowledge
        # vector bytes were added at the end of every ROM (the engine reads them for each interrupt).
        manifest = json.loads((HERE / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["source_sha256"], "f0887a9c72a16e3ab0ee6dedd1e3d385b6bbe8bcd6c01d4d6aad10411e53d78f")
        self.assertEqual(manifest["output"]["sha256"], "e2578b2e83069537c8da1820de8971b131278d653e474f42f09b9fce55793597")
        self.assertEqual(sha256(builder.build(SOURCE)), manifest["output"]["sha256"])
        image = builder.build(SOURCE)
        self.assertEqual([image[-16 + 1 + 2 * level] for level in range(1, 8)], [25, 26, 27, 28, 29, 30, 31])

    def test_scenario_programs_do_what_their_comments_say(self):
        vsync = builder.assemble((HERE / "vsync.s").read_text(encoding="utf-8"))
        stop = builder.assemble((HERE / "stop.s").read_text(encoding="utf-8"))
        halt = builder.assemble((HERE / "halt.s").read_text(encoding="utf-8"))
        # The level-3 autovector ($6C) receives the handler; the handler is INTREQ clear, ADDQ, log, RTE.
        self.assertIn(bytes.fromhex("23fc" "00f80108" "0000006c"), vsync)  # handler at entry + 0x36
        self.assertTrue(vsync.endswith(bytes.fromhex("33fc" "0020" "00dff09c" "5281" "20c0" "4e73")))
        self.assertIn(bytes.fromhex("33fc" "c020" "00dff09a" "46fc" "2000"), vsync)  # INTENA then interrupts on
        # stop.s only replaces the counting loop by STOP #$2000 / BRA.S (which moves the handler by 2 bytes).
        self.assertIn(bytes.fromhex("23fc" "00f8010a" "0000006c"), stop)
        self.assertEqual(stop.replace(bytes.fromhex("4e72" "2000" "60fa"), bytes.fromhex("5280" "60fc"))
                         .replace(bytes.fromhex("00f8010a"), bytes.fromhex("00f80108")), vsync)
        # halt.s ends in MOVEA.L #$1001,A7 / TRAP #0 followed by its (never reached) counting trap handler.
        self.assertIn(bytes.fromhex("2e7c" "00001001" "4e40" "5281" "60fc"), halt)
        self.assertIn(bytes.fromhex("23fc" "00f800fc" "00000080"), halt)  # vector 32 -> trap0 at entry + 0x2A
        # long_ops.s: the DIVU dividend has to fit the quotient, or the instruction takes 10 cycles instead of ~100.
        source = (HERE / "long_ops.s").read_text(encoding="utf-8")
        divisor = int(re.search(r"MOVE\.L\s+#(\d+),D2", source)[1])
        dividend = int(re.search(r"MOVE\.L\s+#\$([0-9A-Fa-f]+),D3", source)[1], 16)
        self.assertTrue(0 < divisor and dividend >> 16 < divisor, (dividend, divisor))
        code = builder.assemble(builder.substitute(source, {"PAD": 0}))
        self.assertIn(bytes.fromhex("48e7fffe" "86c2" "4cdf7fff" "5280" "60f2"), code)
        # copper_irq.s: WAIT + MOVE INTREQ + end of list, stored at $2000 in one word pair per long.
        copper = builder.assemble(builder.substitute((HERE / "copper_irq.s").read_text(encoding="utf-8"), {"HP": 2}))
        self.assertIn(bytes.fromhex("23fc0405fffe00002000" "23fc009c801000002004" "23fcfffffffe00002008"), copper)
        self.assertIn(bytes.fromhex("23fc0000200000dff080" "33fc828000dff096" "33fcc01000dff09a"), copper)

    def test_define_cli(self):
        copper = HERE / "copper_irq.s"
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "copper.rom"
            subprocess.run([sys.executable, str(HERE / "build_rom.py"), str(copper), "--define", "HP=2",
                            "--output", str(output)], check=True)
            self.assertEqual(output.read_bytes(), scenario("copper_irq")[2])
            subprocess.run([sys.executable, str(HERE / "build_rom.py"), str(HERE / "long_ops.s"), "--define", "PAD=3",
                            "--output", str(output)], check=True)
            self.assertEqual(output.read_bytes(), scenario("long_ops")[2])
            output.unlink()
            for args, message in [
                ([str(copper)], "undefined placeholder @HP@"),
                ([str(copper), "--define", "HP=2", "--define", "PAD=1"], "unused define: PAD"),
                ([str(copper), "--define", "HP=2", "--define", "HP=3"], "given twice"),
                ([str(copper), "--define", "HP"], "expected NAME=value"),
                ([str(HERE / "boot.s"), "--define", "HP=2"], "unused define: HP"),
            ]:
                with self.subTest(args=args):
                    run = subprocess.run([sys.executable, str(HERE / "build_rom.py"), *args, "--output", str(output)],
                                         capture_output=True, text=True)
                    self.assertNotEqual(run.returncode, 0)
                    self.assertIn(message, run.stderr)
                    self.assertNotIn("Traceback", run.stderr)
            self.assertEqual(list(Path(directory).iterdir()), [])  # a failed build writes nothing


if __name__ == "__main__":
    unittest.main()
