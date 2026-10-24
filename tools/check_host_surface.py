#!/usr/bin/env python3
"""Conservative lexical audit, not a preprocessor or proof of link closure."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import posixpath
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
TOKENS = re.compile(
    r'(?P<comment>//[^\n]*|/\*[\s\S]*?\*/)'
    r'|(?P<string>(?:u8|u|U|L)?R"(?P<delim>[^ ()\\\t\r\n]{0,16})\([\s\S]*?\)(?P=delim)"'
    r'|"(?:\\[\s\S]|[^"\\])*"|\'(?:\\[\s\S]|[^\'\\])*\')')
HEADERS = re.compile(
    r'^(?:windows\.h|tchar\.h|winsock\w*\.h|winuser\.h|process\.h|'
    r'pthread\.h|threads\.h|thread|mutex|condition_variable|libretro\.h|'
    r'SDL[^/]*(?:/.*)?|Qt[^/]*(?:/.*)?|QApplication|QWidget|'
    r'sys/(?:socket|time)\.h|dlfcn\.h|mach/.*)$', re.I)
SYMBOLS = re.compile(
    r'\b(?:Sleep|SleepEx|sleep|usleep|nanosleep|sched_yield|uae_msleep|'
    r'clock_gettime|gettimeofday|time|getlocaltime|mach_absolute_time|'
    r'QueryPerformanceCounter|GetTickCount\w*|read_processor_time|'
    r'target_get_display_scanline|pthread_\w+|CreateThread|SDL_\w+|'
    r'LoadLibrary\w*|GetProcAddress|dlopen|dlsym|socket|connect|'
    r'fopen|freopen|popen|system|retro_\w+)\b')


def mask(text: str, strings: bool) -> str:
    def replace(match):
        if match.group('comment') is not None or strings:
            return re.sub(r'[^\n]', ' ', match.group())
        return match.group()
    return TOKENS.sub(replace, text)


def scan(text: str) -> list[tuple[int, str]]:
    # Phase-2 line splicing precedes comments. Diagnostics use logical lines.
    text = re.sub(r'\\\r?\n', '', text)
    comments = mask(text, strings=False)
    code = mask(text, strings=True)
    findings = []
    for number, (line, clean) in enumerate(zip(comments.splitlines(), code.splitlines()), 1):
        directive = re.match(r'^\s*(?:#|%:)\s*(?:include|include_next|import)\b(.*)', clean)
        if directive:
            argument = re.sub(r'^\s*(?:#|%:)\s*\w+\s*', '', line).strip()
            literal = re.fullmatch(r'[<"]([^>"\n]+)[>"]', argument)
            if not literal:
                findings.append((number, 'include no literal: requiere revision'))
            elif HEADERS.fullmatch(literal[1]):
                findings.append((number, f'include prohibido: {literal[1]}'))
            continue
        findings.extend((number, f'simbolo host: {m.group()}') for m in SYMBOLS.finditer(clean))
    return findings


def include_graph(root: Path, seeds: list[str]) -> tuple[dict, list]:
    """Conservative union of branches, including local <headers>, with cycles deduplicated."""
    sources, edges, pending = {}, [], list(seeds)
    while pending:
        name = pending.pop()
        if name in sources:
            continue
        path = root / name
        if not path.resolve().is_relative_to(root.resolve()) or path.is_symlink():
            raise ValueError(f'Include fuera del snapshot o symlink: {name}')
        text = path.read_bytes().decode('latin1')  # Preserve every byte; C tokens are ASCII.
        sources[name] = text
        text = re.sub(r'\\\r?\n', '', text)
        comments, code = mask(text, False), mask(text, True)
        for line, (original, clean) in enumerate(zip(comments.splitlines(), code.splitlines()), 1):
            if not re.match(r'^\s*(?:#|%:)\s*(?:include|include_next|import)\b', clean):
                continue
            arg = re.sub(r'^\s*(?:#|%:)\s*\w+\s*', '', original).strip()
            match = re.fullmatch(r'"([^"\n]+)"|<([^>\n]+)>', arg)
            if not match:
                raise ValueError(f'{name}:{line}: include no literal: {arg}')
            header = match[1] or match[2]
            dirs = ([posixpath.dirname(name)] if match[1] else []) + ['include', '.', 'od-win32']
            locations = [posixpath.normpath(posixpath.join(d, header)) for d in dirs]
            locations = [p for p in locations if not (p.startswith('../') or p.startswith('/'))]
            if not locations:
                raise ValueError(f'{name}:{line}: include escapa del snapshot: {header}')
            resolved = next((p for p in locations if (root / p).is_file()), None)
            edges.append({'caller': name, 'line': line, 'header': header, 'resolved': resolved})
            if resolved:
                pending.append(resolved)
    return sources, edges


def audit_surface(root: Path) -> dict:
    selection = json.loads((root / 'manifests/source-manifest.json').read_text())
    surface = json.loads((root / 'manifests/host-surface.json').read_text())
    if surface['commit'] != selection['commit'] or surface['schema_version'] != 1:
        raise ValueError('Superficie host y seleccion no comparten pin/version')
    sources, edges = include_graph(root / 'upstream/winuae', selection['audit_sources'])
    contracts = surface['contracts']
    symbols, headers = {}, {}
    for contract_id, contract in contracts.items():
        for key in ('semantics', 'native', 'waterbox', 'test'):
            if not isinstance(contract[key], str) or not contract[key].strip():
                raise ValueError(f'Contrato incompleto: {contract_id}.{key}')
        for key, index in [('symbols', symbols), ('headers', headers)]:
            for item in contract.get(key, []):
                if item in index:
                    raise ValueError(f'Contrato duplicado: {item}')
                index[item] = contract_id
    errors, occurrences = [], {}
    watched = re.compile(r'\b(?:' + surface['watch_pattern'] + r')\b')
    declared = set(symbols)
    for name in surface['declaration_headers']:
        if name not in sources:
            raise ValueError(f'Header auditado fuera del grafo: {name}')
        # This recognizes explicit extern functions, not arbitrary C++ declarations.
        declared.update(re.findall(r'\bextern\s+[^;{}]*?\b([A-Za-z_]\w*)\s*\(', mask(sources[name], True)))
    for name in sources:
        code = mask(re.sub(r'\\\r?\n', '', sources[name]), True)
        tokens = set(re.findall(r'\b[A-Za-z_]\w*\b', code))
        observed = (tokens & declared) | set(watched.findall(code)) | set(SYMBOLS.findall(code))
        for symbol in sorted(observed):
            if symbol not in symbols:
                errors.append(f'{name}: simbolo sin contrato: {symbol}')
            else:
                occurrences.setdefault(symbol, []).append(name)
    for edge in edges:
        resolved, header = edge['resolved'], edge['header']
        boundary = resolved is None or resolved.startswith('od-win32/')
        if boundary and header not in headers:
            errors.append(f"{edge['caller']}:{edge['line']}: include sin contrato: {header}")
    if errors:
        raise ValueError('\n'.join(sorted(set(errors))))
    return {'sources': len(sources), 'edges': len(edges), 'symbols': occurrences,
            'contracts': len(contracts), 'status': 'static-inventory-only'}


def validate_inventory(root: Path) -> int:
    data = json.loads((root / 'manifests/source-manifest.json').read_text())
    lock = json.loads((root / 'plan/upstream.lock.json').read_text())
    pin = next(s['commit'] for s in lock['sources'] if s['id'] == 'winuae')
    snapshot = json.loads((root / 'manifests/upstream-files.json').read_text())
    if data['schema_version'] != 1 or data['status'] != 'host-contracts-planned':
        raise ValueError('Version/estado del inventario no soportado')
    if data['commit'] != pin or snapshot['source']['commit'] != pin:
        raise ValueError('Pin del inventario no coincide con lock/snapshot')
    candidates = data['audit_sources']
    if not candidates or len(candidates) != len(set(candidates)):
        raise ValueError('Lista candidata vacia o duplicada')
    paths = set(candidates)
    selection = data['selection']
    upstream, generated = selection['upstream_units'], selection['generated_units']
    if set(upstream) & set(generated) or len(upstream + generated) != len(set(upstream + generated)):
        raise ValueError('Unidades duplicadas o generadas compiladas dos veces')
    if not set(upstream + generated) <= paths:
        raise ValueError('Unidades seleccionadas fuera de la auditoria')
    paths.update(selection['textual_inputs'])
    for name, reason in selection['excluded_units'].items():
        if not reason.strip() or name in upstream + generated:
            raise ValueError(f'Exclusion sin motivo o seleccion contradictoria: {name}')
        paths.add(name)
    for generator in data['generators']:
        paths.update(generator['inputs'])
        paths.update(generator['outputs'])
    known = {f['path']: f['sha256'] for f in snapshot['files']}
    for name in sorted(paths):
        relative = PurePosixPath(name)
        if (relative.is_absolute() or '..' in relative.parts or '\\' in name
                or any(c in name for c in '*?[]') or name not in known):
            raise ValueError(f'Ruta no explicita o ajena al snapshot: {name}')
        path = root / 'upstream/winuae' / name
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != known[name]:
            raise ValueError(f'Input ausente o alterado: {name}')
    return len(paths)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('inventory', help='Validar seleccion propuesta, no probar enlace')
    auditor = commands.add_parser('audit', help='Verificar cobertura del inventario estatico host')
    auditor.add_argument('--json', action='store_true', help='Emitir ocurrencias y llamadores por simbolo')
    scanner = commands.add_parser('scan', help='Escanear archivos explicitos, sin expandir includes')
    scanner.add_argument('files', nargs='+', type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == 'inventory':
            count = validate_inventory(ROOT)
            print(f'Inventario valido: {count} rutas. Pendientes implementacion y enlace T06.')
            return 0
        if args.command == 'audit':
            validate_inventory(ROOT)
            report = audit_surface(ROOT)
            print(json.dumps(report, indent=2) if args.json else
                  f"HOST01 inventario: {report['sources']} archivos, {report['edges']} includes, "
                  f"{len(report['symbols'])} identificadores con contrato. No prueba compilacion/emulacion.")
            return 0
        failed = False
        for path in args.files:
            if not path.is_file() or path.suffix not in {'.c', '.cpp', '.cxx', '.h', '.hpp', '.inc'}:
                raise ValueError(f'No es un archivo fuente: {path}')
            findings = scan(path.read_text(encoding='utf-8-sig'))
            for line, message in findings:
                print(f'{path}:{line} (linea logica): {message}')
            failed |= bool(findings)
        print('Scan lexico local: ' + ('RECHAZADO' if failed else 'sin coincidencias; no prueba cierre host'))
        return int(failed)
    except (OSError, UnicodeError, ValueError, KeyError, TypeError, StopIteration) as error:
        print(f'ERROR: {error}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
