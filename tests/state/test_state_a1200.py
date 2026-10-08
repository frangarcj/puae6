"""T24c: the native state of the A1200 (68EC020 cycle exact, AGA, 2 MiB Chip, no clock chip) and the line between the three machines.

What is decided here (docs/adr/0009-state-format.md, validation = hash + identity + structure):
  * the A1200 state has the same eleven chunks, in the same order and with the same versions as the A500's. Two bodies follow the machine, as P6RT's does on the A600: P6CP carries the block
    of the 68020 after the body of the 68000 (the 68000 body is untouched; 662 bytes: the control registers, the prefetch queue with its pipeline, the 64 lines of the instruction cache and
    what the cycle-exact core keeps between two instructions) and P6CH carries CAGA, the 256 colours of 24 bits, right after CHIP. The A500 and A600 states have neither.
  * P6VD's PIXL travels as three bytes per pixel on an AGA machine (the 12-bit code of the others cannot name a colour of 24 bits) and its bound grows with it. The bound of each machine is the
    sum of what its chunks can hold, and the difference between the machines is accounted for here to the byte.
  * a state of one machine is INCOMPATIBLE in another by its configuration hash, before anything is applied; with the hash forged so that the identity matches, the chunks refuse it by their
    lengths.
  * every field of the 68020 block is validated against its range before anything is applied, and every field of it decides the future: a poke that moves it in the live machine changes what the
    machine does (DET02 in tests/state/test_det02_a1200.py shows the rest: that the state puts each one back).
"""
import hashlib
import re
import struct
import unittest
from pathlib import Path

from tests.state import envelope as e
from tests.state import test_det02_a1200 as a1200
from tests.state import test_state_a600 as a600
from tests.state import test_state_upstream as upstream
from tests.state.harness import FieldTable, ProbeCase, SUBSYSTEMS, parse_chunks, reforge

ROOT = Path(__file__).resolve().parents[2]
CPU, RAM, RTC, CHIPSET, VIDEO = 0, 2, 3, 4, 5
INCOMPATIBLE, CORRUPT = 10, 9
CPU_020_BYTES = 662                 # the block of the 68020 in P6CP, see bridge/state_cpu.cpp
CAGA_BYTES = 1024
RING_BYTES = 5 * 4 + 4096 * 22         # DRNG: the five cursors of the Denise ring and its 4096 slots of 22 bytes
VIDEO_MAX = {name: int(value) for name, value in re.findall(r"P6_VIDEO_MAX_(WIDTH|HEIGHT) = (\d+)", (ROOT / "ports/portable/video_present.h").read_text())}
RASTER_GROUP = 128
ROMS = {"vsync": ("vsync", None, None), "cache020": a1200.ROMS["cache020"], "master": a1200.ROMS["master"], "aga-8planes": a1200.ROMS["aga-8planes"],
        "aga-fmode3": a1200.ROMS["aga-fmode3"], "audio-dma-wait": a1200.ROMS["audio-dma-wait"], "aga-fmode-toggle": a1200.ROMS["aga-fmode-toggle"], "tone": a1200.ROMS["tone"]}


