"""Fixtures of the floppy scenario (T12c) on their own: no emulator runs here.

The ADF generator, the independent AmigaDOS MFM model and its decoder, the guest program and the parsers of the RAM dump
are checked against the documented format and against each other. The probe that runs the guest against the engine
builds on these and adds the comparison with what the engine produced.
"""
import hashlib
import re
import unittest

from tests.scenarios.disk.disk import (
    ADF_BYTES, CAPTURE_BASE, CAPTURE_WORDS, CHIP_RAM_BYTES, LOG_BASE, LOG_SLOTS, MARKERS, MARK_BASE, MARK_DONE, SECTORS,
    SECTOR_WORDS, SLOT_SETTLE, SLOT_TAIL, SYNC, TAIL_UNITS, TRACKS, WRITE_BUFFER, WRITE_WORDS, adf_sector, body_words, capture_words,
    decode_capture, encode_track, encode_track0, find_sectors, make_adf, markers, mfm_words_for_sector, pra_log, pra_state,
    rom_source, rotate, swap_gap_bounds, swap_timeline)
from tests.step.test_step import builder

SHA256_A = "3b8ecb62fdd0044069f62a632867d27285104498e05ee9a63ecef3a3ca45c0a8"      # make_adf(0x41)
SHA256_B = "abd9b1aac042bd24d6abbbe4593ed3cc9c0b3ae86ebf2ec9ffef2c9f7a139c63"      # make_adf(0x42)
GAP_WORDS = 6400 - SECTORS * SECTOR_WORDS                                          # what the engine leaves after sector 10


def bit_string(words):
    return "".join(format(w, "016b") for w in words)


class AdfTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.a, cls.b = make_adf(0x41), make_adf(0x42)

    def test_size_determinism_and_the_pinned_digest(self):
        self.assertEqual(len(self.a), ADF_BYTES)
        self.assertEqual((ADF_BYTES, TRACKS, SECTORS), (901120, 160, 11))
        self.assertEqual(make_adf(0x41), self.a)
        self.assertEqual(hashlib.sha256(self.a).hexdigest(), SHA256_A)
        self.assertEqual(hashlib.sha256(self.b).hexdigest(), SHA256_B)

    def test_the_bytes_follow_the_documented_formula(self):
        for label, adf in ((0x41, self.a), (0x42, self.b)):
            for track, sector in ((0, 0), (0, 10), (1, 3), (79, 5), (159, 10)):
                want = bytes((label * 37 + track * 13 + sector * 7 + i * 3 + (i >> 8) * 11) & 0xFF for i in range(512))
                self.assertEqual(adf_sector(adf, track, sector), want, (label, track, sector))

    def test_labels_and_sectors_are_distinct(self):
        self.assertNotEqual(self.a, self.b)
        for track, sector in ((0, 0), (0, 7), (100, 2), (159, 10)):
            self.assertNotEqual(adf_sector(self.a, track, sector), adf_sector(self.b, track, sector))
        self.assertEqual(len({adf_sector(self.a, 0, s) for s in range(SECTORS)}), SECTORS)


class MfmModelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.a, cls.b = make_adf(0x41), make_adf(0x42)
        cls.track0 = encode_track0(cls.a)
        cls.flat = [w for sector in cls.track0 for w in sector]

    def test_the_head_of_track_zero_sector_zero_is_the_documented_one(self):
        words = mfm_words_for_sector(self.a, 0, 0)
        self.assertEqual(len(words), SECTOR_WORDS)
        self.assertEqual(words[:10], [0xAAAA, 0xAAAA, 0x4489, 0x4489, 0x552A, 0xAAA5, 0x552A, 0xAAA9, 0x2AAA, 0xAAAA])
        # info long 0xFF00000B: odd long then even long, clock bits stripped by hand
        odd = ((words[4] << 16) | words[5]) & 0x55555555
        even = ((words[6] << 16) | words[7]) & 0x55555555
        self.assertEqual((odd << 1) | even, 0xFF00000B)
        self.assertEqual(mfm_words_for_sector(self.a, 0, 0, prev_bit=1)[:2], [0x2AAA, 0xAAAA])

    def test_the_sync_is_the_only_missing_clock_and_appears_twice(self):
        for s in (0, 5, 10):
            words = mfm_words_for_sector(self.a, 0, s)
            self.assertEqual([i for i, w in enumerate(words) if w == SYNC], [2, 3])

    def test_a_track_obeys_the_mfm_run_length_rules_across_sector_boundaries(self):
        self.assertEqual(len(self.track0), SECTORS)
        self.assertTrue(all(len(sector) == SECTOR_WORDS for sector in self.track0))
        stream = bit_string(self.flat)
        self.assertNotIn("11", stream)                                  # a clock is 1 only between two 0 data bits
        runs = [len(run) for run in stream.strip("0").split("1")[1:-1]]  # zeros between consecutive ones
        self.assertEqual((min(runs), max(runs)), (1, 3))

    def test_every_sector_of_track_zero_decodes_back_to_its_bytes(self):
        decoded = decode_capture(self.flat)
        self.assertEqual(decoded, {(0, s): adf_sector(self.a, 0, s) for s in range(SECTORS)})
        for found in find_sectors(self.flat):
            self.assertEqual((found.fmt, found.gap, found.header_ok, found.data_ok), (0xFF, SECTORS - found.sector, True, True))
            self.assertEqual(body_words(self.flat, found), mfm_words_for_sector(self.a, 0, found.sector)[4:])

    def test_other_tracks_carry_their_own_number(self):
        for track in (1, 37, 159):
            flat = [w for sector in encode_track(self.a, track) for w in sector]
            self.assertEqual(decode_capture(flat), {(track, s): adf_sector(self.a, track, s) for s in range(SECTORS)})

    def test_a_capture_that_starts_mid_track_after_a_gap_decodes_what_is_complete(self):
        revolution = self.flat + [0xAAAA] * GAP_WORDS
        self.assertEqual(len(revolution), 6400)
        for k in (0, 3, 9):
            # The DMA does not transfer the sync that matched: the capture starts at the second sync, sector k is whole.
            captured = rotate(revolution, k * SECTOR_WORDS + 3, 6400)
            decoded = decode_capture(captured)
            self.assertEqual(len(decoded), SECTORS, k)
            self.assertTrue(all(payload == adf_sector(self.a, t, s) for (t, s), payload in decoded.items()))
            # Without any sync of sector k in the capture (jitter took it), that sector is lost and the others survive.
            lost = decode_capture(rotate(revolution, k * SECTOR_WORDS + 4, 6400))
            self.assertEqual(set(lost), {(0, s) for s in range(SECTORS)} - {(0, k)}, k)

    def test_one_sync_is_enough_and_a_cut_sector_is_skipped(self):
        one_sync = self.flat[:2] + self.flat[3:]
        self.assertEqual(decode_capture(one_sync), decode_capture(self.flat))
        self.assertEqual(set(decode_capture(self.flat[:5 * SECTOR_WORDS + 300])), {(0, s) for s in range(5)})
        self.assertEqual(decode_capture([]), {})

    def test_a_flipped_data_bit_is_rejected_in_the_header_the_checksums_and_the_data(self):
        for what, offset in (("info", 4), ("label", 10), ("header checksum", 24), ("data checksum", 28), ("first data", 32),
                             ("last data", 543)):
            damaged = list(self.flat)
            damaged[3 * SECTOR_WORDS + offset] ^= 0x0010                 # bit 4 of a word is a data bit
            self.assertEqual(set(decode_capture(damaged)), {(0, s) for s in range(SECTORS)} - {(0, 3)}, what)

    def test_noise_and_blank_tracks_decode_to_nothing(self):
        self.assertEqual(decode_capture([0xAAAA] * 6400), {})
        self.assertEqual(decode_capture([0] * 6400), {})
        self.assertEqual(decode_capture([SYNC] * 700), {})

    def test_the_same_sector_from_two_disks_in_one_capture_is_an_error_and_a_repeat_is_not(self):
        other = mfm_words_for_sector(self.b, 0, 0)
        with self.assertRaises(ValueError):
            decode_capture(self.track0[0] + other)
        self.assertEqual(decode_capture(self.track0[0] + self.track0[0]), {(0, 0): adf_sector(self.a, 0, 0)})


class GuestProgramTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = rom_source()
        cls.lines = [line.split(";")[0].strip() for line in cls.source.splitlines()]

    def test_it_assembles_into_a_kickstart_shaped_rom_identically_every_time(self):
        rom = builder.build(self.source)
        self.assertEqual(len(rom), 256 * 1024)
        self.assertEqual(rom[:4], bytes([0x11, 0x11, 0x4E, 0xF9]))
        self.assertEqual(rom, builder.build(rom_source()))
        self.assertEqual(self.source, rom_source())

    def test_the_layout_fits_chip_ram_without_overlaps(self):
        regions = [("vectors and stack", 0, 0x400), ("log", LOG_BASE, LOG_BASE + LOG_SLOTS),
                   ("markers", MARK_BASE, MARK_BASE + 2 * len(MARKERS)),
                   ("write buffer", WRITE_BUFFER, WRITE_BUFFER + 2 * WRITE_WORDS)]
        regions += [("capture " + name, base, base + 2 * CAPTURE_WORDS) for name, base in CAPTURE_BASE.items()]
        for i, (name, low, high) in enumerate(regions):
            self.assertTrue(0 <= low < high <= CHIP_RAM_BYTES, name)
            for other, low2, high2 in regions[i + 1:]:
                self.assertTrue(high <= low2 or high2 <= low, (name, other))
        self.assertEqual(LOG_SLOTS, 244)

    def test_the_log_writes_one_slot_per_wait_in_order(self):
        longs = [m[1] for m in (re.fullmatch(r"\.long \$BFE001,\$([0-9A-F]+)", line) for line in self.lines) if m]
        self.assertEqual([int(v, 16) for v in longs], list(range(LOG_BASE, LOG_BASE + LOG_SLOTS)))
        self.assertEqual(self.lines.count(".word $13F9"), LOG_SLOTS)
        # every wait is a STOP; the DMA waits and the final idle loop add five more
        self.assertEqual(self.lines.count("STOP #$2000"), LOG_SLOTS + 4 + 1)

    def test_the_step_pulse_comes_after_the_hold_and_the_marks_follow_the_phases(self):
        step = self.lines.index("MOVE.B #$76,$BFD100.L")
        self.assertEqual(self.lines[step + 1], "MOVE.B #$77,$BFD100.L")
        self.assertEqual(self.lines[:step].count(".word $13F9"), SLOT_SETTLE)
        self.assertEqual(self.lines[step:].count(".word $13F9"), LOG_SLOTS - SLOT_SETTLE)
        marks = [int(m[1], 16) for m in (re.fullmatch(r"MOVE\.W #\$%04X,\$([0-9A-F]+)\.L" % MARK_DONE, line) for line in self.lines) if m]
        self.assertEqual(marks, [MARK_BASE + 2 * i for i in range(len(MARKERS))])
        end = self.lines.index("MOVE.W #$%04X,$%X.L" % (MARK_DONE, MARK_BASE + 2 * (len(MARKERS) - 1)))
        self.assertEqual(self.lines[:end].count(".word $13F9"), LOG_SLOTS)          # the end marker follows the last wait
        self.assertEqual(SLOT_TAIL + TAIL_UNITS, LOG_SLOTS)

    def test_the_three_reads_target_their_buffers_and_the_write_uses_the_pattern_buffer(self):
        pointers = [int(m[1], 16) for m in (re.fullmatch(r"MOVE\.L #\$([0-9A-F]+),\$DFF020\.L", line) for line in self.lines) if m]
        self.assertEqual(pointers, [CAPTURE_BASE["boot"], CAPTURE_BASE["swapped"], WRITE_BUFFER, CAPTURE_BASE["readback"]])
        self.assertEqual(self.lines.count("MOVE.W #$%04X,$DFF024.L" % (0x8000 | CAPTURE_WORDS)), 6)      # DMAEN | length, twice
        self.assertEqual(self.lines.count("MOVE.W #$%04X,$DFF024.L" % (0xC000 | WRITE_WORDS)), 2)        # DMAEN | WRITE
        self.assertEqual(self.lines.count("MOVE.W #$4489,$DFF07E.L"), 3)
        self.assertEqual(self.lines.count("MOVEM.L D0-D7,-(A0)"), 2 * WRITE_WORDS // 32)


class RamDumpTests(unittest.TestCase):
    def synthetic_ram(self):
        ram = bytearray(CHIP_RAM_BYTES)
        log = [0xE6] * 18 + [0xC6] * 50 + [0xE2] + [0xE2] * 118 + [0xC2] * 30 + [0xC6] * (LOG_SLOTS - 18 - 50 - 1 - 118 - 30)
        ram[LOG_BASE:LOG_BASE + LOG_SLOTS] = bytes(log)
        return ram, log

    def test_the_log_the_markers_and_the_captures_are_read_where_the_guest_wrote_them(self):
        ram, log = self.synthetic_ram()
        self.assertEqual(pra_log(ram), log)
        self.assertEqual(markers(ram), {name: False for name in MARKERS})
        for i in (0, 4):
            ram[MARK_BASE + 2 * i:MARK_BASE + 2 * i + 2] = MARK_DONE.to_bytes(2, "big")
        self.assertEqual([k for k, v in markers(ram).items() if v], [MARKERS[0], MARKERS[4]])
        words = [(i * 7 + 1) & 0xFFFF for i in range(CAPTURE_WORDS)]
        base = CAPTURE_BASE["swapped"]
        ram[base:base + 2 * CAPTURE_WORDS] = b"".join(w.to_bytes(2, "big") for w in words)
        self.assertEqual(capture_words(ram, "swapped"), words)
        self.assertNotEqual(capture_words(ram, "boot"), words)
        with self.assertRaises(ValueError):
            pra_log(bytes(1000))

    def test_the_drive_lines_are_active_low(self):
        self.assertEqual(pra_state(0xE6), (False, True, True, False))    # /CHNG high, /WPRO low, /TK0 low, /RDY high: spinning up
        self.assertEqual(pra_state(0xC6), (False, True, True, True))     # ready
        self.assertEqual(pra_state(0xE2), (True, True, True, False))     # disk gone: /CHNG low, not ready
        self.assertEqual(pra_state(0xC2), (True, True, True, True))      # new disk spun up, /CHNG still low

    def test_the_swap_is_found_in_the_log_and_missing_steps_are_none(self):
        _, log = self.synthetic_ram()
        self.assertEqual(swap_timeline(log), {"ready": 18, "ejected": 68, "reinserted": 187, "acknowledged": 217})
        self.assertEqual(swap_timeline([0xE6] * 10), {"ready": None, "ejected": None, "reinserted": None, "acknowledged": None})
        self.assertEqual(swap_timeline([0xE6] * 3 + [0xC6] * 5)["ejected"], None)
        self.assertEqual(swap_timeline([0xE6, 0xC6, 0xE2, 0xE2])["reinserted"], None)

    def test_the_swap_gap_bounds_contain_the_gaps_measured_in_the_prototype(self):
        (pal_low, pal_high), (ntsc_low, ntsc_high) = swap_gap_bounds("pal"), swap_gap_bounds("ntsc")
        self.assertTrue(pal_low <= 119 <= pal_high, (pal_low, pal_high))        # eject seen at unit 69, disk ready at unit 188
        self.assertTrue(ntsc_low <= 142 <= ntsc_high, (ntsc_low, ntsc_high))
        self.assertLess(pal_high, ntsc_low)                                      # the standards are told apart


if __name__ == "__main__":
    unittest.main()
