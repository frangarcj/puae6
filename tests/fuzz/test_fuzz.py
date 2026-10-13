"""FUZZ01 for the surfaces that exist today: config struct, firmware image, logical names and scenario JSON.

The native targets run in the sanitizer build (build/native-debug, ASan + UBSan, no recover), so a memory error
or undefined behaviour ends the run. Runs are deterministic: same seed and count, same result. The state target (T15: StateFuzzTests) loads mutated states of a
running image through the public p6_state_load, with the allocator capped so that an allocation sized by a forged length is a report, and checks that every refusal leaves the machine bit for
bit as it was and that the image then runs like a control that never saw the attempt. T17 adds what the libretro adapter takes from the frontend:
ContentFuzzTests (M3U playlists, a lying VFS and option values through the adapter's own headers, built here with the sanitizers and no engine) and
ConfigNormalizeFuzzTests (tools/generate_options.py normalize_config with random keys, values and types).
"""
import copy
import json
import os
from pathlib import Path
import random
import re
import subprocess
import sys
import tempfile
import unittest
import zlib

from tests.host.test_bridge_lifecycle import ROOT, TOOLS, build_probe
from tests.step.test_step import builder

sys.path.insert(0, str(ROOT / "tools"))
import generate_options  # noqa: E402
from scenario_schema import load_json, validate_scenario  # noqa: E402

CORPUS = json.loads((ROOT / "tests/fuzz/corpus/regressions.json").read_text())
ENV = {**os.environ, "ASAN_OPTIONS": "detect_leaks=0"}


def clean(result):
    return result.returncode == 0 and "runtime error" not in result.stderr and "Sanitizer" not in result.stderr


@unittest.skipUnless(TOOLS, "toolchain unavailable")
class NativeFuzzTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.exe = build_probe(ROOT / "build/native-debug", True, "p6_fuzz_probe")
        source = (ROOT / "tests/fixtures/microrom/vsync.s").read_text(encoding="utf-8")
        cls.tmp = tempfile.TemporaryDirectory(prefix="p6-fuzz-")
        cls.rom = Path(cls.tmp.name) / "vsync.rom"
        cls.rom.write_bytes(builder.build(source))

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def run_target(self, target, seed, iterations, extra_env=None):
        args = [target] + ([str(self.rom)] if target in ("firmware", "media") else []) + [str(seed), str(iterations)]
        return subprocess.run([str(self.exe), *args], capture_output=True, text=True, timeout=900,
                              env={**ENV, **(extra_env or {})})

    def test_the_recorded_seeds_run_clean(self):
        for run in CORPUS["runs"]:
            with self.subTest(**{k: run[k] for k in ("target", "seed", "iterations")}):
                result = self.run_target(run["target"], run["seed"], run["iterations"])
                self.assertTrue(clean(result), result.stderr[-600:])
                self.assertTrue(result.stdout.strip())

    def test_a_run_is_reproducible(self):
        for target in ("config", "blob"):
            first = self.run_target(target, 7, 3000)
            self.assertTrue(clean(first), first.stderr[-400:])
            self.assertEqual(first.stdout, self.run_target(target, 7, 3000).stdout)

    def test_the_config_fuzzer_reaches_accepted_and_rejected_inputs(self):
        result = self.run_target("config", 3, 5000)
        codes = {int(line.split(":")[0].rsplit(" ", 1)[1]) for line in result.stdout.splitlines()}
        self.assertLessEqual({0, 1, 4}, codes)      # OK, BAD_ARGUMENT, UNSUPPORTED

    def test_the_firmware_fuzzer_loads_some_images_and_rejects_most(self):
        result = self.run_target("firmware", 5, 60)
        self.assertTrue(clean(result), result.stderr[-400:])
        text = result.stdout
        self.assertIn("firmware result 0:", text)
        self.assertIn("firmware result 6:", text)

    def test_the_media_fuzzer_loads_valid_lists_and_refuses_the_rest_like_the_oracle(self):
        result = self.run_target("media", 3, 40)
        self.assertTrue(clean(result), result.stderr[-600:])
        text = result.stdout
        self.assertIn("media result 0:", text)           # some lists load
        self.assertIn("media result 6:", text)           # WRONG_MEDIA: size, header or identity
        self.assertIn("media result 2000:", text)        # some events are queued
        self.assertRegex(text, r"media result 200[1-9]")  # and some refused

    def test_the_harness_can_fail(self):
        # With the oracle inverted on purpose the same run must be reported as a failure.
        result = self.run_target("config", 1, 200, {"P6_FUZZ_SELFTEST": "1"})
        self.assertEqual(result.returncode, 1)
        self.assertIn("FUZZ FAILURE", result.stderr)


STATE_KINDS = ("truncate", "extend", "flip", "flip-resealed", "run", "extreme", "chunk-header", "header-field", "identity", "swap-chunks", "drop-dup-chunk", "foreign", "null-buffer",
               "length-forged", "manifest-prefix")


