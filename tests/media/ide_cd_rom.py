"""Minimal 68000 guest for T84e2's Gayle ATAPI CD transport."""
from tests.hdf import ide_rom

IDBUF = 0x7000
INQUIRY = 0x7200
CAPACITY = 0x7240
SENSE = 0x7280
SECTOR = 0x8000
RES = 0x5000
R_IDENTIFY_STATUS = 0
R_UNSUPPORTED_STATUS = 1
R_UNSUPPORTED_ERROR = 2
R_PLAY_STATUS = 3
R_DONE = 4
DONE = 0x41544150  # "ATAP"


def program():
    a = ide_rom._Ide()
    ide_rom._start(a)
    a.put(a.select, 0xA0)

    # IDENTIFY PACKET DEVICE.
    a.put(a.status, 0xA1)
    a.wait_ready()
    a.move_b_abs_d0(a.status)
    a.move_b_d0_abs(RES + R_IDENTIFY_STATUS)
    a.words_in(IDBUF)

    def packet(cdb, maximum, response, words):
        assert len(cdb) == 12
        a.put(a.lcyl, maximum)
        a.put(a.hcyl, maximum >> 8)
        a.put(a.status, 0xA0)
        a.wait_ready()
        for n in range(0, 12, 2):
            a.move_w_imm_abs((cdb[n] << 8) | cdb[n + 1], a.data)
        a.wait_ready()
        if words:
            a.words_in(response, words)

    packet(bytes((0x12, 0, 0, 0, 36, 0, 0, 0, 0, 0, 0, 0)), 36, INQUIRY, 18)
    packet(bytes((0x25, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0)), 8, CAPACITY, 4)
    packet(bytes((0x28, 0, 0, 0, 0, 0, 0, 0, 1, 0, 0, 0)), 2048, SECTOR, 1024)

    # Unsupported opcodes must end in CHECK CONDITION and preserve the reason
    # until REQUEST SENSE acknowledges it.
    packet(bytes((0xFF, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0)), 18, 0, 0)
    a.move_b_abs_d0(a.error)
    a.move_b_d0_abs(RES + R_UNSUPPORTED_ERROR)
    a.move_b_abs_d0(a.status)
    a.move_b_d0_abs(RES + R_UNSUPPORTED_STATUS)
    packet(bytes((0x03, 0, 0, 0, 18, 0, 0, 0, 0, 0, 0, 0)), 18, SENSE, 9)

    # PLAY AUDIO(10): the native probe keeps running frames after the guest
    # parks and checks that the common emulated CDDA clock advances the head.
    packet(bytes((0x45, 0, 0, 0, 0, 4, 0, 0, 12, 0, 0, 0)), 18, 0, 0)
    a.move_b_abs_d0(a.status)
    a.move_b_d0_abs(RES + R_PLAY_STATUS)
    a.move_l_imm_abs(DONE, RES + R_DONE)
    a.label("park")
    a.op("STOP #$2700", 0x4E72, 0x2700)
    a.branch("BRA.S park", 0x6000, "park")
    return a.source()


def rom():
    return ide_rom.builder.build(program())


def state_program():
    """Long-lived command, data and CDDA phases for T84e3/DET02.

    A runtime DBRA delay follows every packet word, so ordinary frame
    boundaries cut through the six-word command rather than depending on host
    speed. READ(12) then consumes the largest supported 128-sector burst; its
    256 KiB causal buffer spans many more boundaries before PLAY AUDIO parks.
    """
    a = ide_rom._Ide()
    ide_rom._start(a)
    a.put(a.select, 0xA0)

    def delay():
        loop = a.name("delay")
        a.op("MOVE.W #$FFFF,D1", 0x323C, 0xFFFF)
        a.label(loop)
        a.branch("DBRA D1," + loop, 0x51C9, loop, wide=True)

    def packet(cdb, maximum=0xFFFF, delayed=False):
        assert len(cdb) == 12
        a.put(a.lcyl, maximum)
        a.put(a.hcyl, maximum >> 8)
        a.put(a.status, 0xA0)
        a.wait_ready()
        for n in range(0, 12, 2):
            a.move_w_imm_abs((cdb[n] << 8) | cdb[n + 1], a.data)
            if delayed:
                delay()

    # 128 canonical data sectors fill the complete bounded ATAPI response.
    packet(bytes((0xA8, 0, 0, 0, 0, 0, 0, 0, 0, 128, 0, 0)), delayed=True)
    a.wait_ready()
    a.words_in(SECTOR, 65536)
    a.words_in(SECTOR + 131072, 65536)

    # Audio starts at the second track and remains active while later frames
    # are observed, saved and restored.
    packet(bytes((0x45, 0, 0, 0, 0, 132, 0, 0, 64, 0, 0, 0)))
    a.label("cdda")
    a.op("ADDQ.L #1,D0", 0x5280)
    a.branch("BRA.S cdda", 0x6000, "cdda")
    return a.source()


def state_rom():
    return ide_rom.builder.build(state_program())
