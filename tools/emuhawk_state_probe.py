#!/usr/bin/env python3
"""Localize full legacy UAE state differences; never accept T40k by normalization."""
import base64
import hashlib
import json
from pathlib import Path
import re
import struct
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools import emuhawk_coexistence as c, waterbox_state_probe as d

MARKER = 'P6_EMUHAWK_STATE_PROBE '
inputs, prepare = c.inputs, c.prepare
FIELDS = [('Frame', 4), ('LagCount', 4), ('IsLagFrame', 1), ('CycleCount', 8), ('BufferWidth', 4), ('BufferHeight', 4),
    ('clockTime', 8), ('clockRemainder', 4), ('ejectPressed', 1), ('insertPressed', 1), ('nextSlotPressed', 1), ('nextDrivePressed', 1),
    ('currentDrive', 4), ('currentSlot', 4), ('driveSlot0', 4), ('driveSlot1', 4), ('driveSlot2', 4), ('driveSlot3', 4), ('currentDisc', 4)]
NAMES = {phase + '.' + point + str(i) for phase in ('record', 'replay') for point in ('initial', 'final') for i in (0, 1)}


def decode(capture, frame):
    d.require(isinstance(capture, dict) and set(capture) == {'data', 'chunks', 'pages'}, 'Legacy capture schema differs')
    d.require(isinstance(capture['data'], str) and len(capture['data']) <= 12 << 20, 'Legacy encoded snapshot bound')
    data = base64.b64decode(capture['data'], validate=True); chunks = capture['chunks']
    d.require(69 < len(data) <= 8 << 20 and isinstance(chunks, list) and len(chunks) > len(FIELDS), 'Legacy capture bound')
    boundary = len(data) - 69; tail = chunks[-len(FIELDS):]; offset = boundary; regions = []
    for (name, size), chunk in zip(FIELDS, tail):
        d.require(isinstance(chunk, list) and len(chunk) == 2 and all(type(n) is int for n in chunk) and chunk == [offset, size], 'Legacy managed write/width differs: ' + name)
        if size == 1: d.require(data[offset] in (0, 1), 'Invalid managed boolean')
        regions.append((offset, offset + size, 'managed.' + name, None, None)); offset += size
    d.require(struct.unpack_from('<i', data, boundary)[0] == frame, 'Managed frame differs from capture point')
    runtime = {'data': base64.b64encode(data[:boundary]).decode(), 'chunks': chunks[:-len(FIELDS)], 'pages': capture['pages']}
    prefix, native = d.decode(runtime)
    d.require(len(prefix) == boundary and offset == len(data), 'Full legacy state lost bytes')
    return data, native + regions


def compare(left, right):
    a, ar = left; b, br = right
    d.require(len(a) == len(b) and ar == br, 'Legacy snapshot structures differ; unresolved')
    ranges = []
    for start, end, kind, guest, prot in ar:
        at = start
        while at < end:
            if a[at] == b[at]: at += 1; continue
            first = at
            while at < end and a[at] != b[at]: at += 1
            ranges.append({'offset': first, 'bytes': at - first, 'section': kind,
                'guest': None if guest is None else guest + first - start, 'protection': prot,
                'expected': a[first:min(at, first + 16)].hex(), 'actual': b[first:min(at, first + 16)].hex()})
    d.require(len(ranges) <= 4096, 'Legacy diagnostic range bound; unresolved')
    return {'equal': a == b, 'bytes': len(a), 'left_sha256': hashlib.sha256(a).hexdigest(), 'right_sha256': hashlib.sha256(b).hexdigest(), 'ranges': ranges}


def validate(record, native):
    d.require(isinstance(record, dict) and set(record) == {'runtime', 'format', 'frames', 'cores', 'native', 'isolation', 'av', 'captures'}
        and record['runtime'] == '10.0.13' and record['format'] == 'bk2' and type(record['frames']) is int and record['frames'] == 12
        and record['cores'] == ['UAE', 'PUAE6'] and record['isolation'] is True
        and record['native'] == {n: item['sha256'] for n, item in native.items()}, 'Legacy diagnostic identity/coverage differs')
    d.require(isinstance(record['av'], dict) and set(record['av']) == {'UAE', 'PUAE6'}
        and all(isinstance(value, str) and re.fullmatch('[0-9a-f]{64}', value) for value in record['av'].values()), 'Missing original A/V comparisons')
    d.require(isinstance(record['captures'], dict) and set(record['captures']) == NAMES, 'Missing/extra legacy capture')
    captures = {name: decode(value, 0 if '.initial' in name else 12) for name, value in record['captures'].items()}
    pairs = [(phase + '.' + point + '0', phase + '.' + point + '1') for phase in ('record', 'replay') for point in ('initial', 'final')]
    pairs += [('record.initial0', 'replay.initial0'), ('record.final0', 'replay.final0')]
    observations = {a + ':' + b: compare(captures[a], captures[b]) for a, b in pairs}
    return {'runtime': record['runtime'], 'format': 'bk2', 'frames': 12, 'cores': record['cores'], 'native': record['native'], 'av': record['av'],
        'observations': observations, 'conclusion': 'identical-in-this-path' if all(o['equal'] for o in observations.values()) else 'localized-state-difference'}


if __name__ == '__main__':
    try: print(json.dumps(c.s.run(state_probe=True), sort_keys=True))
    except (OSError, ValueError, KeyError, c.s.builder.RecipeError, c.s.subprocess.TimeoutExpired) as error: sys.exit('BLOCKED: ' + str(error))
