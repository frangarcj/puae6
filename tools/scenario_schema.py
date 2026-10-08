"""Strict v1 scenario and successful emulator-output schemas (standard library).

Validation is a HARNESS assertion, never evidence that an emulator ran.
Unit indices in inputs/events are zero-based; checkpoints count completed units.
At a boundary reset precedes media events (sequence order), then input. Input
keys/buttons hold until replaced; mouse deltas default to zero on later units.
Keys is a 128-bit hexadecimal integer: bit n maps to Amiga key n (not text).
Program describes a raw CPU-test payload, not a firmware ROM or a boot claim.
Machines: A500 (OCS, 512 KiB Chip + 512 KiB Slow) and A600 (ECS, 1 MiB Chip, no Slow); the profile stays deterministic-a500.
A 'slow' probe is invalid on a machine without Slow RAM.
"""
import hashlib
import json
from pathlib import Path
import re

U64 = (1 << 64) - 1
HASH = re.compile(r'[0-9a-f]{64}\Z')
# machine -> (chip_ram_bytes, slow_ram_bytes). Both machines are presets inside the build profile deterministic-a500
# (docs/01-architecture.md): the A500 is OCS with 512 KiB Chip + 512 KiB Slow, the A600 is ECS with 1 MiB Chip and no Slow.
MACHINES = {'A500': (524288, 524288), 'A600': (1048576, 0)}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def obj(value, fields):
    require(type(value) is dict and set(value) == set(fields.split()), 'Campos invalidos: ' + fields)


def integer(value, low=0, high=U64):
    require(type(value) is int and low <= value <= high, 'Entero fuera de rango')


def array(value, limit):
    require(type(value) is list and len(value) <= limit, 'Lista invalida o excesiva')


def digest(value):
    require(type(value) is str and HASH.fullmatch(value), 'SHA-256 invalido')


def canonical_hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                    ensure_ascii=True, allow_nan=False).encode()).hexdigest()


def load_json(path):
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, 'Campo JSON duplicado: ' + key)
            result[key] = value
        return result
    def constant(value):
        raise ValueError('Constante JSON invalida: ' + value)
    with Path(path).open('rb') as stream:
        raw = stream.read(8 * 1024 * 1024 + 1)
    require(len(raw) <= 8 * 1024 * 1024, 'JSON excede 8 MiB')
    return json.loads(raw, object_pairs_hook=pairs, parse_constant=constant)


