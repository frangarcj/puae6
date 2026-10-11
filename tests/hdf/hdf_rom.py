"""The micro-ROM of T29a: a guest that finds the UAEFS board, runs its DiagArea, finds the resident of uaehf.device and reads blocks of the hard file.

It is a 68000 program of PUAE6 (MIT), hand-assembled here into `.word` lines for tests/fixtures/microrom/build_rom.py, whose own subset has no JSR, no compare and no
conditional branch. It needs no Kickstart and no exec.library: where the boot ROM of a Kickstart would call into exec, a few words of Chip RAM stand in (an ExecBase
with an OpenLibrary that fails, which hwtrap_install of the filesystem ROM tolerates, and the ResModules array that filesys_diagentry adds the residents to).

What it does, in order (the addresses are the constants below):
  1. interrupts off, overlay off, a fake ExecBase at $1000 (OpenLibrary at -552 returns 0, ResModules at +300 points to a zeroed array);
  2. autoconfig: reads er_Type of the board at $E80000, writes its base ($E90000) and so maps the board (expamem_map_filesys);
  3. copies the DiagArea of the board ($E92000, 512 bytes) to Chip RAM, as expansion.library does, and calls its DiagPoint (hwtrap_install, then filesys_diagentry);
  4. scans the copy for a Resident (RT_MATCHWORD $4AFC pointing at itself) named "uaeh..." and takes OpenDevice and BeginIO out of its function table;
  5. calls Open with an IORequest, then BeginIO for: CMD_READ of block 0, CMD_READ of block N, CMD_READ past the end, CMD_WRITE of block 0's bytes to block 3,
     CMD_READ of block 3, CMD_WRITE of blocks 0 and N (1024 bytes, BUF0 then BUF1) over blocks 7 and 8, which cross the 4 KiB chunk of the overlay, CMD_READ of
     those two, HD_SCSICMD;
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
BUF0, BUF1, BUF2 = 0x4000, 0x4200, 0x4400     # BUF0 and BUF1 are contiguous: block 0 and block N, written together as one request of two blocks
BUF3, BUF4 = 0x7000, 0x7400                   # read-backs (BUF4 holds two blocks)
RES = 0x5000
BOARD = 0xE90000
DONE = 0x444F4E45              # "DONE"

CMD_READ, CMD_WRITE, HD_SCSICMD = 2, 3, 28
WRITE_BLOCK, CROSS_BLOCK = 3, 7  # CROSS_BLOCK and the next one are the last block of chunk 0 and the first of chunk 1
# Offsets in the structure at RES
R_TYPE, R_DIAG_D0, R_BEGINIO, R_RESIDENT, R_OPEN = 0, 4, 8, 12, 16
REQUESTS = 8
R_ERR = 17                      # io_Error of the requests in order: read0, readN, read past the end, write 3, read 3, write 7-8, read 7-8, scsi (8 bytes)
R_ACTUAL = 28                   # io_Actual of every request, longs
R_DONE = R_ACTUAL + 4 * REQUESTS


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


def _boot(a):
    """The part every program shares: the stand-in for exec, autoconfig of the board, its DiagArea, the resident, Open (steps 1 to 5 of the module text, up to BeginIO).
    Leaves the address of BeginIO in A4 and in RES + R_BEGINIO."""
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


def _stubs(a):
    """The two subroutines the stand-in for exec points at."""
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


def program(read_offset_block, past_end_block):
    """The micro-ROM source. read_offset_block: the block of the second read; past_end_block: a block just beyond the end of the hard file."""
    a = _Asm()
    _boot(a)

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
        a.move_l_abs_d0(IOREQ + 32)
        a.move_l_d0_abs(RES + R_ACTUAL + 4 * slot)

    request(0, CMD_READ, 512, BUF0, 0)
    request(1, CMD_READ, 512, BUF1, read_offset_block)
    request(2, CMD_READ, 512, BUF2, past_end_block)
    request(3, CMD_WRITE, 512, BUF0, WRITE_BLOCK)
    request(4, CMD_READ, 512, BUF3, WRITE_BLOCK)
    request(5, CMD_WRITE, 1024, BUF0, CROSS_BLOCK)
    request(6, CMD_READ, 1024, BUF4, CROSS_BLOCK)
    request(7, HD_SCSICMD, 30, BUF2, 0)
    a.label("finish")
    a.move_l_imm_abs(DONE, RES + R_DONE)
    a.label("park")
    a.op("STOP #$2700", 0x4E72, 0x2700)
    a.branch("BRA.S park", 0x6000, "park")
    _stubs(a)
    return a.source()


def rom(read_offset_block=5, past_end_block=0x100000):
    return builder.build(program(read_offset_block, past_end_block))


# --- the program of DET02 (T29c) --------------------------------------------------------------------------------------------------------------------------------
STATE, COUNT = RES + 0x100, RES + 0x104     # whether the interrupt of the EXTER queue is in progress (0/1) and the vertical blanks the handler has served
EXTER_HELPER = 0xF0FF50                      # exter_int_helper, in the trap area
RTAREA_INTREQ = 0xF0FFFC                     # a read of this byte takes the request line of the EXTER interrupt back (uae_int_requested bit 0)
DRAIN_BLOCK = 7                              # the first write of the loop: blocks 7 and 8, across the chunk boundary of the overlay; each vertical blank moves it one chunk on


def loop_program():
    """What DET02 runs (tests/state/test_det02_hdf.py): the boot of program(), then a vertical-blank handler that, every unit, writes two blocks across a chunk boundary of the overlay
    (a new chunk every unit, with the unit count as data), reads them back, reads block 0 of the base, and takes one step of the EXTER interrupt of the filesystem (exter_int_helper:
    n = 10 to start it, 11 to go on, as the boot ROM does until it answers 0). Every request leaves a ReplyMsg in the queue, so the queue grows by two a unit and the interrupt is in
    progress at most boundaries: the sample of what a state taken with the controller busy has to carry. INTENA keeps INT2 off, so the handler is the only reader of the queue. Every
    other unit takes the request line back with a read of the INTREQ byte of the trap area, so that uae_int_requested is 0 at some boundaries and 1 at others."""
    a = _Asm()
    _boot(a)
    a.move_l_imm_abs(0, STATE)
    a.move_l_imm_abs(0, COUNT)
    a.move_l_label_abs("handler", 0x6C)
    a.move_w_imm_abs(0xC020, 0xDFF09A)               # INTENA: SET | INTEN | VERTB
    a.op("MOVE.W #$2000,SR", 0x46FC, 0x2000)
    a.label("main")
    a.op("ADDQ.L #1,D7", 0x5287)
    a.branch("BRA.S main", 0x6000, "main")
    a.label("finish")                                # the boot found no resident: park
    a.op("STOP #$2700", 0x4E72, 0x2700)
    a.branch("BRA.S finish", 0x6000, "finish")

    def issue(command, length, data, offset=None):
        a.move_w_imm_abs(command, IOREQ + 28)
        a.move_b_imm_abs(0, IOREQ + 30)
        a.move_b_imm_abs(0, IOREQ + 31)
        a.move_l_imm_abs(0, IOREQ + 32)
        a.move_l_imm_abs(length, IOREQ + 36)
        a.move_l_imm_abs(data, IOREQ + 40)
        if offset is not None:
            a.move_l_imm_abs(offset, IOREQ + 44)
        a.movea_l_imm(1, IOREQ)
        a.movea_l_imm(6, LIBBASE)
        a.op32("MOVEA.L BeginIO,A4", 0x2879, RES + R_BEGINIO)
        a.op("JSR (A4)                 ; BeginIO", 0x4E94)

    a.label("handler")
    a.move_w_imm_abs(0x0020, 0xDFF09C)               # INTREQ: clear VERTB
    a.op("MOVEM.L D0-D7/A0-A6,-(A7)", 0x48E7, 0xFFFE)
    a.op32("MOVE.L COUNT,D3", 0x2639, COUNT)
    a.op("ADDQ.L #1,D3", 0x5283)
    a.op32("MOVE.L D3,COUNT", 0x23C3, COUNT)
    a.op32("MOVE.L D3,BUF0", 0x23C3, BUF0)           # the data: the unit count at the start of the first block and of the second
    a.op32("MOVE.L D3,BUF1", 0x23C3, BUF1)
    a.op("MOVE.L D3,D0", 0x2003)
    a.op("SUBQ.L #1,D0", 0x5380)
    a.op("LSL.L #8,D0", 0xE188)                      # the unit count (from 0) times 4096: one chunk a unit
    a.op("LSL.L #4,D0", 0xE988)
    a.op32("ADDI.L #3584,D0", 0x0680, 512 * DRAIN_BLOCK)
    a.op32("MOVE.L D0,offset", 0x23C0, IOREQ + 44)
    issue(CMD_WRITE, 1024, BUF0)                     # the offset is the one just stored
    issue(CMD_READ, 1024, BUF4)                      # the same blocks back
    issue(CMD_READ, 512, BUF2, 0)                    # block 0 of the base
    a.op32("MOVE.L STATE,D2", 0x2439, STATE)
    a.op("MOVEQ #10,D0", 0x700A)
    a.op("TST.L D2", 0x4A82)
    a.branch("BEQ.S go", 0x6700, "go")
    a.op("MOVEQ #11,D0", 0x700B)
    a.label("go")
    a.op32("JSR exter_int_helper", 0x4EB9, EXTER_HELPER)
    a.op("MOVEQ #0,D2", 0x7400)
    a.op("TST.L D0", 0x4A80)
    a.branch("BEQ.S idle", 0x6700, "idle")
    a.op("MOVEQ #1,D2", 0x7401)
    a.label("idle")
    a.op32("MOVE.L D2,STATE", 0x23C2, STATE)
    a.op32("MOVE.L COUNT,D3", 0x2639, COUNT)
    a.op32("ANDI.L #1,D3", 0x0283, 1)
    a.branch("BEQ.S keep", 0x6700, "keep")
    a.move_b_abs_d0(RTAREA_INTREQ)
    a.label("keep")
    a.op("MOVEM.L (A7)+,D0-D7/A0-A6", 0x4CDF, 0x7FFF)
    a.op("RTE", 0x4E73)
    _stubs(a)
    return a.source()


def loop_rom():
    return builder.build(loop_program())


# --- the program of the hard file fuzzer (T29d) -------------------------------------------------------------------------------------------------------------------
PARM, FSRES, FSNODE, NODE = 0x8000, 0x9000, 0x9100, 0x9200      # the parameter packet, a FileSystem.resource without file systems, its (empty) list, a device node
MOUNT_RES = RES + 0x200                                          # the result of each filesys_dev_storeinfo, a long per partition asked for
MOUNT_ASKED = 8                                                  # partitions asked for
STOREINFO, REMEMBER = 0xF0FF28, 0xF0FF20                         # filesys_dev_storeinfo and filesys_dev_remember, in the trap area
PP_FSPTR, PP_ADDTOFSRES, PP_FSRES, PP_DEVICEPROC, PP_EXPLIB = 404, 408, 412, 420, 424


def mount_program():
    """What the hard file fuzzer runs (tests/fuzz/fuzz_probe.cpp, `hardfile`): the boot of program(), then what expansion.library and the boot ROM of the filesystem do for a hard file with an RDB,
    without exec: for each of MOUNT_ASKED partitions, filesys_dev_storeinfo (which reads the RDB, the PART blocks and the FSHD and LSEG chain from the image the fuzzer mutated) and, when it
    answers a mount (D0 >= 0), filesys_dev_remember, which hands the file system the RDB brought over and lets the host free it. The answers are stored at MOUNT_RES, then DONE."""
    a = _Asm()
    _boot(a)
    a.move_l_imm_abs(FSNODE + 4, FSRES + 18)         # fsr_FileSysEntries: a list whose first node has no successor
    a.move_l_imm_abs(0, FSNODE + 4)
    a.move_l_imm_abs(0, FSNODE)
    for sub in range(MOUNT_ASKED):
        for field, value in ((PP_FSPTR, 0), (PP_ADDTOFSRES, 0), (PP_FSRES, FSRES), (PP_DEVICEPROC, 0), (PP_EXPLIB, 0)):
            a.move_l_imm_abs(value, PARM + field)
        a.move_l_imm_abs(0, NODE + 28)
        a.movea_l_imm(0, PARM)
        a.op32("MOVE.L #sub<<16,D6", 0x2C3C, sub << 16)
        a.op("MOVEQ #0,D7", 0x7E00)
        a.op32("JSR filesys_dev_storeinfo", 0x4EB9, STOREINFO)
        a.move_l_d0_abs(MOUNT_RES + 4 * sub)
        a.op("TST.L D0", 0x4A80)
        a.branch("BMI.S skip%d" % sub, 0x6B00, "skip%d" % sub)
        a.op("MOVEQ #0,D3", 0x7600)
        a.movea_l_imm(3, NODE)
        a.movea_l_imm(1, PARM)
        a.op32("JSR filesys_dev_remember", 0x4EB9, REMEMBER)
        a.label("skip%d" % sub)
    a.move_l_imm_abs(DONE, MOUNT_RES + 4 * MOUNT_ASKED)
    a.label("finish")
    a.op("STOP #$2700", 0x4E72, 0x2700)
    a.branch("BRA.S finish", 0x6000, "finish")
    _stubs(a)
    return a.source()


def mount_rom():
    return builder.build(mount_program())
