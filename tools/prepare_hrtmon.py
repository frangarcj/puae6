#!/usr/bin/env python3
"""Compile the GPL HRTMon builtin of the pinned WinUAE snapshot into build/."""
import argparse
import gzip
import hashlib
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
SOURCE_SHA = 'f3cdc921cb33d9f7b16197d66a182dda63a107e331032fb30ffad96dc98b9367'
RAW_SHA = '15e3e106248822665cc64f5c9a3ed5f52a67c07f81b06eb7118da39099d171f5'


def prepare(root=ROOT):
    root = Path(root)
    source = (root/'upstream/winuae/hrtmon.rom.cpp').read_bytes()
    if hashlib.sha256(source).hexdigest() != SOURCE_SHA:
        raise ValueError('HRTMon source differs from the pinned snapshot')
    packed = bytes(int(x, 16) for x in re.findall(rb'0x([0-9a-fA-F]{2})', source))
    if len(packed) != 41970:
        raise ValueError('HRTMon packed size differs')
    raw = gzip.decompress(packed)
    if len(raw) != 198732 or raw[4:8] != b'HRT!' or hashlib.sha256(raw).hexdigest() != RAW_SHA:
        raise ValueError('HRTMon raw content differs from the pinned builtin')
    rows = [','.join(f'0x{x:02x}' for x in raw[at:at+24]) for at in range(0, len(raw), 24)]
    cpp = ('// Generated from pinned WinUAE hrtmon.rom.cpp (GPL HRTMon); do not edit.\n'
           f'// raw sha256: {RAW_SHA}\n#include <stdint.h>\nnamespace p6_bridge {{\n'
           'extern const uint8_t hrtmon_builtin[198732] = {\n' + ',\n'.join(rows) + '\n};\n}\n').encode()
    output = root/'build/assets/hrtmon_builtin.cpp'
    output.parent.mkdir(parents=True, exist_ok=True)
    if not output.is_file() or output.read_bytes() != cpp:
        output.write_bytes(cpp)
    return output


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=ROOT)
    print(prepare(parser.parse_args().root))
