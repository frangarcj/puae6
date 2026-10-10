"""The micro-ROM of T29a: a guest that finds the UAEFS board, runs its DiagArea, finds the resident of uaehf.device and reads blocks of the hard file.

It is a 68000 program of PUAE6 (MIT), hand-assembled here into `.word` lines for tests/fixtures/microrom/build_rom.py, whose own subset has no JSR, no compare and no
conditional branch. It needs no Kickstart and no exec.library: where the boot ROM of a Kickstart would call into exec, a few words of Chip RAM stand in (an ExecBase
with an OpenLibrary that fails, which hwtrap_install of the filesystem ROM tolerates, and the ResModules array that filesys_diagentry adds the residents to).

What it does, in order (the addresses are the constants below):
  1. interrupts off, overlay off, a fake ExecBase at $1000 (OpenLibrary at -552 returns 0, ResModules at +300 points to a zeroed array);
  2. autoconfig: reads er_Type of the board at $E80000, writes its base ($E90000) and so maps the board (expamem_map_filesys);
  3. copies the DiagArea of the board ($E92000, 512 bytes) to Chip RAM, as expansion.library does, and calls its DiagPoint (hwtrap_install, then filesys_diagentry);
  4. scans the copy for a Resident (RT_MATCHWORD $4AFC pointing at itself) named "uaeh..." and takes OpenDevice and BeginIO out of its function table;
  5. calls Open with an IORequest, then BeginIO for: CMD_READ of block 0, CMD_READ of block N, CMD_READ past the end, CMD_WRITE of one block, HD_SCSICMD;
  6. stores what it saw at RES and stops.
"""
import importlib.util
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("microrom_builder", _ROOT / "tests/fixtures/microrom/build_rom.py")
builder = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(builder)

EXEC = 0x1000
OPENLIB_VECTOR = EXEC - 552
ADDTASK_VECTOR = EXEC - 282
ALLOCMEM_VECTOR = EXEC - 198
ADDINTSERVER_VECTOR = EXEC - 168
HEAP_POINTER = 0x2F00           # the bump pointer of the fake AllocMem
HEAP = 0x6000
RESMODULES = 0x1200            # the array ExecBase+300 points at (zeroed: filesys_diagentry puts its residents in it)
DIAG_COPY = 0x2000
DIAG_SIZE = 0x200
CONFIGDEV = 0x2800
EXPLIB = 0x2900
IOREQ = 0x3000
LIBBASE = 0x4800               # 64 scratch bytes: uaehf.device counts its openers at +32
BUF0, BUF1, BUF2 = 0x4000, 0x4200, 0x4400
RES = 0x5000
BOARD = 0xE90000
DONE = 0x444F4E45              # "DONE"

CMD_READ, CMD_WRITE, HD_SCSICMD = 2, 3, 28
# Offsets in the structure at RES
R_TYPE, R_DIAG_D0, R_BEGINIO, R_RESIDENT, R_OPEN = 0, 4, 8, 12, 16
R_ERR = 17                      # io_Error of the requests in order: read0, readN, read past the end, write, scsi (5 bytes)
R_ACTUAL = 24                   # io_Actual of the three reads, longs
R_DONE = 40


class _Asm:
    def __init__(self):
        self.lines, self.size, self.labels, self.fixups, self.refs = [], 0, {}, [], []

    def word(self, value, text=""):
        self.lines.append("    .word $%04X%s" % (value & 0xFFFF, "      ; " + text if text else ""))
        self.size += 2

    def long(self, value, text=""):
        self.word(value >> 16, text)
        self.word(value & 0xFFFF)

    def op(self, text, *words):
        for i, value in enumerate(words):
            self.word(value, text if i == 0 else "")

    def op32(self, text, opcode, *longs):
        self.word(opcode, text)
        for value in longs:
            self.long(value)

    def label(self, name):
        self.labels[name] = self.size

    def branch(self, text, opcode, target, wide=False):
        self.fixups.append((len(self.lines), self.size, opcode, target, text, wide))
        self.word(opcode, text)
        if wide:
            self.word(0)

    def source(self):
        lines = list(self.lines)
        for index, at, opcode, target, text, wide in self.fixups:
            displacement = self.labels[target] - (at + 2)
            if wide:
                assert displacement and displacement % 2 == 0 and -32768 <= displacement <= 32766, (target, displacement)
                lines[index] = "    .word $%04X      ; %s" % (opcode, text)
                lines[index + 1] = "    .word $%04X" % (displacement & 0xFFFF)
            else:
                assert displacement and displacement % 2 == 0 and -128 <= displacement <= 126, (target, displacement)
                lines[index] = "    .word $%04X      ; %s" % (opcode | (displacement & 0xFF), text)
        for index, label in self.refs:
            value = self.rom_address(label)
            lines[index] = "    .word $%04X      ; %s" % (value >> 16, label)
            lines[index + 1] = "    .word $%04X" % (value & 0xFFFF)
        return "\n".join(lines) + "\n"

    def rom_address(self, name):
        return builder.ROM_BASE + builder.ENTRY_OFFSET + self.labels[name]

    # --- the instructions used -------------------------------------------------------------------------------------------------------------
    def move_b_imm_abs(self, imm, address):
        self.op("MOVE.B #$%02X,$%X.L" % (imm, address), 0x13FC, imm & 0xFF)
        self.long(address)

    def move_w_imm_abs(self, imm, address):
        self.op("MOVE.W #$%04X,$%X.L" % (imm, address), 0x33FC, imm)
        self.long(address)

    def move_l_imm_abs(self, imm, address):
        self.op32("MOVE.L #$%X,$%X.L" % (imm, address), 0x23FC, imm, address)

    def move_l_label_abs(self, label, address):
        """MOVE.L #label,address.L: the absolute ROM address of the label, known once the program is laid out."""
        self.refs.append((len(self.lines) + 1, label))
        self.op32("MOVE.L #%s,$%X.L" % (label, address), 0x23FC, 0, address)

    def move_b_abs_d0(self, address):
        self.op32("MOVE.B $%X.L,D0" % address, 0x1039, address)

    def move_b_d0_abs(self, address):
        self.op32("MOVE.B D0,$%X.L" % address, 0x13C0, address)

    def move_l_abs_d0(self, address):
        self.op32("MOVE.L $%X.L,D0" % address, 0x2039, address)

    def move_l_d0_abs(self, address):
        self.op32("MOVE.L D0,$%X.L" % address, 0x23C0, address)

    def movea_l_imm(self, reg, imm):
        self.op32("MOVEA.L #$%X,A%d" % (imm, reg), 0x207C | (reg << 9), imm)


