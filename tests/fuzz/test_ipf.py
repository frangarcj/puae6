"""FUZZ01 for the IPF reader (T58a): mutated images against an independent oracle, under ASan and UBSan.

The reader (ports/portable/media/ipf_track.cpp over the decoder of MAME) is built without the engine (tests/media/ipf_probe.cpp) and given bundles of mutated images, each copied into a heap
block of exactly its size. Mutations: bytes flipped anywhere (the checksum of a tag fails), fields set to boundary values with every checksum put right again (the structure is read, the
descriptions of the blocks reach the decoder), tags dropped, repeated and swapped, the file cut and extended, and random bytes in the descriptions. The verdict "the file opens" must be the one of
the Python oracle (tests/media/ipf.py accepts), the digest of everything decoded must be the same on a second run, and no single allocation may pass 24 MiB. Every report of a sanitizer is a failure.
Run: python3 -m unittest tests.fuzz.test_ipf
"""
import os
from pathlib import Path
import random
import re
import struct
import subprocess
import tempfile
import unittest
import zlib

from tests.media import ipf
from tests.media.test_ipf import ENV, Probe, build_probe, sector_blocks, SYNC

ROOT = Path(__file__).resolve().parents[2]
RUNS = ((1, 1500), (2, 1500), (3, 1500), (20261013, 1500))      # (seed, images), recorded: a failure is reproduced by its seed
BOUNDARY = (0, 1, 2, 7, 8, 15, 16, 31, 32, 0xFF, 0x100, 0xFFFF, 0x10000, 0x7FFFFFFF, 0x80000000, 0xFFFFFFF0, 0xFFFFFFFF, 160000, 160001, 262144, 262145, 168, 167, 166, 83, 82)


def seeds():
    """Images that exercise the decoder: every kind of block, gap, element and timing."""
    weak = [ipf.Block([("sync", SYNC), ("mfm", b"\xAA" * 8), ("weak", 6), ("mfm", b"\x55" * 4)], gap_cells=120, pattern=0x00), ipf.Block([("sync", SYNC), ("weak", 3)], gap_cells=100)]
    raw = [ipf.Block([("raw", bytes([0x92, 0x49, 0x24, 0x92, 0xAA, 0xAA]))], gap_cells=96, encoder=2), ipf.Block([("raw", b"\x55\x55\x44\x89")], gap_cells=96, encoder=2)]
    described = [ipf.Block([("sync", SYNC), ("mfm", b"\x12\x34\x56\x78")], gap_cells=g, gap_type=t, gap_pattern=bytes([0x4E, 0x00])) for g in (96, 160) for t in (1, 2)]
    nine = sector_blocks(6, gap=120)
    nine[0].speed_mask = 0b10110
    tiny = [ipf.Block([("sync", b"\x44")], gap_cells=g, pattern=p) for g, p in ((40, 0), (9, 0xFF), (64, 0x55), (8, 0))] + [ipf.Block([("mfm", b"\x00")], gap_cells=100, gap_type=t) for t in (1, 2, 3)]
    both = [ipf.Block([("sync", SYNC), ("mfm", b"\x12\x34")], gap_cells=g, gap_type=3, gap_pattern=bytes([0x4E, 0x00])) for g in (95, 160)]
    tracks = [ipf.Track(5, 0, tiny, index_cells=33), ipf.Track(5, 1, both, index_cells=20), ipf.Track(0, 0, sector_blocks(11, gap=200)), ipf.Track(0, 1, sector_blocks(3, gap=64), index_cells=500), ipf.Track(1, 0, weak), ipf.Track(1, 1, raw),
              ipf.Track(2, 0, described), ipf.Track(2, 1, sector_blocks(3, gap=160), type=6), ipf.Track(3, 0, sector_blocks(7, gap=160), type=3), ipf.Track(3, 1, nine, type=9),
              ipf.Track(4, 0, [], type=1, noise_cells=20000)]
    return [ipf.build(tracks), ipf.build(tracks[2:3]), ipf.build([tracks[4]]), ipf.build(tracks[-1:]), ipf.build(tracks[:2]), ipf.build(tracks, encoder_type=1)]


def fix_checksums(blob):
    """Recompute the CRC of every tag (and of the data after a DATA tag) as far as the tag chain is readable."""
    out = bytearray(blob)
    pos = 0
    while pos + 12 <= len(out):
        size = struct.unpack(">I", out[pos + 4:pos + 8])[0]
        if size < 12 or pos + size > len(out):
            break
        end = pos + size
        if out[pos:pos + 4] == b"DATA" and size == 28:
            length = struct.unpack(">I", out[pos + 12:pos + 16])[0]
            if end + length <= len(out):
                out[pos + 20:pos + 24] = struct.pack(">I", zlib.crc32(bytes(out[end:end + length])) & 0xFFFFFFFF)
                end += length
        out[pos + 8:pos + 12] = b"\0\0\0\0"
        out[pos + 8:pos + 12] = struct.pack(">I", zlib.crc32(bytes(out[pos:pos + size])) & 0xFFFFFFFF)
        pos = end
    return bytes(out)


def tag_spans(blob):
    spans, pos = [], 0
    while pos + 12 <= len(blob):
        size = struct.unpack(">I", blob[pos + 4:pos + 8])[0]
        if size < 12 or pos + size > len(blob):
            break
        end = pos + size
        if blob[pos:pos + 4] == b"DATA" and size == 28:
            end += struct.unpack(">I", blob[pos + 12:pos + 16])[0]
        spans.append((pos, min(end, len(blob)), blob[pos:pos + 4]))
        pos = end
    return spans


