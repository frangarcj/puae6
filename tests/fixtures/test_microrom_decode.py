"""Cross-check the micro-ROM encodings against the engine's own 68000 decoder.

microrom_decode_probe.cpp links build/upstream/readcpu.cpp and the generated build/generated/cpudefs.cpp (the
opcode table the emulated CPU executes from) and reports, for every instruction of a code blob, the mnemonic, size,
length in words, addressing modes and register fields the decoder derives. The expectation is written here from the
assembly source alone, so a wrong opcode, a wrong register field, a wrong length, a 68020-only opcode, a branch that
does not land on the labelled instruction or a vector that does not point at its handler fails the comparison.
"""
import collections
import importlib.util
import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest

from tests.host import ensure_generated

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = Path(__file__).parent
MICROROM = FIXTURES / "microrom"
TREE = ROOT / "build/upstream"
GENERATED = ROOT / "build/generated"
ENTRY_ADDRESS = 0xF80000 + 0xD2  # the reset vector of the image: where the first instruction is

spec = importlib.util.spec_from_file_location("microrom_builder", MICROROM / "build_rom.py")
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)

Want = collections.namedtuple("Want", "mnemonic size words smode dmode sreg dreg")
# MOVEM register lists used by the sources -> (mask for (An)+, mask for -(An)), from the programmer's reference:
# bit 0 = D0 ... bit 15 = A7, and mirrored for the predecrement mode.
MOVEM_MASKS = {
    "D0-D7/A0-A6": (0x7FFF, 0xFFFE),
    "D0-D3/A0/A2-A4": (0x1D0F, 0xF0B8),
    "A7": (0x8000, 0x0001),
    "D0": (0x0001, 0x8000),
}
AREG = r"(A[0-7]|SP)"

# Every instruction form of the subset with all its register variants (the ROMs use only some of them).
FORMS = """
start:
    MOVE.W #$2700,SR
    LEA $400.W,A7
    LEA $10.W,A3
    LEA $7FFF.W,A0
    MOVE.B #3,$BFE201.L
    MOVE.B #-128,$10.L
    MOVE.W #$C020,$DFF09A.L
    MOVE.L #$12345678,$6C.L
    MOVE.L #start,$70.L
    MOVE.L #near,$74.L
    MOVE.L #7,D2
    MOVE.L #-1,D7
    MOVE.L #near,D5
    MOVEA.L #$1001,A7
    MOVEA.L #$1001,A0
    MOVEA.L #near,A3
    MOVE.W D2,$DFF100.L
    MOVE.L D7,$DFF080.L
    MOVE.W D0,$10.L
    EXG D2,D3
    EXG D0,D7
    MOVE.W $DFF00A.L,(A0)+
    MOVE.W $DFF00C.L,(A3)+
    MOVE.W #$AA55,(A0)+
    MOVE.W D2,(A0)+
    MOVE.W D7,(A5)+
    MOVE.B $BFE001.L,D2
    MOVEQ #0,D3
    MOVEQ #-1,D7
    MOVE.L D0,(A0)+
    MOVE.L D7,(A3)+
    MOVE.L D1,(SP)+
    DIVU.W D2,D3
    DIVU.W D7,D6
    DIVU.W D0,D0
    ADDQ.L #1,D0
    ADDQ.L #8,D3
    ADDQ.L #5,D7
    NOP
    RTE
    STOP #$2700
    TRAP #0
    TRAP #15
    MOVEM.L D0-D7/A0-A6,-(SP)
    MOVEM.L (SP)+,D0-D7/A0-A6
    MOVEM.L D0-D3/A0/A2-A4,-(A7)
    MOVEM.L (A1)+,D0-D3/A0/A2-A4
    MOVEM.L A7,-(SP)
    MOVEM.L (SP)+,A7
    MOVEM.L D0,-(A2)
    MOVEM.L (A2)+,D0
near:
    NOP
    BRA.S near
    BRA.S ahead
    NOP
ahead:
    NOP
    BRA.W start
    BRA.W later
    NOP
later:
    NOP
"""


