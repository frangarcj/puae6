"""Real CIA/visitor with an observed map request; no engine, codegen or bank-map claim."""
import hashlib
import json
from pathlib import Path
import platform
import subprocess
import sys
import tempfile
import unittest
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tools'))
import materialize_upstream as mat


class CiaOverlayStateTests(unittest.TestCase):
    def test_cd32_latch_and_unchanged_cia_overlay_body(self):
        with tempfile.TemporaryDirectory(prefix='cia-overlay-', dir=ROOT / 'build') as tmp:
            tree = Path(tmp)
            series = json.loads((ROOT / 'patches/winuae/series.json').read_text())['patches']
            names = {'cia.cpp', 'events.cpp'} | {f['path'] for e in series for f in e['files'] if f['path'].endswith('.h')}
            for name in names:
                dest = tree / name; dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes((ROOT / 'upstream/winuae' / name).read_bytes())
            for entry in series:
                files = [f for f in entry['files'] if f['path'] in names]
                if not files:
                    continue
                patch = (ROOT / entry['file']).read_bytes()
                self.assertEqual(hashlib.sha256(patch).hexdigest(), entry['sha256'])
                patch = b''.join(b'diff --git ' + c for c in patch.split(b'diff --git ')[1:]
                                 if c.splitlines()[0].split()[0][2:].decode() in names)
                for f in files:
                    self.assertEqual(mat._sha(tree / f['path']), f['before_sha256'])
                mat._apply(patch, tree, True); mat._apply(patch, tree, False)
                for f in files:
                    self.assertEqual(mat._sha(tree / f['path']), f['after_sha256'])
            includes = ('ports/portable', 'ports/portable/state_io', 'bridge', 'bridge/include')
            command = ['clang++', '-std=c++17', '-O1', '-g', '-UNDEBUG', '-ffunction-sections', '-fdata-sections',
                       '-fsanitize=address,undefined', '-fno-sanitize-recover=all',
                       *(f'-I{ROOT / p}' for p in includes), f'-I{tree / "include"}', f'-I{tree}',
                       f'-I{ROOT / "upstream/winuae/include"}', f'-I{ROOT / "upstream/winuae"}']
            command += ['-Wl,-dead_strip' if platform.system() == 'Darwin' else '-Wl,--gc-sections']
            sources = ['tests/host/cia_overlay_state_probe.cpp', 'bridge/state_devices.cpp',
                       'ports/portable/gui.cpp', 'ports/portable/diagnostics.cpp']
            exe = tree / 'probe'
            subprocess.run([*command, *(str(ROOT / p) for p in sources), str(tree / 'events.cpp'), '-o', str(exe)],
                           check=True, timeout=60)
            subprocess.run([str(exe)], check=True, timeout=30)
