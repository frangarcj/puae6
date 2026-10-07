"""T15i3, T15f (T15f1, T15f2, T15f3 and its closure) and T15e: DET02 end to end with every chunk of the state, and where it still diverges.

DET02 (docs/04-validation.md): a state saved at unit N, the machine run on, the state loaded, and the units after it run again must give the machine of the original run, subsystem by
subsystem. The probe digests cpu, scheduler, ram, rtc, cia, chipset (by group: beam, counters, irq_dma, clxdat, lof, timing), audio, input, video and bridge, and hashes the PCM of
every unit; all of them are compared, unit by unit, with the original run. "Unit 0" is the machine right after the load (the picture and the PCM are the output of the unit that ran
before, so they are compared from unit 1 on); "unit j" is the j-th unit that ran after the load. Left out: the chipset's custom_storage (the engine only writes it, for its debugger),
bit 15 of clxdat (a read ORs it in; restore_custom stores the saved image with it) and the picture's frame counter (hand-overs of this process): none of them is state.

The machine that loads the state is one of (the "mode" of a test):
  same-image            the image of the original, REWIND units past N: its history is not the state's;
  same-image-beam-set   the same, with the beam counters set where the state had them right after the load (probe poke beam:V:L:H). Until T15f1 the load threw the beam away
                        (restore_custom reads VPOSR and VHPOSR and discards them) and this mode showed what DET02 finds once it is out of the way; BEAM carries it now and the poke
                        puts the beam where the load already did: the mode is kept as a control;
  reset-image-beam-set  the image restarted its program (p6_reset) one unit before the load, so that what the program wrote earlier, to registers a state does not carry,
                        is not there: the detector of hidden state that the same program, run for more units, writes to the same values again (a same image has them);
  new-process           a fresh image that ran its reset head: since T15f1 it is on the original's line, in its frame phase and with its line length, and it is the detector
                        of what the other chunks still lack (the picture, the audio phase);
  new-process-beam-set  the same, with the beam set.
A scenario that diverges is a test that EXPECTS to fail (unittest.expectedFailure, counted by tests/host/run_parallel.py as an "expected failure") and next to it a test that pins
where the first divergence is (DIVERGENCES: first unit and every subsystem that differs in it), so that a divergence that moves is seen. A scenario that matches is an ordinary test:
the regression gate of T15d, T15e, T15f and T15g. When a task makes a scenario match, its entry leaves DIVERGENCES and nothing else changes (the expected failure would otherwise
be an unexpected success, which unittest reports as a failure). Anything that does not match is attributed by the subsystem digest: disk to T15d, audio and PCM to T15e,
chipset and RAM written by the blitter to T15f, the picture to T15g; CAUSES says what is known of each.

What a unit boundary can show: a unit ends in the vertical blank (vpos 4), so bitplane DMA, sprite DMA, the line being drawn and Denise's input ring are idle there. The starting list of
the card, item by item:
  beam position (VPOSR/VHPOSR)       fixed by T15f1 (BEAM, patch 0094): chipset.beam matches at unit 0 in every scenario, with the frame phase, the strobe that raises VERTB and the DMAL
  Copper waiting                      fixed by T15f2 (COPR, patch 0095): copper_irq (WAIT for line 4) in PAL and NTSC, in every mode, no longer diverges by the Copper; what is left of
                                      those rows is the audio phase
  blitter in flight                   fixed by T15f3 (BLTP, patch 0097, and the fourth RGA slot of CHSL, patch 0096): busy, busy-blitter, blit+bitplanes, blit+sprites, blit+audio, blit+disk and busy-ntsc no
                                      longer diverge in RAM, DMACON, INTREQ or the DMA slots, in an image restarted before the load or not; what is left of those rows is the audio phase
  derived Agnus values                fixed by the closure of T15f (patch 0098, p6_custom_rebuild_derived, run after the bodies of CHIP): what setup_fmodes and SPRxCTLPOS derive from BPLCON0, FMODE
                                      and the sprite registers (the plane count, the fetch sequence, the first and last line of each sprite) is in no body, and WinUAE's own load gets it from
                                      custom_reset, which the bridge never runs on a live machine. An image that had not written the same registers itself (restarted before the load, or a new
                                      process) fetched its bitplanes from the planes of its reset: the Kickstart at 107 (keyboard handshake) lost bus slots differently and its CPU, scheduler, RAM
                                      and clock differed a unit after the load. late-bitplanes (planes set up late, the CPU reading Chip RAM under them) pins the planes through the picture; the Kickstart rows are now
                                      the palette and the audio phase like every other program
  rga_denise ring                     not shown: the boundary is in the blank, nothing of Denise is pending there
  bitplane DMA, spr[].dmastate        not shown: busy-bitplanes and busy-sprites match in an image restarted one unit before the load (idle at the boundary)
  dsklen2, linecounter                not shown: the DMA of busy-disk-dma stays armed (WORDSYNC): in 14 units it writes nothing to RAM and never raises DSKBLK, so what matters
                                      when a transfer ends is not exercised; T15d needs a micro-ROM whose transfer completes, with a boundary in the middle of it
  delayed disk change                 not shown any more: the cpu digest that differed at unit 10 was the DMAL in flight, which T15f1 saves, and the audio
                                      rows that followed were T15e's: disk-change matches now
  audio filter and resampler          fixed by T15e (AUDP, patch 0099): the resampler phase, the cycles not yet integrated, the output and LED filters, the interpolation
                                      integrator and the per-channel latches AUDn does not carry (dat2, pbufldl, irqcheck, the exact period with its PERIOD_MAX - 1 sentinel,
                                      the ADKCON masks). The PCM of every unit after the load is the original's sample for sample, in every mode, silent programs, tone and
                                      tone-modulated (channel 0 modulating the volume of channel 1 with the LED filter on) included. A new process also handed over, in its
                                      first unit, the samples its own reset head had queued before the load: load() now drops them (the PCM transport is not state)
Not testable at a boundary: a machine mid-line.

DIVERGENCES: "scenario@N/mode" -> (first unit after the load, subsystems that differ there, owner, cause).
"""
import hashlib
import struct
import unittest

