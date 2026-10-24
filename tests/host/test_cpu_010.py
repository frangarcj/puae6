"""T52: the 68010 cycle-exact core (op_smalltbl_13, patch 0310) runs a 68010 micro-ROM and leaves what the architecture says.

The expected values are derived here from the MC68010 User's Manual, with Python integer arithmetic: the control registers that MOVEC reaches (VBR, SFC and DFC three bits wide,
USP) and the one it does not (the 68020's CACR is an illegal instruction), the vector table at VBR, the four word exception frame with the format/vector offset word (the 68000
pushes three words), the 29 word format 8 frame of an address error, RTD, MOVES, MOVE from CCR in user mode, MOVE from SR as a privilege violation in user mode, and the loop mode,
which makes the DBRA loop of one instruction cheaper than the 68000's 8 + 10 cycles per pass. Nothing is copied from the engine's output. The program is tests/host/cpu_010_program.s;
the assembler of the fixtures only encodes the 68000 subset, so the other instructions are `.word` lines whose comments are checked against Capstone when it is installed.

The probe links the engine objects of the shared build tree (the ones p6_lifecycle_probe uses) and loads the A500 with p6_config.cpu_model = 68010 (code 2). The same program on the
A500's own 68000 must not finish (MOVEC is an illegal instruction there): the comparison can fail.
"""
import importlib.util
from pathlib import Path
import re
import struct
import subprocess
import tempfile
import unittest

from tests.build_support import build_targets
from tests.host import ensure_generated

ROOT = Path(__file__).resolve().parents[2]
TREE = ROOT / "build/link-audit"
PROGRAM = ROOT / "tests/host/cpu_010_program.s"
ROM_BASE = 0xF80000
PASSES = 2000                     # the loop of the program: D0 = 1999 and DBRA falls through when it wraps

spec = importlib.util.spec_from_file_location("microrom_builder", ROOT / "tests/fixtures/microrom/build_rom.py")
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


def expected_results():
    """name -> (value, mask) in the order the program stores them (the program tags the stores `;=name`)."""
    e = {}

    def put(name, value, mask=0xFFFFFFFF):
        assert name not in e, name
        e[name] = (value & 0xFFFFFFFF, mask)

    put("vbr_reset", 0)                             # the reset vector base
    put("vbr", 0x4000)
    put("sfc", 5 & 7)                               # SFC and DFC are three bits wide
    put("dfc", 13 & 7)
    put("usp", 0xA00)
    put("at_movec_cacr", 0, 0)                      # the address of the instruction, checked against the image
    put("movec_cacr_d1", 0xCAFE0001)                # the 68010 has no CACR: illegal instruction, D1 untouched
    put("at_trap0_next", 0, 0)
    put("move_ccr", 0x1F)                           # MOVE from CCR zero extends to the word
    put("rtd_sp_before", 0x800)                     # SUBQ #8, JSR pushes the return address, RTD #8 pops it and adds 8: the stack is as it was
    put("rtd_sp_after", 0x800)
    put("moves_store", 0xA5A55A5A)
    put("moves_load", 0xA5A55A5A)
    put("at_move_sr", 0, 0)
    put("move_sr_d3", 0xDEADBEEF)                   # privileged on a 68010: the instruction was skipped by the handler
    put("move_ccr_user", 0x15)                      # MOVE from CCR is not privileged
    put("at_addr_resume", 0, 0)
    put("sp_after_address_error", 0x800)            # the RTE of the handler popped the 29 words of the format 8 frame
    put("timer_low", 0, 0)                          # the loop, in E clocks, is checked on its own
    put("timer_high", 0, 0)
    put("loop_counter", 0xFFFF)                     # DBRA stops when the low word wraps
    put("sp_end", 0x800)
    put("frames_end", 0x2800 + 4 * (3 + 3 + 3 + 3 + 5))
    return e


