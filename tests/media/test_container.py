"""T17d, MED01 and FUZZ01 for the disk containers of the libretro adapter: ADZ (gzip), ZIP and DMS decoded in memory to the ADF they hold.

adapters/libretro/container.h, inflate.h and dms.h are header-only and know nothing of the engine, so they are built here with ASan and UBSan next to no
engine (tests/media/container_probe.cpp). The containers are written at run time (tests/media/containers.py), nothing binary is committed. What is checked:
every container of a disk decodes to the same bytes (the SHA-256 of the ADF is the medium's identity, so it is the same medium); every format the core
does not read, and every damaged or hostile file, is a clear message and never a crash or a large allocation; the DMS unpackers agree byte for byte with
the xDMS sources WinUAE ships on random streams (dms_differential.cpp); and a seeded fuzz target runs truncations, bit flips, lying sizes, bombs and DMS
tracks resealed so that the unpackers see garbage. How the core uses them (same content id and same run for any container) is in tests/libretro-host.
Run: python3 -m unittest discover -s tests/media -t .
"""
import hashlib
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
import tempfile
import unittest
import warnings
import zlib
import zipfile

from tests.media import containers as c

ROOT = Path(__file__).resolve().parents[2]
COMPILER = shutil.which("clang++")
SANITIZE = ["-fsanitize=address,undefined", "-fno-sanitize-recover=all"]
ENV = {**os.environ, "ASAN_OPTIONS": "detect_leaks=0:max_allocation_size_mb=8"}
DMS_SOURCES = ROOT / "build/upstream/archivers/dms"


def build(name, sources, extra=()):
    exe = Path(_TMP.name) / name
    command = [COMPILER, "-std=c++17", "-O1", "-g", *SANITIZE, "-I" + str(ROOT / "adapters/libretro"), *extra, *map(str, sources), "-o", str(exe)]
    built = subprocess.run(command, capture_output=True, text=True, timeout=900)
    if built.returncode:
        raise RuntimeError(built.stderr[-3000:])
    return exe


_TMP = None
PROBE = None


def setUpModule():
    global _TMP, PROBE
    if COMPILER:
        _TMP = tempfile.TemporaryDirectory(prefix="p6-container-")
        PROBE = build("container_probe", [ROOT / "tests/media/container_probe.cpp"], ["-Wall", "-Wextra", "-Werror"])


def tearDownModule():
    if _TMP:
        _TMP.cleanup()


def decode(data, name="disk.bin"):
    """The probe's verdict on a file: (True, adf bytes) or (False, message). A file is written for the probe, and only here: the core never writes."""
    path = Path(_TMP.name) / name
    path.write_bytes(data)
    result = subprocess.run([str(PROBE), "decode", str(path)], capture_output=True, timeout=120, env=ENV)
    assert result.returncode == 0 and b"Sanitizer" not in result.stderr and b"runtime error" not in result.stderr, result.stderr[-1500:]
    head, _, body = result.stdout.partition(b"\n")
    if head.startswith(b"ok "):
        assert int(head[3:]) == len(body), "the size printed is not the size delivered"
        return True, body
    assert head.startswith(b"refused: "), head
    return False, head[9:].decode()