from tests.state import test_state_cia as cia_tests
from tests.state import test_state_input as input_tests
from tests.state import test_state_rtc as rtc_tests
from tests.state import test_state_upstream as upstream
from tests.state.harness import ProbeCase, SUBSYSTEMS, TRACE, USER
from tests.scenarios.audio.audio import Tone, rom_source as tone_source

# What of the chipset digest is compared, by group. "registers" (custom_storage, the last value written to each register) is left out: the engine only writes it, for its debugger,
# and a restored machine, which never saw the writes, cannot have it; nothing that runs reads it.
CHIP_GROUPS = ("beam", "counters", "irq_dma", "clxdat", "lof", "timing")
UNITS_AFTER = 6         # M: units that run after the load
REWIND = 3              # k: units the same image has run past N when it loads the state

# Ways the machine that loads the state differs from the one that saved it (the modes of a scenario):
SAME = "same-image"                    # the same image, REWIND units past N: the beam of the loaded machine is where the machine already was
SAME_BEAM = "same-image-beam-set"      # the same, with the beam counters then set where the state had them (diagnosis: what DET02 shows once the beam is out of the way)
RESET_BEAM = "reset-image-beam-set"    # the image restarted its program (p6_reset) one unit before the load, so that what the program wrote earlier is not there: the detector of
                                       # state that a state does not carry and that the same program, run for more units, writes again to the same values; beam set
NEW = "new-process"                    # a fresh image that ran its reset head
NEW_BEAM = "new-process-beam-set"