@unittest.skipUnless(TOOLS, "toolchain unavailable")
class StateFuzzTests(unittest.TestCase):
    """FUZZ01 for the native state: truncated, tampered, extended, spliced and other-build/ROM/config/media states and forged manifest prefixes, loaded into a running image with a disk passed to p6_load, with one appended after it and without one."""

    # Any single allocation above this is a report: the engine's biggest buffers are well under it, and a state of at most ~1.3 MB cannot ask for more honestly.
    ENV = {**ENV, "ASAN_OPTIONS": "detect_leaks=0:max_allocation_size_mb=24"}

    @classmethod
    def setUpClass(cls):
        cls.exe = build_probe(ROOT / "build/native-debug", True, "p6_fuzz_probe")
        source = (ROOT / "tests/fixtures/microrom/vsync.s").read_text(encoding="utf-8")
        cls.tmp = tempfile.TemporaryDirectory(prefix="p6-fuzz-state-")
        cls.rom = Path(cls.tmp.name) / "vsync.rom"
        cls.rom.write_bytes(builder.build(source))

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def run_state(self, seed, iterations, extra_env=None, machine=None):
        return subprocess.run([str(self.exe), "state", str(self.rom), str(seed), str(iterations), *([machine] if machine else [])], capture_output=True, text=True, timeout=3600,
                              env={**self.ENV, **(extra_env or {})})

    @staticmethod
    def kinds(text):
        found = {}
        for line in text.splitlines():
            match = re.match(r"^state kind (\S+): accepted (\d+) refused (\d+)$", line)
            if match:
                found[match[1]] = (int(match[2]), int(match[3]))
        return found

    def test_the_recorded_state_seeds_run_clean_and_reach_every_mutation_and_every_outcome(self):
        for run in CORPUS["state_runs"]:
            with self.subTest(seed=run["seed"], iterations=run["iterations"], machine=run.get("machine")):
                result = self.run_state(run["seed"], run["iterations"], machine=run.get("machine"))      # T23b: the A600 has its own states (1 MiB Chip, an empty clock chunk)
                self.assertTrue(clean(result), result.stderr[-1200:])
                text = result.stdout
                kinds = self.kinds(text)
                self.assertEqual(sorted(STATE_KINDS), sorted(kinds))
                for kind, (accepted, refused) in kinds.items():
                    self.assertGreater(accepted + refused, 0, kind)
                results = {int(m[1]): int(m[2]) for m in re.finditer(r"^state result (\d+): (\d+)$", text, re.M)}
                self.assertLessEqual({0, 1, 9, 10}, set(results), results)         # accepted, null buffer, corrupt, incompatible
                self.assertFalse(set(results) - {0, 1, 9, 10}, f"a result outside the documented set: {results}")
                self.assertRegex(text, r"state unit checks [1-9]\d*, accepted states that ran [1-9]\d*, resyncs [1-9]\d*")

    def test_a_state_run_is_reproducible(self):
        first = self.run_state(5, 30)
        self.assertTrue(clean(first), first.stderr[-600:])
        self.assertEqual(first.stdout, self.run_state(5, 30).stdout)

    def test_the_state_harness_can_fail(self):
        # With the comparison of the machine after a refusal spoiled on purpose, the same run has to be reported as a failure.
        result = self.run_state(1, 60, {"P6_FUZZ_SELFTEST": "1"})
        self.assertEqual(result.returncode, 1)
        self.assertIn("FUZZ FAILURE", result.stderr)
        self.assertIn("a refused state changed the machine", result.stderr)


@unittest.skipUnless(TOOLS, "toolchain unavailable")
class StateWritableFuzzTests(StateFuzzTests):
    """FUZZ01 for the write overlay of the floppies (T27): the same mutated states, but of a session with P6_CONFIG_FLOPPY_WRITES whose good states carry the sectors a guest wrote
    (so the edits reach the overlay's count, media, sectors and data, and the chunk length that holds them), and p6_media_export / p6_media_write_status called with arguments from
    outside (null buffers, capacities around the size of a disk, indexes outside the manifest, forged struct sizes), checked against the merged image and for writes past the capacity."""

    def run_state(self, seed, iterations, extra_env=None, machine=None):
        return subprocess.run([str(self.exe), "state-writable", str(self.rom), str(seed), str(iterations), *([machine] if machine else [])], capture_output=True, text=True,
                              timeout=3600, env={**self.ENV, **(extra_env or {})})

    def test_the_recorded_state_seeds_run_clean_and_reach_every_mutation_and_every_outcome(self):
        for run in CORPUS["state_writable_runs"]:
            with self.subTest(seed=run["seed"], iterations=run["iterations"]):
                result = self.run_state(run["seed"], run["iterations"])
                self.assertTrue(clean(result), result.stderr[-1200:])
                kinds = self.kinds(result.stdout)
                self.assertEqual(sorted(STATE_KINDS), sorted(kinds))
                results = {int(m[1]): int(m[2]) for m in re.finditer(r"^state result (\d+): (\d+)$", result.stdout, re.M)}
                self.assertLessEqual({0, 1, 9, 10}, set(results), results)
                self.assertFalse(set(results) - {0, 1, 9, 10}, f"a result outside the documented set: {results}")

    test_the_state_harness_can_fail = StateFuzzTests.test_the_state_harness_can_fail


