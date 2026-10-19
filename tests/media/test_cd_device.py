"""Actual WinUAE blkdev entry points over Disc; no machine/CDDA claim."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tools'))
import materialize_upstream as mat


@unittest.skipUnless(shutil.which('clang++'), 'clang++ unavailable')
class CdDeviceTests(unittest.TestCase):
    def test_actual_blkdev_returns_bounded_reads_and_transactional_faults(self):
        with tempfile.TemporaryDirectory(prefix='cd-device-', dir=ROOT / 'build') as tmp:
            work = Path(tmp)
            entry = next(p for p in json.loads((ROOT / 'patches/winuae/series.json').read_text())['patches'] if p['id'] == '0355-cdrom-mode1-sync')
            target = work / 'cdrom.cpp'; target.write_bytes((ROOT / 'upstream/winuae/cdrom.cpp').read_bytes())
            patch = (ROOT / entry['file']).read_bytes()
            self.assertEqual(hashlib.sha256(target.read_bytes()).hexdigest(), entry['files'][0]['before_sha256'])
            self.assertEqual(hashlib.sha256(patch).hexdigest(), entry['sha256'])
            mat._apply(patch, work, True); mat._apply(patch, work, False)
            self.assertEqual(hashlib.sha256(target.read_bytes()).hexdigest(), entry['files'][0]['after_sha256'])
            exe = work / 'probe'
            command = ['clang++', '-std=c++17', '-O1', '-g', '-Wall', '-Wextra', '-Werror', '-fno-exceptions', '-fno-rtti',
                       '-fsanitize=address,undefined', '-fno-sanitize-recover=all', '-DUAE_SYSDEPS_H', '-include', 'cstring',
                       '-I' + str(ROOT / 'ports/portable'), '-I' + str(ROOT / 'ports/portable/media'), '-I' + str(ROOT / 'upstream/winuae/include'),
                       str(ROOT / 'tests/media/cd_device_probe.cpp'), str(target)]
            command += [str(ROOT / 'ports/portable/media' / (name + '.cpp')) for name in ('cd_device', 'cd_akiko', 'cd_disc', 'cd_source', 'cd_read', 'cd_subchannel')]
            result = subprocess.run(command + ['-o', str(exe)], capture_output=True, text=True, timeout=60)
            self.assertEqual(result.returncode, 0, result.stderr)
            result = subprocess.run([str(exe)], capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