# name -> (rom name, extra probe arguments, units N at which the state is taken, modes). Arguments name absolute units: a unit above N is shifted to the unit the loaded image is at
# (by REWIND in the same image, by -N in a new process: the host sends in the unit after the load what it sent in the unit after the saved one). Arguments at or below N are the
# history a new process has to be given at its start: only boot media (unit 0) and held keys and ports, which collapse to the packet of the first unit, are allowed there. A media
# event or a reset is applied after the unit it names, and the digest of that unit is taken before it: N is never the unit of one.
CORE = (SAME, SAME_BEAM, RESET_BEAM, NEW, NEW_BEAM)
SHAPES = (SAME_BEAM, NEW)                      # programs that differ in how the CPU runs, not in what the chipset does
CHANNELS = (SAME_BEAM, RESET_BEAM)             # one DMA channel at a time
SCENARIOS = {
    "vsync": ("vsync", [], (3,), CORE),
    "copper_irq": ("copper_irq", [], (2,), CORE),
    "tone": ("tone", [], (3,), CORE),
    "tone-modulated": ("tone-mod", [], (3, 4), CORE),
    "busy": ("busy", [], (2, 5), CORE),
    "busy-disk": ("busy", "disk", (8,), CORE),
    "copper_irq-wait": ("copper_irq", [], (1, 3), (SAME_BEAM, RESET_BEAM)),
    "vsync-late": ("vsync", [], (6,), (SAME_BEAM, RESET_BEAM)),
    "stop": ("stop", [], (3,), SHAPES),
    "halt": ("halt", [], (2,), SHAPES),
    "trace": ("trace", [], (3,), SHAPES),
    "user": ("user", [], (3,), SHAPES),
    "long_ops": ("long_ops", [], (3, 4), SHAPES),
    "clock": ("clock", [], (4,), SHAPES),
    "cias": ("cias", [], (8,), SHAPES),
    "mouse": ("input_all", ["--port", "0:2:1:40:-25", "--port", "1:1:65:0:0"], (4,), SHAPES),
    "keys": ("vsync", input_tests.SCENARIOS["keys"][3], (6,), SHAPES),
    "pot": ("potgo", input_tests.SCENARIOS["pot-measuring"][3], (6,), SHAPES),
    "lof": ("lof_stuck", [], (4,), SHAPES),
    # The same program with one DMA channel at a time (the blit is always started, and runs only when BLTEN is on): which of them a divergence belongs to.
    "busy-blitter": ("busy-blt", [], (2, 5), CHANNELS),
    "busy-bitplanes": ("busy-bpl", [], (5,), CHANNELS),
    "busy-sprites": ("busy-spr", [], (5,), CHANNELS),
    "busy-copper": ("busy-cop", [], (5,), CHANNELS),
    "busy-audio": ("busy-aud", [], (5,), CHANNELS),
    "busy-disk-dma": ("busy-dsk", "disk", (8,), CHANNELS),
    # The blit in flight (unit 2) with each of the other channels stealing slots from it.
    "blit+bitplanes": ("blt-bpl", [], (2,), CHANNELS),
    "blit+sprites": ("blt-spr", [], (2,), CHANNELS),
    "blit+copper": ("blt-cop", [], (2,), CHANNELS),
    "blit+audio": ("blt-aud", [], (2,), CHANNELS),
    "blit+disk": ("blt-dsk", "disk", (2,), CHANNELS),
    "late-bitplanes": ("late-bpl", [], (8,), (SAME_BEAM, RESET_BEAM, NEW)),
    "busy-ntsc": ("busy", ["--ntsc"], (2,), (SAME, RESET_BEAM, NEW)),
    "copper_irq-ntsc": ("copper_irq", ["--ntsc"], (2,), (SAME, RESET_BEAM, NEW)),
    # The medium out of DF0 (unit 9, applied in 10) and in again (unit 11, applied in 12): the state is taken before, in the middle of and after the change.
    "disk-change": ("busy", "change", (10, 12, 13), (SAME_BEAM,)),
    "disk-change-reset": ("busy", "change", (10,), (RESET_BEAM,)),
}

# A program that sets its bitplanes up late: after about two and a half frames of NOPs, so that an image restarted one unit before the load has not got there (and its Agnus has the
# planes of its reset), with the CPU reading Chip RAM in a loop under the DMA of four planes. What Agnus derives from BPLCON0 (the plane count, the fetch sequence) is in no body.
LATE_BITPLANES = """; PUAE6 test program: four bitplanes set up after a long wait, and a loop that reads Chip RAM while they are fetched.
entry:
    MOVE.W #$2700,SR
    LEA $400.W,A7
    .nops 100000
    MOVE.W #$4200,$DFF100.L     ; BPLCON0: 4 planes
    MOVE.W #$0038,$DFF092.L     ; DDFSTRT
    MOVE.W #$00D0,$DFF094.L     ; DDFSTOP
    MOVE.W #$2C81,$DFF08E.L     ; DIWSTRT
    MOVE.W #$2CC1,$DFF090.L     ; DIWSTOP
    MOVE.L #$4000,$DFF0E0.L     ; BPL1PT .. BPL4PT
    MOVE.L #$5000,$DFF0E4.L
    MOVE.L #$6000,$DFF0E8.L
    MOVE.L #$7000,$DFF0EC.L
    MOVE.W #$8300,$DFF096.L     ; DMACON: SET, master and bitplanes
loop:
    MOVE.B $4000.L,D2
    ADDQ.L #1,D0
    BRA.S loop
"""