def program(read_offset_block, past_end_block):
    """The micro-ROM source. read_offset_block: the block of the second read; past_end_block: a block just beyond the end of the hard file."""
    a = _Asm()
    a.label("entry")
    a.op("MOVE.W #$2700,SR", 0x46FC, 0x2700)
    a.op("LEA $1F00.W,A7", 0x4FF8, 0x1F00)
    a.move_b_imm_abs(3, 0xBFE201)                    # CIA-A DDRA: OVL and the power LED are outputs
    a.move_b_imm_abs(2, 0xBFE001)                    # overlay off: Chip RAM at 0
    # (1) a stand-in for exec: ExecBase pointer and version, the four functions the filesystem ROM calls while it installs itself (OpenLibrary fails, AllocMem
    # hands out memory from a bump pointer, AddTask and AddIntServer do nothing), ResModules -> a zeroed array
    a.move_l_imm_abs(EXEC, 4)
    a.move_w_imm_abs(34, EXEC + 20)                  # LIB_VERSION: 1.3
    for vector, label in ((OPENLIB_VECTOR, "nothing"), (ADDTASK_VECTOR, "nothing"), (ADDINTSERVER_VECTOR, "nothing"), (ALLOCMEM_VECTOR, "allocmem")):
        a.move_w_imm_abs(0x4EF9, vector)             # JMP label
        a.move_l_label_abs(label, vector + 2)
    a.move_l_imm_abs(HEAP, HEAP_POINTER)
    # ExecBase+300 = ResModules
    a.move_l_imm_abs(RESMODULES, EXEC + 300)
    for k in range(4):                               # Chip RAM powers up as $FF: the array must end with a zero, and the counters start at zero
        a.move_l_imm_abs(0, RESMODULES + 4 * k)
    a.move_w_imm_abs(0, LIBBASE + 32)
    # (2) autoconfig: er_Type, then the board base address (low byte first, then high: the board is mapped when the high byte is written)
    a.move_b_abs_d0(0xE80000)
    a.move_b_d0_abs(RES + R_TYPE)
    a.move_b_imm_abs(0x90, 0xE8004A)
    a.move_b_imm_abs(0xE0, 0xE80048)                 # -> $E90000
    # (3) the DiagArea, 32 bytes at a time (MOVEM stores downwards, so each block gets its own destination)
    for block in range(DIAG_SIZE // 32):
        a.movea_l_imm(0, BOARD + 0x2000 + 32 * block)
        a.op("MOVEM.L (A0)+,D0-D7", 0x4CD8, 0x00FF)
        a.movea_l_imm(1, DIAG_COPY + 32 * block + 32)
        a.op("MOVEM.L D0-D7,-(A1)", 0x48E1, 0xFF00)
    a.movea_l_imm(0, BOARD)
    a.movea_l_imm(2, DIAG_COPY)
    a.movea_l_imm(3, CONFIGDEV)
    a.movea_l_imm(5, EXPLIB)
    a.movea_l_imm(6, EXEC)
    a.op("JSR $1E0(A2)             ; the DiagPoint of the board", 0x4EAA, 0x01E0)
    a.move_l_d0_abs(RES + R_DIAG_D0)
    # (4) the resident: the first $4AFC in the copy whose RT_MATCHTAG is its own address and whose RT_NAME starts with "uaeh"
    a.movea_l_imm(1, DIAG_COPY)
    a.label("scan")
    a.op("CMPI.W #$4AFC,(A1)", 0x0C51, 0x4AFC)
    a.branch("BNE.S advance", 0x6600, "advance")
    a.op("MOVE.L 2(A1),D1", 0x2229, 0x0002)
    a.op("CMP.L A1,D1", 0xB289)
    a.branch("BNE.S advance", 0x6600, "advance")
    a.op("MOVEA.L 14(A1),A0", 0x2069, 0x000E)
    a.op("CMPI.B #'u',(A0)         ; the name is a string at any address: byte by byte", 0x0C10, 0x0075)
    a.branch("BNE.S advance", 0x6600, "advance")
    for k, letter in enumerate("aeh", 1):
        a.op("CMPI.B #'%s',%d(A0)" % (letter, k), 0x0C28, ord(letter), k)
        a.branch("BNE.S advance", 0x6600, "advance")
    a.branch("BRA.S found", 0x6000, "found")
    a.label("advance")
    a.op("ADDQ.L #2,A1", 0x5489)
    a.op32("CMPA.L #end,A1", 0xB3FC, DIAG_COPY + DIAG_SIZE)
    a.branch("BCS.S scan", 0x6500, "scan")
    a.branch("BRA.W finish (no resident)", 0x6000, "finish", wide=True)
    a.label("found")
    a.op("MOVE.L A1,D0", 0x2009)
    a.move_l_d0_abs(RES + R_RESIDENT)
    a.op("MOVEA.L 22(A1),A1        ; RT_INIT: {size, functable, datatable, initcode}", 0x2269, 0x0016)
    a.op("MOVEA.L 4(A1),A1         ; the function table: Open, Close, Expunge, Null, BeginIO, AbortIO", 0x2269, 0x0004)
    a.op("MOVEA.L (A1),A0          ; Open", 0x2051)
    a.op("MOVEA.L 16(A1),A4        ; BeginIO", 0x2869, 0x0010)
    a.op("MOVE.L A4,D0", 0x200C)
    a.move_l_d0_abs(RES + R_BEGINIO)
    # (5) OpenDevice's work: Open(unit 0, ioreq)
    a.movea_l_imm(1, IOREQ)
    a.movea_l_imm(6, LIBBASE)
    a.op("MOVEQ #0,D0", 0x7000)
    a.op("JSR (A0)", 0x4E90)
    a.move_b_abs_d0(IOREQ + 31)
    a.move_b_d0_abs(RES + R_OPEN)

    def request(slot, command, length, data, block):
        a.move_w_imm_abs(command, IOREQ + 28)
        a.move_b_imm_abs(0, IOREQ + 30)
        a.move_b_imm_abs(0, IOREQ + 31)
        a.move_l_imm_abs(0, IOREQ + 32)
        a.move_l_imm_abs(length, IOREQ + 36)
        a.move_l_imm_abs(data, IOREQ + 40)
        a.move_l_imm_abs(512 * block, IOREQ + 44)
        a.movea_l_imm(1, IOREQ)
        a.movea_l_imm(6, LIBBASE)
        a.op("JSR (A4)                 ; BeginIO", 0x4E94)
        a.move_b_abs_d0(IOREQ + 31)
        a.move_b_d0_abs(RES + R_ERR + slot)
        if slot < 3:
            a.move_l_abs_d0(IOREQ + 32)
            a.move_l_d0_abs(RES + R_ACTUAL + 4 * slot)

    request(0, CMD_READ, 512, BUF0, 0)
    request(1, CMD_READ, 512, BUF1, read_offset_block)
    request(2, CMD_READ, 512, BUF2, past_end_block)
    request(3, CMD_WRITE, 512, BUF0, 0)
    request(4, HD_SCSICMD, 30, BUF2, 0)
    a.label("finish")
    a.move_l_imm_abs(DONE, RES + R_DONE)
    a.label("park")
    a.op("STOP #$2700", 0x4E72, 0x2700)
    a.branch("BRA.S park", 0x6000, "park")
    a.label("nothing")
    a.op("MOVEQ #0,D0", 0x7000)
    a.op("RTS", 0x4E75)
    a.label("allocmem")                              # D0 = size: returns the heap pointer and moves it on
    a.op32("MOVEA.L heap,A0", 0x2079, HEAP_POINTER)
    a.op("MOVE.L A0,D1", 0x2208)
    a.op("ADD.L D0,D1", 0xD280)
    a.op32("MOVE.L D1,heap", 0x23C1, HEAP_POINTER)
    a.op("MOVE.L A0,D0", 0x2008)
    a.op("RTS", 0x4E75)
    return a.source()


def rom(read_offset_block=5, past_end_block=0x100000):
    return builder.build(program(read_offset_block, past_end_block))
