"""T63b: hard files in the PUAE6 core of BizHawk (Puae6Core), in the real Waterbox sandbox.

The core takes HDF images from the loader (Puae6Parameters.HardFiles; EmuHawk's constructor splits the loader's .adf and .hdf files), mounts them at power-on on the UAE controller
(uaehf.device, DH0...) and answers for them like for the floppies: they are the p6 media after the floppies (media index = floppies + n), the guest's writes are the 4 KiB overlay of the
sandbox, and the images the core was given never change. The guest is the micro-ROM of tests/hdf/hdf_rom.py (loop_program): at every unit it writes two blocks across a 4 KiB chunk
boundary (a new chunk each unit), reads them back and reads block 0.

MOUNT   the hard file the core exports after N units is, byte for byte, the one the native engine exports for the same ROM and image (p6_headless --hdf --export-hdf); the overlay counts
        the chunks the guest wrote; the core has ISaveRam without FloppyWrites, and with floppies the hard file is the medium after them.
SAVERAM the SaveRam holds only the chunks that differ from the image (version 2 of P6SR, sparse: a 32 MiB image gives about 170 KiB for 42 chunks), each with the bytes the guest wrote;
        SaveRamModified tells whether it differs from the last copy kept; a fresh core stores it before its first unit and has the image, the overlay empty and the SaveRam it would give
        again byte for byte; going on from there ends where a core that never reloaded ends (the union of the chunks).
REFUSED a SaveRam of another image, cut, damaged, from a core with other media, given to a core without anything writable or after the first unit is refused with its reason and changes
        nothing; padding is accepted.
WB03    save at N, go on M, load, go on M again: the same units and digest, the overlay (the exported hard file) goes back with the load, a new sandbox and another process continue the same
        (WB04, WB15); a state or a movie of another hard file, of none or of the same hard file but other heaps is refused before the sandbox is touched and the instance goes on as if never asked.
MED02   the files the core was given (the images, the ROM) are the same after everything the tests do (tearDown compares size, mtime and SHA-256); a movie plays back to the recorded units.
HEAP    the heaps grow by twice HardFileHeapKiB of each image (manifests/waterbox-memory.json, the same rule as the smoke harness) and are the base ones without hard files; a 32 MiB image runs.
LIMITS  more than four hard files, a size that is not a multiple of 512 and two copies of one image are refused when the core is created, with the reason.

Needs the Waterbox runtime and the guest (tools/build_waterbox.py --puae6-run build/waterbox/boot.rom), dotnet and the local NuGet cache; skips without them.
    python3 -m unittest discover -s tests/waterbox-media -p "test_media_hdf.py"
"""
import hashlib
import json
import os
from pathlib import Path
import re
import struct
import subprocess
import unittest

from support import AVAILABLE, SKIP, ROOT, Sessions, sha, tree, build_waterbox  # noqa: F401
from tests.hdf import hdf_image, hdf_rom  # noqa: E402
from tests.host.test_bridge_lifecycle import build_probe  # noqa: E402

CHUNK = 4096
UNITS = 40
HEADER = struct.Struct("<4sII")


def parse_saveram(blob):
    """(version, floppies, {hard file: (content id, image bytes, {chunk: bytes})}) of a SaveRam, checking its checksum and that nothing follows it."""
    magic, version, floppies = HEADER.unpack_from(blob)
    assert magic == b"P6SR", magic
    assert floppies == 0, "these tests give the SaveRam hard files only"
    at, result = 12, {}
    assert version == 2, version
    (hards,) = struct.unpack_from("<I", blob, at)
    at += 4
    for _ in range(hards):
        index, = struct.unpack_from("<I", blob, at)
        content_id = blob[at + 4:at + 36]
        size, chunk_bytes, count = struct.unpack_from("<QII", blob, at + 36)
        assert chunk_bytes == CHUNK
        at += 4 + 32 + 8 + 4 + 4
        chunks = {}
        for _ in range(count):
            (chunk,) = struct.unpack_from("<I", blob, at)
            length = min(CHUNK, size - chunk * CHUNK)
            chunks[chunk] = blob[at + 4:at + 4 + length]
            at += 4 + length
        result[index] = (content_id, size, chunks)
    assert hashlib.sha256(blob[:at]).digest() == blob[at:at + 32], "the checksum"
    assert len(blob) - at - 32 == 0 or not any(blob[at + 32:]), "nothing but padding after it"
    return version, floppies, result


