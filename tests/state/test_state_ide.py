"""T56: the state of a session with a hard file on the IDE port of Gayle (STATE01, DET03, the refusals around the base image and the controller).

What the state carries (bridge/state_hdf.cpp, visit_ide: the section of P6DK that exists only while a hard file is mounted): the registers of Gayle, four IDE slots (the master and the slave of
the channel, the pair behind the splitter) with the values of the transfer in hand and the bytes of their sector buffer, and, of each hard file, the 4 KiB chunks the guest wrote and nothing of the
base. DET02 with the controller busy is tests/state/test_det02_ide.py; here:

  * STATE01: p6_state_bound is one number for the whole life of the image, it follows the SIZE of the hard file and every state fits in it, also the state of a machine whose overlay is
    entirely dirty; a state does not carry the base.
  * The base and the controller are part of the identity: a state of another base, of a session without the hard file, of a session whose hard file is on the UAE controller, and a body whose
    content id was changed under right checksums are refused and the machine goes on as if it had not been asked. A config that asks for IDE and has no hard file is the default machine: it
    loads the states of the default machine.
  * A body outside what Gayle and a drive can have (a byte beyond 255, a transfer longer than the buffer, a transfer in a slot without a drive, a sector buffer of another length, the two drives
    of a channel on different select lines) is CORRUPT and untouched.
  * DET03: through the public ABI, across processes, with the controller busy; and a state saved again after a load is the state of a machine that never loaded.
"""
import hashlib
import unittest

from tests.hdf import hdf_image, ide_rom
from tests.state import test_det02 as d
from tests.state import test_det03 as det03
from tests.state.harness import FieldTable, ProbeCase, parse_chunks, reforge, with_bytes

CORRUPT, INCOMPATIBLE = 9, 10
DISK = 8                       # the index of P6DK in the payload
CHUNK = 4096
WORDS = 54                     # semantic words of one HDF or CD slot (bridge/ide_units.h)


class IdeCase(ProbeCase):
    MACHINE = "a600"
    ROMS = {"ideloop": (ide_rom.loop_program(), None, None), "vsync": ("vsync", None, None)}
    IDE = ("--hdf-controller", "ide")

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.small = cls.dir / "small.hdf"                    # 512 KiB: 128 chunks, all of them dirty after 128 passes of the loop
        cls.small.write_bytes(hdf_image.rdb_image(cylinders=16))
        cls.medium = cls.dir / "medium.hdf"                  # 1 MiB
        cls.medium.write_bytes(hdf_image.rdb_image(cylinders=32))
        cls.large = cls.dir / "large.hdf"                    # 4 MiB
        cls.large.write_bytes(hdf_image.rdb_image(cylinders=128))
        cls.other = cls.dir / "other.hdf"                    # the same size and one byte of difference from `medium`
        data = bytearray(cls.medium.read_bytes())
        data[100] ^= 1
        cls.other.write_bytes(bytes(data))

    def fields(self, name, unit, *extra, rom="ideloop"):
        path = self.dir / f"{name}.state"
        run = self.probe(rom, "--units", str(unit), "--save-fields-at", str(unit), str(path), *extra)
        self.assertEqual([], run.save_errors)
        return path.read_bytes(), FieldTable(path.read_bytes(), DISK, run.fields)

    def ide(self, image):
        return ("--hdf", str(image), *self.IDE)