@unittest.skipUnless(TOOLS, "toolchain unavailable")
class HardFileFuzzTests(unittest.TestCase):
    """FUZZ01 for the hard files of the UAE controller (T29d): hostile Rigid Disk Blocks. Disks with an RDB, partitions and a file system in LoadSegBlocks, a plain partition and a disk with no
    partition table are mutated (fields set to boundary values, links sent elsewhere, blocks swapped, copied, zeroed or cut off, sizes and checksums forged or put right again so that the
    parser gets past the checksum), given to p6_load, and a micro-ROM asks the filesystem for the partitions, which reads all of it (pt_rdsk, rdb_mount, the FSHD and LSEG chains). The
    engine is built with ASan and UBSan, no recover, and any single allocation above 24 MiB is a report (the images are 0.5 and 2 MiB; the file system of an RDB is capped at 256 KiB).
    The state of the machine that parsed the disk is saved and loaded back and the machine runs a unit more."""

    ENV = {**ENV, "ASAN_OPTIONS": "detect_leaks=0:max_allocation_size_mb=24"}

    @classmethod
    def setUpClass(cls):
        from tests.hdf import hdf_image, hdf_rom
        cls.exe = build_probe(ROOT / "build/native-debug", True, "p6_fuzz_probe")
        cls.tmp = tempfile.TemporaryDirectory(prefix="p6-fuzz-hdf-")
        directory = Path(cls.tmp.name)
        cls.rom = directory / "mount.rom"
        cls.rom.write_bytes(hdf_rom.mount_rom())
        images = {"handler": hdf_image.rdb_image(cylinders=16, handler_blocks=3), "rdb": hdf_image.rdb_image(cylinders=16), "big": hdf_image.rdb_image(cylinders=64, handler_blocks=1),
                  "loop": hdf_image.rdb_image(cylinders=16, fshd_loop=True), "plain": hdf_image.plain_partition(256)}
        cls.images = []
        for name, data in images.items():
            path = directory / f"{name}.hdf"
            path.write_bytes(data)
            cls.images.append(str(path))

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def run_target(self, seed, iterations, extra_env=None):
        return subprocess.run([str(self.exe), "hardfile", str(self.rom), str(seed), str(iterations), *self.images], capture_output=True, text=True, timeout=3600,
                              env={**self.ENV, **(extra_env or {})})

    def test_the_recorded_seeds_run_clean_and_reach_loaded_and_refused_disks_and_every_kind_of_answer(self):
        for run in CORPUS["hardfile_runs"]:
            with self.subTest(seed=run["seed"], iterations=run["iterations"]):
                result = self.run_target(run["seed"], run["iterations"])
                self.assertTrue(clean(result), result.stderr[-1200:])
                text = result.stdout
                results = {int(m[1]): int(m[2]) for m in re.finditer(r"^hardfile result (-?\d+): (\d+)$", text, re.M)}
                self.assertIn(0, results, "some disks load")
                self.assertTrue(set(results) - {0}, "and some are refused (not whole blocks)")
                answers = {int(m[1]): int(m[2]) for m in re.finditer(r"^hardfile mount answer (-?\d+): (\d+)$", text, re.M)}
                self.assertLessEqual({-2, 2}, set(answers), f"a partition mounts with its file system from the RDB, and the list ends: {answers}")
                self.assertTrue(set(answers) - {-2, 2}, f"and some mutations reach the other answers: {answers}")
                loaded = re.search(r"^hardfile loaded (\d+) states (\d+)$", text, re.M)
                self.assertEqual(loaded[1], loaded[2], "every loaded disk gave a state that loaded back")

    def test_a_run_is_reproducible(self):
        first = self.run_target(3, 60)
        self.assertTrue(clean(first), first.stderr[-600:])
        self.assertEqual(first.stdout, self.run_target(3, 60).stdout)

    def test_the_harness_can_fail(self):
        # A target that is asked for nothing to mutate refuses (a harness that cannot fail would pass on an empty pool).
        result = subprocess.run([str(self.exe), "hardfile", str(self.rom), "1", "5"], capture_output=True, text=True, timeout=600, env=self.ENV)
        self.assertNotEqual(result.returncode, 0, "no image given: the target refuses it")


@unittest.skipUnless(TOOLS, "toolchain unavailable")
class IdeHardFileFuzzTests(unittest.TestCase):
    """FUZZ01 for the hard files on the IDE port of Gayle (T56): the same hostile Rigid Disk Blocks as the UAE controller's, on the A600 with p6_config.hdf_controller = IDE. The engine
    parses them in hdf_hd_open (the geometry out of the RDB, the RDB it makes for a bare partition); the guest is the micro-ROM of tests/hdf/ide_rom.py that identifies the drive and reads and
    writes blocks through the registers of Gayle, with the interrupt enabled. Built with ASan and UBSan, no recover; the state of the machine that parsed the disk is saved and loaded back and
    the machine runs a unit more. Also: a guest that pokes at the registers at random (ide_rom.chaos_program) on a good disk, with the same state round trip."""

    ENV = {**ENV, "ASAN_OPTIONS": "detect_leaks=0:max_allocation_size_mb=24"}

    @classmethod
    def setUpClass(cls):
        from tests.hdf import hdf_image, ide_rom
        cls.exe = build_probe(ROOT / "build/native-debug", True, "p6_fuzz_probe")
        cls.tmp = tempfile.TemporaryDirectory(prefix="p6-fuzz-ide-")
        directory = Path(cls.tmp.name)
        cls.rom = directory / "ide.rom"
        cls.rom.write_bytes(ide_rom.rom())
        cls.chaos = directory / "chaos.rom"
        cls.chaos.write_bytes(ide_rom.chaos_rom(0x1234ABCD))
        images = {"handler": hdf_image.rdb_image(cylinders=16, handler_blocks=3), "rdb": hdf_image.rdb_image(cylinders=16), "big": hdf_image.rdb_image(cylinders=64, handler_blocks=1),
                  "loop": hdf_image.rdb_image(cylinders=16, fshd_loop=True), "plain": hdf_image.plain_partition(256)}
        cls.images = []
        for name, data in images.items():
            path = directory / f"{name}.hdf"
            path.write_bytes(data)
            cls.images.append(str(path))

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def run_target(self, seed, iterations, rom=None):
        return subprocess.run([str(self.exe), "hardfile-ide", str(rom or self.rom), str(seed), str(iterations), *self.images], capture_output=True, text=True, timeout=3600, env=self.ENV)

    def test_the_recorded_seeds_run_clean_and_reach_loaded_and_refused_disks(self):
        for run in CORPUS["hardfile_runs"]:
            with self.subTest(seed=run["seed"], iterations=run["iterations"]):
                result = self.run_target(run["seed"], run["iterations"])
                self.assertTrue(clean(result), result.stderr[-1200:])
                results = {int(m[1]): int(m[2]) for m in re.finditer(r"^hardfile result (-?\d+): (\d+)$", result.stdout, re.M)}
                self.assertIn(0, results, "some disks load")
                self.assertTrue(set(results) - {0}, "and some are refused (not whole blocks)")
                loaded = re.search(r"^hardfile loaded (\d+) states (\d+)$", result.stdout, re.M)
                self.assertEqual(loaded[1], loaded[2], "every loaded disk gave a state that loaded back")

    def test_a_guest_that_pokes_at_random_on_hostile_disks_runs_clean(self):
        for seed in (1, 2):
            with self.subTest(seed=seed):
                result = self.run_target(seed, 30, rom=self.chaos)
                self.assertTrue(clean(result), result.stderr[-1200:])
                loaded = re.search(r"^hardfile loaded (\d+) states (\d+)$", result.stdout, re.M)
                self.assertEqual(loaded[1], loaded[2])

    def test_a_run_is_reproducible(self):
        first = self.run_target(3, 40)
        self.assertTrue(clean(first), first.stderr[-600:])
        self.assertEqual(first.stdout, self.run_target(3, 40).stdout)