def register(text):
    return 7 if text.upper() == "SP" else int(text[1])


def expect(line):
    """What the decoder has to say about one source instruction (None = not compared)."""
    for pattern, make in [
        (r"MOVE\.W\s+#.+,\s*SR", lambda m: Want("MOVE", "W", 2, "imm", "unknown", None, None)),
        (r"LEA\s+.+\.W\s*,\s*" + AREG, lambda m: Want("LEA", "L", 2, "absw", "Areg", None, register(m[1]))),
        (r"MOVE\.L\s+#.+,\s*D([0-7])", lambda m: Want("MOVE", "L", 3, "imm", "Dreg", None, int(m[1]))),
        (r"MOVEA\.L\s+#.+,\s*" + AREG, lambda m: Want("MOVEA", "L", 3, "imm", "Areg", None, register(m[1]))),
        (r"MOVE\.L\s+D([0-7])\s*,\s*\(" + AREG + r"\)\+",
         lambda m: Want("MOVE", "L", 1, "Dreg", "Aipi", int(m[1]), register(m[2]))),
        (r"MOVE\.([WL])\s+D([0-7])\s*,.+\.L",
         lambda m: Want("MOVE", m[1].upper(), 3, "Dreg", "absl", int(m[2]), None)),
        (r"MOVE\.W\s+.+\.L\s*,\s*\(" + AREG + r"\)\+",
         lambda m: Want("MOVE", "W", 3, "absl", "Aipi", None, register(m[1]))),
        (r"MOVE\.W\s+#.+,\s*\(" + AREG + r"\)\+",
         lambda m: Want("MOVE", "W", 2, "imm", "Aipi", None, register(m[1]))),
        (r"MOVE\.W\s+D([0-7])\s*,\s*\(" + AREG + r"\)\+",
         lambda m: Want("MOVE", "W", 1, "Dreg", "Aipi", int(m[1]), register(m[2]))),
        (r"MOVE\.B\s+.+\.L\s*,\s*D([0-7])", lambda m: Want("MOVE", "B", 3, "absl", "Dreg", None, int(m[1]))),
        (r"MOVEQ\s+#.+,\s*D([0-7])", lambda m: Want("MOVE", "L", 1, "immi", "Dreg", None, int(m[1]))),
        (r"EXG\s+D([0-7])\s*,\s*D([0-7])", lambda m: Want("EXG", "L", 1, "Dreg", "Dreg", int(m[1]), int(m[2]))),
        (r"MOVE\.([BWL])\s+#.+,.+\.L",
         lambda m: Want("MOVE", m[1].upper(), 5 if m[1].upper() == "L" else 4, "imm", "absl", None, None)),
        (r"DIVU\.W\s+D([0-7])\s*,\s*D([0-7])", lambda m: Want("DIVU", "W", 1, "Dreg", "Dreg", int(m[1]), int(m[2]))),
        (r"ADDQ\.L\s+#([1-8])\s*,\s*D([0-7])", lambda m: Want("ADD", "L", 1, "immi", "Dreg", int(m[1]), int(m[2]))),
        (r"NOP", lambda m: Want("NOP", None, 1, None, None, None, None)),
        (r"RTE", lambda m: Want("RTE", None, 1, None, None, None, None)),
        (r"STOP\s+#.+", lambda m: Want("STOP", None, 2, "imm1", None, None, None)),
        (r"TRAP\s+#(\d+)", lambda m: Want("TRAP", None, 1, "immi", None, int(m[1]), None)),
        (r"BRA\.S\s+\w+", lambda m: Want("BRA", "B", 1, "immi", None, None, None)),
        (r"BRA\.W\s+\w+", lambda m: Want("BRA", "W", 2, "imm1", None, None, None)),
        (r"MOVEM\.L\s+\S+\s*,\s*-\(" + AREG + r"\)", lambda m: Want("MOVEM", "L", 2, "imm1", "Apdi", None, register(m[1]))),
        (r"MOVEM\.L\s+\(" + AREG + r"\)\+\s*,\s*\S+", lambda m: Want("MOVEM", "L", 2, "imm1", "Aipi", None, register(m[1]))),
    ]:
        match = re.fullmatch(pattern, line, re.I)
        if match:
            return make(match)
    raise ValueError("no decoder expectation for: " + line)


