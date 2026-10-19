"""CDA component on actual audio.cpp/events.cpp; no machine-state/CD32 claim."""
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
from tests.host.test_audio_math import function


class CdAudioTests(unittest.TestCase):
    def test_pcm_pause_semantic_restore_and_bounds(self):
        with tempfile.TemporaryDirectory(prefix='cda-', dir=ROOT / 'build') as tmp:
            tree = Path(tmp)
            series = json.loads((ROOT / 'patches/winuae/series.json').read_text())['patches']
            names = {'audio.cpp', 'events.cpp'} | {p.relative_to(ROOT / 'upstream/winuae').as_posix() for p in (ROOT / 'upstream/winuae/softfloat').iterdir() if p.is_file()} | {f['path'] for e in series for f in e['files'] if f['path'].endswith('.h')}
            for name in names:
                dest = tree / name; dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes((ROOT / 'upstream/winuae' / name).read_bytes())
            # Apply only these real translation units, with the complete hash
            # chain; no CPU generator or engine build is needed for this probe.
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
            (tree / 'upstream_audio_config.h').write_text(function(
                (ROOT / 'upstream/winuae/cfgfile.cpp').read_text(), 'void set_config_changed('))
            common = ['clang++', '-std=c++17', '-O1', '-g', '-UNDEBUG', '-fno-fast-math', '-ffp-contract=off',
                      '-ffunction-sections', '-fdata-sections', '-fsanitize=address,undefined', '-fno-sanitize-recover=all',
                      '-I' + str(ROOT / 'ports/portable'), '-I' + str(tree / 'include'), '-I' + str(tree),
                      '-I' + str(ROOT / 'upstream/winuae/include'), '-I' + str(ROOT / 'upstream/winuae')]
            strip = '-Wl,-dead_strip' if platform.system() == 'Darwin' else '-Wl,--gc-sections'
            sources = [ROOT / 'tests/audio/cd_audio_probe.cpp', tree / 'events.cpp',
                       tree / 'softfloat/softfloat.cpp',
                       tree / 'softfloat/softfloat_fpsp.cpp']
            sources += [ROOT / 'ports/portable' / n for n in
                        ('audio_math.cpp', 'atomic.cpp', 'pcm_output.cpp', 'sound_lifecycle.cpp', 'drive_sound.cpp', 'diagnostics.cpp', 'gui.cpp')]
            exe = tree / 'probe'
            subprocess.run([*common, strip, *map(str, sources), '-o', str(exe)], check=True, timeout=60)
            subprocess.run([str(exe)], check=True, timeout=30)
