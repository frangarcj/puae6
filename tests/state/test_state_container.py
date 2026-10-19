"""T15a: the native state container on the real engine (target p6_state_probe, --save-at / --load-at / --public-at).

Every chunk is read and applied by a load (the upstream bodies since T15i2), and the public p6_state_* functions refuse until the native state is released. What this proves is the
container: that the state is what the ADR says, that its bound is fixed, that RAM goes back to the saved unit in the same process and in a
new one, and above all STATE02: every bad state is refused with its code and the field it names, before the machine is touched, and the
machine goes on exactly as a run that never saw the attempt.
"""
import hashlib
import struct
import unittest

from tests.state import envelope as e
from tests.state.harness import ProbeCase, Run, SUBSYSTEMS, TOOLS, parse_chunks, reforge

CHIP = SLOW = 0x80000
RAM_BODY = 8 + CHIP + SLOW
CPU_BODY = 155          # P6CP (T15b1)
SCHED_BODY = 314        # P6SC (T15b2)
RTC_BODY = 31           # P6RT (T15c1)
CIA_BODY = 143          # P6CI (T15c2)
BRIDGE_BODY = 40        # P6BR (T15h) with no media event queued: 40 bytes plus 6 per queued event
INPUT_BODY = 170 + 4    # P6IN: empty keyboard ring, plus T70d's two parallel types and two button bytes
CHIPSET_BODY = 1702 + 24  # P6CH: CHIP/BLTX/BLIT/CHSL/SPRn/BEAM/COPR/BLTP, plus T70c's LPEN (16 + 8 header)
VIDEO_FIXED = 484       # P6VD (T15j): BPLX 292, DNSP (64) and GEOM (104), each with its tag and length, and then PIXL, the rows of the picture, whose length is the picture's
AUDIO_BODY = 496 + 168  # P6AU: four channels of 25 and AUDP (356), plus T74a's DCLK (160 + 8 header)
DISK_BODY = 441         # P6DK with no medium: four DSKn of 24, four absent DSDn, DISK 17 and DSKX 248, each with its tag and length