class CPU010Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        ensure_generated()
        build_targets(TREE, ["p6_lifecycle_probe"])
        cls.tmp = tempfile.TemporaryDirectory(prefix="p6-cpu010-")
        tmp = Path(cls.tmp.name)
        cls.source = PROGRAM.read_text(encoding="utf-8")
        cls.image = builder.build(cls.source, {})
        rom = tmp / "cpu010.rom"
        rom.write_bytes(cls.image)
        cls.exe, cls.rom = cls.build_probe(tmp), rom
        cls.output, cls.returncode, cls.stderr, cls.lines = cls.run_probe()

    @staticmethod
    def build_probe(tmp):
        """tests/host/cpu_010_probe.cpp linked with the engine objects of the shared tree (what p6_lifecycle_probe links)."""
        objects = sorted(str(p) for p in (TREE / "CMakeFiles/p6_engine.dir").rglob("*.o"))
        include = ["-I" + str(ROOT / d) for d in ("ports/portable", "build/generated", "build/upstream/include", "build/upstream",
                                                     "bridge/include", "bridge")]
        exe = tmp / "probe"
        subprocess.run(["clang++", "-std=c++17", "-UNDEBUG", "-O1", "-g", *include, str(ROOT / "tests/host/cpu_010_probe.cpp"),
                        *objects, str(TREE / "libp6_bridge.a"), str(TREE / "libp6_portable.a"), "-o", str(exe)],
                       check=True, timeout=300)
        return exe

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    @classmethod
    def run_probe(cls, *extra):
        result = subprocess.run([str(cls.exe), str(cls.rom), "20", *extra], capture_output=True, text=True, timeout=300)
        return result.stdout, result.returncode, result.stderr, [line.split() for line in result.stdout.splitlines()]

    def values(self, prefix, lines=None):
        found = {}
        for line in lines or self.lines:
            if len(line) == 3 and line[0] == prefix:
                found[int(line[1])] = int(line[2], 16)
        return found

    def tags(self):
        return re.findall(r";=(\w+)", self.source)

    def result(self, name):
        return self.values("result")[self.tags().index(name)]

    def state(self, name):
        for line in self.lines:
            if line[:2] == ["state", name]:
                return int(line[2], 16) if name != "instructions" else int(line[2])
        self.fail("no state " + name)

    def test_the_probe_ran_the_program_on_a_68010(self):
        self.assertEqual(self.returncode, 0, self.stderr)
        header = next(line for line in self.lines if line[0] == "cpu")
        self.assertEqual(header[1:], "68010 fpu 0 mmu 0 24bit 1 cycle_exact 1 memory_cycle_exact 1 clock_multiplier 512".split())
        units = next(line for line in self.lines if line[0] == "units")
        self.assertEqual(units[3], "600df00d", "the program did not reach its end")
        self.assertEqual(self.state("vbr"), 0x4000)
        self.assertEqual(self.state("a7"), 0x800)

    def test_the_same_program_fails_on_the_68000(self):
        # The comparison can fail: with the machine's own 68000 MOVEC is an illegal instruction and the program never finishes.
        _, code, stderr, lines = self.run_probe("--cpu", "68000")
        self.assertEqual(code, 0, stderr)
        self.assertEqual(next(line for line in lines if line[0] == "cpu")[1], "68000")
        self.assertNotEqual(next(line for line in lines if line[0] == "units")[3], "600df00d")

    def test_every_store_of_the_program_has_an_expectation(self):
        tags = self.tags()
        self.assertEqual(len(tags), len(set(tags)), "a tag is used twice")
        self.assertEqual(tags, list(expected_results()), "the order of the tags is the order of the stores")

    def test_results_are_what_the_architecture_says(self):
        got = self.values("result")
        for index, (name, (want, mask)) in enumerate(expected_results().items()):
            if mask:
                with self.subTest(name):
                    self.assertEqual(got[index] & mask, want & mask, "%s: got %08x want %08x" % (name, got[index], want))

    def addresses(self):
        image = self.image

        def at(opcode_words):
            position = image.find(bytes.fromhex(opcode_words))
            self.assertGreaterEqual(position, 0, opcode_words)
            return ROM_BASE + position

        movec = at("4E7A1002")
        got = {name: self.result(name) for name in ("at_movec_cacr", "at_trap0_next", "at_move_sr", "at_addr_resume")}
        self.assertEqual(got["at_movec_cacr"], movec)
        self.assertEqual(got["at_move_sr"], at("40C3"))
        self.assertEqual(got["at_trap0_next"], at("4E40") + 2)
        self.assertEqual(image[got["at_addr_resume"] - ROM_BASE:][:2].hex().upper(), "2A0F")        # move.l a7, d5
        return got, at("4E41") + 2

    def test_exception_frames_are_the_four_words_of_the_68010_and_the_29_of_an_address_error(self):
        got, after_trap1 = self.addresses()
        raw = b"".join(struct.pack(">I", v) for _, v in sorted(self.values("frame").items()))
        position = 0

        def take(size, name):
            nonlocal position
            frame, position = raw[position:position + size], position + size
            sr, pc, vector, a7 = struct.unpack(">HIHI", frame[:12])
            return sr, pc, vector, a7, frame[12:]

        # A trap, an illegal instruction and a privilege violation push SR, PC and the format/vector word, format 0: 8 bytes below the supervisor stack, ISP = $800.
        for name, vector, pc, supervisor in (("MOVEC CACR", 4, got["at_movec_cacr"], True), ("TRAP #0", 32, got["at_trap0_next"], True),
                                             ("MOVE from SR in user mode", 8, got["at_move_sr"], False), ("TRAP #1", 33, after_trap1, False)):
            sr, frame_pc, word, a7, _ = take(12, name)
            with self.subTest(name):
                self.assertEqual((sr >> 8, frame_pc, word, a7), (0x27 if supervisor else 0x00, pc, 0x0000 | vector * 4, 0x800 - 8))
        # The address error of JMP to $3001: format 8, offset of vector 3; 29 words (58 bytes) were pushed, so ISP = $800 - 58. The stacked SSW is word 4 and the fault address words 5 and 6.
        sr, _, word, a7, rest = take(20, "address error")
        ssw, fault_high, fault_low = struct.unpack(">HHH", rest[:6])
        self.assertEqual((sr >> 8, word, a7, fault_high << 16 | fault_low), (0x27, 0x8000 | 3 * 4, 0x800 - 58, 0x3001))
        self.assertEqual(position, self.result("frames_end") - 0x2800, "the handlers logged other frames than the program caused")

    def test_the_dbra_loop_runs_in_loop_mode(self):
        # CIA-A timer A counts E clocks (one every ten CPU clocks) down from $FFFF. The 68000 pays TST.W (An)+ 8 cycles and a taken DBRA 10, and the last pass 14 for the DBRA that falls
        # through: 2000 * 8 + 1999 * 10 + 14 = 36004 clocks, 3600 E clocks (the start and the stop of the timer add the E clock synchronisation, a clock or two).
        elapsed = 0xFFFF - (self.result("timer_high") << 8 | self.result("timer_low"))
        slow = (PASSES * 8 + (PASSES - 1) * 10 + 14) // 10
        # In loop mode the looped instruction is not fetched again and the DBRA does not refill its queue: between 10 and 14 clocks a pass instead of 18 (the first pass and the exit
        # pay the normal ones). The engine's cycle-exact table gives 12 (2404 E clocks); the bounds are the manual's order of magnitude, the slow end well under the 68000's 3600.
        self.assertLess(elapsed, slow * 3 // 4, "the loop took %d E clocks, a 68000 would take %d" % (elapsed, slow))
        self.assertGreaterEqual(elapsed, PASSES * 10 // 10 - 20, "the loop took %d E clocks: faster than any loop mode" % elapsed)

    LOOP_ONLY = """entry:
    MOVE.W #$2700,SR
    LEA $800.W,A7
    MOVE.B #3,$BFE201.L
    MOVE.B #2,$BFE001.L
    MOVEA.L #$2000,A0
    MOVEA.L #$F80000,A2
    MOVE.L #1999,D0
    MOVEQ #0,D2
    MOVEQ #0,D3
    MOVE.B #$FF,$BFE401.L
    MOVE.B #$FF,$BFE501.L
    MOVE.B #9,$BFEE01.L
loop:
    .word $4A5A                  ; tst.w (a2)+
    .word $51C8,$FFFC            ; ~ dbra d0, loop
    MOVE.B #0,$BFEE01.L
    MOVE.B $BFE401.L,D2
    MOVE.B $BFE501.L,D3
    MOVE.L D2,(A0)+
    MOVE.L D3,(A0)+
    MOVE.L #$600DF00D,$1FFC.L
done:
    BRA.S done
"""

    def test_the_same_loop_on_a_68000_pays_the_manual_cycles_and_on_a_68010_fewer(self):
        # The loop alone (no 68010 instruction) runs on both CPUs: the 68000 takes the 8 + 10 clocks a pass that the manual says, the 68010 the loop mode.
        rom = Path(self.tmp.name) / "loop.rom"
        rom.write_bytes(builder.build(self.LOOP_ONLY))
        elapsed = {}
        for name, extra in (("68000", ["--cpu", "68000"]), ("68010", [])):
            result = subprocess.run([str(self.exe), str(rom), "20", *extra], capture_output=True, text=True, timeout=300)
            self.assertEqual(result.returncode, 0, result.stderr)
            lines = [line.split() for line in result.stdout.splitlines()]
            self.assertEqual(next(line for line in lines if line[0] == "cpu")[1], name)
            self.assertEqual(next(line for line in lines if line[0] == "units")[3], "600df00d")
            results = self.values("result", lines)
            elapsed[name] = 0xFFFF - (results[1] << 8 | results[0])
        slow = (PASSES * 8 + (PASSES - 1) * 10 + 14) // 10
        self.assertAlmostEqual(elapsed["68000"], slow, delta=3)
        self.assertLess(elapsed["68010"], elapsed["68000"] * 3 // 4)

    @unittest.skipUnless(importlib.util.find_spec("capstone"), "capstone is not installed")
    def test_comments_of_the_word_lines_are_what_capstone_disassembles(self):
        import capstone
        md = capstone.Cs(capstone.CS_ARCH_M68K, capstone.CS_MODE_BIG_ENDIAN | capstone.CS_MODE_M68K_010)
        checked = 0
        for number, line in enumerate(self.source.splitlines(), 1):
            match = re.match(r"\s*\.word\s+([^;]+?)\s*;\s*(.*)$", line)
            if not match or match[2].startswith("~"):
                continue
            words = [int(w.strip().lstrip("$"), 16) for w in match[1].split(",")]
            code = b"".join(struct.pack(">H", w) for w in words)
            text = " ".join(("%s %s" % (i.mnemonic, i.op_str)).strip() for i in md.disasm(code, 0))
            want = match[2].split(" | ")[0]
            with self.subTest(line=number):
                self.assertEqual(re.sub(r"\s+", "", text).lower(), re.sub(r"\s+", "", want).lower(), line.strip())
            checked += 1
        self.assertGreater(checked, 25)


if __name__ == "__main__":
    unittest.main()
