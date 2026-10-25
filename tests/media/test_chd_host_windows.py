"""Run ctypes against a real host library; Windows requires a real Windows runner.

P6_CHD_HOST_LIBRARY=<built DLL/dylib/so> python -m unittest tests.media.test_chd_host_windows -v
No compilation, packages, downloaded corpus, or emulated Windows success.
"""
import ctypes
import os
from pathlib import Path
import struct
import tempfile
import unittest
from tests.media.test_chd_reader import cd_map, metadata_chain

ROOT = Path(__file__).resolve().parents[2]
PAYLOAD = bytes((n * 19 + n // 2448) % 251 for n in range(4 * 2448))


def image(physical=124):
    return metadata_chain(cd_map([(4, PAYLOAD)], first=physical, sector=PAYLOAD),
        [b'TRACK:1 TYPE:AUDIO SUBTYPE:NONE FRAMES:4'], tag=0x43485452)


def write_sparse(path, physical):
    with path.open('wb') as output:
        if os.name == 'nt':
            import msvcrt
            kernel = ctypes.WinDLL('kernel32', use_last_error=True)
            kernel.DeviceIoControl.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_void_p,
                ctypes.c_uint32, ctypes.c_void_p, ctypes.c_uint32, ctypes.POINTER(ctypes.c_uint32), ctypes.c_void_p]
            kernel.DeviceIoControl.restype = ctypes.c_int
            returned = ctypes.c_uint32()
            if not kernel.DeviceIoControl(msvcrt.get_osfhandle(output.fileno()), 0x900C4,
                    None, 0, None, 0, ctypes.byref(returned), None):
                raise RuntimeError('BLOCKED: FSCTL_SET_SPARSE failed: ' + str(ctypes.get_last_error()))
        output.write(image(physical))
        output.seek(physical)
        output.write(PAYLOAD)


class Info(ctypes.Structure):
    _fields_ = [(name, ctypes.c_uint32) for name in ('version', 'size', 'unit', 'hunk', 'tracks', 'reserved')] + [('bytes', ctypes.c_uint64)]


class FixtureTests(unittest.TestCase):
    def test_own_fixture_encodes_64bit_physical_offset_and_cd_metadata(self):
        large = 2**32 + 4096
        data = image(large)
        map_at = struct.unpack_from('>Q', data, 40)[0]
        self.assertEqual(int.from_bytes(data[map_at+4:map_at+10], 'big'), large)
        self.assertEqual(struct.unpack_from('>Q', data, 32)[0], len(PAYLOAD))
        self.assertIn(b'TRACK:1 TYPE:AUDIO SUBTYPE:NONE FRAMES:4', data)
        self.assertEqual((ctypes.sizeof(Info), Info.bytes.offset), (32, 24))


@unittest.skipUnless(os.environ.get('P6_CHD_HOST_LIBRARY'), 'explicit real host library required')
class NativeChdHostTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.library = Path(os.environ['P6_CHD_HOST_LIBRARY']).resolve(strict=True)
        cls.lib = ctypes.CDLL(str(cls.library)) # Cdecl, including on Windows.
        cls.lib.p6_chd_host_open.argtypes = [ctypes.c_char_p, ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(Info)]
        cls.lib.p6_chd_host_get_track.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_void_p]
        cls.lib.p6_chd_host_read.argtypes = [ctypes.c_void_p, ctypes.c_uint64, ctypes.c_void_p, ctypes.c_uint32]
        cls.lib.p6_chd_host_close.argtypes = [ctypes.c_void_p]
        cls.lib.p6_chd_host_close.restype = None
        (ROOT / 'build').mkdir(exist_ok=True)
        cls.temp = tempfile.TemporaryDirectory(prefix='chd-native-', dir=ROOT / 'build')
        cls.addClassCleanup(cls.temp.cleanup)
        cls.work = Path(cls.temp.name)

    def open_and_read(self, path):
        info, owner = Info(1, 32), ctypes.c_void_p()
        self.assertEqual(self.lib.p6_chd_host_open(str(path).encode('utf-8'), ctypes.byref(owner), ctypes.byref(info)), 0)
        try:
            self.assertEqual((info.unit, info.hunk, info.tracks, info.bytes), (2448, len(PAYLOAD), 1, len(PAYLOAD)))
            for at, count in ((0, 2448), (2440, 17), (len(PAYLOAD)-24, 24)):
                out = (ctypes.c_ubyte * count)()
                self.assertEqual(self.lib.p6_chd_host_read(owner, at, out, count), 0)
                self.assertEqual(bytes(out), PAYLOAD[at:at+count])
            out = (ctypes.c_ubyte * 2449)(*([0xA5]*2449)); before = bytes(out)
            for at, count in ((0, 2449), (len(PAYLOAD), 1), (2**64-1, 1)):
                self.assertNotEqual(self.lib.p6_chd_host_read(owner, at, out, count), 0)
                self.assertEqual(bytes(out), before)
            track = (ctypes.c_uint32 * 24)(); track[0], track[1] = 1, 96; before = bytes(track)
            self.assertEqual(self.lib.p6_chd_host_get_track(owner, 1, track), 3)
            self.assertEqual(bytes(track), before)
            self.assertEqual(self.lib.p6_chd_host_get_track(owner, 0, track), 0)
            self.assertEqual(list(track[:4]), [1, 96, 1, 4])
            self.assertEqual(bytes(track)[48:54], b'AUDIO\0')
        finally:
            self.lib.p6_chd_host_close(owner)

    def test_unicode_path_and_transactional_errors(self):
        path = self.work / 'audio-ñ-日本-📀.chd'; path.write_bytes(image())
        self.open_and_read(path)
        bad = self.work / 'truncated.chd'; bad.write_bytes(b'MComprHD' + bytes(20))
        for name in (str(bad).encode(), str(self.work / 'missing.chd').encode(), str(self.work).encode()):
            info, owner = Info(1, 32, 77), ctypes.c_void_p(0x1234); before = bytes(info)
            self.assertNotEqual(self.lib.p6_chd_host_open(name, ctypes.byref(owner), ctypes.byref(info)), 0)
            self.assertEqual((owner.value, bytes(info)), (0x1234, before))
        for version, size in ((2, 32), (1, 31)):
            info, owner = Info(version, size), ctypes.c_void_p(0x1234); before = bytes(info)
            self.assertEqual(self.lib.p6_chd_host_open(str(path).encode(), ctypes.byref(owner), ctypes.byref(info)), 1)
            self.assertEqual((owner.value, bytes(info)), (0x1234, before))

    def test_real_seek_past_four_gibibytes(self):
        path = self.work / 'offset64.chd'
        write_sparse(path, 2**32 + 4096)
        self.assertGreater(path.stat().st_size, 2**32)
        self.open_and_read(path)

    @unittest.skipUnless(os.name == 'nt', 'strict UTF8 conversion is a Windows boundary')
    def test_windows_invalid_utf8_is_rejected_transactionally(self):
        for name in (b'\xc0\xaf.chd', b'\xed\xa0\x80.chd', b'\xf4\x90\x80\x80.chd'):
            info, owner = Info(1, 32, 77), ctypes.c_void_p(0x1234); before = bytes(info)
            self.assertEqual(self.lib.p6_chd_host_open(name, ctypes.byref(owner), ctypes.byref(info)), 1)
            self.assertEqual((owner.value, bytes(info)), (0x1234, before))


if __name__ == '__main__':
    unittest.main()