@unittest.skipUnless(COMPILER, "clang++ unavailable")
class ContainerTests(unittest.TestCase):
    def assertDecodesTo(self, data, expected, name="disk.bin"):
        ok, result = decode(data, name)
        self.assertTrue(ok, result)
        # Compared by identity: the SHA-256 is what the core registers, whichever way the disk came.
        self.assertEqual(hashlib.sha256(result).hexdigest(), hashlib.sha256(expected).hexdigest())

    def assertRefused(self, data, text, name="disk.bin"):
        ok, result = decode(data, name)
        self.assertFalse(ok, "the file was accepted")
        self.assertIn(text, result)
        self.assertLess(len(result), 400)
        return result

    # --- the containers of one disk give that disk ---------------------------------------------------------------------------------------------

    def test_a_plain_adf_is_itself_and_is_never_judged_by_what_it_starts_with(self):
        disk = c.varied_adf("plain")
        self.assertDecodesTo(disk, disk, "game.adf")
        for head in (b"DMS!", b"PK\3\4", b"\x1f\x8b", b"CAPS"):          # 901120 bytes are an ADF whatever they start with
            self.assertDecodesTo(head + disk[len(head):], head + disk[len(head):])

    def test_an_adz_is_the_gzip_of_the_adf_whatever_the_level_the_strategy_and_the_header(self):
        for disk in (c.adf("a"), c.varied_adf("b")):
            for level in range(0, 10):
                with self.subTest(level=level):
                    self.assertDecodesTo(c.adz(disk, level), disk, "game.adz")
            for strategy in (zlib.Z_DEFAULT_STRATEGY, zlib.Z_FILTERED, zlib.Z_HUFFMAN_ONLY, zlib.Z_RLE, zlib.Z_FIXED):
                with self.subTest(strategy=strategy):
                    self.assertDecodesTo(c.gzip_with(disk, 6, strategy), disk)
        disk = c.varied_adf("header")
        for kwargs in ({"name": "Speedball.adf"}, {"comment": "a comment"}, {"extra": b"xyz" * 20}, {"header_crc": True},
                       {"name": "n", "comment": "c", "extra": b"e", "header_crc": True}):
            with self.subTest(**kwargs):
                self.assertDecodesTo(c.gzip_with(disk, 9, zlib.Z_DEFAULT_STRATEGY, **kwargs), disk)

    def test_a_zip_with_one_disk_gives_the_disk_whatever_else_it_holds(self):
        disk = c.varied_adf("zip")
        for label, entries in (("alone", [("game.adf", disk)]),
                               ("in a folder", [("games/", b""), ("games/Game (1990).adf", disk)]),
                               ("with a readme", [("readme.txt", b"hello"), ("game.adf", disk), ("game.nfo", b"x"), ("sub/", b"")]),
                               ("with a resource fork", [("__MACOSX/", b""), ("__MACOSX/._game.adf", b"fork"), ("game.adf", disk), ("._game.adf", b"fork")]),
                               ("upper case", [("GAME.ADF", disk)]),
                               ("an adz inside", [("game.adz", c.adz(disk))]),
                               ("a dms inside", [("game.dms", c.dms_file(disk, lambda n: n % 2))])):
            for method in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
                with self.subTest(label, method=method):
                    self.assertDecodesTo(c.zip_file(entries, method), disk, "game.zip")
        with self.subTest("a comment at the end"):
            self.assertDecodesTo(c.zip_file([("game.adf", disk)], comment=b"c" * 300), disk)

    def test_a_dms_in_every_mode_the_encoder_can_write_gives_the_disk(self):
        disk = c.varied_adf("dms")
        mixed = lambda n: (0, 1, 2)[n % 3]
        for label, modes in (("stored", None), ("rle", lambda n: 1), ("mixed stored, rle and quick", mixed)):
            with self.subTest(label):
                self.assertDecodesTo(c.dms_file(disk, modes), disk, "game.dms")
        for label, kwargs in (("no-zero tracks left out", {"no_zero": True}), ("with a banner", {"banner": True}), ("a different disk type", {"disk_type": 4})):
            with self.subTest(label):
                self.assertDecodesTo(c.dms_file(disk, **kwargs), disk)
        fake_boot = c.track(0, b"fake boot block".ljust(1024, b"\0"), 0)         # advertising DMS files: a track 0 of 1024 bytes, then FILE_ID.DIZ
        file_id = c.track(80, b"FILE_ID.DIZ text\n".ljust(1500, b" "), 0)
        with self.subTest("fake boot block and FILE_ID.DIZ"):
            self.assertDecodesTo(c.dms_file(disk, extras=[fake_boot, file_id]), disk)
        with self.subTest("trailing text after the data"):
            self.assertDecodesTo(c.dms_file(disk) + b"\r\nPacked with DMS\r\n" * 3, disk)
        with self.subTest("a track 0 sent twice: the later one wins"):
            wrong = c.track(0, bytes([7]) * c.TRACK_BYTES, 0)
            self.assertDecodesTo(c.dms_file(disk, extras=[wrong]), disk)

    def test_heavy_tracks_of_one_value_and_the_dictionary_flag(self):
        disk = bytearray(c.varied_adf("heavy"))
        for n in (1, 2, 3):
            disk[n * c.TRACK_BYTES:(n + 1) * c.TRACK_BYTES] = bytes([0xA0 + n]) * c.TRACK_BYTES
        disk = bytes(disk)
        for mode in (5, 6, 7):
            with self.subTest(mode=mode):
                self.assertDecodesTo(c.dms_file(disk, lambda n, m=mode: m if n in (1, 2, 3, 4, 40) else 0), disk)

    def test_the_same_disk_in_every_container_is_the_same_medium(self):
        disk = c.varied_adf("identity")
        variants = [disk, c.adz(disk), c.zip_file([("a.adf", disk)]), c.dms_file(disk, lambda n: n % 3 % 2), c.zip_file([("a.dms", c.dms_file(disk))])]
        digests = {hashlib.sha256(decode(v)[1]).hexdigest() for v in variants}
        self.assertEqual(digests, {hashlib.sha256(disk).hexdigest()})

    # --- what is refused, and why -------------------------------------------------------------------------------------------------------------

    def test_formats_the_core_does_not_read_are_refused_with_a_clear_message(self):
        ipf = b"CAPS\0\0\0\x0c" + bytes(2000)
        for name in ("game.ipf", "game.IPF", "game.bin"):                  # by content or by name
            with self.subTest(name):
                self.assertRefused(ipf if name == "game.bin" else bytes(5000), "capsimg", name)
        self.assertRefused(ipf, "licence forbids commercial use", "x.ipf")
        self.assertRefused(b"Formatted Disk Image file\r\n" + bytes(2000), "FDI images are not supported", "x.bin")
        self.assertRefused(bytes(3000), "FDI images are not supported", "game.fdi")
        self.assertRefused(bytes(3000), "this disk format is not supported", "game.scp")
        self.assertRefused(bytes(3000), "only ZIP, 7z and RP9 archives are read", "game.rar")
        self.assertRefused(b"UAE-1ADF" + bytes(2000), "extended ADF", "game.adf")
        self.assertRefused(b"UAE--ADF" + bytes(2000), "extended ADF", "game.adf")
        self.assertRefused(bytes(901119), "is 901119 bytes, not an ADF of 901120", "game.adf")
        self.assertRefused(bytes(901121), "is 901121 bytes, not an ADF of 901120", "game.adf")
        self.assertRefused(b"", "is 0 bytes", "game.adf")
        self.assertRefused(bytes(2 * 1024 * 1024 + 1), "larger than any disk file", "game.adf")

    def test_a_zip_that_is_not_exactly_one_disk_is_refused_and_says_what_to_do(self):
        disk = c.adf("z")
        self.assertRefused(c.zip_file([("readme.txt", b"x"), ("dir/", b"")]), "holds no disk image")
        self.assertRefused(c.zip_file([("a.adf", disk), ("b.adf", c.adf("y"))]), "holds 2 disk images", "set.zip")
        self.assertRefused(c.zip_file([("a.adf", disk), ("b.adf", c.adf("y"))]), ".m3u playlist")
        self.assertRefused(c.zip_file([("a.adf", disk), ("b.dms", c.dms_file(disk))]), "holds 2 disk images")
        self.assertRefused(c.zip_file([("game.ipf", bytes(1000))]), "a format the core does not read")
        self.assertRefused(c.zip_file([("inner.zip", c.zip_file([("a.adf", disk)]))]), "holds no disk image")
        self.assertRefused(c.zip_file([("a.adf", bytes(900000))]), "the ZIP entry a.adf: the file is 900000 bytes")
        self.assertRefused(c.zip_file([("a.adf", disk)], zipfile.ZIP_BZIP2), "compression method 12")
        self.assertRefused(c.zip_file([("a.adf", disk)], zipfile.ZIP_LZMA), "compression method 14")
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")           # zipfile warns about the duplicate name, which is the point
            twice = c.zip_file([("a.adf", disk), ("a.adf", disk)])
        self.assertRefused(twice, "holds 2 disk images")
        self.assertRefused(c.zip_file([]), "ZIP file is empty")
        # An encrypted entry: the flag is set in the local header and the central directory.
        raw = bytearray(c.zip_file([("a.adf", disk)], zipfile.ZIP_STORED))
        for signature in (b"PK\3\4", b"PK\1\2"):
            at = raw.index(signature) + (6 if signature == b"PK\3\4" else 8)
            raw[at] |= 1
        self.assertRefused(bytes(raw), "encrypted")

    def test_a_container_that_decodes_to_the_wrong_size_is_refused(self):
        for size in (0, 1, 900000, 901119, 901121, 1000000):
            with self.subTest(size=size):
                self.assertRefused(c.adz(bytes(size)), f"holds {size} bytes, not an ADF of 901120" if size <= 901120 else "more than", "g.adz")
        self.assertRefused(c.dms_file(c.adf("d"), tracks=range(79)), "79 of 80 tracks")
        self.assertRefused(c.dms_file(c.adf("d"), tracks=range(40)), "40 of 80 tracks")
        self.assertRefused(c.dms_file(c.adf("d"), tracks=[]), "no disk tracks")

    def test_damaged_files_are_refused_not_believed(self):
        disk = c.varied_adf("damage")
        gz = c.adz(disk)
        zp = c.zip_file([("a.adf", disk)])
        dms = c.dms_file(disk, lambda n: n % 3 % 2)
        for label, data, text in (
                ("gzip cut in the middle", gz[: len(gz) // 2], "truncated"),
                ("gzip without its trailer", gz[:-8], "truncated"),
                ("gzip with a wrong CRC", gz[:-8] + bytes(4) + gz[-4:], "CRC or size mismatch"),
                ("gzip with a wrong size", gz[:-4] + struct.pack("<I", ADF_BYTES + 1), "CRC or size mismatch"),
                ("gzip with data after it", gz + b"\0", "data after its stream"),
                ("gzip of another method", gz[:2] + b"\x07" + gz[3:], "method other than deflate"),
                ("gzip with reserved flags", gz[:3] + b"\xe0" + gz[4:], "reserved flags"),
                ("gzip of one byte", gz[:2], "truncated"),
                ("zip cut", zp[:-30], "no central directory"),
                ("zip without its end record", zp[:-22], "no central directory"),
                ("zip with a wrong CRC", c.raw_zip("a.adf", 0, disk, 12345, len(disk), len(disk)), "CRC mismatch"),
                ("dms cut in a track", dms[: len(dms) // 2], "truncated"),
                ("dms with a wrong header CRC", dms[:54] + b"\0\0" + dms[56:], "header is corrupt"),
                ("dms of 10 bytes", dms[:10], "truncated"),
        ):
            with self.subTest(label):
                ok, message = decode(data, "damaged.bin")
                self.assertFalse(ok)
                self.assertIn(text, message)

    def test_dms_archives_the_core_does_not_unpack_are_refused(self):
        disk = c.adf("e")
        self.assertRefused(c.dms_file(disk, info=2), "encrypted")
        self.assertRefused(c.dms_file(disk, info=16), "high-density")
        self.assertRefused(c.dms_file(disk, disk_type=7), "FMS")
        # A corrupt track: one byte of the packed data flips and the CRC of the data says so.
        data = bytearray(c.dms_file(disk))
        data[56 + 20 + 100] ^= 1
        self.assertRefused(bytes(data), "CRC mismatch on track 0")
        # A track whose unpacked data does not add up to its checksum (the header and data CRCs are valid).
        data = bytearray(c.dms_file(disk))
        struct.pack_into(">H", data, 56 + 14, (struct.unpack_from(">H", data, 56 + 14)[0] + 1) & 0xFFFF)
        struct.pack_into(">H", data, 56 + 18, c.crc16(bytes(data[56:56 + 18])))
        self.assertRefused(bytes(data), "checksum mismatch")
        # An unknown mode, and a mode whose stream is garbage.
        for mode, text in ((9, "unknown compression mode"), (2, "does not decompress"), (3, "does not decompress"), (4, "does not decompress"),
                           (5, "does not decompress"), (6, "does not decompress")):
            with self.subTest(mode=mode):
                data = bytearray(c.dms_file(disk))
                data[56 + 13] = mode
                struct.pack_into(">H", data, 56 + 18, c.crc16(bytes(data[56:56 + 18])))
                self.assertRefused(bytes(data), text)

    # --- hostile sizes and bombs, with the memory bounded --------------------------------------------------------------------------------------

    def test_a_decompression_bomb_costs_at_most_one_disk_of_memory_and_a_message(self):
        # 256 MiB of zeros in a few hundred KB: the ratio deflate allows. The probe runs with every allocation above 8 MB a report.
        compressor = zlib.compressobj(9, zlib.DEFLATED, -15)
        body = b"".join(compressor.compress(bytes(1 << 20)) for _ in range(256)) + compressor.flush()
        self.assertLess(len(body), 1024 * 1024)
        gz = b"\x1f\x8b\x08\0\0\0\0\0\0\3" + body + struct.pack("<II", 0, 0)
        self.assertRefused(gz, "more than", "bomb.adz")
        # The zip lies: it declares a disk, or less, or nothing, and the stream inflates to 256 MiB; or it declares a size nobody would allocate.
        crc = 0
        for declared in (ADF_BYTES, 1, 0, 2 * 1024 * 1024):
            with self.subTest(declared=declared):
                self.assertRefused(c.raw_zip("a.adf", 8, body, crc, len(body), declared), "ZIP entry")
        for declared in (0xFFFFFFFF, 0x7FFFFFFF, 0x80000000, 2 * 1024 * 1024 + 1):
            with self.subTest(huge=declared):
                self.assertRefused(c.raw_zip("a.adf", 8, body, crc, len(body), declared), "larger than any disk file")
                self.assertRefused(c.raw_zip("a.adf", 0, body, crc, declared, declared), "larger than any disk file")
        self.assertRefused(c.raw_zip("a.adf", 0, bytes(100), 0, 100, 101), "stored sizes differ")
        self.assertRefused(c.raw_zip("a.adf", 8, body, crc, len(body) + 1000, ADF_BYTES), "")       # a compressed size past the file

    def test_declared_sizes_in_a_dms_never_decide_an_allocation(self):
        disk = c.adf("sizes")
        for label, edit, text in (("packed 0xFFFF", (6, 0xFFFF), "more data than a track can hold"),
                                  ("middle 0xFFFF", (8, 0xFFFF), "more data than a track can hold"),
                                  ("unpacked 0xFFFF", (10, 0xFFFF), "more data than a track can hold"),
                                  ("packed 32000", (6, 32000), "CRC mismatch"),
                                  ("unpacked 4096", (10, 4096), "does not unpack to 11264")):
            with self.subTest(label):
                data = bytearray(c.dms_file(disk))
                struct.pack_into(">H", data, 56 + edit[0], edit[1])
                struct.pack_into(">H", data, 56 + 18, c.crc16(bytes(data[56:56 + 18])))
                self.assertRefused(bytes(data), text)
        # A million track headers' worth of nothing: the loop is bounded by the file, which is capped.
        self.assertRefused(c.dms_file(disk)[:56] + (b"TR" + bytes(18)) * 100000, "")

    # --- the unpackers against WinUAE's xDMS --------------------------------------------------------------------------------------------------

    @unittest.skipUnless(DMS_SOURCES.is_dir(), "build/upstream is not materialized (python3 tools/materialize_upstream.py)")
    def test_the_dms_unpackers_agree_with_xdms_on_random_streams_in_every_mode(self):
        sources = [DMS_SOURCES / f"{name}.cpp" for name in ("u_init", "u_rle", "u_quick", "u_medium", "u_deep", "u_heavy", "getbits", "maketbl", "tables")]
        exe = build("dms_differential", [ROOT / "tests/media/dms_differential.cpp", *sources], ["-w", "-I" + str(DMS_SOURCES)])
        for seed, count in ((1, 2500), (2, 2500), (3, 2500)):
            with self.subTest(seed=seed):
                result = subprocess.run([str(exe), str(seed), str(count)], capture_output=True, text=True, timeout=900, env=ENV)
                self.assertEqual(result.returncode, 0, result.stderr[-1500:])
                modes = {m[0]: (int(m[1]), int(m[2])) for m in re.findall(r"^dms mode (\w+): accepted (\d+) refused (\d+)$", result.stdout, re.M)}
                self.assertEqual(sorted(modes), ["deep", "heavy1", "heavy2", "medium", "quick"])
                for mode, (accepted, refused) in modes.items():
                    self.assertGreater(accepted, 50, mode)       # the comparison reached streams that decode, not only refusals
                    self.assertGreater(refused, 50, mode)


ADF_BYTES = c.ADF_BYTES


@unittest.skipUnless(COMPILER, "clang++ unavailable")
class ContainerFuzzTests(unittest.TestCase):
    """FUZZ01 for the containers: the seeded fuzz target of container_probe.cpp on valid ADZ, ZIP and DMS seeds, with ASan, UBSan and every single
    allocation above 8 MB a report (a disk is 0.9 MB, a file at most 2 MiB). A variant that is accepted must be exactly the disk."""

    SEEDS = ((11, 1500), (12, 1500), (13, 1500))
    KINDS = {"none", "truncate", "extend", "flip", "zero-run", "extreme-byte", "huge-field", "splice", "random-bytes", "dms-data-resealed",
             "dms-track-resealed", "zip-field", "header-flip"}

    @classmethod
    def setUpClass(cls):
        disk = c.varied_adf("fuzz")
        cls.directory = Path(_TMP.name) / "fuzz-seeds"
        cls.directory.mkdir()
        cls.adf = cls.directory.parent / "fuzz.adf"
        cls.adf.write_bytes(disk)
        constant = bytearray(disk)
        for n in (1, 2):
            constant[n * c.TRACK_BYTES:(n + 1) * c.TRACK_BYTES] = bytes([0xC0 + n]) * c.TRACK_BYTES
        constant = bytes(constant)
        cls.constant = constant
        small = {
            "gz9.adz": c.adz(disk, 9), "gz1.adz": c.adz(disk, 1), "gz0.adz": c.adz(disk, 0), "fixed.adz": c.gzip_with(disk, 6, zlib.Z_FIXED),
            "stored.zip": c.zip_file([("a.adf", disk)], zipfile.ZIP_STORED), "deflate.zip": c.zip_file([("readme.txt", b"hi"), ("a.adf", disk)]),
            "nested.zip": c.zip_file([("a.adz", c.adz(disk))]), "dms.zip": c.zip_file([("a.dms", c.dms_file(disk, lambda n: n % 3 % 2))]),
            "stored.dms": c.dms_file(disk), "rle.dms": c.dms_file(disk, lambda n: 1), "mixed.dms": c.dms_file(disk, lambda n: (n < 6) * (n % 3) % 3),
            "nozero.dms": c.dms_file(disk, no_zero=True, banner=True),
        }
        for name, data in small.items():
            (cls.directory / name).write_bytes(data)
        # The Heavy seed needs another disk (its tracks are constant), so it is its own run.
        cls.heavy = cls.directory.parent / "heavy-seeds"
        cls.heavy.mkdir()
        cls.heavy_adf = cls.directory.parent / "heavy.adf"
        cls.heavy_adf.write_bytes(constant)
        (cls.heavy / "heavy.dms").write_bytes(c.dms_file(constant, lambda n: {1: 5, 2: 6, 4: 7, 40: 5}.get(n, 0)))
        (cls.heavy / "stored.dms").write_bytes(c.dms_file(constant))
        (cls.heavy / "gz.adz").write_bytes(c.adz(constant))

    def run_fuzz(self, seed, count, adf, directory):
        seeds = sorted(str(p) for p in directory.iterdir())
        return subprocess.run([str(PROBE), "fuzz", str(seed), str(count), str(adf), *seeds], capture_output=True, text=True, timeout=1800, env=ENV)

    @staticmethod
    def clean(result):
        return result.returncode == 0 and "runtime error" not in result.stderr and "Sanitizer" not in result.stderr

    def test_the_recorded_seeds_run_clean_and_reach_every_mutation_and_both_outcomes(self):
        for seed, count in self.SEEDS:
            for adf, directory in ((self.adf, self.directory), (self.heavy_adf, self.heavy)):
                with self.subTest(seed=seed, count=count, seeds=directory.name):
                    result = self.run_fuzz(seed, count, adf, directory)
                    self.assertTrue(self.clean(result), result.stderr[-1500:])
                    kinds = {m[0]: (int(m[1]), int(m[2])) for m in re.findall(r"^container kind (\S+): accepted (\d+) refused (\d+)$", result.stdout, re.M)}
                    self.assertEqual(set(kinds), self.KINDS)
                    self.assertGreater(sum(a for a, _ in kinds.values()), 0)         # some variants still decode (the damage was in a field nobody reads)
                    self.assertGreater(sum(r for _, r in kinds.values()), count // 2)
                    self.assertEqual(kinds["none"][1], 0)                            # the untouched seed always decodes
                    for reason in ("the gzip", "DMS") + (("the ZIP",) if directory is self.directory else ()):
                        self.assertIn(reason, result.stdout)

    def test_a_run_is_reproducible(self):
        first = self.run_fuzz(5, 300, self.adf, self.directory)
        self.assertTrue(self.clean(first), first.stderr[-800:])
        self.assertEqual(first.stdout, self.run_fuzz(5, 300, self.adf, self.directory).stdout)
        self.assertNotEqual(first.stdout, self.run_fuzz(6, 300, self.adf, self.directory).stdout)

    def test_the_harness_can_fail(self):
        # A "seed" that is not the disk the variants are compared with must stop the run: an accepted variant that differs is a failure.
        other = self.directory.parent / "other.adf"
        other.write_bytes(c.adf("not the disk"))
        result = self.run_fuzz(1, 50, other, self.directory)
        self.assertEqual(result.returncode, 1)
        self.assertIn("FUZZ FAILURE", result.stderr)


if __name__ == "__main__":
    unittest.main()