def parse_source(source):
    """([(source line, Want)] in program order, {label: index of the instruction that follows it})."""
    items, labels = [], {}
    for raw in source.splitlines():
        line = raw.split(";", 1)[0].strip()
        label = re.match(r"([A-Za-z_]\w*):\s*(.*)$", line)
        if label:
            labels[label[1].lower()] = len(items)
            line = label[2].strip()
        if not line or re.fullmatch(r"\.align\s+2", line, re.I):
            continue
        nops = re.fullmatch(r"\.nops\s+(\d+)", line, re.I)
        if nops:
            items += [("NOP", expect("NOP"))] * int(nops[1])
        else:
            items.append((line, expect(line)))
    return items, labels


def literal(token, offsets, labels):
    """Value of a plain $hex/decimal literal or of a label (its ROM address); None for an expression."""
    token = token.strip()
    if re.fullmatch(r"\$[0-9A-Fa-f]+", token):
        return int(token[1:], 16)
    if re.fullmatch(r"\d+", token):
        return int(token)
    if re.fullmatch(r"-\d+", token):
        return int(token)
    if token.lower() in labels and labels[token.lower()] < len(offsets):
        return ENTRY_ADDRESS + offsets[labels[token.lower()]]
    return None


def operand_problems(text, at, code, offsets, labels):
    """Compare the immediates, addresses, displacements and register masks of one instruction."""
    problems = []

    def field(position, size):
        return int.from_bytes(code[at + position:at + position + size], "big")

    def same(what, value, expected, size):
        if expected is not None and value != expected & ((1 << (8 * size)) - 1):
            problems.append("%s: %s is %x, expected %x" % (text, what, value, expected & ((1 << (8 * size)) - 1)))

    match = re.fullmatch(r"MOVE\.([BWL])\s+#(.+?)\s*,\s*(.+?)\.L", text, re.I)
    if match:
        size = 4 if match[1].upper() == "L" else 2
        mask = {"B": 0xFF, "W": 0xFFFF, "L": 0xFFFFFFFF}[match[1].upper()]
        value = literal(match[2], offsets, labels)
        same("immediate", field(2, size), None if value is None else value & mask, size)
        same("address", field(2 + size, 4), literal(match[3], offsets, labels), 4)
    match = re.fullmatch(r"MOVE\.[WL]\s+D[0-7]\s*,\s*(.+?)\.L", text, re.I)
    if match:
        same("address", field(2, 4), literal(match[1], offsets, labels), 4)
    match = re.fullmatch(r"MOVEA?\.L\s+#(.+?)\s*,\s*(?:D[0-7]|" + AREG + ")", text, re.I)
    if match:
        same("immediate", field(2, 4), literal(match[1], offsets, labels), 4)
    match = re.fullmatch(r"MOVE\.W\s+#(.+?)\s*,\s*SR|STOP\s+#(.+)", text, re.I)
    if match:
        same("immediate", field(2, 2), literal(match[1] or match[2], offsets, labels), 2)
    match = re.fullmatch(r"LEA\s+(.+?)\.W\s*,\s*" + AREG, text, re.I)
    if match:
        same("address", field(2, 2), literal(match[1], offsets, labels), 2)
    match = re.fullmatch(r"BRA\.([SW])\s+(\w+)", text, re.I)
    if match:
        target = labels[match[2].lower()]
        if match[1].upper() == "S":
            displacement = code[at + 1] - 256 if code[at + 1] >= 128 else code[at + 1]
        else:
            displacement = field(2, 2) - 65536 if field(2, 2) >= 32768 else field(2, 2)
        if target >= len(offsets):
            problems.append("%s: the label is not an instruction" % text)
        elif at + 2 + displacement != offsets[target]:
            problems.append("%s: branches to %x, the label is at %x" % (text, at + 2 + displacement, offsets[target]))
    match = re.fullmatch(r"MOVEM\.L\s+(\S+)\s*,\s*-\(" + AREG + r"\)", text, re.I)
    if match:
        same("register mask", field(2, 2), MOVEM_MASKS[match[1].upper()][1], 2)
    match = re.fullmatch(r"MOVEM\.L\s+\(" + AREG + r"\)\+\s*,\s*(\S+)", text, re.I)
    if match:
        same("register mask", field(2, 2), MOVEM_MASKS[match[2].upper()][0], 2)
    return problems