TONE_MODULATED = """; PUAE6 test program: channel 0 modulates the volume of channel 1 (ADKCON USE0V1), with the power LED on (the LED filter on).
entry:
    MOVE.W #$2700,SR
    LEA $400.W,A7
    MOVE.B #3,$BFE201.L
    MOVE.B #0,$BFE001.L         ; power LED on: the LED filter is on
    MOVE.W #$000F,$DFF096.L     ; audio DMA off
    MOVE.L #$64646464,$20000.L  ; a square wave of 16 bytes for channel 1
    MOVE.L #$64646464,$20004.L
    MOVE.L #$9C9C9C9C,$20008.L
    MOVE.L #$9C9C9C9C,$2000C.L
    MOVE.L #$00100040,$20100.L  ; four volumes for channel 0 to write into AUD1VOL
    MOVE.L #$00200008,$20104.L
    MOVE.L #$20100,$DFF0A0.L    ; AUD0LC
    MOVE.W #4,$DFF0A4.L         ; AUD0LEN: 4 words
    MOVE.W #1500,$DFF0A6.L      ; AUD0PER: a new volume every 1500 colour clocks
    MOVE.W #64,$DFF0A8.L
    MOVE.L #$20000,$DFF0B0.L    ; AUD1LC
    MOVE.W #8,$DFF0B4.L         ; AUD1LEN: 8 words
    MOVE.W #200,$DFF0B6.L       ; AUD1PER
    MOVE.W #32,$DFF0B8.L
    MOVE.W #$8001,$DFF09E.L     ; ADKCON: SET | USE0V1
    MOVE.W #$8203,$DFF096.L     ; DMACON: SET | DMAEN | AUD0EN | AUD1EN
loop:
    ADDQ.L #1,D0
    BRA.S loop
"""

# cause id -> (owner, what is known). The owner is the task whose card has to make the scenarios of that cause match; the text is where the attribution came from.
CAUSES = {
    "palette": ("T15g", "the colour registers are put back (the register image and agnus_colors) but Denise's derived colour table is not rebuilt: in the first picture "
                        "after the load the border is black where the state's COLOR00 is red. Only a machine that has not already written the same colours shows it "
                        "(a new image, or one restarted before the load): with the beam and the frame phase restored it is the first thing a new image shows in "
                        "every program that sets a colour). Denise's presentation memory is the same owner: a same image that has run on to 576 lines shows 576 where the original, "
                        "still at 574, shows 574, for the units after the load (the Kickstart at 107, where it changes)"),
}


def row(cause, unit, subsystems):
    """(first unit after the load, subsystems that differ in it, owner, cause)."""
    return (unit, tuple(subsystems.split()), CAUSES[cause][0], CAUSES[cause][1])


# Where DET02 diverges, from a run of this module: scenario@N/mode -> (first unit after the load, what differs there, owner, cause).
DIVERGENCES = {
    "busy@2/new-process": row("palette", 1, "video"),
    "busy@2/new-process-beam-set": row("palette", 1, "video"),
    "busy@5/new-process": row("palette", 1, "video"),
    "busy@5/new-process-beam-set": row("palette", 1, "video"),
    "busy-disk@8/new-process": row("palette", 1, "video"),
    "busy-disk@8/new-process-beam-set": row("palette", 1, "video"),
    "lof@4/new-process": row("palette", 1, "video"),
    "busy-ntsc@2/new-process": row("palette", 1, "video"),
}


def shifted(args, n, offset, fresh):
    """The packet arguments of the machine that loads the state at its unit `offset` (a unit past N, or 0 for a new process)."""
    widths = {"--key": 4, "--port-at": 3, "--event-at": 5, "--reset-at": 2}
    out, i = [], 0
    while i < len(args):
        width = widths.get(args[i])
        if not width:
            out.append(args[i])
            i += 1
            continue
        unit = int(args[i + 1])
        if unit > n:
            unit = unit - n + offset
        elif fresh and args[i] != "--event-at":
            unit = 0                    # held keys and ports: the packet of the first unit
        elif fresh and unit != 0:
            raise AssertionError("a media event before the saved unit cannot be replayed in a new process")
        out += [args[i], str(unit), *args[i + 2:i + width]]
        i += width
    return out


