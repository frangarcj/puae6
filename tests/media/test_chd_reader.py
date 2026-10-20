"""MED01: bounded CHD I/O, maps, SELF and metadata; exact CD codec completion and transactional cleanup."""
import os
import binascii
import lzma
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest
import zlib
from tests.media import test_chd_dependencies as dependency

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests/fixtures/chd"
SECTOR = bytes(n % 251 for n in range(2448))


def cd_map(entries, tree=None, symbols=None, widths=(24, 32, 0), first=124, codec=b"zlib", sector=SECTOR):
    """Legal v5 map encoder with a flat Huffman tree; fields are independently CRC'd."""
    attributes, raw, body = [], bytearray(), bytearray()
    for kind, value in entries:
        at, length, crc = first + len(body), 0, 0
        if kind < 5:
            length = len(value); body += value
            crc = binascii.crc_hqx(value if kind == 4 else sector, 0xffff)
            if kind < 4:
                attributes.append(format(length, f"0{widths[0]}b"))
            attributes.append(format(crc, "016b"))
        elif kind == 5:
            at = value; attributes.append(format(value, f"0{widths[1]}b"))
        raw += bytes([kind]) + length.to_bytes(3, "big") + at.to_bytes(6, "big") + crc.to_bytes(2, "big")
    bits = ("0100" * 16 if tree is None else tree)
    bits += "".join(format(kind, "04b") for kind, _ in entries) if symbols is None else symbols
    bits += "".join(attributes)
    packed = int(bits + "0" * (-len(bits) % 8), 2).to_bytes((len(bits) + 7) // 8, "big")
    header = bytearray(124); header[:8] = b"MComprHD"
    struct.pack_into(">II4I3QII", header, 8, 124, 5, int.from_bytes(codec, "big"), 0, 0, 0, len(entries) * len(sector), 124 + len(body), 0, len(sector), 2448)
    map_header = struct.pack(">I", len(packed)) + first.to_bytes(6, "big") + struct.pack(">H", binascii.crc_hqx(raw, 0xffff)) + bytes([*widths, 0])
    return header + body + map_header + packed


def deflate(data, finish=zlib.Z_FINISH):
    encoder = zlib.compressobj(6, zlib.DEFLATED, -15)
    return encoder.compress(data) + encoder.flush(finish)


def flac_constant(frames=588, rate=9, bits=4, channels=1):
    """Numeric constant-zero stereo FLAC frame, independent of dr_flac/chdman."""
    def crc(data, width, polynomial):
        value = 0
        for b in data:
            value ^= b << (width - 8)
            for _ in range(8):
                value = ((value << 1) ^ (polynomial if value >> (width - 1) else 0)) & ((1 << width) - 1)
        return value
    header = bytes([0xff,0xf8,0x70 | rate,(channels << 4) | (bits << 1),0]) + (frames - 1).to_bytes(2,"big")
    header += bytes([crc(header, 8, 7)])
    frame = header + (bytes(1 + (24 if bits == 6 else 16) // 8) * (channels + 1))
    return frame + crc(frame, 16, 0x8005).to_bytes(2,"big")


def metadata_chain(container, payloads, cycle=None, tag=0x54455354, flags=0x81):
    result = bytearray(container); positions, at = [], len(result)
    for payload in payloads:
        positions.append(at); at += 16 + len(payload)
    struct.pack_into(">Q", result, 48, positions[0])
    for n, payload in enumerate(payloads):
        next_at = positions[n + 1] if n + 1 < len(positions) else positions[cycle] if cycle is not None else 0
        result += struct.pack(">IIQ", tag if isinstance(tag, int) else tag[n], (flags << 24) | len(payload), next_at) + payload
    return result


class ChdReaderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        (ROOT / "build").mkdir(exist_ok=True)
        cls.temp = tempfile.TemporaryDirectory(prefix="chd-reader-", dir=ROOT / "build")
        cls.addClassCleanup(cls.temp.cleanup)
        cls.work = Path(cls.temp.name)
        cls.expected = cls.work / "expected.bin"
        cls.pattern = dependency.load("chd_pattern", FIXTURES / "generate.py")
        cls.expected.write_bytes(cls.pattern.expected())
        (cls.work / "CMakeLists.txt").write_text(
            'cmake_minimum_required(VERSION 3.19)\nproject(chd_reader C CXX)\n'
            f'include("{ROOT}/cmake/Chd.cmake")\n'
            f'add_executable(probe "{ROOT}/tests/media/chd_reader_probe.cpp")\n'
            'target_link_libraries(probe PRIVATE p6_chd_reader)\n'
            'target_compile_definitions(p6_chd PRIVATE malloc=chd_test_malloc calloc=chd_test_calloc '
            'realloc=chd_test_realloc free=chd_test_free)\n')
        flags = "-O1 -g -fsanitize=address,undefined -fno-sanitize-recover=all -fno-omit-frame-pointer"
        dependency.ChdDependencyTests.run_ok(["cmake", "-S", str(cls.work), "-B", str(cls.work / "out"),
            "-DCMAKE_C_FLAGS=" + flags, "-DCMAKE_CXX_FLAGS=" + flags, "-DCMAKE_EXE_LINKER_FLAGS=-fsanitize=address,undefined"])
        dependency.ChdDependencyTests.run_ok(["cmake", "--build", str(cls.work / "out"), "--target", "probe", "-j2"])

    def probe(self, case, fixture, expected):
        env = {**os.environ, "ASAN_OPTIONS": "detect_leaks=" + ("0" if sys.platform == "darwin" else "1")}
        result = subprocess.run([str(self.work / "out/probe"), case, str(fixture), str(expected)],
                                capture_output=True, text=True, env=env, timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_four_codecs_offsets_crossings_repeat_and_duplicate_codec_ownership(self):
        for codec in self.pattern.CODECS:
            with self.subTest(codec=codec):
                self.probe("positive", FIXTURES / (codec + ".chd"), self.expected)

    def test_false_and_short_io_at_each_open_and_cross_hunk_read_step_are_atomic(self):
        for codec in self.pattern.CODECS:
            with self.subTest(codec=codec):
                self.probe("faults", FIXTURES / (codec + ".chd"), self.expected)

    def test_headers_geometry_resource_limits_parent_and_write_rejections(self):
        self.probe("headers", FIXTURES / "none.chd", FIXTURES / "cdzl.chd")

    def case(self, name, data, mode="reject", expected=None):
        path = self.work / (name + ".chd"); path.write_bytes(data)
        self.probe(mode, path, self.expected if expected is None else expected)

    def test_map_crc_types_spans_rle_huffman_exhaustion_and_bit_widths(self):
        good = cd_map([(4, SECTOR)]); map_at = struct.unpack_from(">Q", good, 40)[0]
        bad_crc = bytearray(good); bad_crc[map_at + 10] ^= 1
        truncated = bytearray(good[:-1]); struct.pack_into(">I", truncated, map_at, len(good) - map_at - 17)
        incomplete = "".join("00010001" if n == 4 else "0000" for n in range(16))
        for name, data in {"map-crc": bad_crc, "map-truncated": truncated,
            "map-slot": cd_map([(1, b"x")]), "map-parent": cd_map([(6, 0)]), "map-kind": cd_map([(14, 0)]),
            "map-range": cd_map([(4, SECTOR)], first=1 << 40), "map-length": cd_map([(0, b"")]),
            "length-bits": cd_map([(4, SECTOR)], widths=(25,32,0)), "self-bits": cd_map([(4, SECTOR)], widths=(24,33,0)),
            "map-rle": cd_map([(4, SECTOR)], symbols="01110000"),
            "tree-rle": cd_map([(4, SECTOR)], tree="000100001111"),
            "tree-oversubscribed": cd_map([(4, SECTOR)], tree="00010001" * 16),
            "tree-invalid-slot": cd_map([(4, SECTOR)], tree=incomplete, symbols="1")}.items():
            with self.subTest(case=name): self.case(name, data)
        sector = self.work / "sector.bin"; sector.write_bytes(SECTOR)
        self.case("tree-valid-padding", cd_map([(4, SECTOR)], tree=incomplete, symbols="0"), "self", sector)
        self.case("block-cap", cd_map([(0, bytes(1024 * 1024 + 1))]), "limit")
        encoder = zlib.compressobj(0, zlib.DEFLATED, -15); packed = encoder.compress(SECTOR) + encoder.flush()
        self.assertGreater(len(packed), len(SECTOR))
        self.case("compressed-larger-than-hunk", cd_map([(0, packed)]), "self", sector)
        self.probe("bits", FIXTURES / "none.chd", self.expected)

    def test_self_long_forward_chain_cycles_range_and_sparse_zero_hunks(self):
        sector = self.work / "self-sector.bin"; sector.write_bytes(SECTOR)
        self.case("self-unaligned-32", cd_map([(5,1),(5,2),(4,SECTOR)]), "self", sector)
        self.case("self-long", cd_map([(5,n + 1) for n in range(4095)] + [(4,SECTOR)]), "self", sector)
        for name, entries in {"self-cycle": [(5,1),(5,0)], "self-loop": [(5,0)], "self-range": [(5,1)]}.items():
            with self.subTest(case=name): self.case(name, cd_map(entries))
        sparse = bytearray((FIXTURES / "none.chd").read_bytes())
        struct.pack_into(">I", sparse, struct.unpack_from(">Q", sparse, 40)[0], 0)
        self.case("sparse-zero", sparse, "zero")

    def test_metadata_chain_flags_caps_cycles_spans_and_transactional_query_errors(self):
        base = (FIXTURES / "none.chd").read_bytes(); payload = b"\1\2\3\4"
        self.case("opaque-metadata", metadata_chain(base, [payload] + [b""] * 4095), "metadata")
        self.case("metadata-payload-boundary", metadata_chain(base, [bytes(65536)]), "accept")
        self.case("metadata-total-boundary", metadata_chain(base, [bytes(65536)] * 16), "accept")
        for name, data in {"metadata-loop": metadata_chain(base, [payload], 0), "metadata-cycle": metadata_chain(base, [payload,b""], 0),
            "metadata-truncated": metadata_chain(base, [payload])[:-1]}.items():
            with self.subTest(case=name): self.case(name, data)
        for name, payloads in {"metadata-count": [b""] * 4097, "metadata-payload": [bytes(65537)],
                              "metadata-total": [bytes(65536)] * 17}.items():
            with self.subTest(case=name): self.case(name, metadata_chain(base, payloads), "limit")

    def test_zlib_requires_exact_input_output_and_stream_end(self):
        for name, payload in {"trailing": deflate(SECTOR) + b"x", "unfinished": deflate(SECTOR, zlib.Z_SYNC_FLUSH),
                              "short-output": deflate(SECTOR[:-1]), "long-output": deflate(SECTOR + b"x")}.items():
            with self.subTest(case=name): self.case("zlib-" + name, cd_map([(0,payload)]), "bad-read")

    def test_cd_split_codec_lengths_errors_and_lzma_end_status(self):
        zero = bytes(2448); oracle = self.work / "codec-zero.bin"; oracle.write_bytes(zero)
        for codec in (b"cdzl", b"cdlz"):
            base = deflate(bytes(2352)) if codec == b"cdzl" else lzma.compress(bytes(2352), format=lzma.FORMAT_RAW,
                filters=[{"id": lzma.FILTER_LZMA1, "dict_size": 4096}])
            sub = deflate(bytes(96))
            def payload(a=base, b=sub): return b"\0" + len(a).to_bytes(2,"big") + a + b
            self.case(codec.decode() + "-valid", cd_map([(0,payload())], codec=codec, sector=zero), "self", oracle)
            for name, data in {"short-header": b"\0", "zero-base": b"\0\0\0" + sub, "oversize-base": b"\0\xff\xff" + base + sub,
                "missing-sub": payload(b=b""), "base-truncated": payload(a=base[:-1]), "base-trailing": payload(a=base + b"x"),
                "sub-truncated": payload(b=sub[:-1]), "sub-trailing": payload(b=sub + b"x"), "sub-short-output": payload(b=deflate(bytes(95)))}.items():
                with self.subTest(codec=codec, case=name): self.case(codec.decode() + "-" + name, cd_map([(0,data)], codec=codec, sector=zero), "bad-read")
            # >=64KiB hunks use three length bytes and a larger ECC bitmap.
            large = bytes(2448 * 27)
            a = deflate(bytes(2352 * 27)) if codec == b"cdzl" else lzma.compress(bytes(2352 * 27), format=lzma.FORMAT_RAW,
                filters=[{"id": lzma.FILTER_LZMA1, "dict_size": 65536}])
            packed = bytes(4) + len(a).to_bytes(3,"big") + a + deflate(bytes(96 * 27))
            self.case(codec.decode() + "-large", cd_map([(0,packed)], codec=codec, sector=large), "self", oracle)
            for name, data in {"large-short-header": packed[:6], "large-oversize": bytes(4) + b"\xff\xff\xff" + packed[7:]}.items():
                with self.subTest(codec=codec, case=name): self.case(codec.decode() + name, cd_map([(0,data)], codec=codec, sector=large), "bad-read")

    def test_cd_flac_frames_format_completion_and_subcode_lengths(self):
        zero = bytes(2448); oracle = self.work / "flac-zero.bin"; oracle.write_bytes(zero); sub = deflate(bytes(96))
        frame = self.work / "constant.flacframe"; frame.write_bytes(flac_constant())
        self.probe("flac-wrapper", frame, oracle)
        self.case("flac-valid", cd_map([(0,flac_constant() + sub)], codec=b"cdfl", sector=zero), "self", oracle)
        for name, payload in {"short": b"x", "frame-short": flac_constant(100) + sub, "frame-extra": flac_constant(589) + sub,
            "rate": flac_constant(rate=8) + sub, "bits": flac_constant(bits=6) + sub, "mono": flac_constant(channels=0) + sub,
            "no-sub": flac_constant(), "sub-truncated": flac_constant() + sub[:-1], "sub-trailing": flac_constant() + sub + b"x"}.items():
            with self.subTest(case=name): self.case("flac-" + name, cd_map([(0,payload)], codec=b"cdfl", sector=zero), "bad-read")

    def test_failed_codec_allocations_cleanup_and_retry(self):
        for codec in self.pattern.CODECS:
            with self.subTest(codec=codec): self.probe("allocations", FIXTURES / (codec + ".chd"), self.expected)

    def test_one_hunk_cache_reuse_eviction_failure_and_retry(self):
        for codec in self.pattern.CODECS:
            with self.subTest(codec=codec): self.probe("cache", FIXTURES / (codec + ".chd"), self.expected)

    def toc_case(self, name, records, units, mode="toc-invalid", expected=None, tag=0x43485432):
        base = cd_map([(4,SECTOR * 8)] * ((units + 7) // 8), sector=SECTOR * 8)
        struct.pack_into(">Q", base, 32, units * 2448)
        self.case(name, metadata_chain(base, records, tag=tag), mode, expected)

    def test_cd_metadata_chtr_cht2_offsets_gaps_padding_and_limits(self):
        def verify(name, rows, units=None, modern=True):
            total = sum((r[2] + 3) // 4 * 4 for r in rows); units = total if units is None else units
            expected = bytearray(struct.pack("<IQQ", len(rows), total, units - total)); records = []; at = 0
            for n, (kind, sub, frames, gap, pgtype, pgsub, post, size, pgsize) in enumerate(rows):
                text = f"TRACK:{n+1} TYPE:{kind} SUBTYPE:{sub} FRAMES:{frames}"
                if modern: text += f" PREGAP:{gap} PGTYPE:{pgtype} PGSUB:{pgsub} POSTGAP:{post}"
                records.append(text.encode() + (b"\0" if n % 2 else b""))
                expected += struct.pack("<Q7I2B", at, n+1, frames, gap, post, (-frames) & 3, size, pgsize, pgtype.startswith('V'), 0x81)
                for value, width in ((kind,16),(sub,8),(pgtype,16),(pgsub,8)): expected += value.encode().ljust(width,b"\0")
                at += (frames + 3) // 4 * 4
            oracle = self.work / (name + '.toc'); oracle.write_bytes(expected)
            self.toc_case(name, records, units, "toc", oracle, 0x43485432 if modern else 0x43485452)
        kinds = (("MODE1",2048),("MODE1/2048",2048),("MODE1_RAW",2352),("MODE1/2352",2352),("MODE2",2336),
            ("MODE2/2336",2336),("MODE2_FORM1",2048),("MODE2/2048",2048),("MODE2_FORM2",2324),("MODE2/2324",2324),
            ("MODE2_FORM_MIX",2336),("MODE2_RAW",2352),("MODE2/2352",2352),("CDI/2352",2352),("AUDIO",2352))
        verify("chtr-types", [(kind,('NONE','RW','RW_RAW')[n % 3],5,0,'MODE1','NONE',0,size,2048) for n,(kind,size) in enumerate(kinds)], modern=False)
        verify("cht2-gaps", [('AUDIO','RW',7,2,'VMODE1','RW_RAW',3,2352,2048),('MODE2_FORM2','RW_RAW',5,99,'AUDIO','NONE',4,2324,2352)])
        for units in (12,13,16): verify('last-hunk-' + str(units), [('MODE1','NONE',9,0,'MODE1','NONE',0,2048,2048)], units)
        verify('99tracks', [('AUDIO','NONE',1,0,'MODE1','NONE',0,2352,2048)] * 99)

    def test_cd_metadata_errors_and_short_reads_do_not_publish(self):
        good = b'TRACK:1 TYPE:AUDIO SUBTYPE:NONE FRAMES:9 PREGAP:0 PGTYPE:MODE1 PGSUB:NONE POSTGAP:0'
        for name, text in {'zero': good.replace(b'FRAMES:9',b'FRAMES:0'), 'gap': good.replace(b'PREGAP:0 PGTYPE:MODE1',b'PREGAP:9 PGTYPE:VAUDIO'),
            'stored-zero': good.replace(b'PGTYPE:MODE1',b'PGTYPE:VAUDIO'), 'number': good.replace(b'TRACK:1',b'TRACK:2'),
            'overflow': good.replace(b'FRAMES:9',b'FRAMES:4294967296'), 'rounding': good.replace(b'FRAMES:9',b'FRAMES:4294967295'),
            'negative': good.replace(b'FRAMES:9',b'FRAMES:-1'), 'extra': good + b' EXTRA:1', 'nul': good + b'\0\0',
            'ascii': good + b'\xff', 'spaces': good.replace(b' TYPE:',b'  TYPE:'), 'truncated': good[:-2]}.items():
            with self.subTest(case=name): self.toc_case(name,[text],12)
        for units in (11,17): self.toc_case('bad-tail-' + str(units),[good],units)
        self.toc_case('duplicate', [good,good],24)
        for text in (good.replace(b'TYPE:AUDIO',b'TYPE:UNKNOWN'), good.replace(b'SUBTYPE:NONE',b'SUBTYPE:X'), bytes(257)):
            self.toc_case('unsupported-text',[text],12,'toc-unsupported')
        self.toc_case('mixed',[good,b'TRACK:1 TYPE:AUDIO SUBTYPE:NONE FRAMES:9'],12,'toc-unsupported',tag=[0x43485432,0x43485452])
        for tag in (0x43484344,0x43484754,0x43484744,0x44564420,0x54455354): self.toc_case('noncd',[b''],12,'toc-unsupported',tag=tag)
        tracks = [good.replace(b'TRACK:1 ',f'TRACK:{n} '.encode()).replace(b'FRAMES:9',b'FRAMES:1') for n in range(1,101)]
        self.toc_case('100tracks',tracks,400,'toc-unsupported')
        self.toc_case('toc-io',[good],12,'toc-errors')
        self.case('query-io', metadata_chain((FIXTURES/'none.chd').read_bytes(),[b'\1\2\3\4']), 'query', self.expected)
