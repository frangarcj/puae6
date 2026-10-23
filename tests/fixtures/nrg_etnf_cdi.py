"""MIT own CDI/payload fixture converted by pinned GPL2 CDI2Nero, entirely offline."""
import functools
import hashlib
import json
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[2]
SOURCES = ROOT / 'tests/fixtures/cdi2nero'
PIN = 'f43561d27e9a75061171f0e69a872c9439ccf898'
NRG_SHA = '563558e7b8a39d583b760e280ff37565ac59d39761dcd2b6142546455bdbd210'
HOST_SHIM = r'''
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>
#include <unistd.h>
#include <errno.h>
_Static_assert(sizeof(char)==1 && sizeof(short)==2 && sizeof(long)==8, "tested LP64 host");
_Static_assert(__BYTE_ORDER__==__ORDER_LITTLE_ENDIAN__, "tested little-endian host");
#undef BIG_ENDIAN
#define MAX_PATH 4096
#define stricmp strcasecmp
extern char *gets(char *);
static struct { unsigned char nFilterIndex; } ofn = { 1 };
static inline int converter_getopt(int argc, char **argv, const char *options) {
    static int next=1; (void)options;
    while(next<argc) {
        const char *s=argv[next++];
        if(!strcmp(s,"-t")) return 't';
        if(!strcmp(s,"-h")) return 'h';
    }
    return -1;
}
#define getopt converter_getopt
'''


def own_cdi():
    # CDI2Nero cdi.c:43..97; IMG4DC20d1c148 cdihead.h:204..212.
    # Original coordinates: session1 INDEX01=0/end302; session2=11702/end12002.
    specs = ((0, 2352, 0, 302), (2, 2336, 11702, 300))
    parts, payloads, descriptors = [], [], []
    for number, (mode, stride, start, length) in enumerate(specs, 1):
        payload = bytearray()
        for index in range(length):
            marker = f'OWN TRACK {number} SECTOR {index:06d}'.encode()
            payload += marker + bytes((number,)) * (stride - len(marker))
        payloads.append(bytes(payload)); parts.append(bytes((0xD0+number,)) * (150*stride) + payload)
        descriptor = bytearray(141); mark = bytes.fromhex('000001000000ffffffff')
        descriptor[4:14] = descriptor[14:24] = mark
        for offset, value in ((54,150),(58,length),(68,mode),(84,start),(88,150+length),(108,{2336:1,2352:2}[stride])):
            struct.pack_into('<I', descriptor, offset, value)
        descriptors.append(descriptor)
    data = b''.join(parts); header = struct.pack('<H', 2)
    for descriptor in descriptors: header += struct.pack('<H', 1) + descriptor + bytes(12)
    cdi = data + header + struct.pack('<II', 0x80000004, len(data))
    assert hashlib.sha256(cdi).hexdigest() == '19e91676c3497128a961fd6a3092209be84af041d8cfa9c6c76c080305a04603'
    return cdi, tuple(payloads)


@functools.lru_cache(maxsize=1)
def converted_fixture():
    manifest = json.loads((SOURCES / 'source.json').read_text())
    assert manifest['commit'] == PIN and manifest['license'] == 'GPL-2.0'
    expected = {'cdi2nero.c','cdi2nero.h','cdi.c','cdi.h','buffer.c','buffer.h','common.c','common.h','LICENSE'}
    assert set(manifest['files']) == expected
    (ROOT / 'build').mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='nrg-producer-', dir=ROOT / 'build') as directory:
        work = Path(directory)
        for name in sorted(expected):
            assert hashlib.sha256((SOURCES / name).read_bytes()).hexdigest() == manifest['files'][name]['sha256'], name
            shutil.copyfile(SOURCES / name, work / name)
        # Only this local is partially initialized by a 4-byte read into LP64 long.
        reader = work / 'cdi.c'; text = reader.read_text()
        assert text.count('unsigned long track_mode;') == 1
        reader.write_text(text.replace('unsigned long track_mode;', 'unsigned long track_mode = 0;'))
        shim = work / 'host-compat.h'; shim.write_text(HOST_SHIM)
        command = ['clang','-std=gnu11','-O0','-g','-include',str(shim)]
        command += [str(work / name) for name in sorted(expected) if name.endswith('.c')]
        subprocess.run([*command,'-o',str(work / 'cdi2nero')], check=True, capture_output=True, timeout=60)
        cdi, payloads = own_cdi(); source = work / 'own.cdi'; source.write_bytes(cdi)
        target = work / 'own.nrg'
        subprocess.run([str(work / 'cdi2nero'),str(source),str(target),'-t'], check=True, capture_output=True, timeout=30)
        raw = target.read_bytes()
        assert raw[:1411104] == b''.join(payloads) and hashlib.sha256(raw).hexdigest() == NRG_SHA
        assert raw[1411104:] == (b'ETNF\0\0\0\x14'+struct.pack('>IIIII',0,710304,7,0,0)+b'SINF\0\0\0\x04\0\0\0\x01'
            +b'ETNF\0\0\0\x14'+struct.pack('>IIIII',710304,700800,3,11702,0)+b'SINF\0\0\0\x04\0\0\0\x01'
            +b'END!\0\0\0\0NERO'+struct.pack('>I',1411104))
        return raw, payloads
