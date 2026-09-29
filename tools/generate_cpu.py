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
from materialize_upstream import materialize, _destination


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(args, cwd, env, **kwargs):
    result = subprocess.run(list(map(str, args)), cwd=cwd, env=env,
                            stderr=subprocess.PIPE, **kwargs)
    if result.returncode:
        raise RuntimeError(f"Fallo {args}:\n{result.stderr.decode(errors='replace')}")
    return result


def generate(root=ROOT, output="build/generated", cc="clang", cxx="clang++"):
    root = Path(root).resolve()
    destination = _destination(root, output)
    receipt, upstream = materialize(root)
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
            raise ValueError(f"Dependencia HOST no inventariada: {path}")
        tools = {}
        for generator in recipe['generators']:
            name, language = generator['name'], generator['language']
            compiler = compilers[language]['path']
            flags = recipe['flags'][language]
            includes = ['-I' + str(root / 'tools/codegen_host'), '-I' + str(upstream / 'include')]
            objects, inputs = [], {}
            for i, source in enumerate(generator['sources']):
                obj, dep = binaries / f'{name}-{i}.o', binaries / f'{name}-{i}.d'
                run([compiler, *flags, *includes, '-x', language, '-MMD', '-MF', dep,
                     '-c', locate(source), '-o', obj], work, env, stdout=subprocess.PIPE)
                for entry in shlex.split(dep.read_text().replace('\\\n', '').split(':', 1)[1]):
                    path = Path(entry).resolve()
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
        truth = root / 'tests/codegen/blitter_truth.c'
        identity['truth_test'] = sha(truth)
        for i, directory in enumerate((root / 'upstream/winuae', generated)):
            executable = binaries / f'blitter-truth-{i}'
            run([compilers['c']['path'], '-std=c11', '-I' + str(directory), truth,
                 '-o', executable], work, env, stdout=subprocess.PIPE)
            run([executable], work, env, stdout=subprocess.PIPE)
        result = dict(schema_version=1, identity=identity, tools=tools, files=files,
                      checks={'blitter_truth': '2048 cases per version, pristine and generated'})
        verify_source(root)
        if destination.exists():
            old = json.loads((destination / 'codegen-receipt.json').read_text())
            actual = [f for f in inventory(destination) if f['path'] != 'codegen-receipt.json']
            if old['identity'] != identity or actual != files or old['files'] != files:
                raise ValueError(f"Salida existente distinta; no se sobrescribe: {destination}")
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
    args = parser.parse_args()
    if args.check:
        (ROOT / 'build').mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='codegen-check-', dir=ROOT / 'build') as tmp:
            results = [generate(output=str((Path(tmp) / name).relative_to(ROOT)),
                                cc=args.cc, cxx=args.cxx) for name in ('a', 'b')]
            if results[0]['files'] != results[1]['files']:
                raise ValueError('GEN01: dos regeneraciones difieren')
            print(f"GEN01: {len(results[0]['files'])} salidas identicas en dos builds HOST limpios")
    else:
        result = generate(output=args.output, cc=args.cc, cxx=args.cxx)
        print(f"Generadas {len(result['files'])} salidas verificadas en {args.output}")


if __name__ == '__main__':
    main()
