"""The micro-ROMs of T56: a guest that talks to the IDE port of Gayle (A600 and A1200) through its registers, with no Kickstart and no exec.

Three programs, all 68000 code of PUAE6 (MIT) hand-assembled into `.word` lines for tests/fixtures/microrom/build_rom.py, with the assembler class of tests/hdf/hdf_rom.py:

  program()       the I/O test (tests/hdf/test_ide.py): reads the Gayle ID, identifies drive 0, reads block 0 and block N, writes block 3, reads it back, writes two blocks across a
                  chunk boundary of the overlay, reads them back, asks for a block past the end of the disk and reads the interrupt status of Gayle after a command. It polls
                  (no interrupt) and records what it saw at RES.
  loop_program()  what DET02 runs (tests/state/test_det02_ide.py): an INT2 handler that acknowledges the drive, and a main loop that for ever writes two blocks (a new 4 KiB chunk of the
                  overlay every pass), reads them back and reads block 0 of the base, so that at most video boundaries a command is in progress: BSY or DRQ set, a sector half
                  transferred, the interrupt of the drive pending or on its way.
  chaos_program(seed)  a guest that never stops poking at Gayle: an xorshift generator picks, at every pass, an even address in $DA0000-$DAFFFE (the task files of both channels, the
                  interrupt status and enable, the card slot registers that are not there, and what is mapped around them), an operation (a write of a byte, a word, a long, a read
                  of a byte, a word, a long) and a value. The commands it issues are random, so is the order of the data transfers: the drive's state machine is driven the way no OS does. Used by the
                  fuzz tests (tests/hdf/test_ide.py, tests/fuzz): the machine must go on, repeat itself and save and load its state whatever the guest does.

Registers of the IDE of Gayle, as the A600 and A1200 Kickstarts address them: the task file at $DA2000 with a long between registers (data, error/features, sector count, sector,
cylinder low, cylinder high, drive/head, status/command), the interrupt status at $DA9000 (bit 7 is the IDE), its enable at $DAA000, and the Gayle ID at $DE1000.
"""
from tests.hdf.hdf_rom import _Asm, builder

IDE = 0xDA2000
DATA, ERROR, NSECTOR, SECTOR, LCYL, HCYL, SELECT, STATUS = (IDE + 4 * register for register in range(8))
GAYLE_IRQ, GAYLE_INT, GAYLE_ID = 0xDA9000, 0xDAA000, 0xDE1000
CMD_IDENTIFY, CMD_READ, CMD_WRITE = 0xEC, 0x20, 0x30
BUSY, DRQ, ERR = 0x80, 0x08, 0x01
STATUS_ADDRESS = STATUS
STATUS_ADDRESS = STATUS

BUF0, BUF1 = 0x4000, 0x4200                      # contiguous: block 0 and block N, written together
BUF3, BUF4, BUF5, IDBUF = 0x6000, 0x6400, 0x6800, 0x7000
RES = 0x5000
DONE = 0x444F4E45                                 # "DONE"
BLOCKS_READ = 5                                   # the second read, in blocks
WRITE_BLOCK, CROSS_BLOCK = 3, 7                   # CROSS_BLOCK and the next one are the last block of chunk 0 and the first of chunk 1
PAST_END = 0x0FFFFF00                             # an LBA beyond any disk of the tests

# What the guest recorded at RES: the 8 values of the Gayle ID register, then
R_ID = 0                                          # 8 bytes
R_IDENTIFY_STATUS, R_IRQ_AFTER_IDENTIFY, R_IRQ_AFTER_READ_STATUS = 8, 9, 10   # status after IDENTIFY; Gayle IRQ status with the drive's interrupt pending; ... after reading the drive's status
R_PAST_STATUS, R_PAST_ERROR = 11, 12              # status and error registers after a read past the end
R_DONE = 16                                       # long
R_INTCOUNT = 20                                   # long: INT2 interrupts served (loop_program)
R_COUNT = 24                                      # long: passes of the main loop (loop_program)