class Det02Case(ProbeCase):
    """The machinery of DET02: the original run, the run that loads, and what differs between them."""

    ROMS = {}
    cache = {}

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.adf = cls.dir / "a.adf"
        cls.adf.write_bytes(hashlib.sha256(b"disk a").digest() * (901120 // 32))
        cls.cache = {}

    def arguments(self, extra):
        """The probe arguments of a scenario: the disk is mounted at unit 0 as a boot medium; "change" ejects it at unit 9 and mounts it again at unit 11."""
        if extra == "disk":
            return ["--adf", str(self.adf), "--event-at", "0", "0", "0", "1"]
        if extra == "change":
            return ["--adf", str(self.adf), "--event-at", "0", "0", "0", "1", "--event-at", "9", "0", "-1", "2", "--event-at", "11", "0", "0", "3"]
        return list(extra)

    def original(self, rom, extra, n):
        key = ("original", rom, tuple(map(str, self.arguments(extra))), n)
        if key not in self.cache:
            path = self.dir / f"det02-{len(self.cache)}.state"
            run = self.probe(rom, "--units", str(n + UNITS_AFTER), "--every", "1", "--dump", "chipset", *self.arguments(extra), "--save-at", str(n), str(path))
            self.cache[key] = (run, path)
        return self.cache[key]

    @staticmethod
    def beam_of(run, n):
        """The beam counters (vpos, linear_vpos, agnus_hpos) the chipset digest hashed at unit n: its first bytes."""
        b = run.dumps[(n, "chipset")]
        return struct.unpack_from("<iiB", b, 0)

    def other(self, rom, extra, n, mode):
        """The run of the machine that loads the state, and the index of its digests after the load: `mode` says whether it is the same image, N + REWIND units on, or a
        fresh image, and whether the beam counters are set where the state had them right after the load."""
        key = ("other", rom, tuple(map(str, self.arguments(extra))), n, mode)
        if key not in self.cache:
            original, path = self.original(rom, extra, n)
            fresh = mode.startswith("new-process")
            offset = 0 if fresh else n + REWIND
            ops = ["--load-at", str(offset), str(path)]
            if mode.startswith("reset-image"):
                ops = ["--reset-at", str(offset - 1)] + ops          # the image restarts its program one unit before the load: its history is not the state's
            if mode.endswith("beam-set"):
                v, l, h = self.beam_of(original, n)
                ops += ["--poke-at", str(offset), f"beam:{v}:{l}:{h}", "--digest-at", str(offset)]
            run = self.probe(rom, "--units", str(offset + UNITS_AFTER), "--every", "1", *shifted(self.arguments(extra), n, offset, fresh), *ops)
            self.assertEqual([(offset, 0, "")], run.loads[:1], "the state is refused")
            self.cache[key] = (run, offset, len(run.after) - 1)
        return self.cache[key]

    def differing(self, original, run, n, offset, j):
        """What differs, in the unit j after the load, from the original j units after N."""
        different = [s for s in SUBSYSTEMS if s != "chipset" and run.digests[(offset + j, s)] != original.digests[(n + j, s)]]
        different += [f"chipset.{g}" for g in CHIP_GROUPS if run.chip[(offset + j, g)] != original.chip[(n + j, g)]]
        if run.pcm.get(offset + j) != original.pcm.get(n + j):
            different.append("pcm")
        return different

    def first_divergence(self, rom, extra, n, mode):
        """(units after the load, [what differs], [what differs in the last unit]) of the first unit in which the loaded machine is not the original, or None.
        The chipset is told apart by group (chipset.beam, chipset.clxdat...) and the picture is the output of the unit before the load, so it is left out of unit 0."""
        original, _ = self.original(rom, extra, n)
        run, offset, at = self.other(rom, extra, n, mode)
        loaded = run.after[at]
        different = [s for s in SUBSYSTEMS if s not in ("video", "chipset") and loaded[s] != original.digests[(n, s)]]
        different += [f"chipset.{g}" for g in CHIP_GROUPS if run.chip_after[at][g] != original.chip[(n, g)]]
        last = self.differing(original, run, n, offset, UNITS_AFTER)
        if different:
            return 0, different, last
        for j in range(1, UNITS_AFTER + 1):
            different = self.differing(original, run, n, offset, j)
            if different:
                return j, different, last
        return None


def populate(cls, scenarios, divergences, gate=lambda test: test):
    """One test per scenario, boundary and mode on `cls`. A scenario that diverges has two: DET02 itself, which is expected to fail, and the pin of where it first diverges."""
    for name, (rom, extra, points, modes) in scenarios.items():
        for n in points:
            for mode in modes:
                key = f"{name}@{n}/{mode}"
                slug = key.replace("@", "_at_").replace("/", "_").replace("-", "_").replace("+", "_")

                def det02(self, rom=rom, extra=extra, n=n, mode=mode, key=key):
                    found = self.first_divergence(rom, extra, n, mode)
                    if found is not None:
                        owner = divergences.get(key, (0, (), "unowned", "not attributed"))
                        self.fail(f"{key}: not the original {found[0]} units after the load, in {found[1]} ({owner[2]}: {owner[3]}); still different {UNITS_AFTER} units on: {found[2]}")

                def pin(self, rom=rom, extra=extra, n=n, mode=mode, key=key):
                    found = self.first_divergence(rom, extra, n, mode)
                    unit, subsystems, owner, cause = divergences[key]
                    self.assertEqual((unit, list(subsystems)), found and found[:2], f"{key}: the divergence moved ({owner}: {cause})")

                if key in divergences:
                    setattr(cls, f"test_det02_diverges_{slug}", gate(unittest.expectedFailure(det02)))
                    setattr(cls, f"test_det02_first_divergence_of_{slug}", gate(pin))
                else:
                    setattr(cls, f"test_det02_{slug}", gate(det02))


class Det02Tests(Det02Case):
    ROMS = {
        "vsync": ("vsync", None, None), "stop": ("stop", None, None), "halt": ("halt", None, None), "trace": (TRACE, None, None), "user": (USER, None, None),
        "long_ops": ("long_ops", {"PAD": "3"}, None), "copper_irq": ("copper_irq", {"HP": "2"}, None), "input_all": ("input_all", None, None),
        "lof_stuck": ("lof_stuck", None, None), "clock": (rtc_tests.CLOCK, None, None), "cias": (cia_tests.CIAS, {"PRA": "2", "KBD": "$41"}, None),
        "potgo": (input_tests.POTGO, {"NEXT": "again"}, None), "busy": (upstream.BUSY, None, None),
        "busy-blt": (upstream.BUSY, None, {"#$83FF,$DFF096.L": "#$8240,$DFF096.L"}), "busy-bpl": (upstream.BUSY, None, {"#$83FF,$DFF096.L": "#$8300,$DFF096.L"}),
        "busy-spr": (upstream.BUSY, None, {"#$83FF,$DFF096.L": "#$8220,$DFF096.L"}), "busy-cop": (upstream.BUSY, None, {"#$83FF,$DFF096.L": "#$8280,$DFF096.L"}),
        "busy-aud": (upstream.BUSY, None, {"#$83FF,$DFF096.L": "#$820F,$DFF096.L"}), "busy-dsk": (upstream.BUSY, None, {"#$83FF,$DFF096.L": "#$8210,$DFF096.L"}),
        "blt-bpl": (upstream.BUSY, None, {"#$83FF,$DFF096.L": "#$8340,$DFF096.L"}), "blt-spr": (upstream.BUSY, None, {"#$83FF,$DFF096.L": "#$8260,$DFF096.L"}),
        "blt-cop": (upstream.BUSY, None, {"#$83FF,$DFF096.L": "#$82C0,$DFF096.L"}), "blt-aud": (upstream.BUSY, None, {"#$83FF,$DFF096.L": "#$824F,$DFF096.L"}),
        "blt-dsk": (upstream.BUSY, None, {"#$83FF,$DFF096.L": "#$8250,$DFF096.L"}),
        "late-bpl": (LATE_BITPLANES, None, None),
        "tone": (tone_source(Tone("det02", channels=(0, 1), period=200, volume=64)), None, None),
        "tone-mod": (TONE_MODULATED, None, None),
    }


populate(Det02Tests, SCENARIOS, DIVERGENCES)


if __name__ == "__main__":
    unittest.main()
