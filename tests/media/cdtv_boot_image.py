"""Original ISO/HUNK fixture; the user's TM bytes only live in temporary images.

CDFS TM layout: cdtv-qdtitle 61436de22883fc88c8b00ec38e37c67686291691.
DOS ABI: FPCSource 710d6eb5c5add99f01b73e08778e1cf64aba76ad,
rtl/amiga/m68k/doslibf.inc (Open/Read/Close/Delay precede the v36 functions).
"""
from pathlib import Path
import shlex
import struct

MAGIC = b'P6CDTV_BOOT_v1!\0'
NONCE = bytes.fromhex('37b42f1e9260c85adcf3106879e254ab')


def trademark(cue):
    """Read only the first data-track PVD and its declared TM extent, bounded."""
    cue = Path(cue)
    if cue.stat().st_size > 65536: raise ValueError('CUE too large')
    source = None
    for line in cue.read_text(encoding='latin1').splitlines():
        parts = shlex.split(line)
        if parts and parts[0].upper() == 'FILE': source = cue.parent / parts[1]
        if parts[:2] == ['TRACK', '01']:
            if parts[2] != 'MODE1/2048': raise ValueError('TM fixture requires MODE1/2048 track01')
            break
    if source is None: raise ValueError('Missing data track')
    with source.open('rb') as stream:
        stream.seek(16 * 2048); pvd = stream.read(2048)
        if pvd[:7] != b'\x01CD001\x01': raise ValueError('Missing primary volume descriptor')
        app, at = pvd[883:1395], 1
        while at + 4 <= len(app) and app[at:at+2] != b'\0\0':
            tag, size = app[at:at+2], int.from_bytes(app[at+2:at+4], 'big')
            if size > len(app) - at - 4: raise ValueError('CDFS option extent')
            if tag == b'TM':
                if size != 20: raise ValueError('TM option layout')
                count, lba, *_ = struct.unpack('>5I', app[at+4:at+24])
                if not 0 < count <= 65536 or not lba: raise ValueError('TM extent')
                stream.seek(lba * 2048); tm = stream.read(count)
                if len(tm) != count: raise ValueError('Short TM source')
                return tm
            at += 4 + size
    raise ValueError('Missing TM option')


def program():
    """PC-relative 68000 code and private data, one writable HUNK_CODE, no OS code."""
    code, labels, fixups = bytearray(), {}, []
    def words(*values): code.extend(struct.pack('>' + 'H' * len(values), *(v & 65535 for v in values)))
    def relative(op, label):
        words(op); fixups.append((len(code), label)); words(0)
    words(0x48e7, 0x3c22, 0x2c78, 4)  # preserve d2-d5/a2/a6; ExecBase
    relative(0x45fa, 'data'); words(0x52aa, 36)  # diagnostic stage1: actual entry
    relative(0x43fa, 'dos'); words(0x7000, 0x4eae, -552, 0x2540, 40, 0x52aa, 36, 0x4a80)  # OpenLibrary v0; result/stage2
    relative(0x6700, 'end'); words(0x2c40)
    relative(0x41fa, 'path')
    words(0x2208, 0x243c, 0, 1005, 0x4eae, -30, 0x2540, 44, 0x52aa, 36, 0x2a00)  # Open OLD; result/stage3; d5=fh
    relative(0x6700, 'close')
    words(0x2205, 0x41ea, 16, 0x2408, 0x7610, 0x4eae, -42, 0x2540, 48, 0x52aa, 36, 0x2800, 0x2205, 0x4eae, -36)
    words(0x0c84, 0, 16); relative(0x6600, 'close')
    words(0x52aa, 36)  # stage5: read length checked, about to call Delay
    labels['loop'] = len(code)
    words(0x7201, 0x4eae, -198, 0x52aa, 32)  # Delay(1), then ++private counter
    relative(0x6000, 'loop')
    labels['close'] = len(code)
    words(0x224e, 0x2c78, 4, 0x4eae, -414)
    labels['end'] = len(code)
    words(0x7014, 0x4cdf, 0x443c, 0x4e75)
    for name, data in (('dos', b'dos.library\0'), ('path', b'SYS:P6DATA\0'), ('data', MAGIC + bytes(36))):
        labels[name] = len(code); code.extend(data); code.extend(bytes(len(code) % 2))
    for at, label in fixups: struct.pack_into('>h', code, at, labels[label] - at)
    code.extend(bytes(-len(code) % 4)); n = len(code) // 4
    return struct.pack('>8I', 1011, 0, 1, 0, 0, n, 1001, n) + code + struct.pack('>I', 1010)