def mutate(rng, blob):
    kind = rng.choice(("flip", "field", "field", "field", "field", "stream", "stream", "drop", "repeat", "swap", "cut", "extend", "tagid", "size"))
    out = bytearray(blob)
    spans = tag_spans(blob)
    if kind == "flip":
        for _ in range(rng.randint(1, 3)):
            out[rng.randrange(len(out))] ^= 1 << rng.randrange(8)
        return kind, bytes(out)
    if kind == "field":
        pos, end, ident = rng.choice(spans)
        if ident == b"DATA":
            end = pos + 28 + min(len(blob) - pos - 28, 32 * 12)       # the table of block descriptors at the head of the data
        at = pos + 12 + 4 * rng.randrange(max(1, (end - pos - 12) // 4))
        if at + 4 <= len(out):
            out[at:at + 4] = struct.pack(">I", rng.choice(BOUNDARY))
        return kind, fix_checksums(bytes(out))
    if kind == "stream":
        pos, end, ident = rng.choice([s for s in spans if s[2] == b"DATA"] or spans)
        start = pos + 28 + 32
        for _ in range(rng.randint(1, 4)):
            if end - 1 > start:
                out[rng.randrange(start, end)] = rng.choice((0, 1, 2, 3, 4, 5, 0x20, 0x21, 0x22, 0x23, 0x40, 0x41, 0x61, 0x81, 0xA2, 0xFF, rng.randrange(256)))
        return kind, fix_checksums(bytes(out))
    if kind == "drop" and len(spans) > 1:
        pos, end, _ = rng.choice(spans)
        return kind, fix_checksums(bytes(out[:pos] + out[end:]))
    if kind == "repeat":
        pos, end, _ = rng.choice(spans)
        return kind, fix_checksums(bytes(out[:end] + out[pos:end] + out[end:]))
    if kind == "swap" and len(spans) > 2:
        a, b = sorted(rng.sample(range(len(spans)), 2))
        (pa, ea, _), (pb, eb, _) = spans[a], spans[b]
        return kind, bytes(out[:pa] + out[pb:eb] + out[ea:pb] + out[pa:ea] + out[eb:])
    if kind == "cut":
        return kind, bytes(out[:rng.randrange(len(out))])
    if kind == "extend":
        return kind, bytes(out) + bytes(rng.randrange(256) for _ in range(rng.randint(1, 40)))
    if kind == "tagid":
        pos, _, _ = rng.choice(spans)
        out[pos:pos + 4] = rng.choice((b"INFO", b"IMGE", b"DATA", b"CAPS", b"CTEX", b"\0\0\0\0"))
        return kind, fix_checksums(bytes(out))
    pos, _, _ = rng.choice(spans)
    out[pos + 4:pos + 8] = struct.pack(">I", rng.choice(BOUNDARY))
    return kind, fix_checksums(bytes(out))


class IpfFuzzTests(Probe, unittest.TestCase):
    def run_bundle(self, images, revolutions=2):
        path = Path(self.tmp.name) / "bundle.bin"
        path.write_bytes(b"".join(struct.pack("<I", len(i)) + i for i in images))
        result = subprocess.run([str(self.exe), "--bundle", str(path), str(revolutions)], capture_output=True, text=True, env=ENV, timeout=3000)
        self.assertEqual(result.returncode, 0, result.stderr[-2000:])
        self.assertNotIn("ERROR", result.stderr)
        lines = result.stdout.splitlines()
        self.assertEqual(len(lines), len(images))
        return lines

    def campaign(self, seed, count):
        rng = random.Random(seed)
        pool = seeds()
        images, kinds = [], []
        for _ in range(count):
            kind, blob = mutate(rng, rng.choice(pool))
            images.append(blob)
            kinds.append(kind)
        lines = self.run_bundle(images)
        again = self.run_bundle(images)
        self.assertEqual(lines, again, "the same images decode to the same bits in a second process")
        opened = failed_tracks = 0
        for i, (blob, line) in enumerate(zip(images, lines)):
            accepted = line.startswith("ok")
            self.assertEqual(accepted, ipf.accepts(blob), f"image {i} ({kinds[i]}): the reader says {line!r}, the oracle {ipf.accepts(blob)}")
            if accepted:
                opened += 1
                failed_tracks += int(re.search(r"failed (\d+)", line)[1])
        return opened, failed_tracks, len(images)

    def test_the_recorded_seeds_agree_with_the_oracle_and_run_clean(self):
        total_opened = total_failed = 0
        for seed, count in RUNS:
            with self.subTest(seed=seed):
                opened, failed, n = self.campaign(seed, count)
                self.assertGreater(opened, n // 20, "a share of the mutated images still opens (the structure is read)")
                self.assertLess(opened, n, "and a share is refused")
                total_opened += opened
                total_failed += failed
        self.assertGreater(total_failed, 0, "some opened images have a track the decoder cannot read (the descriptions reach it)")

    def test_the_unmutated_seeds_open_and_decode_every_track(self):
        for i, line in enumerate(self.run_bundle(seeds())):
            with self.subTest(seed=i):
                self.assertTrue(line.startswith("ok failed 0 "), line)

    def test_the_harness_can_fail(self):
        # An oracle that said "refused" for everything would differ from the reader on the seeds.
        self.assertTrue(all(ipf.accepts(s) for s in seeds()))
        flipped = bytearray(seeds()[1])
        flipped[40] ^= 1
        self.assertFalse(ipf.accepts(bytes(flipped)))


if __name__ == "__main__":
    unittest.main()