class BoundTests(IdeCase):
    def test_the_bound_is_one_number_that_follows_the_size_of_the_hard_file_and_every_state_fits(self):
        bounds = {}
        for name, path in (("small", self.small), ("medium", self.medium), ("large", self.large)):
            states = [(unit, self.dir / f"bound-{name}-{unit}.state") for unit in (1, 4, 12, 20)]
            arguments = ["--public-at", "0"]
            for unit, state in states:
                arguments += ["--public-at", str(unit), "--save-at", str(unit), str(state)]
            run = self.probe("ideloop", "--units", "20", *self.ide(path), *arguments)
            numbers = {size[1] for size in run.public_size} | {save[3] for save in run.saves}
            self.assertEqual(1, len(numbers), f"{name}: the bound moved during the session: {sorted(numbers)}")
            bounds[name] = numbers.pop()
            for unit, size, _, bound in run.saves:
                self.assertLessEqual(size, bound, f"{name}: the state of unit {unit} does not fit")
        chunks = lambda path: -(-path.stat().st_size // CHUNK)
        for a, b in (("small", "medium"), ("medium", "large")):
            pa, pb = getattr(self, a), getattr(self, b)
            self.assertEqual(pb.stat().st_size - pa.stat().st_size + 4 * (chunks(pb) - chunks(pa)), bounds[b] - bounds[a], f"the overlay at its largest: {a} to {b}")

    def test_a_state_of_a_hard_file_that_is_all_dirty_fits_in_the_bound(self):
        # The loop writes two blocks across a chunk boundary every pass, one chunk further on: after 128 passes (about 40 units) every chunk of the 512 KiB image has been written.
        blob, table = self.fields("full", 50, *self.ide(self.small))
        self.assertEqual(128, len([label for label in table.order if label.startswith("hdf.chunk_index")]), "every chunk of the image is in the state")
        run = self.probe("ideloop", "--units", "50", *self.ide(self.small), "--save-at", "50", str(self.dir / "full2.state"), "--public-at", "50")
        (_, written, _, bound), = run.saves
        self.assertEqual(len(blob), written)
        self.assertLessEqual(written, bound)
        self.assertEqual(run.public_size[0][1], bound)

    def test_the_state_does_not_carry_the_base(self):
        run = self.probe("ideloop", "--units", "3", *self.ide(self.large), "--save-at", "3", str(self.dir / "large-3.state"))
        (_, written, _, bound), = run.saves
        self.assertLess(written, 2 << 20, "a 4 MiB hard file with a few dirty chunks gives a state of the size of the machine")
        self.assertGreater(bound, 4 << 20)

    def test_the_state_has_the_dirty_chunks_in_ascending_order_and_the_content_id_of_the_base(self):
        _, table = self.fields("order", 6, *self.ide(self.medium))
        indexes = [table.get(label) for label in table.order if label.startswith("hdf.chunk_index")]
        self.assertEqual(sorted(set(indexes)), indexes)
        self.assertEqual(list(range(1, len(indexes) + 1)), indexes, "the loop dirties one more chunk every pass, from chunk 1 (it starts at block 15)")
        self.assertEqual(hashlib.sha256(self.medium.read_bytes()).digest(), bytes(table.blob[table.where["hdf.content_id"][0]:table.where["hdf.content_id"][0] + 32]))


class IdentityTests(IdeCase):
    def test_a_state_of_another_base_another_controller_or_no_hard_file_is_incompatible_and_the_machine_is_untouched(self):
        medium, _ = self.fields("medium", 5, *self.ide(self.medium))
        uae, _ = self.fields("uae", 5, "--hdf", str(self.medium))
        plain, _ = self.fields("plain", 5)
        # The same ROM in every case: the only difference is the set of hard files and the controller they are on.
        run = self.refusals([("another base of the same size", medium, INCOMPATIBLE, "media_manifest_hash"),
                             ("the UAE controller", uae, INCOMPATIBLE, "config_hash"),
                             ("no hard file", plain, INCOMPATIBLE, "config_hash")], rom="ideloop", extra=self.ide(self.other), unit=6, units=9)
        self.assertEqual([(6, INCOMPATIBLE)] * 3, [(unit, code) for unit, code, _ in run.loads])
        # The other way: the state of the IDE in a machine on the UAE controller, and in one without a hard file.
        run = self.refusals([("the IDE", medium, INCOMPATIBLE, "config_hash")], rom="ideloop", extra=("--hdf", str(self.medium)), unit=6, units=9)
        self.assertEqual([(6, INCOMPATIBLE)], [(unit, code) for unit, code, _ in run.loads])
        run = self.refusals([("a hard file", medium, INCOMPATIBLE, "config_hash")], rom="ideloop", extra=(), unit=6, units=9)
        self.assertEqual([(6, INCOMPATIBLE)], [(unit, code) for unit, code, _ in run.loads])

    def test_a_content_id_that_is_not_the_one_of_the_base_is_incompatible_even_with_right_checksums(self):
        blob, table = self.fields("good", 5, *self.ide(self.medium))
        offset, size = table.where["hdf.content_id"]
        forged = bytearray(blob[offset:offset + size])
        forged[0] ^= 0x80
        bad = with_bytes(blob, DISK, offset, bytes(forged))
        run = self.refusals([("a content id that is not the base's", bad, INCOMPATIBLE, "content id")], rom="ideloop", extra=self.ide(self.medium), unit=6, units=9)
        self.assertEqual([(6, INCOMPATIBLE)], [(unit, code) for unit, code, _ in run.loads])

    def test_ide_without_a_hard_file_is_the_default_machine_and_loads_its_states(self):
        # A state of the default A600 (no Gayle) loads into a machine whose config asks for the IDE and has no hard file: the same machine, the same identity.
        state = self.dir / "default.state"
        self.probe("vsync", "--units", "5", "--save-at", "5", str(state))
        run = self.probe("vsync", "--units", "9", *self.IDE, "--load-at", "5", str(state))
        self.assertEqual([(5, 0)], [(unit, code) for unit, code, *_ in run.loads])


class BodyTests(IdeCase):
    def test_a_body_outside_what_the_chip_and_a_drive_can_have_is_corrupt_and_untouched(self):
        blob, table = self.fields("body", 6, *self.ide(self.medium))
        chunk_labels = [label for label in table.order if label.startswith("hdf.chunk_index")]
        attempts = []
        for label, values, text in (
                ("a splitter with no drive behind it", {"ide.gayle_word[3]": 1}, "Gayle"),
                ("an interrupt status beyond a byte", {"ide.gayle_word[0]": 0x100}, "Gayle"),
                ("a register beyond a byte", {"ide.drive_word[1]": 0x100}, "drive can have"),
                ("a transfer longer than the buffer", {"ide.drive_word[16]": 300000}, "drive can have"),
                ("an interrupt delay of 3", {"ide.drive_word[25]": 3}, "drive can have"),
                ("a direction of 3", {"ide.drive_word[18]": 3}, "drive can have"),
                ("a transfer in a slot without a drive", {f"ide.drive_word[{WORDS + 16}]": 512}, "drive can have"),
                ("a transfer that says another length of buffer", {"ide.drive_word[16]": table.get("ide.drive_word[16]") + 1000}, "transfer buffer"),
                ("the two drives of a channel on other select lines", {"ide.drive_word[30]": 1 - table.get("ide.drive_word[30]")}, "select line"),
                ("a chunk beyond the image", {chunk_labels[-1]: 5000}, "range")):
            attempts.append((label, table.with_values(values), CORRUPT, text))
        run = self.refusals(attempts, rom="ideloop", extra=self.ide(self.medium), unit=6, units=9)
        self.assertEqual(len(attempts), len(run.loads))

    def test_a_state_whose_section_is_cut_short_or_has_bytes_after_it_is_corrupt(self):
        blob, table = self.fields("cut", 6, *self.ide(self.medium))
        chunks = parse_chunks(blob)

        def shorter(chunks_):
            chunks_[DISK][2] = chunks_[DISK][2][:-3]

        def longer(chunks_):
            chunks_[DISK][2] = chunks_[DISK][2] + b"\0\0\0\0"
        self.assertGreater(len(chunks[DISK][2]), 20000)
        run = self.refusals([("one dirty chunk short", reforge(blob, shorter), CORRUPT, "chunk disk"), ("bytes after the last chunk", reforge(blob, longer), CORRUPT, "chunk disk")],
                            rom="ideloop", extra=self.ide(self.medium), unit=6, units=9)
        self.assertEqual(2, len(run.loads))


class Det03Ide(det03.Det03Case):
    """DET03 through the public ABI with the controller busy: the state of one process continues in a fresh one as the original did."""

    MACHINE = "a600"
    ROMS = {"ideloop": (ide_rom.loop_program(), None, None)}

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.hdf = cls.dir / "d.hdf"
        cls.hdf.write_bytes(hdf_image.rdb_image(cylinders=64))

    def arguments(self, extra):
        return ["--hdf", str(self.hdf), "--hdf-controller", "ide"] if extra == "ide" else super().arguments(extra)


d.populate(Det03Ide, {"ide-loop": ("ideloop", "ide", (2, 3, 6, 9), (d.NEW,))}, {})


class ResaveIdeTests(IdeCase):
    def test_a_state_saved_after_a_load_is_the_state_of_a_machine_that_never_loaded(self):
        # In place (rewound or not) and in a new process, the state saved again is the original's byte for byte: Gayle, the four slots with their buffers and the overlay come back exactly.
        hdf = self.ide(self.medium)
        for n, k in ((3, 7), (6, 10)):
            with self.subTest(saved=n, again=k):
                plain, loaded, rewound, saved = (self.dir / f"r{n}.plain", self.dir / f"r{n}.loaded", self.dir / f"r{n}.rewound", self.dir / f"r{n}.state")
                self.probe("ideloop", "--units", str(k), *hdf, "--public-save-at", str(k), str(plain))
                self.probe("ideloop", "--units", str(k), *hdf, "--public-save-at", str(n), str(saved), "--public-load-at", str(n), str(saved), "--public-save-at", str(k), str(loaded))
                self.assertEqual(plain.read_bytes(), loaded.read_bytes())
                self.probe("ideloop", "--units", str(k + 2), *hdf, "--public-save-at", str(n), str(saved), "--public-load-at", str(n + 2), str(saved), "--public-save-at", str(k + 2), str(rewound))
                self.assertEqual(plain.read_bytes(), rewound.read_bytes())
        new, fresh = self.dir / "fresh.state", self.dir / "fresh.again"
        self.probe("ideloop", "--units", "6", *hdf, "--public-save-at", "6", str(new))
        self.probe("ideloop", "--units", "0", *hdf, "--public-load-at", "0", str(new), "--public-save-at", "0", str(fresh))
        self.assertEqual(new.read_bytes(), fresh.read_bytes())

    def test_a_reset_opens_the_drives_again_and_the_machine_goes_on_deterministically(self):
        # gayle_reset powers the drives on again at every reset (the same hard files, the overlay of the guest kept by the block backend): the run with two resets is repeatable unit by unit and
        # it is not the run without them.
        hdf = self.ide(self.medium)
        first = self.probe("ideloop", "--units", "24", "--every", "1", *hdf, "--reset-at", "9", "--reset-at", "15")
        again = self.probe("ideloop", "--units", "24", "--every", "1", *hdf, "--reset-at", "9", "--reset-at", "15")
        plain = self.probe("ideloop", "--units", "24", "--every", "1", *hdf)
        for unit in range(1, 25):
            self.assertEqual(first.unit(unit), again.unit(unit), f"unit {unit}")
        self.assertNotEqual([first.unit(n) for n in range(1, 25)], [plain.unit(n) for n in range(1, 25)])

    def test_the_section_is_the_tail_of_the_disk_chunk_and_a_session_without_a_hard_file_has_none(self):
        none, _ = self.fields("none", 4)
        with_hdf, _ = self.fields("with", 4, *self.ide(self.medium))
        bare, mounted = parse_chunks(none)[DISK][2], parse_chunks(with_hdf)[DISK][2]
        self.assertTrue(mounted.startswith(bare))
        self.assertGreater(len(mounted), len(bare) + 4 * (WORDS * 4 + 4))

    def test_the_uae_controller_is_not_in_the_section_of_the_ide(self):
        # No UAEFS board, no trap area and no EXTER queue exist behind Gayle: the section has none of their fields.
        _, table = self.fields("labels", 4, *self.ide(self.medium))
        for label in table.order:
            self.assertFalse(label.startswith(("hdf.board", "hdf.rtarea", "hdf.queue", "hdf.device", "hdf.unit", "hdf.filesys", "hdf.expamem")), label)


if __name__ == "__main__":
    unittest.main()
