"""Select a verified hosted loader environment; no target tools execute here."""
import json
import os
from pathlib import Path
import shlex
import stat
import tempfile

from . import prepare_hosted_tools as hosted

ROOT = Path(__file__).resolve().parents[2]
SELECTOR = 'P6_WATERBOX_HOSTED_TOOLS'
NAMES = {'clang': 'clang', 'clang++': 'clang', 'ld.lld': 'lld', 'cargo': 'cargo', 'rustc': 'rustc'}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def directory(path):
    require(path.is_dir() and path.resolve(strict=True) == path, 'canonical real directory required')
    require(path.stat().st_uid == os.getuid(), 'directory belongs to another user')


def publish(output, scripts):
    """Publish only a complete owned script set; never repair foreign contents."""
    if output.exists() or output.is_symlink():
        directory(output)
        require(set(p.name for p in output.iterdir()) == set(scripts), 'foreign launcher inventory')
        for name, text in scripts.items():
            path = output / name
            info = path.lstat()
            require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_uid == os.getuid()
                    and stat.S_IMODE(info.st_mode) == 0o755 and path.read_text() == text, 'foreign launcher file')
        return
    directory(output.parent)
    with tempfile.TemporaryDirectory(prefix='.hosted-launchers-', dir=output.parent) as temporary:
        staged = Path(temporary) / 'scripts'
        staged.mkdir(mode=0o755)
        for name, text in scripts.items():
            path = staged / name
            path.write_text(text)
            path.chmod(0o755)
        require(not output.exists() and not output.is_symlink(), 'launcher output appeared during preparation')
        staged.rename(output)


def select(paths, env=None):
    """Return direct_env, tool_env, shell_exports and launchers (all copies).

    direct_env loads raw pinned tools; tool_env uses SIP-safe launchers through
    PATH/RUSTC. shell_exports restores DYLD after a musl wrapper's shebang. The
    caller continues hashing raw paths, never these launchers. No selector means
    unchanged environments, empty exports and no launcher/cache filesystem IO.
    """
    base = dict(os.environ if env is None else env)
    if SELECTOR not in base:
        return {'direct_env': dict(base), 'tool_env': dict(base), 'shell_exports': '', 'launchers': {}}
    cache = ROOT / 'build/hosted-tools'
    require(base[SELECTOR] in ('build/hosted-tools', str(cache)), 'hosted tools selector must be canonical build/hosted-tools')
    require(hosted.prepare(fetch=False) == cache, 'verified cache location mismatch')
    manifest, _ = hosted.inputs()
    expected = {'schema_version': 1, 'paths': {
        **{name: str(cache / rel) for name, rel in manifest['entrypoints'].items()}, 'make': '/usr/bin/make'}}
    mapping = cache / 'paths.json'
    hosted.regular(mapping)
    require(json.loads(mapping.read_text()) == expected, 'verified tool paths mismatch')
    require({name: str(path) for name, path in paths.items()} == expected['paths'], 'selected tools differ from hosted paths')
    libs = [cache / rel for rel in manifest['library_directories']]
    require(len(libs) == 10 and len(set(libs)) == 10, 'ten distinct verified library directories required')
    for path in libs:
        require(path.is_relative_to(cache) and ':' not in str(path), 'invalid loader directory')
        directory(path)
    loader = os.pathsep.join(map(str, libs))
    exports = 'export DYLD_LIBRARY_PATH=' + shlex.quote(loader) + '\n'
    scripts = {name: '#!/bin/sh\n' + exports + 'exec ' + shlex.quote(expected['paths'][raw])
               + (' --driver-mode=g++' if name == 'clang++' else '') + ' "$@"\n' for name, raw in NAMES.items()}
    output = ROOT / 'build/hosted-tool-launchers'
    # Validate parent components before touching an existing or new output.
    directory(ROOT); directory(output.parent)
    publish(output, scripts)
    launchers = {('lld' if name == 'ld.lld' else name): output / name for name in NAMES}
    direct = {**base, 'DYLD_LIBRARY_PATH': loader}
    tools = {**direct, 'PATH': str(output) + os.pathsep + base.get('PATH', ''), 'RUSTC': str(launchers['rustc'])}
    return {'direct_env': direct, 'tool_env': tools, 'shell_exports': exports, 'launchers': launchers}
