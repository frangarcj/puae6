"""T66e1 native offset sources; real assets are opt-in, never copied or retained."""
import hashlib
import os
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile
import unittest
ROOT = Path(__file__).resolve().parents[2]


def chunk(tag, data):
    return tag + struct.pack("<I", len(data)) + data + bytes(len(data) & 1)


def wave(data, *, channels=2):
    body = b"WAVE" + chunk(b"JUNK", b"odd") + chunk(b"fmt ", struct.pack("<HHIIHH", 1, channels, 44100, 176400, 4, 16)) + chunk(b"data", data)
    return b"RIFF" + struct.pack("<I", len(body)) + body


@unittest.skipUnless(shutil.which("clang++"), "clang++ unavailable")
class CdFileTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        (ROOT / "build").mkdir(exist_ok=True)
        cls.temp = tempfile.TemporaryDirectory(prefix="cd-file-", dir=ROOT / "build")
        cls.addClassCleanup(cls.temp.cleanup)
        cls.work = Path(cls.temp.name)
        cls.exe = cls.work / "probe"
        command = ["clang++", "-std=c++17", "-O1", "-g", "-Wall", "-Wextra", "-Werror", "-fsanitize=address,undefined", "-fno-sanitize-recover=all",
                   "-I" + str(ROOT / "references/libretro-api/include"), "-I" + str(ROOT / "ports/portable/media"), "-x", "c++",
                   str(ROOT / "tests/media/cd_file_probe.cpp"), str(ROOT / "ports/portable/media/cd_wave.cpp"),
                   str(ROOT / "ports/portable/media/cd_source.cpp"), str(ROOT / "ports/portable/media/sha256.c"), "-o", str(cls.exe)]
        out = subprocess.run(command, capture_output=True, text=True, timeout=60)
        if out.returncode:
            raise AssertionError(out.stderr)

    def run_probe(self, *args, code=0):
        out = subprocess.run([str(self.exe), *map(str, args)], capture_output=True, text=True, timeout=30,
                             env={**os.environ, "ASAN_OPTIONS": "detect_leaks=0:max_allocation_size_mb=24"})
        self.assertEqual(code, out.returncode, out.stderr)
        return out.stdout

    def verify(self, path, kind, offset, length):
        got = dict(word.split("=") for word in self.run_probe(kind, path).split())
        self.assertEqual(1 if kind == "iso" else 2, int(got["encoding"]))
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            stream.seek(offset)
            left = length
            while left:
                data = stream.read(min(left, 65536)); self.assertTrue(data)
                digest.update(data); left -= len(data)
            hashes = []
            sector = 2048 if kind == "iso" else 2352
            for at in (offset, offset + length - sector):
                stream.seek(at); hashes.append(hashlib.sha256(stream.read(sector)).hexdigest())
        self.assertEqual((int(got["bytes"]), int(got["offset"]), got["id"], got["first"], got["last"]), (length, offset, digest.hexdigest(), *hashes))

    def test_vfs_large_offset_faults_and_streamed_identity(self):
        self.assertEqual(hashlib.sha256(bytes((n ^ (n >> 32)) * 17 & 255 for n in range(131073))).hexdigest(), self.run_probe("vfs").strip())

    def test_iso_and_wave_slice_preserve_bytes_and_reject_invalid_metadata(self):
        payload = bytes(range(256)) * 147
        iso, wav = self.work / "fixture.iso", self.work / "fixture.wav"
        iso.write_bytes(payload[:2048 * 9]); wav.write_bytes(wave(payload))
        self.verify(iso, "iso", 0, iso.stat().st_size)
        self.verify(wav, "wave", 56, len(payload))
        for bad in (b"", b"not RIFF", wave(payload, channels=1), wave(b"x"), wave(payload)[:-1], wave(payload) + b"x",
                    wave(payload).replace(b"fmt ", b"JUNK"), wave(payload).replace(b"data", b"fmt ")):
            wav.write_bytes(bad); self.run_probe("wave", wav, code=2)
        for body in (wave(payload)[8:] + chunk(b"data", payload), b"WAVE" + chunk(b"JUNK", b"") * 1025,
                     wave(payload)[8:48] + b"data\xff\xff\xff\xff", wave(payload)[8:23] + wave(payload)[24:]):
            wav.write_bytes(b"RIFF" + struct.pack("<I", len(body)) + body)
            self.run_probe("wave", wav, code=2)

    def test_real_iso_and_wave_authorized_by_environment(self):
        if not os.environ.get("P6_CD_ISO_FILE") or not os.environ.get("P6_CD_WAV_FILE"):
            self.skipTest("BLOCKED: set P6_CD_ISO_FILE and P6_CD_WAV_FILE; no ROM required")
        iso, wav = (Path(os.environ[name]) for name in ("P6_CD_ISO_FILE", "P6_CD_WAV_FILE"))
        self.verify(iso, "iso", 0, iso.stat().st_size)
        import wave as standard_wave
        with standard_wave.open(str(wav), "rb") as reader:
            length = reader.getnframes() * reader.getnchannels() * reader.getsampwidth()
        with wav.open("rb") as stream:
            stream.seek(12)
            while True:
                tag, size = struct.unpack("<4sI", stream.read(8))
                if tag == b"data":
                    offset = stream.tell(); break
                stream.seek(size + (size & 1), 1)
        self.verify(wav, "wave", offset, length)
