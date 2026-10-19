"""Actual derived Akiko hsync service, real Disc backend and explicit RAM/IRQ fixtures."""
from pathlib import Path
import platform
import subprocess
import sys
import tempfile
import unittest
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tools'))
from materialize_upstream import materialize


class AkikoServiceTests(unittest.TestCase):
    def test_fifo_dma_buffers_and_restore_intent_without_threads(self):
        (ROOT / 'build').mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='akiko-service-', dir=ROOT / 'build') as tmp:
            work = Path(tmp)
            _, tree = materialize(ROOT, str((work / 'upstream').relative_to(ROOT)))
            exe = work / 'probe'
            strip = '-Wl,-dead_strip' if platform.system() == 'Darwin' else '-Wl,--gc-sections'
            sources = [ROOT / 'tests/media/akiko_service_probe.cpp', tree / 'crc32.cpp', tree / 'cdrom.cpp',
                       ROOT / 'ports/portable/diagnostics.cpp', ROOT / 'ports/portable/gui.cpp']
            sources += [ROOT / 'ports/portable/media' / (name + '.cpp') for name in ('cd_device', 'cd_akiko', 'cd_disc', 'cd_source')]
            command = ['clang++', '-std=c++17', '-O1', '-g', '-UNDEBUG', '-DSAVESTATE', '-ffunction-sections', '-fdata-sections',
                       '-fsanitize=address,undefined', '-fno-sanitize-recover=all', strip,
                       '-I' + str(ROOT / 'ports/portable'), '-I' + str(tree / 'include'), '-I' + str(tree), *map(str, sources), '-o', str(exe)]
            result = subprocess.run(command, capture_output=True, text=True, timeout=60)
            self.assertEqual(0, result.returncode, result.stderr)
            result = subprocess.run([str(exe)], capture_output=True, text=True, timeout=30)
            self.assertEqual(0, result.returncode, result.stderr)