def image(tm, nonce=NONCE, *, startup=True):
    """ISO9660 with both path tables, C/S directories, fixed dates and CDFS options."""
    if len(nonce) != 16 or not 0 < len(tm) <= 65536: raise ValueError('Fixture inputs')
    def dual(value, width): return value.to_bytes(width, 'little') + value.to_bytes(width, 'big')
    def record(name, lba, size, directory=False):
        n = 33 + len(name) + (not len(name) % 2)
        return bytes((n, 0)) + dual(lba, 4) + dual(size, 4) + bytes((91, 1, 1, 0, 0, 0, 0, 2 if directory else 0, 0, 0)) + dual(1, 2) + bytes((len(name),)) + name + bytes(n - 33 - len(name))
    def path(name, lba, parent, endian):
        return bytes((len(name), 0)) + lba.to_bytes(4, endian) + parent.to_bytes(2, endian) + name + bytes(len(name) % 2)
    executable = program(); tm_lba = 23; code_lba = tm_lba + (len(tm) + 2047) // 2048
    data_lba, script_lba = code_lba + (len(executable) + 2047) // 2048, code_lba + (len(executable) + 2047) // 2048 + 1
    sectors = script_lba + 17; iso = bytearray(sectors * 2048)
    def put(lba, data): iso[lba * 2048:lba * 2048 + len(data)] = data
    root = record(b'\0', 20, 2048, True)
    pvd = bytearray(2048); pvd[:7] = b'\x01CD001\x01'; pvd[8:40] = b'CDTV'.ljust(32); pvd[40:72] = b'P6CDTV_BOOT'.ljust(32)
    pvd[80:88] = dual(sectors, 4); pvd[120:124] = dual(1, 2); pvd[124:128] = dual(1, 2); pvd[128:132] = dual(2048, 2)
    for endian, lba in (('little', 18), ('big', 19)):
        table = path(b'\0', 20, 1, endian) + path(b'C', 22, 1, endian) + path(b'S', 21, 1, endian)
        put(lba, table)
    pvd[132:140] = dual(len(table), 4); struct.pack_into('<I', pvd, 140, 18); struct.pack_into('>I', pvd, 148, 19); pvd[156:190] = root
    pvd[190:813] = b' ' * 623
    for at in (813, 830, 847, 864): pvd[at:at+17] = b'1991010100000000\0'
    pvd[881] = 1
    options = b'\0TM' + struct.pack('>H5I', 20, len(tm), tm_lba, 0, 0, 0) + b'\0\0'
    pvd[883:883+len(options)] = options
    put(16, pvd); put(17, b'\xffCD001\x01')
    put(20, root + record(b'\1', 20, 2048, True) + record(b'C', 22, 2048, True) + record(b'P6DATA;1', data_lba, 16) + record(b'S', 21, 2048, True))
    script = b'C:P6CDTV\n'
    put(21, record(b'\0', 21, 2048, True) + record(b'\1', 20, 2048, True) + (record(b'Startup-Sequence;1', script_lba, len(script)) if startup else b''))
    put(22, record(b'\0', 22, 2048, True) + record(b'\1', 20, 2048, True) + record(b'P6CDTV;1', code_lba, len(executable)))
    put(tm_lba, tm); put(code_lba, executable); put(data_lba, nonce); put(script_lba, script)
    return bytes(iso)
