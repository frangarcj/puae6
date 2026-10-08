"""T24a: the 68020 cycle-exact core (cpuemu_21) runs a 68020 micro-ROM and leaves what the architecture says.

The expected values are derived here with Python integer arithmetic from the instruction semantics of the MC68020 User's
Manual (bit fields, 64 bit multiply and divide, CAS, CMP2/CHK2, EXTB, LINK.L, RTD, PACK/UNPK, scaled and memory indirect
effective addresses, TRAPcc, 32 bit branches, MOVEC, the master and interrupt stacks, stack frames of formats 0, 2 and B, the
non-snooped instruction cache). Nothing is copied from the engine's output. The program is tests/host/cpu_020_program.s; the
assembler of the fixtures only encodes the 68000 subset, so the 68020 instructions are `.word` lines whose comments are checked
against Capstone, an independent disassembler, when it is installed.

The probe links the engine objects of the shared build tree (the ones p6_lifecycle_probe uses) and loads the A1200 preset (T24b):
68EC020, cycle exact, 14 MHz, no FPU, no MMU.
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
PROGRAM = ROOT / "tests/host/cpu_020_program.s"
ROM_BASE = 0xF80000
M32 = 0xFFFFFFFF

spec = importlib.util.spec_from_file_location("microrom_builder", ROOT / "tests/fixtures/microrom/build_rom.py")
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


def s32(v):
    v &= M32
    return v - (1 << 32) if v >> 31 else v


def s64(v):
    v &= (1 << 64) - 1
    return v - (1 << 64) if v >> 63 else v


def trunc_div(a, b):
    q = abs(a) // abs(b)
    return q if (a < 0) == (b < 0) else -q


def cmp_flags(dest, src, bits=32):
    """CCR of CMP/CAS/CMP2-style compare, dest - src (X not included)."""
    mask = (1 << bits) - 1
    d, s = dest & mask, src & mask
    r = (d - s) & mask
    n, z = r >> (bits - 1), int(r == 0)
    v = ((d ^ s) & (d ^ r)) >> (bits - 1)
    c = int(s > d)
    return n << 3 | z << 2 | v << 1 | c


def field(value, offset, width):
    return ((value << offset) & M32) >> (32 - width)


def insert(value, data, offset, width):
    shift = 32 - offset - width
    mask = ((1 << width) - 1) << shift
    return (value & ~mask & M32) | ((data << shift) & mask)


def ccr_nz(value, bits):
    return (value >> (bits - 1) & 1) << 3 | int(value == 0) << 2


def pack(src, adjust):
    t = ((src & 0xFFFF) + adjust) & 0xFFFF
    return (t >> 4 & 0xF0) | (t & 0x0F)


def unpk(src, adjust):
    return ((((src & 0xF0) << 4) | (src & 0x0F)) + adjust) & 0xFFFF


def expected_results():
    """name -> (value, mask) in the order the program stores them (the program tags the stores `;=name`)."""
    e = {}

    def put(name, value, mask=M32):
        assert name not in e, name
        e[name] = (value & M32, mask)

    # MOVEC. CACR keeps the four control bits of the 68020 but reads back only enable and freeze; SFC/DFC are three bits wide;
    # with M=0 the active stack pointer is the ISP, with M=1 the MSP.
    put("vbr", 0x4000)
    put("cacr_ei_fi", 0x0B & 0x3)
    put("cacr_ei", 1)
    put("caar", 0x12345678)
    put("sfc", 5 & 7)
    put("dfc", 13 & 7)
    put("msp", 0x900)
    put("isp", 0x800)
    put("usp", 0xA00)
    put("a7_master", 0x900)
    put("a7_interrupt", 0x800)
    # Bit fields (offset counted from the most significant bit; N is the first bit of the field, Z field == 0).
    put("bfextu", field(0x12345678, 8, 12))
    sign = field(0xF0000000, 0, 4)
    put("bfexts", sign | (M32 << 4) & M32 if sign >> 3 else sign)
    put("bfexts_ccr", ccr_nz(sign, 4))
    first_one = next(i for i in range(16) if field(0x00080000, 4, 16) >> (15 - i) & 1)
    put("bfffo", 4 + first_one)
    put("bfffo_ccr", ccr_nz(field(0x00080000, 4, 16), 16))
    put("bfffo_zero", 4 + 16)                       # an all zero field: offset + width
    put("bfffo_zero_ccr", ccr_nz(0, 16))
    put("bfins", insert(0, 0xAB, 4, 8))
    put("bfins_ccr", ccr_nz(0xAB, 8))
    put("bfins_memory", insert(0, 0xABC, 20, 12))   # bit 20 of the long at $3000 is the low nibble of byte 2
    put("bfextu_negative_offset", 0x5A)             # offset -8 in Dn: the byte before (A1)
    v = 0x12345678
    v = insert(v, field(v, 4, 8) ^ 0xFF, 4, 8)
    put("bfchg", v)
    v = insert(v, 0, 0, 4)
    put("bfclr", v)
    v = insert(v, 0xF, 28, 4)
    put("bfset", v)
    put("bftst_ccr", ccr_nz(field(v, 8, 8), 8))
    # Multiply: Dh:Dl = Dl * source. N is bit 63 (64 bit form) or 31, Z the whole result, V only when 32 bits overflow.
    p = 0x89ABCDEF * 0xFEDCBA98
    put("mulu64_hi", p >> 32)
    put("mulu64_lo", p)
    put("mulu64_ccr", ccr_nz(p, 64))
    p = (s32(0xFEDCBA98) * s32(0x12345678)) & ((1 << 64) - 1)
    put("muls64_hi", p >> 32)
    put("muls64_lo", p)
    put("muls64_ccr", ccr_nz(p, 64))
    p = 0x10000 * 0x10000
    put("mulu32_overflow", p)
    put("mulu32_overflow_ccr", ccr_nz(p & M32, 32) | 2)
    p = -3 * 7
    put("muls32", p)
    put("muls32_ccr", ccr_nz(p & M32, 32))
    # Divide: Dr:Dq / source, quotient to Dq, remainder (sign of the dividend) to Dr.
    dividend = 1 << 32 | 0x23456789
    q, r = divmod(dividend, 0x1000)
    put("divu64_quotient", q)
    put("divu64_remainder", r)
    put("divu64_ccr", ccr_nz(q, 32))
    dividend = s64(0xFFFFFF17 << 32 | 0x2B5AF000)
    q = trunc_div(dividend, 12345)
    put("divs64_quotient", q)
    put("divs64_remainder", dividend - q * 12345)
    put("divs64_ccr", ccr_nz(q & M32, 32))
    put("divul32_quotient", 1000003 // 1000)
    put("divul32_remainder", 1000003 % 1000)
    put("divu_overflow_d2", 1)                      # overflow: both operands are left alone
    put("divu_overflow_d3", 0)
    put("divu_overflow_ccr", 2, 0x3)                # V set, C clear; N and Z are undefined
    for name in ("at_zdiv", "at_zdiv_next"):
        put(name, 0, 0)                             # addresses: checked against the image below
    # CAS: compare like CMP (ea - Dc); on a match Du is stored, otherwise Dc is loaded from memory.
    put("cas_match_memory", 0xAAAABBBB)
    put("cas_match_ccr", cmp_flags(0x11112222, 0x11112222))
    put("cas_mismatch_dc", 0xAAAABBBB)
    put("cas_mismatch_memory", 0xAAAABBBB)
    put("cas_mismatch_ccr", cmp_flags(0xAAAABBBB, 0x12345678))
    # CMP2/CHK2 against (lower, upper) = ($10, $20): Z when equal to a bound, C when outside.
    for name, value in (("inside", 0x18), ("lower_bound", 0x10), ("upper_bound", 0x20), ("above", 0x30), ("below", 5)):
        out = value < 0x10 or value > 0x20
        put("cmp2_" + name, int(value in (0x10, 0x20)) << 2 | int(out), 0x5)    # N and V are undefined
    put("chk2_inside", 0, 0x5)
    for name in ("at_chk2", "at_chk2_next", "at_trapne", "at_trapne_next", "at_chkl", "at_chkl_next", "at_trap0_next"):
        put(name, 0, 0)
    put("extb", 0xFFFFFFF0)
    put("extb_ccr", 8)
    put("link_a6", 0x800 - 4)
    put("link_sp", 0x800 - 4 - 32)
    put("unlk_sp", 0x800)
    put("unlk_a6", 0x12345678)
    put("rtd_sp_before", 0x800)
    put("rtd_sp_after", 0x800)                      # subq #8, a7; jsr pushes 4; rtd #8 pops 4 and adds 8
    put("pack", 0xAABBCC00 | pack(0x812, 0x101))
    put("unpk", 0x11110000 | unpk(0x35, 0x3030))
    put("ea_brief_long_index", 0xCAFEBABE)          # (A1 + $10 + D2*4) = $3024
    put("ea_brief_word_index", 0x0F1E2D3C)          # D7.W = -2, scaled by 4: $3000 + $10 - 8 = $3008
    put("ea_memory_post_indexed", 0x0BADF00D)       # ([$3040]=$3200) + D2*2 + 8 = $320E
    put("ea_memory_pre_indexed", 0x13572468)        # ([$3000 + $20 + D2*4]=$3300) + 4 = $3304
    put("bra_long", 0x11111111)
    put("beq_long_not_taken", 0x22222222)
    put("bne_long_taken", 1)
    for name in ("at_illegal", "at_aline", "at_fline", "at_addr_resume"):
        put(name, 0, 0)
    put("icache_first", 0x11111111)
    put("icache_stale", 0x11111111)                 # the 68020 cache does not snoop the write: the old instruction runs
    put("icache_fresh", 0x22222222)                 # CACR.CI cleared the line
    put("sp_end", 0x800)                            # every exception frame, format B included, was popped by RTE
    put("frames_end", 0x2800 + 4 * 12 + 4 * 8 + 12)  # four format 2 frames, four format 0, the address error's 12 logged bytes
    put("results_end", 0x2000 + 4 * len(e))                  # A0 after every earlier store
    return e


class CPU020Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        ensure_generated()
        build_targets(TREE, ["p6_lifecycle_probe"])
        cls.tmp = tempfile.TemporaryDirectory(prefix="p6-cpu020-")
        tmp = Path(cls.tmp.name)
        cls.source = PROGRAM.read_text(encoding="utf-8")
        cls.image = builder.build(cls.source, {"RADDR": "$" + format(cls.cache_routine_address(), "X")})
        rom = tmp / "cpu020.rom"
        rom.write_bytes(cls.image)
        objects = sorted(str(p) for p in (TREE / "CMakeFiles/p6_engine.dir").rglob("*.o"))
        include = ["-I" + str(ROOT / d) for d in ("ports/portable", "build/generated", "build/upstream/include", "build/upstream",
                                                     "bridge/include", "bridge")]
        exe = tmp / "probe"
        subprocess.run(["clang++", "-std=c++17", "-UNDEBUG", "-O1", "-g", *include, str(ROOT / "tests/host/cpu_020_probe.cpp"),
                        *objects, str(TREE / "libp6_bridge.a"), str(TREE / "libp6_portable.a"), "-o", str(exe)],
                       check=True, timeout=300)
        result = subprocess.run([str(exe), str(rom), "20"], capture_output=True, text=True, timeout=300)
        cls.exe, cls.rom = exe, rom
        cls.output = result.stdout
        cls.returncode = result.returncode
        cls.stderr = result.stderr
        cls.lines = [line.split() for line in result.stdout.splitlines()]

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    @classmethod
    def cache_routine_address(cls):
        """Where the cache test puts its routine: two cache lines (index = bits 2..7 of the address, 64 lines) that no
        instruction between the first and the stale run of the routine fetches from, so that nothing evicts them."""
        source = PROGRAM.read_text(encoding="utf-8")
        image = builder.build(source, {"RADDR": "$3400"})
        first = image.find(bytes.fromhex("2E3C57A15711")) + ROM_BASE
        last = image.find(bytes.fromhex("2E3C57A15712")) + ROM_BASE + 6
        assert first > ROM_BASE and last > first
        used = {(a >> 2) & 63 for a in range((first - 16) & ~3, last + 16, 4)}
        for k in range(32):
            if not ({2 * k, 2 * k + 1, (2 * k + 2) & 63} & used):
                return 0x3400 + 8 * k
        raise AssertionError("no free cache lines")

    def values(self, prefix):
        found = {}
        for line in self.lines:
            if len(line) == 3 and line[0] == prefix:
                found[int(line[1])] = int(line[2], 16)
        return found

    def frames_logged(self):
        """Bytes the handlers logged, from the frame log pointer the program stored at its end."""
        return self.values("result")[self.tags().index("frames_end")] - 0x2800

    def state(self, name):
        for line in self.lines:
            if line[:2] == ["state", name]:
                return int(line[2], 16) if name != "instructions" else int(line[2])
        self.fail("no state " + name)

    def tags(self):
        return re.findall(r";=(\w+)", self.source)

    def test_the_probe_ran_the_program_on_a_68020(self):
        self.assertEqual(self.returncode, 0, self.stderr)
        header = next(line for line in self.lines if line[0] == "cpu")
        self.assertEqual(header[1:], "68020 fpu 0 mmu 0 24bit 1 cycle_exact 1 memory_cycle_exact 1 clock_multiplier 1024".split())
        units = next(line for line in self.lines if line[0] == "units")
        self.assertEqual(units[3], "600df00d", "the program did not reach its end")
        self.assertGreater(self.state("instructions"), 300)
        self.assertEqual(self.state("vbr"), 0x4000)
        self.assertEqual(self.state("a7"), 0x800)
        self.assertEqual(self.state("cacr") & 3, 1)

    def test_the_same_program_fails_on_the_68000(self):
        # The comparison can fail: with the A600's own 68000 the 68020 opcodes are illegal and the program never finishes.
        result = subprocess.run([str(self.exe), str(self.rom), "3", "68000"], capture_output=True, text=True, timeout=300)
        self.assertEqual(result.returncode, 0, result.stderr)
        lines = [line.split() for line in result.stdout.splitlines()]
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

    def test_addresses_of_the_exception_sites_are_where_the_image_has_them(self):
        got = dict(zip(self.tags(), (self.values("result")[i] for i in range(len(self.tags())))))
        image = self.image

        def at(opcode_words, nth=0):
            position = -1
            for _ in range(nth + 1):
                position = image.find(bytes.fromhex(opcode_words), position + 1)
            self.assertGreaterEqual(position, 0, opcode_words)
            return ROM_BASE + position

        self.assertEqual(got["at_zdiv"], at("4C473402"))
        self.assertEqual(got["at_zdiv_next"], got["at_zdiv"] + 4)
        self.assertEqual(got["at_chk2"], at("04D35800", 1))      # the second CHK2 is the one that traps
        self.assertEqual(got["at_chk2_next"], got["at_chk2"] + 4)
        self.assertEqual(got["at_trapne"], at("56FC"))
        self.assertEqual(got["at_trapne_next"], got["at_trapne"] + 2)
        self.assertEqual(got["at_chkl"], at("4504"))
        self.assertEqual(got["at_chkl_next"], got["at_chkl"] + 2)
        self.assertEqual(got["at_illegal"], at("4AFC"))
        self.assertEqual(got["at_aline"], got["at_illegal"] + 2)
        self.assertEqual(got["at_fline"], got["at_aline"] + 2)
        self.assertEqual(image[got["at_fline"] - ROM_BASE:][:4].hex().upper(), "F2800000")
        trap0 = image.find(bytes.fromhex("4E40"))
        self.assertEqual(got["at_trap0_next"], ROM_BASE + trap0 + 2)
        self.assertEqual(image[got["at_addr_resume"] - ROM_BASE - 6:][:6].hex().upper(), "203C" + "BADB" + "AD00")

    def test_exception_frames(self):
        results = self.values("result")
        at = dict(zip(self.tags(), (results[i] for i in range(len(self.tags())))))
        raw = b"".join(struct.pack(">I", v) for _, v in sorted(self.values("frame").items()))
        position = 0

        def take(format_, size, name, check_sr_low=False):
            nonlocal position
            frame, position = raw[position:position + size], position + size
            sr, pc, vector = struct.unpack(">HIH", frame[:8])
            with self.subTest(name):
                # The stacked SR is the one at the time of the exception: supervisor, interrupt mask 7, no trace.
                self.assertEqual(sr >> 8, 0x27, name)
                if not check_sr_low:
                    self.assertEqual(sr & 0xFF, 0, name)
                self.assertEqual(vector >> 12, format_, name)
            return pc, vector & 0xFFF, frame[8:]

        for name, vector, here, after in (("zero divide", 5, "at_zdiv", "at_zdiv_next"), ("CHK2", 6, "at_chk2", "at_chk2_next"),
                                           ("TRAPcc", 7, "at_trapne", "at_trapne_next"), ("CHK.L", 6, "at_chkl", "at_chkl_next")):
            pc, offset, rest = take(2, 12, name, check_sr_low=True)       # format 2: SR, PC, format/vector, instruction address
            with self.subTest(name):
                self.assertEqual((pc, offset, struct.unpack(">I", rest)[0]), (at[after], vector * 4, at[here]))
        pc, offset, _ = take(0, 8, "TRAP #0")
        self.assertEqual((pc, offset), (at["at_trap0_next"], 32 * 4))
        for name, vector, here in (("illegal", 4, "at_illegal"), ("line A", 10, "at_aline"), ("line F", 11, "at_fline")):
            pc, offset, _ = take(0, 8, name, check_sr_low=True)           # format 0; the stacked PC is the faulting instruction
            with self.subTest(name):
                self.assertEqual((pc, offset), (at[here], vector * 4))
        # Address error (JMP to $3001): format B, 46 words (92 bytes) on the stack, popped by the RTE of the handler.
        pc, offset, rest = take(0xB, 12, "address error")
        self.assertEqual(offset, 3 * 4)
        self.assertEqual(struct.unpack(">I", rest)[0], 0x800 - 92)
        self.assertEqual(position, self.frames_logged(), "the handlers logged other frames than the program caused")

    @unittest.skipUnless(importlib.util.find_spec("capstone"), "capstone is not installed")
    def test_comments_of_the_word_lines_are_what_capstone_disassembles(self):
        import capstone
        md = capstone.Cs(capstone.CS_ARCH_M68K, capstone.CS_MODE_BIG_ENDIAN | capstone.CS_MODE_M68K_020)
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
        self.assertGreater(checked, 60)


if __name__ == "__main__":
    unittest.main()