class _Ide(_Asm):
    """The instructions the programs need on top of hdf_rom's assembler, for one drive: `channel` 0 is the channel of Gayle ($DA2000), 1 the second one behind the IDE splitter
    ($DA3000); `drive` 0 is the master, 1 the slave."""

    def __init__(self, channel=0, drive=0):
        super().__init__()
        self.unique = 0
        self.drive = drive
        base = IDE + 0x1000 * channel
        self.data, self.error, self.nsector, self.sector, self.lcyl, self.hcyl, self.select, self.status = (base + 4 * register for register in range(8))

    def name(self, stem):
        self.unique += 1
        return "%s%d" % (stem, self.unique)

    def put(self, register, value):
        self.move_b_imm_abs(value & 0xFF, register)

    def lba(self, lba, count):
        """The task file of an LBA-28 command on the drive."""
        self.put(self.nsector, count)
        self.put(self.sector, lba)
        self.put(self.lcyl, lba >> 8)
        self.put(self.hcyl, lba >> 16)
        self.put(self.select, 0xE0 | (self.drive << 4) | ((lba >> 24) & 0x0F))

    def wait_ready(self):
        """Poll the status until BSY is clear."""
        loop = self.name("busy")
        self.label(loop)
        self.move_b_abs_d0(self.status)
        self.op("BTST #7,D0", 0x0800, 7)
        self.branch("BNE.S " + loop, 0x6600, loop)

    def words_in(self, buffer, count=256):
        loop = self.name("in")
        self.movea_l_imm(0, buffer)
        self.op("MOVE.W #%d,D1" % (count - 1), 0x323C, count - 1)
        self.label(loop)
        self.op32("MOVE.W $%X.L,(A0)+" % self.data, 0x30F9, self.data)
        self.branch("DBRA D1," + loop, 0x51C9, loop, wide=True)

    def words_out(self, buffer, count=256):
        loop = self.name("out")
        self.movea_l_imm(0, buffer)
        self.op("MOVE.W #%d,D1" % (count - 1), 0x323C, count - 1)
        self.label(loop)
        self.op32("MOVE.W (A0)+,$%X.L" % self.data, 0x33D8, self.data)
        self.branch("DBRA D1," + loop, 0x51C9, loop, wide=True)

    def read_sectors(self, lba, count, buffer):
        """READ SECTORS: after each sector the drive needs a moment (BSY) before it hands over the next."""
        self.lba(lba, count)
        self.put(self.status, CMD_READ)
        for sector in range(count):
            self.wait_ready()
            self.words_in(buffer + 512 * sector)

    def write_sectors(self, lba, count, buffer):
        self.lba(lba, count)
        self.put(self.status, CMD_WRITE)
        for sector in range(count):
            self.wait_ready()
            self.words_out(buffer + 512 * sector)
        self.wait_ready()


def _start(a):
    a.label("entry")
    a.op("MOVE.W #$2700,SR", 0x46FC, 0x2700)
    a.op("LEA $1F00.W,A7", 0x4FF8, 0x1F00)
    a.move_b_imm_abs(3, 0xBFE201)                     # CIA-A DDRA: OVL and the power LED are outputs
    a.move_b_imm_abs(2, 0xBFE001)                     # overlay off: Chip RAM at 0
    a.move_l_imm_abs(0, RES + R_DONE)


