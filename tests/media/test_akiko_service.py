"""Actual derived Akiko hsync service, real Disc backend and explicit RAM/IRQ fixtures."""
from contextlib import contextmanager
from pathlib import Path
import platform
import subprocess
import sys
import tempfile
import unittest
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tools'))
from materialize_upstream import materialize
from tests.host.test_audio_math import function


@contextmanager
def built_probe(probe, *, reset=False):
    (ROOT / 'build').mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='akiko-service-', dir=ROOT / 'build') as tmp:
        work = Path(tmp)
        _, tree = materialize(ROOT, str((work / 'upstream').relative_to(ROOT)))
        (tree / 'upstream_audio_config.h').write_text(function(
            (ROOT / 'upstream/winuae/cfgfile.cpp').read_text(), 'void set_config_changed('))
        exe = work / 'probe'
        flags = ['-O1', '-g', '-UNDEBUG', '-fsanitize=address,undefined', '-fno-sanitize-recover=all',
                 '-ffunction-sections', '-fdata-sections']
        strip = '-Wl,-dead_strip' if platform.system() == 'Darwin' else '-Wl,--gc-sections'
        sources = [ROOT / 'tests/media' / probe, tree / 'crc32.cpp', tree / 'cdrom.cpp', tree / 'events.cpp',
                   tree / 'softfloat/softfloat.cpp', tree / 'softfloat/softfloat_fpsp.cpp']
        sources += [ROOT / 'ports/portable' / (name + '.cpp') for name in
                    ('audio_math', 'atomic', 'pcm_output', 'sound_lifecycle', 'drive_sound', 'diagnostics', 'gui')]
        sources += [ROOT / 'ports/portable/media' / (name + '.cpp') for name in
                    ('cd_device', 'cd_akiko', 'cd_disc', 'cd_source', 'cd_read', 'cd_subchannel', 'cd_audio', 'cd_utilities')]
        if reset:
            blob = work / 'blob.o'
            subprocess.run(['clang', '-std=c11', *flags, '-c', str(ROOT / 'ports/portable/blob_io.c'), '-o', str(blob)], check=True, timeout=30)
            sources += [tree / 'flashrom.cpp', tree / 'zfile.cpp', blob]
        command = ['clang++', '-std=c++17', *flags, '-DSAVESTATE', strip,
                   '-I' + str(ROOT / 'ports/portable'), '-I' + str(tree / 'include'), '-I' + str(tree), *map(str, sources), '-o', str(exe)]
        result = subprocess.run(command, capture_output=True, text=True, timeout=60)
        if result.returncode:
            raise AssertionError(result.stderr)
        yield exe


class AkikoServiceTests(unittest.TestCase):
    def test_fifo_dma_buffers_and_restore_intent_without_threads(self):
        with built_probe('akiko_service_probe.cpp') as exe:
            result = subprocess.run([str(exe)], capture_output=True, text=True, timeout=30)
            self.assertEqual(0, result.returncode, result.stderr)
