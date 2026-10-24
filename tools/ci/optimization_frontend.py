"""Hosted frontend opt/SAN observations; no measurements or Docker substitution."""
import argparse
import hashlib
import json
from pathlib import Path
import platform
import re
import sys

from tests.simd import frontend_matrix as matrix, frontend_rgb565 as frontend
from tools.ci import hosted_matrix as hosted

ROOT = Path(__file__).resolve().parents[2]
PROFILE, MARKER = 'hosted-native-optimization-v1', 'P6_OPTIMIZATION_RESULT '
require = hosted.require


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def headers(tree):
    text = hosted.native.execute(['ninja', '-C', str(tree), '-t', 'deps'])
    require('(STALE)' not in text, 'stale frontend compiled dependencies')
    paths = {Path(line[4:]) for line in text.splitlines() if line.startswith('    ') and line[4:]}
    require(paths, 'missing compiled frontend dependency headers')
    values = {str(p): frontend.digest(p if p.is_absolute() else tree / p) for p in paths}
    return dict(count=len(values), sha256=digest(values))


def artifacts(instance):
    tree = instance.tree
    return dict(binaries={n: frontend.digest(p) for n,p in {**instance.cores, 'host':instance.host}.items()},
                build_receipt_sha256=frontend.digest(tree/'build.json'), cmake_cache_sha256=frontend.digest(tree/'CMakeCache.txt'),
                compile_commands_sha256=frontend.digest(tree/'compile_commands.json'), headers=headers(tree), generated=hosted.generated())


def run(row):
    hosted.native.preflight(row, 'O2')
    if row.startswith('macos-'): require(platform.mac_ver()[0].split('.')[0] == '26', 'frontend requires pinned macOS26/AppleClang21')
    common, tools, source = hosted.common(), hosted.tools(row), frontend.source_identity()
    variants, generated, proof, snapshots = {}, None, None, []
    for mode in ('opt','san'):
        instance = frontend.Frontend(row, ROOT/'build/hosted-optimization/frontend'/row, mode == 'san')
        instance.build(); before = artifacts(instance); instance.verify()
        require(artifacts(instance) == before, 'frontend artifacts/headers/codegen changed during verification')
        snapshots.append((instance,before))
        record = json.loads((instance.tree/'verification.json').read_text())
        built = {k:record['build'][k] for k in ('row','compiler','compiler_sha256','isa','binaries','product_variant')}
        require(built['binaries'] == before['binaries'], 'frontend binary provenance differs')
        variant = 'candidate' if row in frontend.NEON_PRODUCT_ROWS else 'current'
        variants[mode] = dict(build=built, cases=record['cases'], inputs_sha256=digest(record['inputs']),
                              product_sha256=frontend.digest(instance.cores[variant]), **{k:v for k,v in before.items() if k not in ('binaries','generated')},
                              codegen_sha256=before['generated']['receipt_sha256'])
        if generated is not None: require(generated == before['generated'], 'frontend variants use different codegen')
        generated = before['generated']
        if mode == 'san': proof = matrix.sanitizer_commands(json.loads((instance.tree/'compile_commands.json').read_text()), before['compile_commands_sha256'])
    require(all(artifacts(instance) == before for instance,before in snapshots), 'frontend variant artifacts/headers/codegen changed after SAN')
    require((hosted.common(),hosted.tools(row),frontend.source_identity()) == (common,tools,source), 'frontend sources/tools/environment changed')
    result = dict(status='PASS', profile=PROFILE, kind='frontend', common=common, row=row, tools=tools, generated=generated,
                  source_environment_sha256=source, variants=variants, sanitizer_commands=proof,
                  runner=dict(os=platform.system(),isa=platform.machine(),macos=platform.mac_ver()[0]))
    validate(result,common)
    require(len(json.dumps(result,separators=(',',':')).encode()) < 100*1024, 'frontend observations exceed100KiB')
    return result