def program(channel=0, drive=0):
    """The I/O test; see the module text. `channel` and `drive` name the drive it talks to."""
    a = _Ide(channel, drive)
    _start(a)
    # the Gayle ID: a write restarts the sequence, then eight reads of the register
    a.move_b_imm_abs(0, GAYLE_ID)
    for k in range(8):
        a.move_b_abs_d0(GAYLE_ID)
        a.move_b_d0_abs(RES + R_ID + k)
    # IDENTIFY DEVICE of drive 0
    a.put(a.select, 0xA0 | (drive << 4))
    a.put(a.status, CMD_IDENTIFY)
    a.wait_ready()
    a.move_b_abs_d0(GAYLE_IRQ)                         # the drive's interrupt is pending: bit 7 of the status of Gayle
    a.move_b_d0_abs(RES + R_IRQ_AFTER_IDENTIFY)
    a.move_b_abs_d0(a.status)                          # reading the status takes the drive's interrupt back
    a.move_b_d0_abs(RES + R_IDENTIFY_STATUS)
    a.move_b_abs_d0(GAYLE_IRQ)                         # the status of Gayle keeps what happened until it is written
    a.move_b_d0_abs(RES + R_IRQ_AFTER_READ_STATUS)
    a.words_in(IDBUF)
    a.read_sectors(0, 1, BUF0)
    a.read_sectors(BLOCKS_READ, 1, BUF1)
    a.write_sectors(WRITE_BLOCK, 1, BUF0)
    a.read_sectors(WRITE_BLOCK, 1, BUF3)
    a.write_sectors(CROSS_BLOCK, 2, BUF0)             # BUF0 then BUF1: blocks 0 and N over blocks 7 and 8
    a.read_sectors(CROSS_BLOCK, 2, BUF4)
    # a read past the end of the disk: the drive answers with ERR and IDNF
    a.lba(PAST_END, 1)
    a.put(a.status, CMD_READ)
    a.wait_ready()
    a.move_b_abs_d0(a.error)
    a.move_b_d0_abs(RES + R_PAST_ERROR)
    a.move_b_abs_d0(a.status)
    a.move_b_d0_abs(RES + R_PAST_STATUS)
    a.move_l_imm_abs(DONE, RES + R_DONE)
    a.label("park")
    a.op("STOP #$2700", 0x4E72, 0x2700)
    a.branch("BRA.S park", 0x6000, "park")
    return a.source()


def rom(channel=0, drive=0):
    return builder.build(program(channel, drive))


def loop_program(channel=0, drive=0):
    """What DET02 runs; see the module text."""
    a = _Ide(channel, drive)
    _start(a)
    a.move_l_imm_abs(0, RES + R_INTCOUNT)
    a.move_l_imm_abs(0, RES + R_COUNT)
    a.move_l_label_abs("handler", 0x68)               # the level 2 autovector: Gayle's IDE interrupt
    a.put(a.select, 0xA0 | (drive << 4))
    a.put(GAYLE_INT, 0x80)                            # the IDE interrupt of Gayle is enabled
    a.move_w_imm_abs(0xC008, 0xDFF09A)                # INTENA: SET | INTEN | PORTS
    a.op("MOVE.W #$2000,SR", 0x46FC, 0x2000)
    a.label("main")
    a.op32("MOVE.L count,D3", 0x2639, RES + R_COUNT)
    a.op("ADDQ.L #1,D3", 0x5283)
    a.op32("MOVE.L D3,count", 0x23C3, RES + R_COUNT)
    a.op32("MOVE.L D3,BUF0", 0x23C3, BUF0)            # the data: the pass count at the start of both blocks
    a.op32("MOVE.L D3,BUF1", 0x23C3, BUF1)
    # the block to write: 7 + 8 * (pass & 127), so each pass puts the two blocks in a new chunk of the overlay (a chunk is 8 blocks)
    a.op("MOVE.L D3,D0", 0x2003)
    a.op32("ANDI.L #127,D0", 0x0280, 127)
    a.op("LSL.L #3,D0", 0xE788)
    a.op("ADDQ.L #7,D0", 0x5E80)
    a.op32("MOVE.L D0,block", 0x23C0, RES + 0x100)
    # the task file, one byte at a time from the block in RES+0x100 (LBA < 2048: the high bytes are 0)
    a.put(a.nsector, 2)
    a.op32("MOVE.B block+3,D1", 0x1239, RES + 0x103)
    a.op32("MOVE.B D1,SECTOR", 0x13C1, a.sector)
    a.op32("MOVE.B block+2,D1", 0x1239, RES + 0x102)
    a.op32("MOVE.B D1,LCYL", 0x13C1, a.lcyl)
    a.put(a.hcyl, 0)
    a.put(a.select, 0xE0 | (drive << 4))
    a.put(a.status, CMD_WRITE)
    for sector in range(2):
        a.wait_ready()
        a.words_out(BUF0 + 512 * sector)
    a.wait_ready()
    # the same blocks back
    a.put(a.nsector, 2)
    a.op32("MOVE.B block+3,D1", 0x1239, RES + 0x103)
    a.op32("MOVE.B D1,SECTOR", 0x13C1, a.sector)
    a.op32("MOVE.B block+2,D1", 0x1239, RES + 0x102)
    a.op32("MOVE.B D1,LCYL", 0x13C1, a.lcyl)
    a.put(a.hcyl, 0)
    a.put(a.select, 0xE0 | (drive << 4))
    a.put(a.status, CMD_READ)
    for sector in range(2):
        a.wait_ready()
        a.words_in(BUF4 + 512 * sector)
    # block 0 of the base
    a.read_sectors(0, 1, BUF5)
    a.branch("BRA.W main", 0x6000, "main", wide=True)
    a.label("handler")
    a.op("MOVEM.L D0-D1,-(A7)", 0x48E7, 0xC000)
    a.move_b_abs_d0(a.status)                           # acknowledges the drive
    a.move_b_imm_abs(0, GAYLE_IRQ)                    # and Gayle
    a.move_w_imm_abs(0x0008, 0xDFF09C)                # INTREQ: clear PORTS
    a.op32("ADDQ.L #1,intcount", 0x52B9, RES + R_INTCOUNT)
    a.op("MOVEM.L (A7)+,D0-D1", 0x4CDF, 0x0003)
    a.op("RTE", 0x4E73)
    return a.source()