def validate_scenario(s):
    obj(s, 'schema_version scenario_id profile config firmware media program units inputs events checkpoints probes render audio')
    integer(s['schema_version'], 1, 1)
    require(type(s['scenario_id']) is str and re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,63}', s['scenario_id']), 'ID invalido')
    require(s['profile'] == 'deterministic-a500', 'Perfil no soportado')
    c = s['config']
    obj(c, 'machine video_standard audio_rate_hz random_seed rtc_epoch_seconds chip_ram_bytes slow_ram_bytes')
    require(type(c['machine']) is str and c['machine'] in MACHINES and c['video_standard'] in ('PAL', 'NTSC'), 'Maquina invalida')
    integer(c['audio_rate_hz'], 8000, 192000)
    integer(c['random_seed'])
    integer(c['rtc_epoch_seconds'], -(1 << 63), (1 << 63) - 1)
    for ram, size in zip(('chip_ram_bytes', 'slow_ram_bytes'), MACHINES[c['machine']]):
        integer(c[ram], size, size)
    integer(s['units'], 1, 100000)
    for flag in ('render', 'audio'):
        require(type(s[flag]) is bool, 'Flag de presentacion invalido')
    array(s['firmware'], 1)
    for value in s['firmware']:
        digest(value)
    array(s['media'], 16)
    ids = set()
    for medium in s['media']:
        obj(medium, 'format sha256')
        require(medium['format'] == 'adf', 'Formato no soportado')
        digest(medium['sha256'])
        require(medium['sha256'] not in ids, 'Medio duplicado')
        ids.add(medium['sha256'])
    if s['program'] is not None:
        p = s['program']
        obj(p, 'sha256 load_address entry_address size_bytes')
        digest(p['sha256'])
        integer(p['load_address'], 0, c['chip_ram_bytes'] - 2)
        integer(p['size_bytes'], 2, c['chip_ram_bytes'] - p['load_address'])
        integer(p['entry_address'], p['load_address'], p['load_address'] + p['size_bytes'] - 2)
        require(not (p['load_address'] | p['entry_address'] | p['size_bytes']) & 1, 'Programa desalineado')
    array(s['inputs'], s['units'])
    previous = -1
    for packet in s['inputs']:
        obj(packet, 'unit keys ports')
        integer(packet['unit'], previous + 1, s['units'] - 1)
        previous = packet['unit']
        require(type(packet['keys']) is str and re.fullmatch(r'[0-9a-f]{32}', packet['keys']), 'Bitmap de teclas invalido')
        array(packet['ports'], 2)
        require(len(packet['ports']) == 2, 'Se requieren dos puertos')
        for port in packet['ports']:
            obj(port, 'type buttons dx dy')
            require(port['type'] in ('none', 'joystick', 'mouse'), 'Puerto no soportado')
            integer(port['buttons'], 0, 7 if port['type'] == 'mouse' else 127)
            for axis in ('dx', 'dy'):
                integer(port[axis], -32768, 32767)
                require(port['type'] == 'mouse' or port[axis] == 0, 'Delta sin raton')
            require(port['type'] != 'none' or port['buttons'] == 0, 'Puerto none con botones')
    array(s['events'], 100000)
    previous, resets = (-1, -1), set()
    for event in s['events']:
        require(type(event) is dict and event.get('type') in ('reset', 'mount', 'eject'), 'Evento desconocido')
        obj(event, 'unit sequence type' + ('' if event['type'] == 'reset' else ' drive media_index'))
        integer(event['unit'], 0, s['units'] - 1)
        integer(event['sequence'])
        order = (event['unit'], event['sequence'])
        require(order > previous, 'Eventos fuera de orden o duplicados')
        require(event['type'] != 'reset' or event['unit'] != previous[0], 'Reset debe ir primero')
        previous = order
        if event['type'] == 'reset':
            require(event['unit'] not in resets, 'Reset duplicado')
            resets.add(event['unit'])
        else:
            integer(event['drive'], 0, 3)
            if event['type'] == 'mount':
                integer(event['media_index'], 0, len(s['media']) - 1)
            else:
                require(event['media_index'] is None, 'Eject no selecciona medio')
    array(s['checkpoints'], s['units'])
    previous = 0
    for checkpoint in s['checkpoints']:
        integer(checkpoint, previous + 1, s['units'])
        previous = checkpoint
    require(previous == s['units'], 'Falta checkpoint final')
    array(s['probes'], 64)
    for probe in s['probes']:
        obj(probe, 'region offset length')
        require(probe['region'] in ('chip', 'slow'), 'Region desconocida')
        size = c[probe['region'] + '_ram_bytes']
        integer(probe['offset'], 0, size)
        integer(probe['length'], 1, size - probe['offset'])
    return s


def validate_result(r, scenario):
    """Validate complete success telemetry only; does not authenticate its producer."""
    validate_scenario(scenario)
    obj(r, 'schema_version scenario_id status exit_code evidence core_build_id config_hash media_hash boundaries host_metrics')
    integer(r['schema_version'], 1, 1)
    require(r['status'] == 'PASS' and r['evidence'] == 'EMULATION', 'No es evidencia de emulacion')
    integer(r['exit_code'], 0, 0)
    require(r['scenario_id'] == scenario['scenario_id'], 'Escenario distinto')
    digest(r['core_build_id'])
    require(r['config_hash'] == canonical_hash(scenario['config']), 'Config distinta')
    assets = {k: scenario[k] for k in ('firmware', 'media', 'program')}
    require(r['media_hash'] == canonical_hash(assets), 'Medios distintos')
    array(r['boundaries'], scenario['units'])
    require(len(r['boundaries']) == scenario['units'], 'Salida truncada: faltan boundaries')
    ticks, samples = -1, 0
    for unit, record in enumerate(r['boundaries'], 1):
        obj(record, 'unit master_ticks hashes samples_total')
        integer(record['unit'], unit, unit)
        integer(record['master_ticks'], ticks + 1)
        integer(record['samples_total'], samples)
        ticks, samples = record['master_ticks'], record['samples_total']
        obj(record['hashes'], 'ram cpu events video pcm')
        for key, value in record['hashes'].items():
            if key in ('video', 'pcm') and not scenario['render' if key == 'video' else 'audio']:
                require(value is None, 'Hash de entrega deshabilitada debe ser null')
            else:
                digest(value)
    obj(r['host_metrics'], 'elapsed_ns peak_memory_bytes')
    for value in r['host_metrics'].values():
        integer(value)
    return r