CONTENT_KINDS = ("random-bytes", "valid-flipped", "long-line", "path-length-boundary", "nul", "bom-middle", "cr-only", "bar", "savedisk", "many-entries",
                 "huge-path", "path-forms", "utf8-invalid", "whitespace", "huge-file")
HDZ_KINDS = ("valid-hdz", "valid-hdf", "hdz-flipped", "hdz-truncated", "hdz-trailing", "hdz-bad-crc", "hdz-bad-size", "wrong-size", "over-cap", "bomb", "random-gzip",
             "random-bytes", "not-gzip", "gzip-named-hdf", "valid-zip", "zip-flipped", "zip-two-hdf", "zip-no-hdf")
VOLUME_KINDS = ("tree-valid", "tree-hostile", "lha-valid", "lha-flipped", "lha-truncated", "lha-random-method", "lha-big-claim", "lha-hostile-names", "zip-valid", "zip-flipped",
                "zip-hostile-names", "zip-big-claim", "zip-truncated")
ARCHIVE_KINDS = ("clean", "flip", "flip-fixed", "truncated", "cut-fixed", "member", "random-lzma", "big-claim", "manifest-flip", "manifest-random", "manifest-deep", "order")
MODEL_KINDS = ("clean", "multi", "near", "bare", "hostile", "explicit", "off", "order")
# Every tag of docs/options.md that picks a model must be the one that decided at least once, and every word and region too (the four tags of machines this core does not have only warn).
MODEL_DECIDERS = ("(A1200OG)", "(A1200NF)", "(A1200)", "CD32", "(CD32NF)", "(CD32FR)", "(A600)", "(A500+)", "(A500PLUS)", "(A500OG)", "(512K)", "(512KB)", "(A500)", "(A2000OG)", "(A2000)",
                  "(A1000)", "word", "region")
FIRMWARE_KINDS = ("clean", "clean-long-key", "plain", "near-tag", "no-key", "wrong-key", "short", "huge", "flipped")
OPTION_KINDS = ("exact", "empty", "padded", "case", "suffix", "prefix", "zeros", "other-value", "non-ascii", "long", "random-bytes", "control",
                "other-options-value", "number-forms", "no-value")


@unittest.skipUnless(TOOLS, "toolchain unavailable")
def write_archive_seeds(directory):
    """The seed archives of the `archive` target (T59): the same three disks in a 7z (LZMA2 solid with an encoded header, LZMA with a plain header, Copy), a ZIP (deflated and stored) and an
    RP9 package, with the disks they have to give, in order (index.txt, and NAME.expect with 'member crc32' lines)."""
    sys.path.insert(0, str(ROOT / "tests/libretro-host"))
    import archives as a
    from tests.media import containers as c
    directory.mkdir()
    disks = [(f"Game (Disk {i}).adf", c.adf(f"fuzz disk {i}")) for i in (1, 2, 3)]
    crc = lambda data: zlib.crc32(data) & 0xFFFFFFFF
    reversed_disks = list(reversed(disks))
    rp9 = a.rp9_file([(name, 3 - i, None) for i, (name, _) in enumerate(disks)], disks)
    seeds = {
        "set-lzma2.7z": (a.sevenzip_file(reversed_disks + [("readme.txt", b"hi"), ("shots/", b"")]), disks),
        "set-lzma.7z": (a.sevenzip_file(disks, method="lzma", solid=False, encode_header=False), disks),
        "set-copy.7z": (a.sevenzip_file(reversed_disks, method="copy", encode_header=False), disks),
        "set.zip": (c.zip_file(reversed_disks + [("readme.txt", b"hi")]), disks),
        "set-stored.zip": (c.zip_file(disks, 0), disks),
        "set.rp9": (rp9, list(reversed(disks))),
    }
    for name, (data, expected) in seeds.items():
        (directory / name).write_bytes(data)
        (directory / (name + ".expect")).write_text("".join(f"{member} {crc(body):08x}\n" for member, body in expected))
    (directory / "index.txt").write_text("".join(f"{name}\n" for name in seeds))