def loop_rom(channel=0, drive=0):
    return builder.build(loop_program(channel, drive))


COMMANDS = (0x20, 0x30, 0xEC, 0x91, 0xC6, 0xC4, 0xC5, 0x10, 0xEF, 0x50, 0x70, 0xE7, 0x90, 0x40, 0x21, 0x31)     # read, write, identify, initialize, multiple mode, read and write multiple, ...
TABLE = RES + 0x200                                                                                              # the address of COMMANDS in the ROM is stored here


COMMANDS = (0x20, 0x30, 0xEC, 0x91, 0xC6, 0xC4, 0xC5, 0x10, 0xEF, 0x50, 0x70, 0xE7, 0x90, 0x40, 0x21, 0x31)     # read, write, identify, initialize, multiple mode, read and write multiple, ...
TABLE = RES + 0x200                                                                                              # the address of COMMANDS in the ROM is stored here


def chaos_program(seed):
    """The poking guest; see the module text. `seed` (not zero) starts the generator. Every pass picks one of eight things at random: a write to a random register of the task file, a
    command from a list of the ones a drive knows, a word out or in of the data port, a byte read of a random register, or a task file set up for a small transfer."""
    a = _Ide()
    _start(a)
    a.move_l_imm_abs(0, RES + R_INTCOUNT)
    a.move_l_label_abs("handler", 0x68)
    a.move_l_label_abs("commands", TABLE)
    a.put(GAYLE_INT, 0x80)
    a.move_w_imm_abs(0xC008, 0xDFF09A)
    a.op32("MOVE.L #seed,D3", 0x263C, seed or 1)
    a.op("MOVE.W #$2000,SR", 0x46FC, 0x2000)
    a.label("main")
    for count, direction in ((13, "LSL"), (17, "LSR"), (5, "LSL")):      # xorshift32: x ^= x << 13; x ^= x >> 17; x ^= x << 5
        a.op("MOVE.L D3,D0", 0x2003)
        a.op("MOVEQ #%d,D1" % count, 0x7200 | count)
        a.op("%s.L D1,D0" % direction, 0xE3A8 if direction == "LSL" else 0xE2A8)
        a.op("EOR.L D0,D3", 0xB183)
    a.op("MOVE.L D3,D2", 0x2403)
    a.op("SWAP D2", 0x4842)                           # the value: the high word of the generator
    a.op("MOVE.L D3,D1", 0x2203)
    a.op("LSR.L #3,D1", 0xE689)
    a.op32("ANDI.L #7,D1", 0x0281, 7)                 # the operation
    labels = ["op%d" % k for k in range(8)]
    for label in labels:
        a.op("TST.L D1", 0x4A81)
        a.branch("BEQ.W " + label, 0x6700, label, wide=True)
        a.op("SUBQ.L #1,D1", 0x5381)
    a.branch("BRA.W main", 0x6000, "main", wide=True)

    def register(a):
        """A0 = the address of a random register of the task file of channel 0."""
        a.op("MOVE.L D3,D0", 0x2003)
        a.op("MOVEQ #8,D1", 0x7208)
        a.op("LSR.L D1,D0", 0xE2A8)
        a.op32("ANDI.L #7,D0", 0x0280, 7)
        a.op("ADD.L D0,D0", 0xD080)
        a.op("ADD.L D0,D0", 0xD080)
        a.op32("ADDI.L #$%X,D0" % IDE, 0x0680, IDE)
        a.op("MOVEA.L D0,A0", 0x2040)

    def finish(a):
        a.branch("BRA.W main", 0x6000, "main", wide=True)

    a.label("op0")                                    # a random byte to a random register
    register(a)
    a.op("MOVE.B D2,(A0)", 0x1082)
    finish(a)
    a.label("op1")                                    # a random byte to a random register
    register(a)
    a.op("MOVE.B D2,(A0)", 0x1082)
    finish(a)
    for label in ("op2", "op3"):                      # a command a drive knows
        a.label(label)
        a.op32("ANDI.L #15,D2", 0x0282, 15)
        a.op32("MOVEA.L table,A1", 0x2279, TABLE)
        a.op("MOVE.B (0,A1,D2.L),D2", 0x1431, 0x2800)
        a.op32("MOVE.B D2,STATUS", 0x13C2, STATUS_ADDRESS)
        finish(a)
    a.label("op4")                                    # a burst of words to the data port (a sector's worth, or what is left of the transfer)
    a.op("MOVE.W #255,D1", 0x323C, 255)
    a.label("burst_out")
    a.op32("MOVE.W D2,DATA", 0x33C2, DATA)
    a.branch("DBRA D1,burst_out", 0x51C9, "burst_out", wide=True)
    finish(a)
    a.label("op5")                                    # a burst of words from the data port
    a.op("MOVE.W #255,D1", 0x323C, 255)
    a.label("burst_in")
    a.op32("MOVE.W DATA,D4", 0x3839, DATA)
    a.branch("DBRA D1,burst_in", 0x51C9, "burst_in", wide=True)
    finish(a)
    a.label("op6")                                    # a byte from a random register
    register(a)
    a.op("MOVE.B (A0),D4", 0x1810)
    finish(a)
    a.label("op7")                                    # a task file for a small transfer in LBA mode
    a.op32("ANDI.L #3,D2", 0x0282, 3)
    a.op32("MOVE.B D2,NSECTOR", 0x13C2, NSECTOR)
    a.op("MOVE.L D3,D2", 0x2403)
    a.op32("ANDI.L #$3F,D2", 0x0282, 0x3F)
    a.op32("MOVE.B D2,SECTOR", 0x13C2, SECTOR)
    a.put(LCYL, 0)
    a.put(HCYL, 0)
    a.put(SELECT, 0xE0)
    finish(a)
    a.label("handler")
    a.op("MOVEM.L D0-D1,-(A7)", 0x48E7, 0xC000)
    a.move_b_abs_d0(STATUS_ADDRESS)
    a.move_b_imm_abs(0, GAYLE_IRQ)
    a.move_w_imm_abs(0x0008, 0xDFF09C)
    a.op32("ADDQ.L #1,intcount", 0x52B9, RES + R_INTCOUNT)
    a.op("MOVEM.L (A7)+,D0-D1", 0x4CDF, 0x0003)
    a.op("RTE", 0x4E73)
    a.label("commands")                               # the table of commands: 16 bytes
    for k in range(0, 16, 2):
        a.word((COMMANDS[k] << 8) | COMMANDS[k + 1])
    return a.source()


def chaos_rom(seed):
    return builder.build(chaos_program(seed))
