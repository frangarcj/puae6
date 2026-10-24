#!/usr/bin/env python3
"""Build pinned HOST generators offline; publish only verified complete outputs."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import tempfile

from import_sources import ROOT, inventory, safe_name, verify_source
from materialize_upstream import materialize, _destination, _no_symlinks, _atomic_json


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(args, cwd, env, **kwargs):
    result = subprocess.run(list(map(str, args)), cwd=cwd, env=env,
                            stderr=subprocess.PIPE, **kwargs)
    if result.returncode:
        raise RuntimeError(f"Fallo {args}:\n{result.stderr.decode(errors='replace')}")
    return result


def _cached_output(destination, products):
    """Reject partial/altered outputs; an identity mismatch is never a cache hit."""
    if not destination.exists():
        return None
    try:
        old = json.loads((destination / 'codegen-receipt.json').read_text())
        actual = [f for f in inventory(destination) if f['path'] != 'codegen-receipt.json']
        valid = (old['schema_version'] == 1 and isinstance(old['identity'], dict)
                 and isinstance(old['tools'], dict) and isinstance(old['checks'], dict) and old['files'] == actual
                 and {f['path'] for f in actual} == set(products)
                 and all(f['mode'] == '100644' for f in actual))
    except (OSError, ValueError, KeyError, TypeError):
        valid = False
    if not valid:
        raise ValueError(f"Salida existente alterada o incompleta; no se sobrescribe: {destination}")
    return old


def _dependencies(data):
    return [Path(entry).resolve() for entry in shlex.split(
        data.replace('\\\n', '').split(':', 1)[1])]


def _host_inputs(root, upstream, generated, recipe, compilers, env):
    """Resolve current includes without compiling or running a generator."""
    inputs = {}
    sources = []
    verified = (upstream, generated, root / 'upstream/winuae', root / 'tools/codegen_host')
    truth = root / 'tests/codegen/blitter_truth.c'
    includes = ['-I' + str(root / 'tools/codegen_host'), '-I' + str(upstream / 'include')]
    for generator in recipe['generators']:
        language = generator['language']
        for source in generator['sources']:
            prefix, relative = source.split('/', 1)
            path = {'upstream': upstream, 'generated': generated}[prefix] / relative
            sources.append((language, recipe['flags'][language], includes, path))
    for directory in (root / 'upstream/winuae', generated):
        sources.append(('c', ['-std=c11'], ['-I' + str(directory)], truth))
    for language, flags, search, source in sources:
        data = run([compilers[language]['path'], *flags, *search, '-x', language,
                    '-M', '-MT', 'p6_dependencies', source], root, env, stdout=subprocess.PIPE).stdout.decode()
        for path in _dependencies(data):
            if path != truth and not any(path.is_relative_to(base) for base in verified):
                inputs[str(path)] = sha(path)
    return inputs


def generate(root=ROOT, output="build/generated", cc="clang", cxx="clang++", *, force=False, refresh_receipt=False):
    root = Path(root).resolve()
    destination = _destination(root, output)
    receipt, upstream = materialize(root, force=force or refresh_receipt)
    recipe_path = root / "manifests/codegen.json"
    recipe = json.loads(recipe_path.read_text())
    if recipe['schema_version'] != 1 or recipe['upstream_commit'] != receipt['upstream_commit']:
        raise ValueError("Codegen y upstream tienen pins distintos")
    selection = json.loads((root / 'manifests/source-manifest.json').read_text())['selection']
    products = [p for g in recipe['generators'] for p in g['outputs']]
    for name in products:
        safe_name(name)
    if (len(products) != len(set(products)) or not products
            or set(products) & set(selection['upstream_units'])
            or not set(selection['generated_units']).issubset(products)):
        raise ValueError("Seleccion duplicada o ajena a los generadores")
    for unit in selection['upstream_units']:
        text = (upstream / unit).read_text(encoding='latin1')
        if any(f'#include "{name}"' in text for name in products):
            raise ValueError(f"Include prefiere copia pristine: {unit}")
    env = {k: v for k, v in os.environ.items() if k not in (
        'CPATH', 'C_INCLUDE_PATH', 'CPLUS_INCLUDE_PATH', 'LIBRARY_PATH', 'DEPENDENCIES_OUTPUT')}
    env.update(recipe['environment'])
    compilers = {}
    for language, command in [('c', cc), ('c++', cxx)]:
        found = shutil.which(command)
        if not found:
            raise ValueError(f"Compilador HOST no disponible: {command}")
        # Preserve clang++ driver spelling: resolving its symlink changes link semantics.
        path = Path(found).absolute()
        compilers[language] = dict(path=str(path), sha256=sha(path), version=run(
            [path, '--version'], root, env, stdout=subprocess.PIPE).stdout.decode().strip())
    identity = dict(upstream_commit=receipt['upstream_commit'], series=receipt['series_sha256'],
                    recipe=sha(recipe_path), runner=sha(Path(__file__)), compilers=compilers,
                    support=inventory(root / 'tools/codegen_host'),
                    source_manifest=sha(root / 'manifests/source-manifest.json'))
    truth = root / 'tests/codegen/blitter_truth.c'
    identity['truth_test'] = sha(truth)
    identity['host_environment'] = {key: env[key] for key in (
        'SDKROOT', 'DEVELOPER_DIR', 'MACOSX_DEPLOYMENT_TARGET', 'GCC_EXEC_PREFIX',
        'COMPILER_PATH', 'SOURCE_DATE_EPOCH') if key in env}
    _no_symlinks(root, destination / 'codegen-receipt.json')
    cached = _cached_output(destination, products)
    if cached is not None:
        previous_identity = dict(cached['identity'])
        previous_identity.pop('host_inputs', None)
        if previous_identity == identity:
            identity['host_inputs'] = _host_inputs(root, upstream, destination, recipe, compilers, env)
            if cached['identity'] == identity and not (force or refresh_receipt):
                return cached
        if cached['identity'] != identity:
            cached = None
    if destination.exists() and cached is None and not (force or refresh_receipt):
        raise ValueError(f"Identidad de codegen distinta; use otra salida o --refresh-receipt para verificar bytes: {destination}")
    with tempfile.TemporaryDirectory(prefix='.codegen-', dir=root / 'build') as tmp:
        work = Path(tmp)
        generated, binaries = work / 'generated', work / 'host-tools'
        generated.mkdir()
        binaries.mkdir()
        def locate(name):
            safe_name(name)
            prefix, relative = name.split('/', 1)
            return {'upstream': upstream, 'generated': generated}[prefix] / relative
        def label(path):
            for base, prefix in [(upstream, 'upstream'), (work, 'work'), (root, 'project')]:
                if path.is_relative_to(base):
                    return prefix + '/' + path.relative_to(base).as_posix()
            return 'host:' + str(path)
        tools = {}
        for generator in recipe['generators']:
            name, language = generator['name'], generator['language']
            compiler = compilers[language]['path']
            flags = recipe['flags'][language]
            includes = ['-I' + str(root / 'tools/codegen_host'), '-I' + str(upstream / 'include')]
            objects, inputs = [], {}
            for i, source in enumerate(generator['sources']):
                obj, dep = binaries / f'{name}-{i}.o', binaries / f'{name}-{i}.d'
                run([compiler, *flags, *includes, '-x', language, '-MD', '-MT', 'p6_dependencies', '-MF', dep,
                     '-c', locate(source), '-o', obj], work, env, stdout=subprocess.PIPE)
                for path in _dependencies(dep.read_text()):
                    inputs[label(path)] = sha(path)
                objects.append(obj)
            executable = binaries / name
            run([compiler, *objects, '-o', executable], work, env, stdout=subprocess.PIPE)
            tools[name] = dict(inputs=inputs, flags=flags, sha256=sha(executable))
            for invocation in generator['runs']:
                cwd = locate(invocation['cwd']) if '/' in invocation['cwd'] else generated
                cwd.mkdir(parents=True, exist_ok=True)
                if any((cwd / absent).exists() for absent in recipe['absent_inputs']):
                    raise ValueError("Input opcional altera la receta fijada")
                data = None
                if 'stdin' in invocation:
                    path = locate(invocation['stdin'])
                    inputs[label(path)] = sha(path)
                    data = path.read_bytes()
                result = run([executable, *invocation['args']], cwd, env,
                             input=data, stdout=subprocess.PIPE)
                if 'stdout' in invocation:
                    locate(invocation['stdout']).write_bytes(result.stdout)
            for product in generator['outputs']:
                if not (generated / product).is_file() or not (generated / product).stat().st_size:
                    raise ValueError(f"Generador {name} no produjo {product}")
        files = inventory(generated)
        if {f['path'] for f in files} != set(products):
            raise ValueError("Codegen produjo archivos adicionales o ausentes")
        # Exercise every bitwise minterm for both expressions, including the three differences.
        for i, directory in enumerate((root / 'upstream/winuae', generated)):
            executable = binaries / f'blitter-truth-{i}'
            run([compilers['c']['path'], '-std=c11', '-I' + str(directory), truth,
                 '-o', executable], work, env, stdout=subprocess.PIPE)
            run([executable], work, env, stdout=subprocess.PIPE)
        identity['host_inputs'] = _host_inputs(root, upstream, generated, recipe, compilers, env)
        result = dict(schema_version=1, identity=identity, tools=tools, files=files,
                      checks={'blitter_truth': '2048 cases per version, pristine and generated'})
        verify_source(root)
        if destination.exists():
            old = json.loads((destination / 'codegen-receipt.json').read_text())
            actual = [f for f in inventory(destination) if f['path'] != 'codegen-receipt.json']
            if (old['identity'] != identity and not (force or refresh_receipt)) or actual != files or old['files'] != files:
                raise ValueError(f"Salida existente distinta; no se sobrescribe: {destination}")
            if refresh_receipt:
                _atomic_json(destination / 'codegen-receipt.json', result)
        else:
            (generated / 'codegen-receipt.json').write_text(json.dumps(result, indent=2) + '\n')
            destination.parent.mkdir(parents=True, exist_ok=True)
            generated.rename(destination)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cc', default='clang', help='Compilador C HOST, nunca target')
    parser.add_argument('--cxx', default='clang++', help='Compilador C++ HOST, nunca target')
    parser.add_argument('--output', default='build/generated')
    parser.add_argument('--check', action='store_true', help='Comparar dos ejecuciones limpias')
    parser.add_argument('--force', action='store_true', help='Regenerar sin cache y comparar; no sobrescribe salidas distintas')
    parser.add_argument('--refresh-receipt', action='store_true',
                        help='Actualizar solo el recibo tras regeneracion limpia con bytes identicos (nunca cambia las fuentes)')
    args = parser.parse_args()
    if args.check:
        (ROOT / 'build').mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='codegen-check-', dir=ROOT / 'build') as tmp:
            results = [generate(output=str((Path(tmp) / name).relative_to(ROOT)),
                                cc=args.cc, cxx=args.cxx, force=True) for name in ('a', 'b')]
            if results[0]['files'] != results[1]['files']:
                raise ValueError('GEN01: dos regeneraciones difieren')
            print(f"GEN01: {len(results[0]['files'])} salidas identicas en dos builds HOST limpios")
    else:
        result = generate(output=args.output, cc=args.cc, cxx=args.cxx,
                          force=args.force, refresh_receipt=args.refresh_receipt)
        print(f"Codegen: {len(result['files'])} salidas verificadas en {args.output}")


if __name__ == '__main__':
    main()
