"""T29c: the state of a session with a hard file (STATE01, DET03 and the refusals around the base image).

What the state carries (bridge/state_hdf.cpp, the section of P6DK that exists only while a hard file is mounted): the UAE controller and, of each hard file, the 4 KiB chunks the guest wrote
and nothing of the base. DET02 with the controller busy and with the owner's Kickstarts is tests/state/test_det02_hdf.py; here:

  * STATE01: p6_state_bound is one number for the whole life of the image, it follows the SIZE of the hard file (the overlay at its largest is the whole image) and every state fits in it,
    also the state of a machine whose overlay is entirely dirty; a state does not carry the base (a 4 MiB image with a few dirty chunks gives a state of the size of the machine).
  * The base is named by its content id and checked before the machine is touched: a state of another base image, of a session without the hard file, of a session whose manifest only
    STARTS with the state's (the media events of a floppy do not make a prefix of a hard file) and a body whose content id was changed under right checksums are refused and the
    machine goes on as if it had not been asked.
  * A body outside what the machine can have (chunks out of order or beyond the image, an EXTER message nobody writes, a board where none can be) is CORRUPT and untouched.
  * DET03: through the public ABI, across processes, with the controller busy; and a state saved again after a load is the state of a machine that never loaded.
"""
import hashlib
import unittest

from tests.hdf import hdf_image, hdf_rom
from tests.state import test_det02 as d
from tests.state import test_det03 as det03
from tests.state.harness import FieldTable, ProbeCase, parse_chunks, reforge, with_bytes

CORRUPT, INCOMPATIBLE = 9, 10
DISK = 8                       # the index of P6DK in the payload
CHUNK = 4096


def pattern(blocks, tag):
    """A hard file of `blocks` blocks that is not a partition table: the micro-ROM reads and writes it by offset."""
    return b"".join(hashlib.sha256(b"%s %d" % (tag, n)).digest() * 16 for n in range(blocks))


