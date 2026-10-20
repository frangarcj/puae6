"""MED01: bounded CHD I/O, maps, SELF and metadata; codec hardening follows."""
import os
import binascii
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


def cd_map(entries, tree=None, symbols=None, widths=(24, 32, 0), first=124):
    """Legal v5 map encoder with a flat Huffman tree; fields are independently CRC'd."""
    attributes, raw, body = [], bytearray(), bytearray()
    for kind, value in entries:
        at, length, crc = first + len(body), 0, 0
        if kind < 5:
            length = len(value); body += value
            crc = binascii.crc_hqx(value if kind == 4 else SECTOR, 0xffff)
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
    struct.pack_into(">II4I3QII", header, 8, 124, 5, 0x7a6c6962, 0, 0, 0, len(entries) * 2448, 124 + len(body), 0, 2448, 2448)
    map_header = struct.pack(">I", len(packed)) + first.to_bytes(6, "big") + struct.pack(">H", binascii.crc_hqx(raw, 0xffff)) + bytes([*widths, 0])
    return header + body + map_header + packed


def metadata_chain(container, payloads, cycle=None):
    result = bytearray(container); positions, at = [], len(result)
    for payload in payloads:
        positions.append(at); at += 16 + len(payload)
    struct.pack_into(">Q", result, 48, positions[0])
    for n, payload in enumerate(payloads):
        next_at = positions[n + 1] if n + 1 < len(positions) else positions[cycle] if cycle is not None else 0
        result += struct.pack(">IIQ", 0x54455354, 0x81000000 | len(payload), next_at) + payload
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
            'target_link_libraries(probe PRIVATE p6_chd_reader)\n')
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