def raster_bound(bytes_per_pixel):
    """kRasterBound of bridge/state_upstream.cpp: the largest picture the presentation can hold, in the groups of the codec."""
    return VIDEO_MAX["HEIGHT"] * (VIDEO_MAX["WIDTH"] * bytes_per_pixel + (VIDEO_MAX["WIDTH"] + RASTER_GROUP - 1) // RASTER_GROUP)


def raster_of(blob, width, height):
    """The rows of the PIXL body of an AGA state (three bytes per pixel, R G B), decoded from the format of bridge/state_upstream.cpp, as width x height XRGB pixels."""
    body = upstream.upstream_bodies(blob)[b"P6VD"]["PIXL"]
    out, at = bytearray(), 0
    for _ in range(height):
        x = 0
        while x < width:
            token = body[at]
            at += 1
            count = (token & 0x7f) + 1
            if token & 0x80:
                pixel = body[at] << 16 | body[at + 1] << 8 | body[at + 2]
                at += 3
                out += struct.pack("<I", pixel) * count
            else:
                for _ in range(count):
                    out += struct.pack("<I", body[at] << 16 | body[at + 1] << 8 | body[at + 2])
                    at += 3
            x += count
        assert x == width
    assert at == len(body), "the rows use all the bytes of the body"
    return bytes(out)


class A1200LayoutTests(ProbeCase):
    MACHINE = "a1200"
    ROMS = ROMS

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        states = {}
        for machine in ("a500", "a600", "a1200"):
            path = cls.dir / f"{machine}.state"
            cls.probe_on(machine, "vsync", "--units", "3", "--save-at", "3", str(path))
            states[machine] = path.read_bytes()
        cls.own, cls.a500, cls.a600 = states["a1200"], states["a500"], states["a600"]

    def test_the_a1200_state_has_the_chunks_of_the_a500_and_two_bodies_that_follow_the_machine(self):
        mine, theirs = parse_chunks(self.own), parse_chunks(self.a500)
        self.assertEqual([(tag, version) for tag, version, _ in theirs], [(tag, version) for tag, version, _ in mine])
        self.assertEqual(11, len(mine))
        self.assertEqual(len(theirs[CPU][2]) + CPU_020_BYTES, len(mine[CPU][2]), "P6CP is the body of the 68000 and the block of the 68020")
        self.assertEqual((b"P6RT", 1, b""), tuple(mine[RTC]), "the A1200 has no clock chip: the chunk is there and empty")
        sizes = {tag: len(body) for tag, _, body in mine}
        for index, (tag, _, body) in enumerate(mine):
            if index not in (CPU, RAM, RTC, CHIPSET, VIDEO):
                self.assertEqual(len(theirs[index][2]), len(body), tag)
        chip, slow = (int.from_bytes(mine[RAM][2][at:at + 4], "little") for at in (0, 4))
        self.assertEqual((2 << 20, 0), (chip, slow))
        self.assertEqual(e.HEADER_SIZE + sum(16 + len(body) for _, _, body in mine), len(self.own))
        self.assertEqual(len(parse_chunks(self.a600)[CPU][2]), len(theirs[CPU][2]), "the A600 body of the CPU is the A500's: the 68000 layout did not change")
        self.assertGreater(sizes[b"P6VD"], 0)

    def test_the_68000_body_is_a_prefix_of_the_68020_one_up_to_the_status_register_and_its_fields(self):
        # The fields in the head of the body are the same fields in the same order on every machine; only the block of the 68020 follows. The A500 state of the same program differs in
        # values (its own registers), so what is compared is the layout: the field names and offsets the probe prints up to the end of the 68000 body.
        names = {}
        for machine in ("a500", "a1200"):
            path = self.dir / f"fields-{machine}.state"
            run = self.probe_on(machine, "vsync", "--units", "3", "--save-fields-at", "3", str(path))
            names[machine] = [(name, size) for chunk, name, offset, size in run.fields if chunk == CPU]
        a500, a1200_fields = names["a500"], names["a1200"]
        self.assertEqual(a500, a1200_fields[:len(a500)])
        self.assertEqual(CPU_020_BYTES, sum(size for _, size in a1200_fields[len(a500):]))
        self.assertEqual(["cpu.vbr", "cpu.msp", "cpu.caar", "cpu.sfc", "cpu.dfc", "cpu.cacr"], [name for name, _ in a1200_fields[len(a500):len(a500) + 6]])

    def test_the_chipset_chunk_has_caga_right_after_chip_on_the_a1200_and_on_no_other_machine(self):
        for machine, blob in (("a500", self.a500), ("a600", self.a600), ("a1200", self.own)):
            tags = [name for name, _ in upstream.sub_bodies(parse_chunks(blob)[CHIPSET][2])]
            if machine == "a1200":
                self.assertEqual(["CHIP", "CAGA", "BLTX"], tags[:3])
            else:
                self.assertNotIn("CAGA", tags, machine)
                self.assertEqual(["CHIP", "BLTX"], tags[:2])
        bodies = upstream.upstream_bodies(self.own)[b"P6CH"]
        self.assertEqual(CAGA_BYTES, len(bodies["CAGA"]))
        for name, size in upstream.FIXED.items():
            if name in bodies:
                self.assertEqual(size, len(bodies[name]), f"{name}: the lengths of the other bodies are the same on the A1200")

    def test_the_video_chunk_has_the_ring_of_denise_between_the_pipeline_and_the_raster_on_the_a1200_only(self):
        for machine, blob in (("a500", self.a500), ("a600", self.a600), ("a1200", self.own)):
            tags = [name for name, _ in upstream.sub_bodies(parse_chunks(blob)[VIDEO][2])]
            self.assertEqual(["BPLX", "DNSP", "DRNG", "GEOM", "PIXL"] if machine == "a1200" else ["BPLX", "DNSP", "GEOM", "PIXL"], tags, machine)
        self.assertEqual(RING_BYTES, len(upstream.upstream_bodies(self.own)[b"P6VD"]["DRNG"]))

    def test_the_palette_body_holds_the_24_bit_colours_the_program_wrote(self):
        blob = self.state_of("aga.state", "aga-8planes", 4)
        caga = upstream.upstream_bodies(blob)[b"P6CH"]["CAGA"]
        colours = struct.unpack(">256I", caga)
        for bank in range(8):
            for index in range(32):
                if (bank, index) == (0, 1):
                    continue            # COLOR01 is rewritten by the vertical-blank handler, high nibbles only
                r, g, b = a1200.colour(bank, index)
                self.assertEqual(r << 16 | g << 8 | b, colours[bank * 32 + index], f"colour {bank * 32 + index}")
        self.assertGreater(sum(1 for r, g, b in (a1200.colour(0, i) for i in range(32)) if r % 17 or g % 17 or b % 17), 24, "the colours are not ECS ones")

    def test_the_register_image_carries_fmode_and_the_picture_is_three_bytes_a_pixel(self):
        blob = self.state_of("fmode3.state", "aga-fmode3", 4)
        chip = upstream.upstream_bodies(blob)[b"P6CH"]["CHIP"]
        self.assertEqual(3, struct.unpack_from(">H", chip, 352)[0], "FMODE")
        geom = struct.unpack(">%di" % len(upstream.GEOM_WORDS), upstream.upstream_bodies(blob)[b"P6VD"]["GEOM"])
        width, height = geom[upstream.GEOM_WORDS.index("alloc_width")], geom[upstream.GEOM_WORDS.index("alloc_height")]
        pixels = raster_of(blob, width, height)
        run = self.probe("aga-fmode3", "--units", "4", "--save-at", "4", str(self.dir / "p.state"))
        picture = run.pictures[4].split()
        self.assertEqual(f"{width}x{height}", picture[0])
        self.assertEqual(picture[-1], hashlib.sha256(pixels).hexdigest(), "the rows of the state are the picture the unit handed over")
        colours = set(struct.unpack("<%dI" % (len(pixels) // 4), pixels))
        self.assertGreater(len(colours), 32)
        self.assertTrue(any((c >> 16 & 255) % 17 or (c >> 8 & 255) % 17 or (c & 255) % 17 for c in colours), "a colour that the 12-bit code could not name")
        piece = upstream.upstream_bodies(blob)[b"P6VD"]["PIXL"]
        self.assertLessEqual(len(piece), raster_bound(3))
        self.assertGreater(len(piece), 100000, "a picture of colours of 24 bits travels in three bytes a pixel in groups: far more than the few bytes of a flat one")

    def test_the_bound_of_each_machine_is_the_sum_of_what_its_chunks_can_hold(self):
        bounds = {}
        for machine in ("a500", "a600", "a1200"):
            run = self.probe_on(machine, "vsync", "--units", "3", "--public-at", "3")
            (_, bound, written, _), = run.public_size
            self.assertLessEqual(written, bound, machine)
            bounds[machine] = bound
        self.assertEqual(31, bounds["a500"] - bounds["a600"])
        # The A1200 against the A600 (neither has the clock): one more MiB of Chip RAM, the palette (a tag, a length and 1024 bytes), the ring of Denise (the same), the block of the
        # 68020, and the picture at three bytes a pixel instead of two.
        expected = bounds["a600"] + (1 << 20) + 8 + CAGA_BYTES + 8 + RING_BYTES + CPU_020_BYTES + raster_bound(3) - raster_bound(2)
        self.assertEqual(expected, bounds["a1200"])
        self.assertEqual(VIDEO_MAX["WIDTH"] * VIDEO_MAX["HEIGHT"], raster_bound(3) - raster_bound(2))


class ForeignMachineCase(ProbeCase):
    """A machine and the state of another one. `OTHER` names the other; the states are taken at unit 3 of the vsync program."""

    ROMS = {"vsync": ("vsync", None, None)}
    OTHER = ""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.own = cls.state_at(cls.MACHINE, "own")
        cls.foreign = cls.state_at(cls.OTHER, "foreign")

    @classmethod
    def state_at(cls, machine, name):
        path = cls.dir / f"{name}.state"
        cls.probe_on(machine, "vsync", "--units", "3", "--save-at", "3", str(path))
        return path.read_bytes()

    def test_a_state_of_the_other_machine_is_incompatible_and_the_machine_is_left_alone(self):
        run = self.refusals([("the state of the other machine", self.foreign, INCOMPATIBLE, "config_hash: differs from the loaded machine")])
        self.assertEqual([(6, INCOMPATIBLE)], [(unit, code) for unit, code, _ in run.loads])

    def test_the_identity_does_not_let_the_state_of_the_other_one_through(self):
        forged = reforge(self.foreign, header={a600.CONFIG_HASH.start: self.own[a600.CONFIG_HASH]})
        self.refusals([("the other machine's state with this machine's hash", forged, CORRUPT, "chunk")])


class A1200AgainstA500StateTests(ForeignMachineCase):
    MACHINE, OTHER = "a1200", "a500"


class A1200AgainstA600StateTests(ForeignMachineCase):
    MACHINE, OTHER = "a1200", "a600"


class A500AgainstA1200StateTests(ForeignMachineCase):
    MACHINE, OTHER = "a500", "a1200"


class A600AgainstA1200StateTests(ForeignMachineCase):
    MACHINE, OTHER = "a600", "a1200"


del ForeignMachineCase              # only the machines' classes run


class A1200RewoundStateTests(a600.RewoundStateCase):
    """The state of a machine that went back to an earlier state and ran a unit, against the state of the one that never went back, on the A1200: the whole bytes, which no DET02 digest sees."""

    MACHINE = "a1200"

    def test_a_unit_after_a_rewind_saves_the_state_of_the_machine_that_never_went_back(self):
        self.assertEqual([], self.differences())


class A1200RewoundCpuStateTests(ProbeCase):
    """The same with programs that fill the instruction cache, switch stacks and write the palette: the 020 block and the palette come back as they were."""

    MACHINE = "a1200"
    ROMS = ROMS

    def test_a_unit_after_a_rewind_saves_the_state_of_the_machine_that_never_went_back(self):
        for rom in ("cache020", "master", "aga-8planes"):
            with self.subTest(rom=rom):
                self.probe(rom, "--units", "6", "--save-at", "3", str(self.dir / "g.state"), "--save-at", "4", str(self.dir / "natural.state"),
                           "--load-at", "5", str(self.dir / "g.state"), "--save-at", "6", str(self.dir / "rewound.state"))
                natural, rewound = (parse_chunks((self.dir / f"{name}.state").read_bytes()) for name in ("natural", "rewound"))
                differences = [(tag, [i for i in range(len(body)) if body[i] != rewound[k][2][i]][:4]) for k, (tag, _, body) in enumerate(natural) if body != rewound[k][2]]
                self.assertEqual([], differences)

    def test_a_state_loaded_into_a_new_process_is_saved_again_byte_for_byte(self):
        for rom in ("cache020", "master", "aga-8planes", "aga-fmode-toggle"):
            with self.subTest(rom=rom):
                source, again = self.dir / "own.state", self.dir / "again.state"
                self.probe(rom, "--units", "4", "--save-at", "4", str(source))
                run = self.probe(rom, "--units", "0", "--load-at", "0", str(source), "--save-at", "0", str(again))
                self.assertEqual([(0, 0, "")], run.loads)
                self.assertEqual(source.read_bytes(), again.read_bytes())


class A1200ValidationTests(ProbeCase):
    """Every field of the 68020 block, of CAGA and of FMODE is refused outside its range before anything is applied."""

    MACHINE = "a1200"
    ROMS = ROMS

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        path = cls.dir / "good.state"
        run = cls.probe("cache020", "--units", "4", "--save-fields-at", "4", str(path))
        cls.good = path.read_bytes()
        cls.cpu = FieldTable(cls.good, CPU, run.fields)

    def attempts(self, table, changes):
        return [(name, table.with_values({label: value}), CORRUPT, "chunk cpu" if table is self.cpu else "chunk chipset") for name, (label, value) in changes.items()]

    def test_a_field_of_the_68020_block_outside_its_range_is_refused_and_the_machine_is_left_alone(self):
        changes = {
            "CACR with a bit that is not there": ("cpu.cacr", 16), "SFC beyond 7": ("cpu.sfc", 8), "DFC beyond 7": ("cpu.dfc", 9),
            "a prefetch word flagged valid by a 2": ("cpu.prefetch020_valid[0]", 2), "pipeline position beyond 255": ("cpu.pipeline_pos", 256),
            "pipeline position below -1": ("cpu.pipeline_pos", -2), "pipeline stop beyond 1": ("cpu.pipeline_stop", 2), "pipeline r8 beyond 255": ("cpu.pipeline_r8[0]", 256),
            "instruction cache line valid by a 2": ("cpu.icache.valid[0]", 2), "instruction cache tag with a bit a tag never has": ("cpu.icache.tag[0]", 2),
            "the extra cycles beyond 16": ("cpu.ce020extracycles", 17), "the remainder of a colour clock beyond a colour clock": ("cpu.ce020_extra", 512),
            "the end of the last prefetch in the past of time": ("cpu.ce020prefetchendcycle", -1), "the start of the last cycle in the past of time": ("cpu.ce020startcycle", -1),
        }
        self.refusals(self.attempts(self.cpu, changes), rom="cache020", unit=6, units=9)

    def test_a_status_register_bit_a_68020_has_is_accepted_and_one_it_has_not_is_refused(self):
        # T0 and M are the 68020's (mask 0xF71F), bit 11 and bits 5-7 of the status register are no one's.
        sr = self.cpu.get("cpu.sr")
        for bit in (14, 12):
            blob = self.cpu.with_values({"cpu.sr": sr | 1 << bit})
            run = self.probe("cache020", "--units", "6", "--load-at", "6", str(self._write(f"sr-{bit}.state", blob)))
            self.assertEqual([(6, 0, "")], run.loads, f"SR bit {bit}")
        self.refusals([(f"SR bit {bit}", self.cpu.with_values({"cpu.sr": sr | 1 << bit}), CORRUPT, "cpu.sr") for bit in (11, 6)], rom="cache020", unit=6, units=9)

    def _write(self, name, blob):
        path = self.dir / name
        path.write_bytes(blob)
        return path

    def test_the_palette_and_fmode_are_validated(self):
        # The sub-bodies are not fields of the archive: they are found in the body of the chunk by their tags.
        body = parse_chunks(self.good)[CHIPSET][2]
        caga_at, chip_at = body.index(b"CAGA") + 8, body.index(b"CHIP") + 8
        wide = reforge(self.good, lambda chunks: chunks[CHIPSET].__setitem__(2, body[:caga_at] + b"\x7f" + body[caga_at + 1:]))
        bad_fmode = reforge(self.good, lambda chunks: chunks[CHIPSET].__setitem__(2, body[:chip_at + 352] + b"\x10\x00" + body[chip_at + 354:]))
        self.refusals([("a colour with bits between the 24 and the genlock bit", wide, CORRUPT, "CAGA"), ("an FMODE with a bit the register does not have", bad_fmode, CORRUPT, "CHIP")],
                      rom="cache020", unit=6, units=9)

    def test_a_p6cp_body_of_the_68000_in_the_68020_machine_and_the_other_way_round_are_refused_by_length(self):
        short = reforge(self.good, lambda chunks: chunks[CPU].__setitem__(2, bytes(chunks[CPU][2])[:-CPU_020_BYTES]))
        longer = reforge(self.good, lambda chunks: chunks[CPU].__setitem__(2, bytes(chunks[CPU][2]) + b"\0" * 4))
        self.refusals([("the body of a 68000", short, CORRUPT, "chunk cpu: the body ends before its last field"), ("four bytes more", longer, CORRUPT, "chunk_length")], rom="cache020", unit=6, units=9)


class A1200NecessityTests(ProbeCase):
    """Every field of the 68020 block decides the future: a poke that moves it in the live machine, and nothing else, changes what the machine does afterwards. Each row is the field,
    the program it matters in and the units at which the poke is made; what has to differ, in a later unit, is a digest or the samples handed over (the CPU's own digest too, except
    for the count of instructions, which stays apart for ever once moved)."""

    MACHINE = "a1200"
    ROMS = ROMS

    # field -> (program, units, argument of the poke): the argument is the size of the move where the default one is absorbed by the program (see bridge/state_probe.cpp)
    POKES = {
        "extracycles": ("master", (3, 4), ""), "extra": ("master", (3, 4), ":128"), "prefetchend": ("master", (3, 4), ":512"), "span": ("tone", (3, 4), ":512"),
        "instruction-cnt": ("audio-dma-wait", a1200.AUDIO_GAP_UNITS[:1], ""), "cacr": ("cache020", (3, 4), ""), "cache-line": ("cache020", (3, 4), ""), "vbr": ("cache020", (3,), ""),
    }

    def effect(self, field, rom, unit, argument):
        after = 4
        clean = self.probe(rom, "--units", str(unit + after), "--every", "1")
        poked = self.probe(rom, "--units", str(unit + after), "--every", "1", "--poke-at", str(unit), f"ce020-{field}{argument}")
        subsystems = [s for s in SUBSYSTEMS if not (field == "instruction-cnt" and s == "cpu")]
        moved = [(u, s) for u in range(unit + 1, unit + after + 1) for s in subsystems if clean.digests[(u, s)] != poked.digests[(u, s)]]
        moved += [(u, "pcm") for u in range(unit + 1, unit + after + 1) if clean.pcm.get(u) != poked.pcm.get(u)]
        return moved

    def test_each_field_changes_the_future_of_the_program_it_matters_in(self):
        for field, (rom, units, argument) in self.POKES.items():
            with self.subTest(field=field):
                effects = [self.effect(field, rom, unit, argument) for unit in units]
                self.assertTrue(any(effects), f"{field}: nothing moved in any of the units {units} of {rom}")

    def test_the_audio_gap_scenario_has_its_boundaries_inside_the_gap(self):
        # DET02 audio-dma-wait is only a test of the count of instructions if the state is taken between the DMA going off and on again.
        for unit in a1200.AUDIO_GAP_UNITS:
            path = self.dir / f"gap{unit}.state"
            self.probe("audio-dma-wait", "--units", str(unit), "--save-at", str(unit), str(path))
            audio = dict(upstream.sub_bodies(parse_chunks(path.read_bytes())[upstream.AUDIO][2]))
            self.assertEqual(1, struct.unpack_from(">i", audio["AUDP"], upstream.audp_at("dmaoff_active", 0))[0], f"unit {unit}")
        for unit in a1200.AUDIO_OFF_UNITS:
            path = self.dir / f"off{unit}.state"
            self.probe("audio-dma-wait", "--units", str(unit), "--save-at", str(unit), str(path))
            audio = dict(upstream.sub_bodies(parse_chunks(path.read_bytes())[upstream.AUDIO][2]))
            self.assertEqual(0x13, audio["AUD0"][0], f"unit {unit}: a sample playing with its DMA switched off (loadperm1 marks the state with 0x10)")


class A1200EventTests(ProbeCase):
    """setup_fmodes_delayed (id 12) is a secondary event an AGA machine can have pending at a boundary."""

    MACHINE = "a1200"
    ROMS = ROMS

    def slots(self, blob):
        body = parse_chunks(blob)[1][2]          # P6SC: 20 bytes of counters, 4 x 17 of the main events, 2 of the round, then 16 x 14 of the secondary events
        return [(body[90 + 14 * i], body[90 + 14 * i + 1]) for i in range(16)]

    def test_a_real_event_with_the_handler_of_the_delayed_fetch_mode_comes_back(self):
        path, again, fresh = self.dir / "e.state", self.dir / "e.again", self.dir / "e.fresh"
        run = self.probe("vsync", "--units", "5", "--every", "1", "--poke-at", "4", "ev2:12", "--digest-at", "4", "--save-at", "4", str(path))
        self.assertIn((1, 12), self.slots(path.read_bytes()), "the event is pending in the state with its own id")
        then = run.after[0]
        same = self.probe("vsync", "--units", "9", "--every", "1", "--load-at", "9", str(path), "--save-at", "9", str(again))
        self.assertEqual([(9, 0, "")], same.loads)
        self.assertEqual(then["scheduler"], same.after[0]["scheduler"])
        self.assertEqual(path.read_bytes(), again.read_bytes())
        new = self.probe("vsync", "--units", "0", "--load-at", "0", str(path), "--save-at", "0", str(fresh))
        self.assertEqual(then["scheduler"], new.after[0]["scheduler"])
        self.assertEqual(path.read_bytes(), fresh.read_bytes())

    def test_a_program_that_rewrites_fmode_all_the_time_has_the_event_pending_at_some_boundaries(self):
        pending = 0
        for unit in range(2, 10):
            self.probe("aga-fmode-toggle", "--units", str(unit), "--save-at", str(unit), str(self.dir / f"t{unit}.state"))
            pending += sum(1 for active, handler in self.slots((self.dir / f"t{unit}.state").read_bytes()) if active and handler == 12)
        self.assertGreater(pending, 0, "DET02 aga-fmode-toggle would never have the delayed event in a state")


if __name__ == "__main__":
    unittest.main()