def validate(record, expected):
    require(record['status'] == 'PASS' and record['profile'] == PROFILE and record['kind'] == 'frontend'
            and record['common'] == expected and record['row'] in matrix.ROWS, 'foreign frontend observation')
    row, variants = record['row'], record['variants']; require(set(variants) == {'opt','san'}, 'missing frontend variant')
    pin = json.loads((ROOT/'plan/toolchains.json').read_text())['portability_matrix']['rows'][row]
    runner = record['runner']; isa = 'arm64' if runner['isa'] == 'aarch64' else runner['isa']
    require(isa == pin['isa'] and runner['os'] == ('Darwin' if row.startswith('macos-') else 'Linux')
            and (not row.startswith('macos-') or runner['macos'].split('.')[0] == '26'), 'wrong frontend runner')
    tools = record['tools']; require(set(tools) == {'cc','cxx','git','cmake','ninja','patch','python'}, 'frontend tools incomplete')
    hashes = [record['source_environment_sha256'],record['generated']['receipt_sha256']]
    require(set(record['generated']['files']) == hosted.generated_names(), 'frontend codegen incomplete')
    hashes += list(record['generated']['files'].values())
    for tool in tools.values():
        require(isinstance(tool['path'],str) and tool['path'] and tool['version'], 'frontend tool identity missing')
        hashes.append(tool['sha256'])
    for name,language in (('cc','c'),('cxx','c++')):
        item=record['generated']['compilers'][language]
        require(item['sha256'] == tools[name]['sha256'] and item['version'] == tools[name]['version'], 'frontend codegen compiler mismatch')
        hashes.append(tools[name]['implementation']['sha256'])
    require(tools['cxx']['version'].splitlines()[0] == pin['compiler_version'], 'frontend compiler pin mismatch')
    for result in variants.values():
        matrix.validate_result(row,result,result['product_sha256'])
        require(result['build']['compiler'] == pin['compiler_version'] and result['build']['compiler_sha256'] == tools['cxx']['sha256']
                and result['build']['isa'] == pin['isa'] and result['codegen_sha256'] == record['generated']['receipt_sha256'], 'frontend build compiler/codegen mismatch')
        hashes += [result[k] for k in ('product_sha256','inputs_sha256','build_receipt_sha256','cmake_cache_sha256','compile_commands_sha256')]
        require(type(result['headers']['count']) is int and result['headers']['count'] > 0, 'frontend headers omitted')
        hashes.append(result['headers']['sha256'])
    proof = record['sanitizer_commands']; counts = proof['targets']
    require(set(counts) == set(matrix.SAN_TARGETS) and all(type(n) is int and n > 0 for n in counts.values())
            and type(proof['count']) is int and proof['count'] == sum(counts.values()) and proof['flags'] == list(matrix.SAN_FLAGS)
            and proof['sha256'] == variants['san']['compile_commands_sha256'], 'frontend sanitizer proof incomplete')
    require(all(isinstance(h,str) and re.fullmatch('[0-9a-f]{64}',h) for h in hashes), 'malformed frontend provenance hash')
    matrix.compare_variants(row,variants['opt'],variants['san'])
    return record


def validate_rows(records):
    require(len(records) == len(matrix.ROWS) and {r['row'] for r in records} == set(matrix.ROWS), 'missing/duplicate frontend rows')
    for mode in ('opt','san'): matrix.compare_rows({r['row']:r['variants'][mode] for r in records})


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__); parser.add_argument('--row',choices=tuple(matrix.ROWS),required=True)
    args=parser.parse_args()
    try: result=run(args.row)
    except (OSError,ValueError,RuntimeError,AssertionError,KeyError,TypeError) as error:
        result=dict(status='FAIL',profile=PROFILE,kind='frontend',diagnostic=str(error))
    print(MARKER+hosted.ci.redact(json.dumps(result,separators=(',',':'),sort_keys=True)))
    raise SystemExit(0 if result['status']=='PASS' else 1)