@unittest.skipUnless(shutil.which("clang++"), "clang++ is not installed")
class MicroRomDecoderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Use the materialized tree when it is there; otherwise build it the way the other host tests do.
        if not (TREE / "readcpu.cpp").exists() or not (GENERATED / "cpudefs.cpp").exists():
            ensure_generated()
        cls.temporary = tempfile.TemporaryDirectory(prefix="p6-microrom-decode-")
        cls.probe = Path(cls.temporary.name) / "microrom_decode_probe"
        command = ["clang++", "-std=c++17", "-O1", "-Wall", "-Wextra", "-fno-fast-math",
                   "-fsanitize=address,undefined", "-fno-sanitize-recover=all"]
        command.extend("-I" + str(path) for path in (ROOT / "tools/codegen_host", GENERATED, TREE / "include", TREE))
        command.extend([str(FIXTURES / "microrom_decode_probe.cpp"), str(TREE / "readcpu.cpp"),
                        str(GENERATED / "cpudefs.cpp"), "-o", str(cls.probe)])
        compiled = subprocess.run(command, timeout=300, capture_output=True, text=True)
        if compiled.returncode:
            raise AssertionError(compiled.stderr)

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def decode(self, code):
        blob = Path(self.temporary.name) / "code.bin"
        blob.write_bytes(code)
        run = subprocess.run([str(self.probe), str(blob)], timeout=60, capture_output=True, text=True)
        rows = []
        for line in run.stdout.splitlines():
            offset, mnemonic, size, words, smode, dmode, sreg, dreg, clev, plev = line.split()
            rows.append(dict(offset=int(offset), mnemonic=mnemonic, size=size, words=int(words), smode=smode,
                             dmode=dmode, sreg=int(sreg), dreg=int(dreg), clev=int(clev), plev=int(plev)))
        return run.returncode, rows, run.stderr

    def problems(self, source, code):
        """Everything in which the decoder's view of `code` differs from the instructions of `source`."""
        returncode, rows, error = self.decode(code)
        if returncode:
            return ["decoder: " + error.strip()]
        items, labels = parse_source(source)
        if len(rows) != len(items):
            return ["%d instructions decoded, %d in the source" % (len(rows), len(items))]
        offsets = [row["offset"] for row in rows]
        problems = []
        for row, (text, want) in zip(rows, items):
            for name, expected in want._asdict().items():
                if expected is not None and row[name] != expected:
                    problems.append("%04x %s: %s is %r, expected %r" % (row["offset"], text, name, row[name], expected))
            if row["clev"] != 0:
                problems.append("%04x %s: needs CPU level %d" % (row["offset"], text, row["clev"]))
            problems += operand_problems(text, row["offset"], code, offsets, labels)
        return problems

    def test_every_rom_decodes_to_the_instructions_of_its_source(self):
        manifest = json.loads((MICROROM / "manifest.json").read_text(encoding="utf-8"))
        roms = [("boot.s", {})] + [(entry["program_source"], entry["defines"]) for entry in manifest["scenarios"].values()]
        self.assertEqual(len(roms), 9)
        for name, defines in roms:
            with self.subTest(source=name, defines=defines):
                text = builder.substitute((MICROROM / name).read_text(encoding="utf-8"), defines)
                image = builder.build((MICROROM / name).read_text(encoding="utf-8"), defines)
                code = builder.assemble(text)
                self.assertEqual(image[0xD2:0xD2 + len(code)], code)
                self.assertEqual(self.problems(text, code), [])
                self.assertEqual(int.from_bytes(image[4:8], "big"), ENTRY_ADDRESS)

    def test_every_default_parameter_of_the_templates_decodes(self):
        # The parameters change values and the number of NOPs, never the shape of the program.
        for name, values in [("copper_irq.s", [{"HP": hp} for hp in (0, 1, 2, 8, 127)]),
                             ("long_ops.s", [{"PAD": pad} for pad in (0, 1, 3, 5, 40)])]:
            source = (MICROROM / name).read_text(encoding="utf-8")
            for defines in values:
                with self.subTest(source=name, defines=defines):
                    text = builder.substitute(source, defines)
                    self.assertEqual(self.problems(text, builder.assemble(text)), [])

    def test_every_instruction_form_and_register_variant(self):
        code = builder.assemble(FORMS)
        self.assertEqual(self.problems(FORMS, code), [])
        returncode, rows, _ = self.decode(code)
        self.assertEqual(returncode, 0)
        self.assertEqual({row["mnemonic"] for row in rows},
                         {"MOVE", "MOVEA", "LEA", "DIVU", "ADD", "NOP", "RTE", "STOP", "TRAP", "MOVEM", "BRA", "EXG"})
        self.assertEqual(sum(row["words"] for row in rows) * 2, len(code))
        # The privileged ones are seen as such by the decoder, and everything is plain 68000.
        privileged = {row["mnemonic"] for row in rows if row["plev"] == 2}
        self.assertEqual(privileged, {"RTE", "STOP", "MOVE"})
        self.assertEqual({row["clev"] for row in rows}, {0})

    def test_the_comparison_can_fail(self):
        # Each broken variant of a correct program must be reported, or the checks above would prove nothing.
        source = "start:\n    MOVEM.L D0-D7/A0-A6,-(SP)\n    DIVU.W D2,D3\n    MOVE.L #start,$6C.L\n    BRA.S start\n"
        good = builder.assemble(source)
        self.assertEqual(self.problems(source, good), [])
        for label, broken in [
            ("wrong divide register", good.replace(bytes.fromhex("86c2"), bytes.fromhex("86c3"))),
            ("signed divide", good.replace(bytes.fromhex("86c2"), bytes.fromhex("87c2"))),
            ("wrong mask", good.replace(bytes.fromhex("48e7fffe"), bytes.fromhex("48e77fff"))),
            ("wrong vector immediate", good.replace(bytes.fromhex("00f800d2"), bytes.fromhex("00f800d4"))),
            ("wrong vector address", good.replace(bytes.fromhex("0000006c"), bytes.fromhex("00000070"))),
            ("branch elsewhere", good[:-2] + bytes.fromhex("60fa")),
            ("truncated", good[:-1]),
            ("extra word", good + bytes.fromhex("4e71")),
            ("illegal opcode", good + bytes.fromhex("4afc")),
            ("68020-only opcode appended", good + bytes.fromhex("4e7b0801")),
        ]:
            with self.subTest(broken=label):
                self.assertNotEqual(self.problems(source, broken), [])
        self.assertEqual(self.decode(bytes.fromhex("4e7b0801"))[1][0]["clev"], 1)  # MOVEC: the level field is live
        self.assertNotEqual(self.problems("NOP\nRTE\n", bytes.fromhex("4e714e75")), [])  # RTS instead of RTE
        self.assertNotEqual(self.problems("DIVU.W D2,D3\n", bytes.fromhex("86c2") * 2), [])

    def test_the_length_comes_from_the_decoder(self):
        # An instruction whose source lists the wrong number of extension words is a length mismatch.
        code = builder.assemble("MOVE.L #1,$10.L\nNOP")
        returncode, rows, _ = self.decode(code)
        self.assertEqual((returncode, [(row["offset"], row["words"]) for row in rows]), (0, [(0, 5), (10, 1)]))
        self.assertNotEqual(self.problems("MOVE.W #1,$10.L\nNOP\n", code), [])


if __name__ == "__main__":
    unittest.main()