class StateContainerTests(ProbeCase):
    ROMS = {"vsync": ("vsync", None, None), "input_all": ("input_all", None, None),
            "vsync_slow": ("vsync", None, {"LEA $1000.W,A0": "MOVEA.L #$C00000,A0"})}      # the log in Slow RAM ($C00000), so that it is not constant

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.good_path = cls.dir / "good.state"
        cls.reference = cls.probe("vsync", "--units", "12", "--every", "1", "--save-at", "3", str(cls.good_path))
        cls.good = cls.good_path.read_bytes()

    def other_state(self, name, rom, *arguments):
        path = self.dir / name
        self.probe(rom, "--units", "4", "--save-at", "3", str(path), *arguments)
        return path.read_bytes()

    # -- what the state is -------------------------------------------------------------------------------------------------------------

    def test_a_state_is_laid_out_as_the_adr_says(self):
        blob = self.good
        magic, version, header_size, profile, flags = struct.unpack_from("<8sIIII", blob, 0)
        unit, ticks, payload_length, chunk_count, reserved0 = struct.unpack_from("<QQQII", blob, 24)
        self.assertEqual((e.MAGIC, e.FORMAT_VERSION, e.HEADER_SIZE, e.PROFILE_A500, 0), (magic, version, header_size, profile, flags))
        self.assertEqual((3, len(blob) - e.HEADER_SIZE, len(e.TAGS), 0), (unit, payload_length, chunk_count, reserved0))
        self.assertGreater(ticks, 0)
        self.assertEqual(e.domain_hash("p6/native-header/v1", blob[:304]), blob[304:336])
        self.assertEqual(e.domain_hash("p6/native-payload/v1", blob[e.HEADER_SIZE:]), blob[272:304])
        chunks, at = {}, e.HEADER_SIZE
        for tag in e.TAGS:
            seen, chunk_version, length = e.CHUNK_HEADER.unpack_from(blob, at)
            self.assertEqual(tag, seen)
            chunks[tag] = (chunk_version, blob[at + 16:at + 16 + length])
            at += 16 + length
        self.assertEqual(len(blob), at)
        self.assertEqual((1, RAM_BODY), (chunks[b"P6RM"][0], len(chunks[b"P6RM"][1])))
        self.assertEqual(struct.pack("<II", CHIP, SLOW), chunks[b"P6RM"][1][:8])
        self.assertEqual((1, CPU_BODY), (chunks[b"P6CP"][0], len(chunks[b"P6CP"][1])))
        self.assertEqual((1, SCHED_BODY), (chunks[b"P6SC"][0], len(chunks[b"P6SC"][1])))
        self.assertEqual((1, RTC_BODY), (chunks[b"P6RT"][0], len(chunks[b"P6RT"][1])))
        self.assertEqual((1, CIA_BODY), (chunks[b"P6CI"][0], len(chunks[b"P6CI"][1])))
        self.assertEqual((1, INPUT_BODY), (chunks[b"P6IN"][0], len(chunks[b"P6IN"][1])))
        self.assertEqual((1, BRIDGE_BODY), (chunks[b"P6BR"][0], len(chunks[b"P6BR"][1])))
        for tag, length in ((b"P6CH", CHIPSET_BODY), (b"P6AU", AUDIO_BODY), (b"P6DK", DISK_BODY)):
            self.assertEqual((1, length), (chunks[tag][0], len(chunks[tag][1])), tag)
        video = chunks[b"P6VD"][1]
        self.assertEqual(1, chunks[b"P6VD"][0])
        self.assertEqual(b"PIXL", video[VIDEO_FIXED:VIDEO_FIXED + 4], "the rows of the picture follow the fixed bodies of the video chunk")
        self.assertEqual(VIDEO_FIXED + 8 + struct.unpack_from("<I", video, VIDEO_FIXED + 4)[0], len(video), "and end it")

    def test_the_state_carries_the_ram_of_the_unit_it_was_taken_in_and_nothing_else_of_the_machine(self):
        saved = self.reference.saves[0]
        self.assertEqual(3, saved[0])
        self.assertEqual(hashlib.sha256(self.good).hexdigest(), saved[2])
        self.assertEqual(len(self.good), saved[1])

    def test_the_bound_is_fixed_by_the_image_and_covers_every_state(self):
        run = self.probe("vsync", "--units", "9", "--save-at", "2", str(self.dir / "b0"), "--save-at", "1", str(self.dir / "b1"),
                         "--save-at", "5", str(self.dir / "b5"), "--save-at", "9", str(self.dir / "b9"))
        bounds = {bound for _, _, _, bound in run.saves}
        self.assertEqual(1, len(bounds), run.saves)
        for unit, size, _, bound in run.saves:
            self.assertLessEqual(size, bound, f"unit {unit}")
        self.assertEqual(bounds, {bound for _, _, _, bound in self.probe("input_all", "--units", "2", "--ntsc", "--save-at", "1", str(self.dir / "b"))
                                  .saves}, "the bound is the profile's, not the program's or the standard's")

    # -- restoring ---------------------------------------------------------------------------------------------------------------------

    def restore(self, rom, state_path):
        run = self.probe(rom, "--units", "8", "--every", "1", "--load-at", "8", str(state_path), "--only", "ram")
        self.assertEqual([(8, 0, "")], run.loads)
        return run

    def test_ram_goes_back_to_the_saved_unit_in_a_new_process_and_in_the_same_one(self):
        saved_ram = self.reference.digests[(3, "ram")]
        self.assertNotEqual(saved_ram, self.reference.digests[(8, "ram")], "the program must change RAM between the units, or nothing is proven")
        new = self.restore("vsync", self.good_path)
        self.assertEqual(saved_ram, new.after[0]["ram"])
        self.assertEqual({s: new.digests[(8, s)] for s in SUBSYSTEMS if s != "ram"}, {s: new.after[0][s] for s in SUBSYSTEMS if s != "ram"},
                         "only the RAM chunk was applied: no other subsystem may move")
        same = self.probe("vsync", "--units", "8", "--every", "1", "--save-at", "3", str(self.dir / "same.state"), "--load-at", "8",
                          str(self.dir / "same.state"), "--only", "ram")
        self.assertEqual(saved_ram, same.after[0]["ram"])
        self.assertEqual(same.digests[(3, "ram")], same.after[0]["ram"])

    def test_slow_ram_is_restored_as_well_as_chip_ram(self):
        path = self.dir / "slow.state"
        run = self.probe("vsync_slow", "--units", "8", "--every", "1", "--save-at", "3", str(path), "--load-at", "8", str(path))
        self.assertNotEqual(run.digests[(3, "ram")], run.digests[(8, "ram")])
        self.assertEqual(run.digests[(3, "ram")], run.after[0]["ram"])
        chip_only = self.probe("vsync", "--units", "8", "--every", "1", "--save-at", "3", str(self.dir / "chip.state"))
        self.assertNotEqual(chip_only.digests[(3, "ram")], run.digests[(3, "ram")], "the two programs must differ in Slow RAM for this test to see it")
        body = parse_chunks(path.read_bytes())[2][2][8 + CHIP:8 + CHIP + SLOW]
        self.assertNotEqual(set(body), {body[0]}, "Slow RAM of this state must not be constant")

    def test_a_state_restores_the_ram_of_a_machine_that_ran_another_number_of_units(self):
        for units in (4, 5, 11):
            with self.subTest(units=units):
                run = self.probe("vsync", "--units", str(units), "--every", "1", "--load-at", str(units), str(self.good_path))
                self.assertEqual([(units, 0, "")], run.loads)
                self.assertEqual(self.reference.digests[(3, "ram")], run.after[0]["ram"])

    def test_a_state_is_bound_to_the_disks_that_were_mounted_and_to_nothing_else_about_them(self):
        a, b = self.dir / "a.adf", self.dir / "b.adf"
        a.write_bytes(hashlib.sha256(b"disk a").digest() * (901120 // 32))
        b.write_bytes(hashlib.sha256(b"disk b").digest() * (901120 // 32))
        with_a = self.dir / "with-a.state"
        saved = self.probe("vsync", "--units", "8", "--every", "1", "--adf", str(a), "--save-at", "3", str(with_a), "--load-at", "8", str(with_a))
        self.assertEqual([(8, 0, "")], saved.loads, "the same image in the same process")
        self.assertEqual(saved.digests[(3, "ram")], saved.after[0]["ram"])
        again = self.probe("vsync", "--units", "8", "--every", "1", "--adf", str(a), "--load-at", "8", str(with_a))
        self.assertEqual([(8, 0, "")], again.loads, "the same image in a new process")
        self.assertEqual(saved.digests[(3, "ram")], again.after[0]["ram"])
        # The manifest only grows (T17e): the state of a manifest [a] is a state of every manifest that starts with [a], and of no other (see test_state_media_append.py).
        for name, arguments in (("no disk", ()), ("another disk", ("--adf", str(b))), ("a disk first and another one", ("--adf", str(b), "--adf", str(a)))):
            with self.subTest(name):
                run = self.probe("vsync", "--units", "8", "--every", "1", *arguments, "--load-at", "8", str(with_a))
                self.assertEqual(1, len(run.loads))
                self.assertEqual((8, 10), run.loads[0][:2], run.loads)
                self.assertIn("media_manifest_hash", run.loads[0][2])
        order = self.dir / "order.state"
        self.probe("vsync", "--units", "3", "--adf", str(a), "--adf", str(b), "--save-at", "3", str(order))
        swapped = self.probe("vsync", "--units", "8", "--adf", str(b), "--adf", str(a), "--load-at", "8", str(order))
        self.assertIn("media_manifest_hash", swapped.loads[0][2], "the order of the media array is part of the identity")
        none = self.dir / "none.state"
        self.probe("vsync", "--units", "3", "--save-at", "3", str(none))
        run = self.probe("vsync", "--units", "8", "--adf", str(a), "--load-at", "8", str(none))
        self.assertEqual([(8, 0, "")], run.loads, "a state taken with no disk is the prefix of every manifest: it fits a machine that has one")
        run = self.probe("vsync", "--units", "8", "--adf", str(a), "--adf", str(b), "--load-at", "8", str(with_a))
        self.assertEqual([(8, 0, "")], run.loads, "the manifest [a, b] starts with [a]")

    def test_the_public_functions_work_and_the_capability_is_set_once_the_native_state_is_released(self):
        # The closure of T15 turned the release on: the public bound, save and load succeed, the state they make is the one the registry makes at the same unit (byte for byte),
        # a buffer that is too small and a state that is garbage are refused with their own codes, and the capability is announced by this image (it links bridge/state.cpp).
        internal = self.dir / "public-internal.state"
        run = self.probe("vsync", "--units", "3", "--save-at", "2", str(internal), "--public-at", "2")
        (unit, cap, bound, save, load, written, junk, small, message), = run.public
        # P6_OK = 0, P6_ERROR_CAPACITY = 7, P6_ERROR_CORRUPT_STATE = 9
        self.assertEqual(("2", "1", "0", "0", "0", "7", "9"), (unit, cap, bound, save, load, small, junk), message)
        self.assertEqual("", message)
        (size_unit, size_bound, size_written, digest), = run.public_size
        self.assertEqual((2, int(written)), (size_unit, size_written))
        self.assertLessEqual(size_written, size_bound)
        (_, internal_size, internal_digest, internal_bound), = [s for s in run.saves if s[0] == 2]
        self.assertEqual((internal_size, internal_digest, internal_bound), (size_written, digest, size_bound), "the public save is the state the registry makes")
        saved = self.dir / "public-saved.state"
        again = self.probe("vsync", "--units", "3", "--public-save-at", "2", str(saved))
        self.assertEqual(internal.read_bytes(), saved.read_bytes(), "the public save is byte for byte the internal one at the same unit")
        self.assertEqual(again.saves[0][2:], (digest, size_bound))
        loaded = self.probe("vsync", "--units", "3", "--public-load-at", "2", str(saved))
        self.assertEqual([(2, 0, "")], loaded.loads, "the public load accepts it")
        self.assertEqual(self.probe("vsync", "--units", "3", "--every", "1").unit(3), loaded.unit(3), "and the machine goes on as the original")

    # -- STATE02 -----------------------------------------------------------------------------------------------------------------------

    def test_every_truncation_and_every_flipped_byte_that_matters_is_refused_and_leaves_the_machine_alone(self):
        good = self.good
        ram_at = e.HEADER_SIZE + 16 + CPU_BODY + 16 + SCHED_BODY + 16     # the chunk header of RAM follows the cpu and scheduler chunks
        cuts = [0, 1, 7, 8, 100, 335, 336, 337, 351, 352, 353, ram_at, ram_at + 15, ram_at + 16, ram_at + 17, ram_at + 24, ram_at + 25,
                ram_at + 16 + RAM_BODY // 2, ram_at + 16 + RAM_BODY - 1, ram_at + 16 + RAM_BODY, len(good) - 17, len(good) - 16, len(good) - 1]
        attempts = [(f"truncated to {n}", good[:n], 9, "state refused") for n in cuts]
        # A flipped byte is a corrupt state wherever it is: the header checksum covers the first 304 bytes and itself, the payload checksum
        # the rest, and both are checked before the identity or any chunk.
        for index in (0, 8, 11, 12, 16, 20, 24, 40, 48, 52, 56, 88, 108, 112, 144, 176, 208, 240, 272, 304, 335, 336, ram_at, ram_at + 8, ram_at + 24,
                      ram_at + 100, ram_at + 16 + RAM_BODY - 1, len(good) - 1):
            data = bytearray(good)
            data[index] ^= 0x01
            # The version of the format is read before the header checksum, so a state of another version says so; everything else is corrupt.
            attempts.append((f"byte {index} flipped", bytes(data), 10 if 8 <= index < 12 else 9, "format_version" if 8 <= index < 12 else "state refused"))
        self.refusals(attempts)

    def test_a_state_of_another_machine_is_incompatible_and_the_message_names_what_differs(self):
        attempts = [
            ("another standard", self.other_state("ntsc.state", "vsync", "--ntsc"), 10, "config_hash"),
            ("another seed", self.other_state("seed.state", "vsync", "--seed", "2"), 10, "config_hash"),
            ("another program", self.other_state("rom.state", "input_all"), 10, "firmware_sha256"),
        ]
        forged = {"profile_id": (16, 2), "core_build_id": (56, 7), "upstream_commit": (88, 7), "patch_series_hash": (112, 7),
                  "codegen_hash": (144, 7), "config_hash": (176, 7), "media_manifest_hash": (208, 7), "firmware_sha256": (240, 7)}
        for field, (offset, size) in forged.items():
            header = {offset: struct.pack("<I", size) if field == "profile_id" else bytes([size]) * 4}
            attempts.append((f"{field} changed and resealed", reforge(self.good, header=header, keep_length=True), 10, field))
        attempts.append(("a Waterbox snapshot", b"P6WATERB" + self.good[8:], 10, "magic"))
        attempts.append(("another version of the format", reforge(self.good, header={8: struct.pack("<I", 2)}, keep_length=True), 10, "format_version"))
        self.refusals(attempts)

    def test_a_state_with_right_checksums_and_a_wrong_structure_is_refused_by_its_chunks_before_anything_is_applied(self):
        def ram(change):
            def edit(chunks):
                chunks[2][2] = change(chunks[2][2])
            return edit

        def chunk(index, version=None, body=None, tag=None):
            def edit(chunks):
                if version is not None:
                    chunks[index][1] = version
                if body is not None:
                    chunks[index][2] = body
                if tag is not None:
                    chunks[index][0] = tag
            return edit
        good = self.good
        attempts = [
            ("the slow RAM one byte short", reforge(good, ram(lambda b: b[:-1])), 9, "chunk ram: the body ends before its last field"),
            ("the slow RAM one byte long", reforge(good, ram(lambda b: b + b"\0")), 9, "chunk_length"),     # the input chunk's bound has slack (its ring), the RAM chunk's has none
            ("an empty RAM body", reforge(good, ram(lambda b: b"")), 9, "chunk ram"),
            ("only the sizes", reforge(good, ram(lambda b: b[:8])), 9, "chunk ram"),
            ("another Chip RAM size", reforge(good, ram(lambda b: struct.pack("<I", CHIP // 2) + b[4:])), 9, "chunk ram: a field is out of its range"),
            ("another Slow RAM size", reforge(good, ram(lambda b: b[:4] + struct.pack("<I", 0) + b[8:])), 9, "chunk ram: a field is out of its range"),
            ("a RAM chunk of version 2", reforge(good, chunk(2, version=2)), 10, "chunk_version"),
            ("a chipset chunk of version 2", reforge(good, chunk(4, version=2)), 10, "chunk_version"),
            ("a chipset chunk of version 0, as when it was a placeholder", reforge(good, chunk(4, version=0)), 10, "chunk_version"),
            ("a chipset chunk longer than its largest body", reforge(good, chunk(4, body=bytes(CHIPSET_BODY + 1))), 9, "chunk_length"),
            ("the rtc chunk of version 2", reforge(good, chunk(3, version=2)), 10, "chunk_version"),
            ("the cpu chunk of version 2", reforge(good, chunk(0, version=2)), 10, "chunk_version"),
            ("the scheduler chunk of version 2", reforge(good, chunk(1, version=2)), 10, "chunk_version"),
            ("an unknown chunk", reforge(good, chunk(4, tag=b"P6XX")), 9, "chunk"),
            ("chunks out of order", reforge(good, lambda c: c.insert(0, c.pop(2))), 9, "chunk"),
            ("the last chunk missing and the count kept", reforge(good, lambda c: c.pop(), count=len(e.TAGS)), 9, "chunk: a chunk has no header"),
            ("the last chunk missing", reforge(good, lambda c: c.pop()), 9, "chunk_count"),
            ("a chunk repeated in the place of another", reforge(good, lambda c: c.__setitem__(3, list(c[5]))), 9, "chunk: unknown, repeated or out of order"),
            ("a chunk more than there are", reforge(good, lambda c: c.insert(3, list(c[5]))), 9, "chunk_count"),    # 16 bytes of header: within the slack of the input chunk's bound
            ("more chunks declared than there are", reforge(good, count=len(e.TAGS) + 1), 9, "chunk_count"),
            ("fewer chunks declared than there are", reforge(good, count=len(e.TAGS) - 1), 9, "chunk_count"),
        ]
        run = self.refusals(attempts)
        self.assertEqual(len(attempts), len(run.after))

    def test_a_refusal_in_the_slow_ram_leaves_chip_ram_as_it_was_not_half_restored(self):
        run = self.refusals([("the slow RAM one byte short", reforge(self.good, lambda c: c[2].__setitem__(2, c[2][2][:-1])), 9, "chunk ram")])
        live_ram = run.digests[(6, "ram")]
        self.assertNotEqual(live_ram, self.reference.digests[(3, "ram")], "the state's RAM is not the live one, so a half restore would show")
        self.assertEqual(live_ram, run.after[0]["ram"])

    def test_a_chunk_that_fails_to_apply_after_it_was_validated_faults_the_image_and_unload_recovers_it(self):
        run = self.probe("vsync", "--units", "8", "--every", "1", "--load-fault-at", "6", str(self.good_path))
        (unit, code, message), = run.loads
        self.assertEqual((6, 11), (unit, code))                           # P6_ERROR_INTERNAL
        self.assertIn("faulted", message)
        self.assertEqual([("run-after-fault", 2, "the image faulted; unload it"), ("unload", 0, ""), ("reload", 0, "")], run.fault)
        # The probe goes on running the reloaded image: its units 1 and 2 are printed as 7 and 8. From the first unit on, a reloaded image is the
        # one of a clean process (before it, until the reset head has run, chipset, audio and input still hold what the last session left).
        clean = self.probe("vsync", "--units", "2", "--every", "1")
        self.assertEqual(clean.unit(1), run.unit(7), "an image loaded again after a fault is the one of a clean process")
        self.assertEqual(clean.unit(2), run.unit(8))

    def test_after_the_refusals_the_machine_still_loads_a_good_state(self):
        bad = reforge(self.good, lambda c: c[2].__setitem__(2, c[2][2][:-1]))
        path_good, path_bad = self.dir / "after-good.state", self.dir / "after-bad.state"
        path_good.write_bytes(self.good)
        path_bad.write_bytes(bad)
        # No unit runs after the good load: the cycle counter goes back to the saved unit and the clock stays anchored to the later one until the rtc
        # chunk (T15c) puts the anchor back, so the next unit would fault the image.
        run = self.probe("vsync", "--units", "7", "--every", "1", "--load-at", "6", str(path_bad), "--load-at", "6", str(path_bad),
                         "--load-at", "7", str(path_good))
        self.assertEqual([9, 9, 0], [code for _, code, _ in run.loads])
        self.assertEqual(self.reference.digests[(3, "ram")], run.after[2]["ram"])


if __name__ == "__main__":
    unittest.main()
