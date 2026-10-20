"""Compressed CHD -> host ABI -> real DiscSystem/regions/source; no guest."""
import ctypes
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile
import unittest
from tests.media import test_cd_source_csharp as source, test_chd_reader as reader
from tests.media.test_chd_canonical_csharp import subq


class ChdHostCSharpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not source.DOTNET or not source.CACHE.is_dir(): raise RuntimeError('BLOCKED: local dotnet/cache required')
        cls.temp=tempfile.TemporaryDirectory(prefix='chd-host-',dir=source.ROOT/'build'); cls.addClassCleanup(cls.temp.cleanup)
        cls.work=Path(cls.temp.name); cls.dll,cls.env=source.build_disc_source(cls.work)
        for name in ('DYLD_LIBRARY_PATH','LD_LIBRARY_PATH'): cls.env.pop(name,None)
        spec=importlib.util.spec_from_file_location('chd_host_recipe',source.ROOT/'integration/bizhawk/patches/build_tree.py')
        recipe=importlib.util.module_from_spec(spec); spec.loader.exec_module(recipe)
        cls.library=recipe.build_chd_host(cls.dll.parent)
        cls.pattern=reader.dependency.load('chd_host_pattern',source.ROOT/'tests/fixtures/chd/generate.py')

    def test_four_codecs_real_mount_regions_audio_subq_crossings_and_ownership(self):
        audio=self.pattern.audio(); expected=b''.join(audio[n*2352:(n+1)*2352]+subq(1,1,n,0,0) for n in range(40))
        identity=hashlib.sha256(b'P6CD2448\0'+struct.pack('<8I',1,40,1,0,0,40,0,0)+expected).hexdigest().upper()
        ids=[]
        hashes=json.loads((source.ROOT/'tests/fixtures/chd/sha256.json').read_text())
        for codec in self.pattern.CODECS:
            with self.subTest(codec=codec):
                original=source.ROOT/'tests/fixtures/chd'/(codec+'.chd')
                self.assertEqual(hashlib.sha256(original.read_bytes()).hexdigest(),hashes[original.name])
                path=self.work/(codec+'-á.chd'); shutil.copy2(original,path)
                result=subprocess.run([source.DOTNET,str(self.dll),'chd-host',str(path)],env=self.env,capture_output=True,text=True,timeout=60)
                self.assertEqual(result.returncode,0,result.stdout+result.stderr)
                rows=result.stdout.splitlines(); ids.append(rows[0]); self.assertEqual(rows[0],identity)
                self.assertEqual(rows[1:],['0 40 1 1 0 0 False 7 2'])
                self.assertEqual(Path(str(path)+'.actual').read_bytes(),expected)
                self.assertEqual(Path(str(path)+'.units').read_bytes(),self.pattern.expected()[19577:19577+2448])
        self.assertEqual(len(set(ids)),1) # Codec/path are not canonical media identity.

    def test_native_layout_version_range_and_failed_open_are_transactional(self):
        class Info(ctypes.Structure):
            _fields_=[('version',ctypes.c_uint32),('size',ctypes.c_uint32),('unit',ctypes.c_uint32),('hunk',ctypes.c_uint32),
                      ('tracks',ctypes.c_uint32),('reserved',ctypes.c_uint32),('bytes',ctypes.c_uint64)]
        lib=ctypes.CDLL(str(self.library)); lib.p6_chd_host_open.argtypes=[ctypes.c_char_p,ctypes.POINTER(ctypes.c_void_p),ctypes.POINTER(Info)]
        lib.p6_chd_host_read.argtypes=[ctypes.c_void_p,ctypes.c_uint64,ctypes.c_void_p,ctypes.c_uint32]
        lib.p6_chd_host_get_track.argtypes=[ctypes.c_void_p,ctypes.c_uint32,ctypes.c_void_p]; lib.p6_chd_host_close.argtypes=[ctypes.c_void_p]
        info=Info(1,32); owner=ctypes.c_void_p(0x1234); before=bytes(info)
        descriptors=len(os.listdir('/dev/fd'))
        for data in (b'',b'MComprHD'+bytes(116),(source.ROOT/'tests/fixtures/chd/cdzl.chd').read_bytes()[:100]):
            bad=self.work/'bad.chd'; bad.write_bytes(data)
            self.assertNotEqual(lib.p6_chd_host_open(str(bad).encode(),ctypes.byref(owner),ctypes.byref(info)),0)
            self.assertEqual((owner.value,bytes(info)),(0x1234,before))
            self.assertEqual(len(os.listdir('/dev/fd')),descriptors)
        path=source.ROOT/'tests/fixtures/chd/cdzl.chd'
        info.version=2; self.assertNotEqual(lib.p6_chd_host_open(str(path).encode(),ctypes.byref(owner),ctypes.byref(info)),0)
        info.version=1; self.assertEqual(lib.p6_chd_host_open(str(path).encode(),ctypes.byref(owner),ctypes.byref(info)),0)
        try:
            self.assertEqual(len(os.listdir('/dev/fd')),descriptors+1)
            self.assertEqual((ctypes.sizeof(info),Info.bytes.offset,info.unit,info.hunk,info.tracks,info.bytes),(32,24,2448,19584,1,97920))
            out=(ctypes.c_ubyte*2449)(*([0xa5]*2449)); before=bytes(out)
            for offset,count in ((0,2449),(97920,1),(2**64-1,1)):
                self.assertNotEqual(lib.p6_chd_host_read(owner,offset,out,count),0); self.assertEqual(bytes(out),before)
            track=(ctypes.c_uint32*24)(); track[0]=1; track[1]=96; before=bytes(track)
            self.assertNotEqual(lib.p6_chd_host_get_track(owner,1,track),0); self.assertEqual(bytes(track),before)
            self.assertEqual(lib.p6_chd_host_get_track(owner,0,track),0)
            self.assertEqual(list(track[:8]),[1,96,1,40,0,0,0,0]); self.assertEqual(bytes(track)[48:54],b'AUDIO\0')
        finally: lib.p6_chd_host_close(owner)
        self.assertEqual(len(os.listdir('/dev/fd')),descriptors)