class ContentFuzzTests(unittest.TestCase):
    """FUZZ01 for the libretro content (T17): the M3U parser, the file reader over a VFS that lies, and the option values, with the adapter's headers
    compiled with ASan and UBSan next to no engine. Any single allocation above 4 MB is a report: a playlist is at most 64 KB."""

    ENV = {**ENV, "ASAN_OPTIONS": "detect_leaks=0:max_allocation_size_mb=4"}

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="p6-fuzz-content-")
        cls.exe = Path(cls.tmp.name) / "fuzz_content"
        command = ["clang++", "-std=c++17", "-O1", "-g", "-Wall", "-Wextra", "-Werror", "-fsanitize=address,undefined", "-fno-sanitize-recover=all",
                   "-I" + str(ROOT / "adapters/libretro"), "-I" + str(ROOT / "bridge/include"), "-I" + str(ROOT / "references/libretro-api/include"),
                   str(ROOT / "tests/fuzz/fuzz_content.cpp"), "-o", str(cls.exe)]
        built = subprocess.run(command, capture_output=True, text=True, timeout=600)
        if built.returncode:
            raise RuntimeError(built.stderr[-2000:])
        cls.schema = generate_options.load_schema()
        cls.seeds = Path(cls.tmp.name) / "archive-seeds"
        write_archive_seeds(cls.seeds)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def run_target(self, target, seed, iterations, extra_env=None):
        extra = [str(self.seeds)] if target == "archive" else []
        return subprocess.run([str(self.exe), target, str(seed), str(iterations), *extra], capture_output=True, text=True, timeout=900,
                              env={**self.ENV, **(extra_env or {})})

    @staticmethod
    def histogram(text, prefix):
        found = {}
        for line in text.splitlines():
            match = re.match(rf"^{prefix} kind (\S+): accepted (\d+) refused (\d+)$", line)
            if match:
                found[match[1]] = (int(match[2]), int(match[3]))
        return found

    def test_the_recorded_seeds_run_clean_and_reach_every_mutation_and_outcome(self):
        for run in CORPUS["content_runs"]:
            with self.subTest(**run):
                result = self.run_target(run["target"], run["seed"], run["iterations"])
                self.assertTrue(clean(result), result.stderr[-1200:])
                text = result.stdout
                if run["target"] == "m3u":
                    kinds = self.histogram(text, "m3u")
                    self.assertEqual(sorted(CONTENT_KINDS), sorted(kinds))
                    self.assertGreater(sum(a for a, _ in kinds.values()), 0)
                    self.assertGreater(sum(r for _, r in kinds.values()), 0)
                    for reason in ("a NUL byte is not text", "more than the cap", "the entry has no file name", "the path is too long"):
                        self.assertIn(f'm3u refusal "{reason}"', text)
                elif run["target"] == "hdz":
                    kinds = self.histogram(text, "hdz")
                    self.assertEqual(sorted(HDZ_KINDS), sorted(kinds))
                    for kind in ("valid-hdz", "valid-hdf", "valid-zip"):
                        self.assertGreater(kinds[kind][0], 0)
                        self.assertEqual(kinds[kind][1], 0, kind)          # a valid hard file is never refused
                    for kind in ("hdz-truncated", "hdz-trailing", "hdz-bad-crc", "hdz-bad-size", "wrong-size", "over-cap", "bomb", "not-gzip", "zip-two-hdf", "zip-no-hdf"):
                        self.assertGreater(kinds[kind][1], 0, kind)
                        self.assertEqual(kinds[kind][0], 0, kind)          # and these never are accepted
                    for reason in ("the gzip stream decompresses to more than it declares or than the core reads (more than the core reads)", "the hard file is larger than the  bytes the core reads",
                                   "a hard file must be a whole number of -byte blocks, at least one (this one is  bytes)", "an .hdz must be a gzip file (or a zip file with one .hdf) of a hard file", "the ZIP file holds  hard files; the core loads one per file, so list them in an .m3u playlist", "the ZIP file holds no hard file (.hdf)"):
                        self.assertIn(f'hdz refusal "{reason[:64]}"', text)
                elif run["target"] == "volume":
                    kinds = self.histogram(text, "volume")
                    self.assertEqual(sorted(VOLUME_KINDS), sorted(kinds))
                    for kind in ("lha-valid", "zip-valid"):
                        self.assertGreater(kinds[kind][0], 0)
                        self.assertEqual(kinds[kind][1], 0, kind)          # a valid source is never refused
                    for kind in ("lha-big-claim", "lha-hostile-names", "zip-big-claim", "zip-hostile-names", "zip-truncated"):
                        self.assertGreater(kinds[kind][1], 0, kind)
                        self.assertEqual(kinds[kind][0], 0, kind)          # and these never are accepted
                    for kind in ("tree-valid", "tree-hostile", "lha-flipped", "lha-truncated", "lha-random-method", "zip-flipped"):
                        self.assertGreater(kinds[kind][1], 0, kind)          # the mutations are refused at least sometimes
                    self.assertGreater(kinds["tree-hostile"][0], 0)
                    for reason in ("CRC of a member", "unsafe member name", "member larger than the limit", "packed data beyond the end of the archive", "invalid Huffman code",
                                   "over-subscribed Huffman code", "a path is both a file and a directory", "the name   cannot be an AmigaDOS name", "the ZIP file has an unsafe name",
                                   "the files of the ZIP file together are larger th"):
                        self.assertIn(f'volume refusal "{reason}', text)
                elif run["target"] == "archive":
                    kinds = self.histogram(text, "archive")
                    self.assertEqual(sorted(ARCHIVE_KINDS), sorted(kinds))
                    for kind in ("clean", "order"):
                        self.assertGreater(kinds[kind][0], 0)
                        self.assertEqual(kinds[kind][1], 0, kind)          # a clean archive is never refused
                    for kind in ("big-claim", "manifest-random", "manifest-deep"):
                        self.assertGreater(kinds[kind][1], 0, kind)
                    self.assertEqual(kinds["big-claim"][0], 0)             # a claim beyond the cap is never taken
                    for kind in ("flip", "flip-fixed", "truncated", "cut-fixed", "member", "random-lzma", "manifest-flip"):
                        self.assertGreater(kinds[kind][1], 0, kind)          # the mutations are refused at least sometimes
                    for kind in ("member", "manifest-flip", "manifest-random"):
                        self.assertGreater(kinds[kind][0], 0, kind)          # and accepted, so that the oracles had something to check
                    for reason in ("the LZMA data is corrupt or truncated", "unpacks to more than the core reads", "is not usable XML", "has no member named", "truncated or damaged",
                                   "entry is corrupt", "archive is damaged"):
                        self.assertIn(reason, text)
                elif run["target"] == "model":
                    kinds = self.histogram(text, "model")
                    self.assertEqual(sorted(MODEL_KINDS), sorted(kinds))
                    for kind in ("clean", "multi", "near", "bare", "hostile", "order"):
                        self.assertGreater(kinds[kind][0], 0, kind)          # a tag decided
                        self.assertGreater(kinds[kind][1], 0, kind)          # and not always
                    for kind in ("explicit", "off"):
                        self.assertEqual(kinds[kind][0], 0, kind)            # a choice made by hand or the tags off: no tag ever decides
                    for tag in MODEL_DECIDERS:
                        self.assertRegex(text, rf'(?m)^model decided "{re.escape(tag)}": [1-9]', tag)
                    self.assertRegex(text, r'(?m)^model warned "unsupported-model-tag": [1-9]')
                elif run["target"] == "firmware":
                    kinds = self.histogram(text, "firmware")
                    self.assertEqual(sorted(FIRMWARE_KINDS), sorted(kinds))
                    for kind in ("clean", "clean-long-key", "huge"):
                        self.assertGreater(kinds[kind][0], 0, kind)
                        self.assertEqual(kinds[kind][1], 0, kind)          # an image encrypted with its key is always decrypted
                    for kind in ("plain", "near-tag", "no-key", "short"):
                        self.assertGreater(kinds[kind][1], 0, kind)
                    for kind in ("plain", "near-tag", "no-key"):
                        self.assertEqual(kinds[kind][0], 0, kind)          # and these never come out as a ROM
                    for result in ("Decrypted", "NeedsKey", "WrongKey", "NotEncrypted"):
                        self.assertRegex(text, rf"(?m)^firmware result {result}: [1-9]", result)
                elif run["target"] == "vfs":
                    for outcome in ("Ok", "CannotOpen", "TooLarge", "ReadFailed"):
                        self.assertIn(f"vfs result {outcome}:", text)
                    self.assertNotIn("OutOfMemory", text)
                else:
                    kinds = self.histogram(text, "options")
                    self.assertEqual(sorted(OPTION_KINDS), sorted(kinds))
                    self.assertGreater(kinds["exact"][0], 0)
                    self.assertEqual(kinds["exact"][1], 0)          # an exact name is never refused
                    for kind, (accepted, _) in kinds.items():
                        if kind not in ("exact", "no-value", "other-options-value"):
                            self.assertEqual(accepted, 0, kind)      # everything else is refused, whatever the mutation
                    # Only the value names of the schema were ever accepted, and each of them was.
                    allowed = {f"{option['libretro']['key']}={value['value']}" for option in self.schema["options"] if option["libretro"]
                               for value in option["values"]}
                    allowed |= {f"{option['libretro']['key']}={extra['value']}" for option in self.schema["options"] if option["libretro"] for extra in option["libretro"].get("extra_values", [])}      # T76: auto, PAL auto, NTSC auto
                    allowed |= {f"{selector['libretro']['key']}={value['value']}" for selector in self.schema["selectors"]
                                for value in (next(o for o in self.schema["options"] if o["id"] == selector["values_of"])["values"] if "values_of" in selector else selector["values"])}      # T76: the selectors
                    allowed |= {f"puae6_preset={name}" for name in ["none"] + [preset["id"] for preset in self.schema["presets"]]}      # T49: the preset is offered beside the options
                    accepted = {m[1] for m in re.finditer(r"^options accepted (.+): \d+$", text, re.M)}
                    self.assertEqual(accepted, allowed)

    def test_a_run_is_reproducible(self):
        for target in ("m3u", "options", "hdz", "volume", "archive", "model", "firmware"):
            first = self.run_target(target, 7, 500)
            self.assertTrue(clean(first), first.stderr[-400:])
            self.assertEqual(first.stdout, self.run_target(target, 7, 500).stdout)

    def test_the_content_harness_can_fail(self):
        # With each oracle spoiled on purpose the same run has to be reported as a failure.
        for target in ("m3u", "vfs", "options", "hdz", "volume", "archive", "model", "firmware"):
            with self.subTest(target=target):
                result = self.run_target(target, 1, 200, {"P6_FUZZ_SELFTEST": "1"})
                self.assertEqual(result.returncode, 1)
                self.assertIn("FUZZ FAILURE", result.stderr)