class HdfCase(ProbeCase):
    ROMS = {"hdfloop": (hdf_rom.loop_program(), None, None), "vsync": ("vsync", None, None)}

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.small = cls.dir / "small.hdf"                    # 64 KiB: 16 chunks, all of them dirty after 15 units of the loop
        cls.small.write_bytes(pattern(128, b"small"))
        cls.medium = cls.dir / "medium.hdf"                  # 512 KiB
        cls.medium.write_bytes(hdf_image.rdb_image(cylinders=16))
        cls.large = cls.dir / "large.hdf"                    # 4 MiB
        cls.large.write_bytes(hdf_image.rdb_image(cylinders=128))
        cls.other = cls.dir / "other.hdf"                    # the same size and one byte of difference from `medium`
        data = bytearray(cls.medium.read_bytes())
        data[100] ^= 1
        cls.other.write_bytes(bytes(data))
        cls.adf = cls.dir / "a.adf"
        cls.adf.write_bytes(hashlib.sha256(b"disk a").digest() * (901120 // 32))

    def fields(self, name, unit, *extra, rom="hdfloop"):
        path = self.dir / f"{name}.state"
        run = self.probe(rom, "--units", str(unit), "--save-fields-at", str(unit), str(path), *extra)
        self.assertEqual([], run.save_errors)
        return path.read_bytes(), FieldTable(path.read_bytes(), DISK, run.fields)


class BoundTests(HdfCase):
    def test_the_bound_is_one_number_that_follows_the_size_of_the_hard_file_and_every_state_fits(self):
        bounds = {}
        for name, path in (("small", self.small), ("medium", self.medium), ("large", self.large)):
            states = [(unit, self.dir / f"bound-{name}-{unit}.state") for unit in (1, 4, 12, 20)]
            arguments = ["--public-at", "0"]
            for unit, state in states:
                arguments += ["--public-at", str(unit), "--save-at", str(unit), str(state)]
            run = self.probe("hdfloop", "--units", "20", "--hdf", str(path), *arguments)
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
        # The loop writes two blocks across a chunk boundary every unit, one chunk further on: after 15 units every chunk of the 64 KiB image has been written.
        blob, table = self.fields("full", 20, "--hdf", str(self.small))
        self.assertEqual(16, len([label for label in table.order if label.startswith("hdf.chunk_index")]), "every chunk of the image is in the state")
        run = self.probe("hdfloop", "--units", "20", "--hdf", str(self.small), "--save-at", "20", str(self.dir / "full2.state"), "--public-at", "20")
        (_, written, _, bound), = run.saves
        self.assertEqual(len(blob), written)
        self.assertLessEqual(written, bound)
        self.assertEqual(run.public_size[0][1], bound)

    def test_the_state_does_not_carry_the_base(self):
        run = self.probe("hdfloop", "--units", "3", "--hdf", str(self.large), "--save-at", "3", str(self.dir / "large-3.state"))
        (_, written, _, bound), = run.saves
        self.assertLess(written, 2 << 20, "a 4 MiB hard file with three dirty chunks gives a state of the size of the machine")
        self.assertGreater(bound, 4 << 20)

    def test_the_state_has_the_dirty_chunks_in_ascending_order_and_the_content_id_of_the_base(self):
        _, table = self.fields("order", 6, "--hdf", str(self.medium))
        indexes = [table.get(label) for label in table.order if label.startswith("hdf.chunk_index")]
        self.assertEqual(sorted(set(indexes)), indexes)
        self.assertEqual(list(range(len(indexes))), indexes, "the loop dirties one more chunk every unit, from the first")
        self.assertEqual(hashlib.sha256(self.medium.read_bytes()).digest(), bytes(table.blob[table.where["hdf.content_id"][0]:table.where["hdf.content_id"][0] + 32]))


class BaseIdentityTests(HdfCase):
    def test_a_state_of_another_base_or_of_another_set_of_hard_files_is_incompatible_and_the_machine_is_untouched(self):
        medium, _ = self.fields("medium", 5, "--hdf", str(self.medium))
        plain, _ = self.fields("plain", 5)
        with_adf, _ = self.fields("adf", 5, "--adf", str(self.adf))
        two = self.dir / "two.hdf"
        two.write_bytes(self.small.read_bytes())
        both, _ = self.fields("both", 5, "--hdf", str(self.medium), "--hdf", str(two))
        # The same ROM in every case: the only difference is the set of hard files.
        run = self.refusals([("another base of the same size", medium, INCOMPATIBLE, "media_manifest_hash"),
                             ("no hard file", plain, INCOMPATIBLE, "media_manifest_hash"),
                             ("a floppy only", with_adf, INCOMPATIBLE, "media_manifest_hash"),
                             ("two hard files", both, INCOMPATIBLE, "media_manifest_hash")], rom="hdfloop", extra=("--hdf", str(self.other)), unit=6, units=9)
        self.assertEqual([(6, INCOMPATIBLE)] * 4, [(unit, code) for unit, code, _ in run.loads])
        # The other way: the state of a hard file in a machine without one.
        run = self.refusals([("a hard file", medium, INCOMPATIBLE, "media_manifest_hash")], rom="hdfloop", extra=(), unit=6, units=9)
        self.assertEqual([(6, INCOMPATIBLE)], [(unit, code) for unit, code, _ in run.loads])

    def test_the_manifest_that_starts_with_the_one_of_the_state_is_not_enough_when_the_rest_is_a_hard_file(self):
        # The manifest only grows at its end (T17e), so a state of [floppy] is a state of [floppy, floppy]. A hard file is not appended, it is there from p6_load: the state of a machine
        # that has only the floppy is not the state of one that has the floppy and the hard file.
        floppy, _ = self.fields("floppy", 5, "--adf", str(self.adf))
        run = self.refusals([("the floppy alone", floppy, INCOMPATIBLE, "media_manifest_hash")], rom="hdfloop", extra=("--adf", str(self.adf), "--hdf", str(self.medium)), unit=6, units=9)
        self.assertEqual([(6, INCOMPATIBLE)], [(unit, code) for unit, code, _ in run.loads])

    def test_a_content_id_that_is_not_the_one_of_the_base_is_incompatible_even_with_right_checksums(self):
        blob, table = self.fields("good", 5, "--hdf", str(self.medium))
        offset, size = table.where["hdf.content_id"]
        forged = bytearray(blob[offset:offset + size])
        forged[0] ^= 0x80
        bad = with_bytes(blob, DISK, offset, bytes(forged))
        count = table.with_values({"hdf.files": 2})
        run = self.refusals([("a content id that is not the base's", bad, INCOMPATIBLE, "content id"),
                             ("two hard files in the body of a session with one", count, CORRUPT, "range")], rom="hdfloop", extra=("--hdf", str(self.medium)), unit=6, units=9)
        self.assertEqual([(6, INCOMPATIBLE), (6, CORRUPT)], [(unit, code) for unit, code, _ in run.loads])


class BodyTests(HdfCase):
    def test_a_body_outside_what_the_machine_can_have_is_corrupt_and_untouched(self):
        blob, table = self.fields("body", 6, "--hdf", str(self.medium))
        first = [label for label in table.order if label.startswith("hdf.queue_value")][0]
        self.assertTrue(first, "the loop leaves replies in the queue")
        chunk_labels = [label for label in table.order if label.startswith("hdf.chunk_index")]
        request_labels = [label for label in table.order if label.startswith("hdf.device_request")]
        attempts = []
        for label, values, text in (
                ("a chunk out of order", {chunk_labels[1]: table.get(chunk_labels[0])}, "ascending"),
                ("a chunk beyond the image", {chunk_labels[-1]: 5000}, "range"),
                ("a queue message nobody writes", {first: 9}, "EXTER queue"),
                ("a queue longer than the ring", {"hdf.queue_count": 300}, "bound"),
                ("an interrupt in progress without the semaphore", {"hdf.filesys_global[5]": 0, "hdf.filesys_global[3]": 1}, "globals"),
                ("a walk beyond the units", {"hdf.filesys_global[4]": 51}, "globals"),
                ("a board that is mapped where none can be", {"hdf.board_word[0]": 0x100000}, "board"),
                ("a board mapped without memory", {"hdf.board_word[1]": 0}, "board"),
                ("cursors of the trap area beyond it", {"hdf.rtarea_word[1]": 0x10000}, "cursors"),
                ("a request line of 2", {"hdf.rtarea_word[4]": 2}, "cursors"),
                ("a file system store longer than the cap", {"hdf.fs_store_size": 262145}, "range")):
            if label.startswith("a chunk") and len(chunk_labels) < 2:
                continue
            attempts.append((label, table.with_values(values), CORRUPT, text))
        self.assertGreaterEqual(len(request_labels), 0)
        run = self.refusals(attempts, rom="hdfloop", extra=("--hdf", str(self.medium)), unit=6, units=9)
        self.assertEqual(len(attempts), len(run.loads))

    def test_a_state_whose_section_is_cut_short_or_has_bytes_after_it_is_corrupt(self):
        blob, table = self.fields("cut", 6, "--hdf", str(self.medium))
        chunks = parse_chunks(blob)
        def shorter(chunks_):
            chunks_[DISK][2] = chunks_[DISK][2][:-3]

        def longer(chunks_):
            chunks_[DISK][2] = chunks_[DISK][2] + b"\0\0\0\0"
        self.assertGreater(len(chunks[DISK][2]), 100000)
        run = self.refusals([("one dirty chunk short", reforge(blob, shorter), CORRUPT, "chunk disk"), ("bytes after the last chunk", reforge(blob, longer), CORRUPT, "chunk disk")],
                            rom="hdfloop", extra=("--hdf", str(self.medium)), unit=6, units=9)
        self.assertEqual(2, len(run.loads))


class Det03Hdf(det03.Det03Case):
    """DET03 through the public ABI with the controller busy: the state of one process continues in a fresh one as the original did."""

    ROMS = {"hdfloop": (hdf_rom.loop_program(), None, None)}

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.hdf = cls.dir / "d.hdf"
        cls.hdf.write_bytes(hdf_image.rdb_image(cylinders=64))

    def arguments(self, extra):
        return ["--hdf", str(self.hdf)] if extra == "hdf" else super().arguments(extra)


d.populate(Det03Hdf, {"hdf-loop": ("hdfloop", "hdf", (2, 3, 6, 9), (d.NEW,))}, {})


class ResaveHdfTests(HdfCase):
    def test_a_state_saved_after_a_load_is_the_state_of_a_machine_that_never_loaded(self):
        # In place (rewound or not) and in a new process, the state saved again is the original's byte for byte: the controller, the trap area and the overlay come back exactly.
        hdf = ("--hdf", str(self.medium))
        for n, k in ((3, 7), (6, 10)):
            with self.subTest(saved=n, again=k):
                plain, loaded, rewound, saved = (self.dir / f"r{n}.plain", self.dir / f"r{n}.loaded", self.dir / f"r{n}.rewound", self.dir / f"r{n}.state")
                self.probe("hdfloop", "--units", str(k), *hdf, "--public-save-at", str(k), str(plain))
                self.probe("hdfloop", "--units", str(k), *hdf, "--public-save-at", str(n), str(saved), "--public-load-at", str(n), str(saved), "--public-save-at", str(k), str(loaded))
                self.assertEqual(plain.read_bytes(), loaded.read_bytes())
                self.probe("hdfloop", "--units", str(k + 2), *hdf, "--public-save-at", str(n), str(saved), "--public-load-at", str(n + 2), str(saved), "--public-save-at", str(k + 2), str(rewound))
                self.assertEqual(plain.read_bytes(), rewound.read_bytes())
        new, fresh = self.dir / "fresh.state", self.dir / "fresh.again"
        self.probe("hdfloop", "--units", "6", *hdf, "--public-save-at", "6", str(new))
        self.probe("hdfloop", "--units", "0", *hdf, "--public-load-at", "0", str(new), "--public-save-at", "0", str(fresh))
        self.assertEqual(new.read_bytes(), fresh.read_bytes())

    def test_the_section_is_the_tail_of_the_disk_chunk_and_a_session_without_a_hard_file_has_none(self):
        # The section exists only while a hard file is mounted, after the bodies of the disks: the chunk of the same program without a hard file is the head of the one with it.
        none, _ = self.fields("none", 4)
        with_hdf, _ = self.fields("with", 4, "--hdf", str(self.medium))
        bare, mounted = parse_chunks(none)[DISK][2], parse_chunks(with_hdf)[DISK][2]
        self.assertTrue(mounted.startswith(bare))
        self.assertGreater(len(mounted), len(bare) + 130000)


if __name__ == "__main__":
    unittest.main()