def saveram_size(chunks, tail=0):
    """Bytes of a version 2 SaveRam of one hard file with that many full chunks."""
    return 12 + 4 + (4 + 32 + 8 + 4 + 4) + chunks * (4 + CHUNK) + tail + 32


def chunks_of(image):
    return [image[at:at + CHUNK] for at in range(0, len(image), CHUNK)]


@unittest.skipUnless(AVAILABLE, SKIP)
class HardFileTests(Sessions):
    FIRMWARE = {}
    native_probe = None

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        (cls.media / "hdfloop.rom").write_bytes(hdf_rom.loop_rom())
        cls.image = {"A": hdf_image.rdb_image(cylinders=64),                      # 2 MiB: 512 chunks
                     "B": hdf_image.rdb_image(cylinders=64, name="DH1"),         # the same size, other bytes
                     "BIG": hdf_image.rdb_image(cylinders=1024)}                  # 32 MiB
        assert len(cls.image["A"]) == len(cls.image["B"]) == 2 << 20 and cls.image["A"] != cls.image["B"]
        for name, image in cls.image.items():
            (cls.media / f"{name}.hdf").write_bytes(image)
        cls.files_before = tree(cls.media)
        cls.manifest = json.loads((ROOT / "manifests/waterbox-memory.json").read_text())
        cls.cache = {}

    def hdf(self, names="A"):
        """The paths of hard files, from names separated by commas ("A,B")."""
        return ",".join(str(self.media / f"{n}.hdf") for n in names.split(","))

    def hcore(self, name, hdf="A", adf="", writes=False, **options):
        return self.core(name, adf=adf, writes=writes, hdf=self.hdf(hdf), **options)

    def run_hdf(self, lines, **kwargs):
        return self.session(lines, firmware="hdfloop", **kwargs)

    def native(self, name, units):
        """The hard file the native engine leaves after `units` units of the loop ROM on image `name`."""
        key = ("native", name, units)
        if key not in self.cache:
            headless = type(self).native_probe or build_probe(ROOT / "build/link-audit", False, "p6_headless")
            type(self).native_probe = headless
            out = self.out / f"native-{name}-{units}.bin"
            result = subprocess.run([str(headless), "--run", str(self.media / "hdfloop.rom"), "--units", str(units), "--hdf", str(self.media / f"{name}.hdf"),
                                     "--export-hdf", str(out)], capture_output=True, text=True, timeout=900)
            self.assertEqual(0, result.returncode, result.stderr)
            self.cache[key] = out.read_bytes()
        return self.cache[key]

    def changed(self, name, units):
        """The chunks the loop ROM changed in `name` after `units` units, by the native engine's image: the guest writes one chunk further on each unit, with the first unit or two spent in the boot."""
        base, written = chunks_of(self.image[name]), chunks_of(self.native(name, units))
        changed = [c for c in range(len(base)) if base[c] != written[c]]
        self.assertEqual(list(range(len(changed))), changed, "a contiguous run of chunks from the first")
        self.assertGreaterEqual(len(changed), units, "at least one chunk a unit")
        return changed

    def saveram(self, name="A", units=UNITS, tag=None):
        """The SaveRam (its bytes and the file it is in) a core that ran `units` units on image `name` gives: made once per class, in a process of its own."""
        key = ("saveram", name, units)
        if key not in self.cache:
            path = self.out / f"{name}-{units}.saveram"
            out = self.run_hdf([self.hcore("W", name), f"run W {units}", "saveram W r", f"writesaveram r {path}"])
            self.assertEqual(1, len(self.saverams(out, "W")), out[-1500:])
            self.cache[key] = (path.read_bytes(), path)
        return self.cache[key]

    # --- MOUNT -------------------------------------------------------------------------------------------------------------------

    def test_the_hard_file_the_core_exports_after_the_guest_wrote_it_is_the_native_one(self):
        native = self.native("A", UNITS)
        exported = self.out / "mount.bin"
        out = self.run_hdf([self.hcore("A"), "services A", "modified A", "status A 0", f"run A {UNITS}", "modified A", "status A 0", f"export A 0 file={exported}"])
        self.assertIn("services A saveram=1 memorydomains=1", out, "a hard file is always writable, so there is something to persist without FloppyWrites")
        self.assertEqual([0, 1], self.modified(out, "A"))
        before, after = self.statuses(out, "A")
        chunks, written = len(self.image["A"]) // CHUNK, len(self.changed("A", UNITS))
        self.assertEqual((0, chunks, 0, 0), (before["result"], before["capacity"], before["used"], before["medium"]))
        self.assertEqual((chunks, written, written), (after["capacity"], after["used"], after["medium"]), "the overlay counts the chunks the guest wrote")
        self.assertEqual([(0, 0, len(self.image["A"]), sha(native))], self.exports(out, "A"))
        self.assertEqual(native, exported.read_bytes())
        self.assertNotEqual(self.image["A"], native, "the guest wrote the hard file")

    def test_a_hard_file_is_the_medium_after_the_floppies_and_the_disk_buttons_are_the_floppies(self):
        out = self.run_hdf([self.hcore("A", adf="AB", drives=2), "status A 0", "status A 2", "run A 20", "status A 2", "export A 2", "export A 0", "export A 3"])
        s0, s2_before, s2_after = self.statuses(out, "A")[:3]
        self.assertEqual(4, s0["result"], "a floppy has no write status without FloppyWrites: UNSUPPORTED")
        self.assertEqual(0, s2_before["result"])
        self.assertEqual((0, len(self.changed("A", 20))), (s2_before["used"], s2_after["used"]), "the third medium is the hard file")
        exports = self.exports(out, "A")
        self.assertEqual((2, 0, sha(self.native("A", 20))), (exports[0][0], exports[0][1], exports[0][3]), "medium 2 is the hard file")
        self.assertEqual(4, exports[1][1], "medium 0 is a floppy and has no writes enabled: UNSUPPORTED")
        self.assertNotEqual(0, exports[2][1], "there is no medium 3")

    def test_two_hard_files_are_mounted_in_order_and_only_the_one_the_guest_wrote_changes(self):
        out = self.run_hdf([self.hcore("A", hdf="A,B"), f"run A {UNITS}", "export A 0", "export A 1", "status A 1", "saveram A r", f"writesaveram r {self.out / 'two.saveram'}"])
        e0, e1 = self.exports(out, "A")
        self.assertEqual(sha(self.native("A", UNITS)), e0[3])
        self.assertEqual(sha(self.image["B"]), e1[3], "DH1 is the second image and the guest did not write it")
        self.assertEqual(0, self.statuses(out, "A")[0]["used"])
        _, _, hards = parse_saveram((self.out / "two.saveram").read_bytes())
        self.assertEqual([0], sorted(hards), "only the hard file that changed is in the SaveRam")

    # --- SAVERAM -----------------------------------------------------------------------------------------------------------------

    def test_the_saveram_holds_the_chunks_that_differ_and_not_the_image(self):
        exported = self.out / "big.bin"
        path = self.out / "big.saveram"
        out = self.run_hdf([self.hcore("A", hdf="BIG"), "modified A", f"run A {UNITS}", "modified A", "saveram A r", "modified A", f"writesaveram r {path}", f"export A 0 file={exported}"])
        self.assertEqual([0, 1, 0], self.modified(out, "A"), "clean, changed, clean once the SaveRam was taken")
        [(tag, size, digest, modified_before)] = self.saverams(out, "A")
        blob = path.read_bytes()
        self.assertEqual((len(blob), 1), (size, modified_before))
        written = self.changed("BIG", UNITS)
        self.assertEqual(saveram_size(len(written)), len(blob), "the chunks of 4 KiB the guest wrote and the headers")
        self.assertLess(len(blob), len(self.image["BIG"]) // 100, "sparse: a 32 MiB hard file, under 1 % of it as SaveRam")
        version, floppies, hards = parse_saveram(blob)
        content_id, image_bytes, chunks = hards[0]
        self.assertEqual((2, 0, sha(self.image["BIG"]), len(self.image["BIG"])), (version, floppies, content_id.hex(), image_bytes))
        base, final = chunks_of(self.image["BIG"]), chunks_of(exported.read_bytes())
        self.assertEqual(written, sorted(chunks), "exactly the chunks the guest wrote")
        for chunk, data in chunks.items():
            self.assertEqual(final[chunk], data, f"chunk {chunk} has the bytes the guest wrote")
            self.assertNotEqual(base[chunk], data)
        self.assertTrue(all(final[c] == base[c] for c in range(len(written), len(base))), "and the others are the image's")

    def test_a_new_core_stores_the_saveram_before_its_first_unit_and_goes_on_as_one_that_never_reloaded(self):
        blob, path = self.saveram("A", UNITS)
        again, fresh = self.out / "again.saveram", self.out / "fresh.saveram"
        stored, grown = self.out / "stored.bin", self.out / "grown.saveram"
        out = self.run_hdf([self.hcore("B"), "modified B", f"readsaveram r {path}", "storesaveram B r", "modified B", f"export B 0 file={stored}", "status B 0",
                            "saveram B same keep", f"writesaveram same {again}", "modified B", f"run B {UNITS + 10}", "modified B", "saveram B grown", f"writesaveram grown {grown}",
                            self.hcore("C"), f"run C {UNITS + 10}", "saveram C control", f"writesaveram control {fresh}"])
        self.assertIn("storedsaveram B r", out)
        self.assertEqual([0, 0, 0, 1], self.modified(out, "B"), "a SaveRam that was stored is what was kept: the core is clean, and the later writes change it")
        self.assertEqual(sha(self.native("A", UNITS)), self.exports(out, "B")[0][3], "before the first unit the hard file already has the writes of the first core")
        self.assertEqual((0, 0), (self.statuses(out, "B")[0]["used"], self.statuses(out, "B")[0]["medium"]), "they are the base of this core: its overlay is empty")
        self.assertEqual(blob, again.read_bytes(), "the SaveRam it would give is the one it was given, byte for byte")
        control, after = fresh.read_bytes(), grown.read_bytes()
        self.assertEqual(control, after, "going on from the stored SaveRam ends where a core that never reloaded ends (the union of the chunks)")
        grown_chunks = self.changed("A", UNITS + 10)
        self.assertEqual(grown_chunks, sorted(parse_saveram(after)[2][0][2]))
        self.assertEqual(saveram_size(len(grown_chunks)), len(after))

    def test_a_saveram_that_was_stored_enters_the_identity_of_states_and_movies(self):
        _, path = self.saveram("A", UNITS)
        state = self.out / "stored.state"
        out = self.run_hdf([self.hcore("S"), f"readsaveram r {path}", "storesaveram S r", f"run S 5", f"savefile S {state}",
                            self.hcore("N"), f"run N 5", f"loadfile N {state}", self.hcore("T"), "storesaveram T r", f"run T 5", f"loadfile T {state}", "digest S s", "digest T t"])
        self.assertEqual(["OtherConfig"], [reason for reason, _ in self.rejections(out, "N")], "a core without that SaveRam does not take the state of one with it")
        self.assertEqual(0, len(self.rejections(out, "T")))
        self.assertEqual(self.digests(out, "S")["s"], self.digests(out, "T")["t"])

    # --- REFUSED -----------------------------------------------------------------------------------------------------------------

    def test_a_saveram_that_is_not_for_this_core_or_not_whole_is_refused_and_changes_nothing(self):
        blob, path = self.saveram("A", UNITS)
        (self.out / "bad").mkdir(exist_ok=True)
        lines = [f"readsaveram r {path}",
                 self.hcore("B", "B"), "storesaveram B r", "export B 0",                                       # another image of the same size
                 self.core("D", adf="", writes=False), "storesaveram D r",                                    # nothing writable
                 self.core("F", adf="AB", writes=True), "storesaveram F r",                                    # floppies only
                 self.hcore("E"), "run E 1", "storesaveram E r",                                               # after its first unit
                 self.hcore("G"), "mutatesaveram r cut truncate:100", "storesaveram G cut", "mutatesaveram r flip flip:5000", "storesaveram G flip",
                 "mutatesaveram r junk junk:10", "storesaveram G junk", "mutatesaveram r pad pad:100", "storesaveram G pad", "export G 0"]
        out = self.run_hdf(lines)
        rows = re.findall(r"^saveram-rejected (\S+) (\S+) reason=(\w+) message=(.*)$", out, re.M)
        by = {(core, tag): reason for core, tag, reason, _ in rows}
        self.assertEqual("OtherMedia", by[("B", "r")])
        self.assertEqual("WritesDisabled", by[("D", "r")])
        self.assertEqual("BadFields", by[("F", "r")], "a core with other media")
        self.assertEqual("TooLate", by[("E", "r")])
        self.assertEqual("BadLength", by[("G", "cut")])
        self.assertEqual("Corrupt", by[("G", "flip")])
        self.assertEqual("BadLength", by[("G", "junk")])
        self.assertNotIn(("G", "pad"), by, "padding after the checksum is accepted")
        self.assertIn("storedsaveram G pad", out)
        self.assertEqual(sha(self.image["B"]), self.exports(out, "B")[0][3], "the refused SaveRam changed nothing")
        self.assertEqual(sha(self.native("A", UNITS)), self.exports(out, "G")[0][3], "and the accepted one set the hard file")
        self.assertEqual(7, len(rows), rows)

    # --- WB03 --------------------------------------------------------------------------------------------------------------------

    def test_wb03_save_go_on_load_and_repeat_and_the_overlay_goes_back_with_the_load(self):
        n, m = 20, 20
        state = self.out / "hdf.state"
        mid, end, back = self.out / "mid.bin", self.out / "end.bin", self.out / "back.bin"
        out = self.run_hdf([self.hcore("A"), f"run A {n}", "save A s", f"savefile A {state}", f"export A 0 file={mid}", f"run A {m}", "digest A first", f"export A 0 file={end}",
                            "load A s", f"export A 0 file={back}", f"run A {m}", "digest A second",
                            self.hcore("B"), "load B s", f"run B {m}", "digest B b", self.hcore("C"), f"loadfile C {state}", f"run C {m}", "digest C c"])
        a, b, c = ([u[1:] for u in self.units(out, name)] for name in "ABC")
        self.assertEqual(a[n:n + m], a[n + m:], "every unit after the load is the one that followed the save")
        self.assertEqual(a[n:n + m], b, "in a new sandbox (WB04)")
        self.assertEqual(a[n:n + m], c, "and in another process (WB15)")
        digests = self.digests(out, "A")
        self.assertEqual(digests["first"], digests["second"])
        self.assertEqual({digests["first"]}, {self.digests(out, "B")["b"], self.digests(out, "C")["c"]})
        self.assertEqual(sha(self.native("A", n)), sha(mid.read_bytes()))
        self.assertEqual(sha(self.native("A", n + m)), sha(end.read_bytes()))
        self.assertEqual(mid.read_bytes(), back.read_bytes(), "the load took the later writes of the hard file back")
        self.assertNotEqual(mid.read_bytes(), end.read_bytes())

    def test_a_state_of_another_hard_file_or_none_is_refused_and_the_instance_goes_on_as_if_never_asked(self):
        out = self.run_hdf([self.hcore("M"), "run M 30", "save M s",
                            self.hcore("T", "B"), "run T 12", "load T s", "run T 25", "digest T after", self.hcore("W", "B"), "run W 12", "run W 25", "digest W twin",
                            self.core("N", adf="", writes=False), "run N 12", "load N s", "run N 25", "digest N after", self.core("Z", adf="", writes=False), "run Z 12", "run Z 25", "digest Z twin",
                            self.hcore("P", "A,B"), "load P s"])
        rejected = {m[1]: (m[2], m[3]) for m in re.finditer(r"^rejected (\S+) s reason=(\w+) message=(.*)$", out, re.M)}
        self.assertEqual("OtherMedia", rejected["T"][0], "the same size, other bytes: the heaps and the configuration are the same, the media are not")
        self.assertIn(rejected["N"][0], ("OtherHeaps", "OtherMedia"), "a core without a hard file")
        self.assertEqual("OtherHeaps", rejected["P"][0], "a core with two hard files has other heaps")
        for loaded, control, label in (("T", "W", "twin"), ("N", "Z", "twin")):
            self.assertEqual([u[1:] for u in self.units(out, control)], [u[1:] for u in self.units(out, loaded)], "the instance that refused the state is the instance that never saw it")
            self.assertEqual(self.digests(out, control)[label], self.digests(out, loaded)["after"])

    def test_a_movie_of_another_hard_file_is_refused_its_own_is_accepted_and_it_plays_back(self):
        movie = self.out / "hdf.bk2"
        recorded = self.run_hdf([self.hcore("R"), f"run R {UNITS} record", f"endmovie R {movie}", "digest R end", f"export R 0 file={self.out / 'rec.bin'}"])
        out = self.run_hdf([self.hcore("Q", "B"), f"moviecheck Q {movie}", self.core("Y", adf="", writes=False), f"moviecheck Y {movie}", self.hcore("P"), f"moviecheck P {movie}"])
        self.assertEqual(1, len(re.findall(r"^movie rejected Q reason=OtherMedia", out, re.M)), out[-1500:])
        self.assertEqual(1, len(re.findall(r"^movie rejected Y reason=\w+", out, re.M)), out[-1500:])
        self.assertEqual(1, out.count("movie accepted P"))
        played = self.run_hdf([self.hcore("P"), f"run P {UNITS} movie={movie}", "digest P end", f"moviefinish P {movie}", f"export P 0 file={self.out / 'play.bin'}"])
        self.assertEqual([u[1:] for u in self.units(recorded, "R")], [u[1:] for u in self.units(played, "P")])
        self.assertEqual(self.digests(recorded, "R")["end"], self.digests(played, "P")["end"])
        self.assertEqual((self.out / "rec.bin").read_bytes(), (self.out / "play.bin").read_bytes(), "the hard file after the movie is the one it was recorded with")

    # --- HEAP and LIMITS -----------------------------------------------------------------------------------------------------------

    @staticmethod
    def heap_kib(size):
        """HardFileHeapKiB: the base, the room for the overlay, the dirty bitmap and 64 KiB of the allocator's own."""
        return 2 * ((size + 1023) // 1024) + (size // 4096 + 7) // 8 // 1024 + 64

    def test_the_heaps_grow_by_twice_the_heap_of_every_hard_file(self):
        layout, factor = self.manifest["layout"], self.manifest["memory_budget"]["mmap_extra_factor"]
        base = [layout[name] // 1024 for name in ("sbrk", "sealed", "invisible", "plain", "mmap")]

        def heaps(*sizes):
            result = build_waterbox.run_ffi("heaps", *(["--hdf-bytes", ",".join(map(str, sizes))] if sizes else []))
            self.assertEqual(0, result.returncode, result.stdout + result.stderr)
            return [int(v) for v in re.search(r"^heaps (.*)$", result.stdout, re.M)[1].split()]

        self.assertEqual(base, heaps(), "no hard file, the heaps it always had")
        for sizes in ((2 << 20,), (32 << 20,), (2 << 20, 3 << 20), (512, 4096, 10000, 1 << 30)):
            expected = base[:4] + [base[4] + factor * sum(self.heap_kib(s) for s in sizes)]
            self.assertEqual(expected, heaps(*sizes), sizes)

    def test_a_32_mib_hard_file_runs_and_its_pages_are_not_in_the_sealed_heap(self):
        exported = self.out / "huge.bin"
        out = self.run_hdf([self.hcore("A", "BIG"), f"run A {UNITS}", "save A s", f"export A 0 file={exported}", "status A 0"])
        self.assertEqual(sha(self.native("BIG", UNITS)), self.exports(out, "A")[0][3])
        size = self.sizes(out)["s"]
        self.assertLess(size, 16 << 20, "a state carries the dirty pages of the overlay and the machine, not the image")

    def test_the_core_refuses_hard_files_that_are_not_valid_when_it_is_created(self):
        odd, tiny = self.out / "odd.hdf", self.out / "tiny.hdf"
        odd.write_bytes(bytes(1000))
        tiny.write_bytes(b"")
        five = []
        for i in range(5):
            path = self.out / f"five{i}.hdf"
            path.write_bytes(bytes([i + 1]) * 4096)
            five.append(str(path))
        cases = (("hdf=" + ",".join(five), "at most 4 hard files"), (f"hdf={odd}", "512-byte blocks"), (f"hdf={tiny}", "512-byte blocks"),
                 (f"hdf={self.media / 'A.hdf'},{self.media / 'A.hdf'}", "the same image"))
        for option, message in cases:
            with self.subTest(option=option[:60]):
                self.run_hdf([f"core X {option}"], expect=1)
                self.assertIn(message, self.stderr)


# --- the owner's Workbench 2.05 as a hard file in the core of BizHawk (T63, opt-in) ---------------------------------------------------------------------------------------
# tests/hdf/test_workbench_hdf.py explains why the bytes of the Workbench 2.05 ADF are a valid plain partition: the core is given that .hdf (an A600 whose Kickstart 2.05 is read in memory
# from the owner's zip and written to the temporary directory of the test, which goes away at the end; no hash, byte or copy of either is kept anywhere), boots from DH0 and reaches the
# AmigaDOS window of the disk's own startup script, the same last picture as the floppy. WB 2.05 does not write while it boots, so the writes of the SaveRAM and WB03 tests come from a copy
# whose volume was not closed (bitmap-valid flag cleared in memory): the file system validates it and rewrites the root block and the bitmap, a real write of a real guest on a real volume.
#   BOOT    the last picture from DH0 is the last picture from DF0; a boot that wrote nothing has nothing to put in the SaveRAM (clean, no SaveRam to store).
#   WB03    save before the guest writes, go on across the validation, load, go on again: the same units and digest, the hard file goes back with the load, in a new sandbox and in another process.
#   SAVERAM the SaveRam holds the one 4 KiB chunk (110: blocks 880 and 881) that differs and the bytes the guest wrote; a new core that stores it before its first unit has the validated
#           volume already, goes on without rewriting it, and gives back the same SaveRam.
KICKSTART_A600_ZIP = os.environ.get("P6_KICKSTART_A600_ZIP")
WORKBENCH_205_ZIP = os.environ.get("P6_WB205_ZIP")
WB_UNITS, WB_FLOPPY_UNITS = 700, 2600          # the disk's script waits for an answer from unit ~510; the validation of the unclean volume is done by 600; the floppy settles at ~2370
WB_EARLY = 150                                 # before the guest writes anything (the first write is between units 170 and 190)
ROOT_CHUNK = 880 * 512 // CHUNK                # the chunk of the root block (880) and of the bitmap (881)


@unittest.skipUnless(AVAILABLE and KICKSTART_A600_ZIP and WORKBENCH_205_ZIP, "BLOCKED: set P6_KICKSTART_A600_ZIP and P6_WB205_ZIP (and build the guest) to boot the Workbench 2.05 from a hard file")
class WorkbenchHardFileTests(Sessions):
    FIRMWARE = {}

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        from tests.hdf.test_workbench_hdf import BITMAP_VALID, ROOT_BLOCK, blocks_changed, unclean
        from tests.step.test_kickstart_a600 import member
        cls.root_block, cls.bitmap_valid, cls.blocks_changed = ROOT_BLOCK, BITMAP_VALID, staticmethod(blocks_changed)
        disk = member(WORKBENCH_205_ZIP, ".adf", 901120)
        cls.disk, cls.unclean = disk, unclean(disk)
        (cls.media / "ks.rom").write_bytes(member(KICKSTART_A600_ZIP, ".rom", 524288))
        for name, data in (("clean.hdf", disk), ("unclean.hdf", cls.unclean), ("W.adf", disk)):
            (cls.media / name).write_bytes(data)
        cls.files_before = tree(cls.media)
        cls.cache = {}

    def wcore(self, name, image="clean", **options):
        """An A600 with the Workbench hard file `image` ("clean" or "unclean") and no floppy."""
        return self.core(name, adf="", writes=False, hdf=str(self.media / f"{image}.hdf"), machine="a600", **options)

    def run_wb(self, lines, **kwargs):
        return self.session(lines, firmware="ks", **kwargs)

    def final_hash(self, out, name):
        return self.units(out, name)[-1][3]

    def booted(self):
        """The unclean volume after WB_UNITS units: (output, exported bytes, SaveRam bytes, its digest), made once per class."""
        if "boot" not in self.cache:
            exported, saveram = self.out / "wb-unclean.bin", self.out / "wb-unclean.saveram"
            out = self.run_wb([self.wcore("U", "unclean"), f"run U {WB_UNITS}", "digest U end", "status U 0", f"export U 0 file={exported}", "saveram U r", f"writesaveram r {saveram}"])
            self.cache["boot"] = (out, exported.read_bytes(), saveram.read_bytes())
        return self.cache["boot"]

    # --- BOOT --------------------------------------------------------------------------------------------------------------------

    def test_the_hard_file_boots_from_dh0_to_the_screen_the_floppy_reaches_and_a_clean_boot_has_nothing_to_save(self):
        out = self.run_wb([self.wcore("H"), f"run H {WB_UNITS}", "modified H", "status H 0", "saveram H r",
                           self.core("F", adf="W", writes=False, machine="a600"), f"run F {WB_FLOPPY_UNITS}"])
        units, floppy = self.units(out, "H"), self.units(out, "F")
        self.assertEqual((WB_UNITS, WB_FLOPPY_UNITS), (len(units), len(floppy)))
        self.assertEqual(floppy[-1][3], units[-1][3], "the last picture is the one the same bytes reach from DF0")
        self.assertEqual(units[-100][3], units[-1][3], "the screen is stable")
        self.assertNotEqual(units[WB_EARLY][3], units[-1][3])
        self.assertEqual([0], self.modified(out, "H"), "the guest wrote nothing: the SaveRam is the empty one")
        (status,) = self.statuses(out, "H")
        self.assertEqual((0, 0), (status["used"], status["medium"]))
        self.assertEqual(self.disk, (self.media / "clean.hdf").read_bytes(), "the file the core was given is the same")

    # --- SAVERAM -----------------------------------------------------------------------------------------------------------------

    def test_the_saveram_holds_the_one_chunk_the_guest_rewrote_and_the_exported_volume_is_valid_again(self):
        out, exported, blob = self.booted()
        self.assertEqual([self.root_block, self.root_block + 1], self.blocks_changed(self.unclean, exported))
        longs = struct.unpack(">128L", exported[self.root_block * 512:(self.root_block + 1) * 512])
        self.assertEqual((0xFFFFFFFF, 0), (longs[self.bitmap_valid], sum(longs) & 0xFFFFFFFF), "the guest validated the volume")
        (status,) = self.statuses(out, "U")
        self.assertEqual((1, 1), (status["used"], status["medium"]), "blocks 880 and 881 are one 4 KiB chunk")
        version, floppies, hards = parse_saveram(blob)
        content_id, size, chunks = hards[0]
        self.assertEqual((2, 0, sha(self.unclean), len(self.unclean)), (version, floppies, content_id.hex(), size))
        self.assertEqual([ROOT_CHUNK], sorted(chunks))
        self.assertEqual(exported[ROOT_CHUNK * CHUNK:(ROOT_CHUNK + 1) * CHUNK], chunks[ROOT_CHUNK])
        self.assertEqual(saveram_size(1), len(blob))

    def test_a_new_core_that_stores_the_saveram_has_the_validated_volume_and_goes_on_without_rewriting_it(self):
        out, exported, blob = self.booted()
        path, again, final = self.out / "wb-in.saveram", self.out / "wb-again.saveram", self.out / "wb-final.bin"
        path.write_bytes(blob)
        second = self.run_wb([self.wcore("B", "unclean"), "modified B", f"readsaveram r {path}", "storesaveram B r", "modified B", "status B 0", f"run B {WB_UNITS}", "modified B",
                              "saveram B same keep", f"writesaveram same {again}", f"export B 0 file={final}", "status B 0"])
        self.assertIn("storedsaveram B r", second)
        self.assertEqual([0, 0, 0], self.modified(second, "B"), "what was stored is what was kept: it stays clean while the guest has nothing left to validate")
        self.assertEqual([0, 0], [s["used"] for s in self.statuses(second, "B")], "the overlay stays empty: the guest wrote nothing")
        self.assertEqual(exported, final.read_bytes(), "the volume is the one the first core validated")
        self.assertEqual(blob, again.read_bytes(), "the SaveRam it would give is the one it was given, byte for byte")
        self.assertEqual(self.final_hash(second, "B"), self.final_hash(out, "U"), "and it ends on the same picture")

    # --- WB03 --------------------------------------------------------------------------------------------------------------------

    def test_wb03_a_state_taken_before_the_guest_writes_replays_the_validation_in_a_new_sandbox_and_in_another_process(self):
        m = WB_UNITS - WB_EARLY
        state, mid, end, back = self.out / "wb.state", self.out / "wb-mid.bin", self.out / "wb-end.bin", self.out / "wb-back.bin"
        out = self.run_wb([self.wcore("A", "unclean"), f"run A {WB_EARLY}", "save A s", f"savefile A {state}", f"export A 0 file={mid}", f"run A {m}", "digest A first",
                           f"export A 0 file={end}", "load A s", f"export A 0 file={back}", f"run A {m}", "digest A second",
                           self.wcore("B", "unclean"), "load B s", f"run B {m}", "digest B b"])
        a, b = ([u[1:] for u in self.units(out, name)] for name in "AB")
        self.assertEqual(a[WB_EARLY:WB_EARLY + m], a[WB_EARLY + m:], "every unit after the load is the one that followed the save")
        self.assertEqual(a[WB_EARLY:WB_EARLY + m], b, "in a new sandbox (WB04)")
        digests = self.digests(out, "A")
        self.assertEqual({digests["first"]}, {digests["second"], self.digests(out, "B")["b"]})
        self.assertEqual(self.unclean, mid.read_bytes(), "before the validation the volume is the one the core was given")
        self.assertEqual(self.booted()[1], end.read_bytes(), "after it, the validated one")
        self.assertEqual(mid.read_bytes(), back.read_bytes(), "the load took the writes back")
        other = self.run_wb([self.wcore("C", "unclean"), f"loadfile C {state}", f"run C {m}", "digest C c"])
        self.assertEqual(digests["first"], self.digests(other, "C")["c"], "and in another process (WB15)")
        refused = self.run_wb([self.wcore("N", "clean"), f"loadfile N {state}", self.core("Z", adf="", writes=False, machine="a600"), f"loadfile Z {state}"])
        reasons = {m[1]: m[2] for m in re.finditer(r"^rejected (\S+) \S+ reason=(\w+) message=", refused, re.M)}
        self.assertEqual("OtherMedia", reasons["N"], "the same size, other bytes: another volume is another machine")
        self.assertIn(reasons["Z"], ("OtherHeaps", "OtherMedia"), "and so is none")


if __name__ == "__main__":
    unittest.main()