class ConfigNormalizeFuzzTests(unittest.TestCase):
    """FUZZ01 for tools/generate_options.py normalize_config (T17): random keys, values and types are normalized exactly or refused with a ConfigError that
    names every offending key; a partial configuration is never returned and the request is never modified."""

    HOSTILE = [None, True, False, 0, 1, -1, 2, 44100, 48000, 2**32, 2**63, 2**64 - 1, 2**64, -2**63, -2**63 - 1, 253402300799, 253402300800, -62135596800,
               1.0, 1.5, float("nan"), float("inf"), "", " ", "PAL", "pal", "PAL ", " PAL", "NTSC", "pixel", "4:3", "44100", "48000", "048000", "48000x",
               "22050", "A500", "A600", "a500", "a600", "A600 ", " A600", "A1200", "a1200", "A1200 ", " A1200", "A1200\n", "A1200OG", "A1200\x00", "A12000", "A 1200", "A4000", "A500+", "A500PLUS", "A600HD", "CD32", "1", "-1", "-0", "+5", "5 ", " 5", "5\n", "5_0", "0x10", "1e3", "\u0663", "\uff11\uff12", "9" * 20, "9" * 64, "9" * 65, "9" * 5000,
               "-" + "9" * 70, "0" * 70 + "1", "\x00", "PAL\x00", "\u00e9", [], {}, [1], ["PAL"], ("PAL",), {"a": 1}, b"PAL", b"", object]
    KEYS_OTHER = ["", " ", "video_standard ", "Video_Standard", "machine ", "Machine", "puae_model", "puae6_machine", "cpu_model", "chipmem_size", "kickstart_rom_file", "uae_config_file", "controller_port1",
                  "turbo", "puae6_video_standard", "\u00e9", "a" * 300, 1, 2.5, None, True, (1, 2), b"video_standard", frozenset()]

    @staticmethod
    def expected(option, raw):
        """The normalized value of `raw`, or None for a refusal, from the schema alone (not from the function under test)."""
        if option["type"] == "enum":
            return raw if type(raw) is str and raw in [v["value"] for v in option["values"]] else None
        if type(raw) is str and 1 <= len(raw.lstrip("-")) <= 64 and len(raw) - len(raw.lstrip("-")) <= 1 and all(c in "0123456789" for c in raw.lstrip("-")):
            raw = int(raw)
        if type(raw) is int and option["min"] <= raw <= option["max"]:
            return raw
        return None

    def test_random_requests_are_normalized_exactly_or_refused_naming_every_bad_key(self):
        schema = generate_options.load_schema()
        options = {o["id"]: o for o in schema["options"]}
        known = list(options) + [u["key"] for u in schema["unsupported"]] + [r["id"] for r in schema["reserved"]]
        for run in CORPUS["config_runs"]:
            rng = random.Random(run["seed"])
            accepted = refused = 0
            for iteration in range(run["iterations"]):
                requested = {}
                for _ in range(rng.randrange(0, 6)):
                    pool = rng.random()
                    key = rng.choice(list(options)) if pool < 0.6 else rng.choice(known) if pool < 0.8 else rng.choice(self.KEYS_OTHER)
                    if isinstance(key, str) and key in options and rng.random() < 0.55:
                        valid = options[key]["values"][rng.randrange(len(options[key]["values"]))]["value"] if options[key]["type"] == "enum" \
                            else rng.choice([options[key]["min"], options[key]["max"], options[key]["default"], str(options[key]["default"]), 42])
                        requested[key] = valid
                    else:
                        requested[key] = rng.choice(self.HOSTILE)
                before = copy.deepcopy(requested)
                bad = [key for key in requested if not (isinstance(key, str) and key in options) or
                       self.expected(options[key], requested[key]) is None]
                try:
                    result = generate_options.normalize_config(schema, requested)
                except generate_options.ConfigError as error:
                    refused += 1
                    self.assertTrue(bad, f"seed {run['seed']} iteration {iteration}: a valid request was refused: {requested!r}: {error}")
                    for key in bad:
                        self.assertIn(f"option {key}", str(error), f"seed {run['seed']} iteration {iteration}: the refusal does not name {key!r}")
                except Exception as error:      # anything but a ConfigError is a defect: it names nothing and may escape a caller that handles ValueError
                    self.fail(f"seed {run['seed']} iteration {iteration}: {type(error).__name__}: {error!s:.200} for {requested!r:.200}")
                else:
                    accepted += 1
                    self.assertFalse(bad, f"seed {run['seed']} iteration {iteration}: accepted {requested!r}")
                    self.assertEqual(list(result), list(options))            # every option, in schema order, never a partial configuration
                    for name, option in options.items():
                        self.assertEqual(result[name], self.expected(option, requested[name]) if name in requested else option["default"])
                    generate_options.to_p6_config(schema, result)            # and the ABI numbers can always be derived
                self.assertEqual(requested, before)
            self.assertGreater(accepted, run["iterations"] // 50)
            self.assertGreater(refused, run["iterations"] // 2)

    def test_the_a1200_is_a_machine_and_its_near_misses_are_not(self):
        # T24e: A1200 is accepted exactly; every other spelling of it, PUAE5's A1200OG and a number are refused naming the key and the three machines.
        schema = generate_options.load_schema()
        self.assertEqual(generate_options.normalize_config(schema, {"machine": "A1200"})["machine"], "A1200")
        self.assertEqual(generate_options.to_p6_config(schema, generate_options.normalize_config(schema, {"machine": "A1200"}))["machine"], 3)
        for raw in ("a1200", "A1200 ", " A1200", "A1200\n", "A1200OG", "A1200\x00", "A12000", "A 1200", "1200", "3", 3, 1200, True, None, ["A1200"], b"A1200"):
            with self.subTest(raw=raw), self.assertRaises(generate_options.ConfigError) as caught:
                generate_options.normalize_config(schema, {"machine": raw})
            self.assertIn("option machine", str(caught.exception))
            self.assertIn("allowed: A500, A600, A1200", str(caught.exception))

    def test_a_request_that_is_not_a_dict_is_a_config_error(self):
        schema = generate_options.load_schema()
        for requested in (None, [], ["video_standard"], "video_standard", 7, 1.5, (("video_standard", "PAL"),), object()):
            with self.subTest(requested=type(requested).__name__), self.assertRaises(generate_options.ConfigError):
                generate_options.normalize_config(schema, requested)


def mutate(value, rng, depth=0):
    """A structural or scalar mutation of a JSON value; always returns a new value."""
    if isinstance(value, dict) and value and rng.random() < 0.7:
        key = rng.choice(sorted(value))
        result = dict(value)
        action = rng.random()
        if action < 0.15:
            del result[key]
        elif action < 0.25:
            result[key + "x"] = result[key]
        else:
            result[key] = mutate(value[key], rng, depth + 1)
        return result
    if isinstance(value, list) and value and rng.random() < 0.7:
        index = rng.randrange(len(value))
        result = list(value)
        action = rng.random()
        if action < 0.2:
            del result[index]
        elif action < 0.4:
            result.insert(index, copy.deepcopy(value[index]))
        else:
            result[index] = mutate(value[index], rng, depth + 1)
        return result
    return rng.choice([None, True, False, 0, 1, -1, 2**63, 2**64, -2**63 - 1, 1.5, float("nan"), "", "0" * 64, "A" * 32,
                       "zz", [], {}, [1], {"a": 1}, 524288, 4294967296, value])


class ScenarioFuzzTests(unittest.TestCase):
    def test_mutated_scenarios_are_accepted_or_rejected_with_a_value_error(self):
        seeds = sorted((ROOT / "tests/scenarios").rglob("*.json"))
        seeds = [p for p in seeds if p.name == "counter68k.json" or p.name.startswith("display-")]
        self.assertGreaterEqual(len(seeds), 2)
        originals = [load_json(p) for p in seeds]
        for run in CORPUS["scenario_runs"]:
            rng = random.Random(run["seed"])
            accepted = rejected = 0
            for iteration in range(run["iterations"]):
                candidate = copy.deepcopy(rng.choice(originals))
                for _ in range(1 + rng.randrange(3)):
                    candidate = mutate(candidate, rng)
                try:
                    validate_scenario(candidate)
                    accepted += 1
                except ValueError:
                    rejected += 1
                except Exception as error:      # anything but a ValueError is a defect of the validator
                    self.fail(f"seed {run['seed']} iteration {iteration}: {type(error).__name__}: {error}")
            self.assertGreater(rejected, run["iterations"] // 2)
            self.assertGreater(accepted, 0)

    def test_broken_json_text_is_rejected_cleanly(self):
        rng = random.Random(11)
        text = (ROOT / "tests/scenarios/counter68k.json").read_bytes()
        with tempfile.TemporaryDirectory(prefix="p6-json-fuzz-") as tmp:
            path = Path(tmp) / "broken.json"
            for _ in range(400):
                data = bytearray(text)
                for _ in range(1 + rng.randrange(4)):
                    data[rng.randrange(len(data))] = rng.randrange(256)
                if rng.random() < 0.3:
                    del data[rng.randrange(len(data)):]
                path.write_bytes(bytes(data))
                try:
                    validate_scenario(load_json(path))
                except (ValueError, UnicodeDecodeError):
                    pass


class CorpusScenarioFuzzTests(unittest.TestCase):
    """T32c: the scenario files of the differential corpus (tests/scenarios/corpus, and the ones of the machines, ECS and AGA next to them) as seeds of the mutation
    fuzz of the scenario validator: every unmutated seed is accepted, a mutated one is accepted or refused with a ValueError and never anything else, and the
    mutations are refused more often than not."""

    def test_every_scenario_of_the_corpus_is_accepted_and_its_mutations_are_accepted_or_refused_cleanly(self):
        seeds = sorted((ROOT / "tests/scenarios/corpus").glob("*.json")) + sorted((ROOT / "tests/scenarios/machines").glob("machine-*.json"))[:6]
        seeds = [p for p in seeds if p.name != "index.json"]
        self.assertGreaterEqual(len(seeds), 28)
        originals = [load_json(p) for p in seeds]
        for original, path in zip(originals, seeds):
            try:
                validate_scenario(original)
            except ValueError as error:
                self.fail(f"{path.name}: {error}")
        for run in CORPUS["scenario_runs"]:
            rng = random.Random(run["seed"] + 32)
            accepted = rejected = 0
            for iteration in range(run["iterations"]):
                candidate = copy.deepcopy(rng.choice(originals))
                for _ in range(1 + rng.randrange(3)):
                    candidate = mutate(candidate, rng)
                try:
                    validate_scenario(candidate)
                    accepted += 1
                except ValueError:
                    rejected += 1
                except Exception as error:
                    self.fail(f"seed {run['seed']} iteration {iteration}: {type(error).__name__}: {error}")
            self.assertGreater(rejected, run["iterations"] // 2)
            self.assertGreater(accepted, 0)


if __name__ == "__main__":
    unittest.main()
