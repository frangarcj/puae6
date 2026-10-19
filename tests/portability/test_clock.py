"""DET06 clock interposition and sanitizer startup; no emulation claims."""
import os
from pathlib import Path
import shlex
import tempfile
import unittest
from unittest import mock

from tests.portability.row_driver import preload_environment
from tests.portability.run_matrix import FAKE_OFFSET, Row, faketime
from tests.portability.rows import ROWS


class PreloadTests(unittest.TestCase):
    def test_dynamic_asan_is_loaded_before_the_clock_and_missing_runtime_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime = Path(directory) / "libasan.so.8"
            runtime.touch()
            clock = str((Path(directory) / "clock.so").resolve())
            listing = f"libasan.so.8 => {runtime} (0x1234)\nlibc.so.6 => /lib/libc.so.6 (0x2345)"
            with mock.patch("sys.platform", "linux"), mock.patch("subprocess.check_output", return_value=listing):
                self.assertEqual(preload_environment("probe", clock), {"LD_PRELOAD": f"{runtime}:{clock}"})
                runtime.unlink()
                with self.assertRaisesRegex(ValueError, "runtime"):
                    preload_environment("probe", clock)

    def test_static_asan_does_not_acquire_a_second_runtime(self):
        with mock.patch("sys.platform", "linux"), mock.patch("subprocess.check_output", return_value="libc.so.6 => /lib/libc.so.6"):
            self.assertEqual(preload_environment("probe", "/clock.so"), {"LD_PRELOAD": "/clock.so"})


@unittest.skipUnless(os.environ.get("P6_TEST_PORTABILITY") == "1", "opt-in: actual matrix clock checks")
class ClockStartupTests(unittest.TestCase):
    def test_sanitized_program_sees_three_shifted_civil_clocks_and_an_unshifted_monotonic_clock(self):
        for name, spec in ROWS.items():
            with self.subTest(row=name):
                row = Row(name)
                library, check = faketime(row)
                self.assertTrue(check["shifted"], check)
                compiler = "/usr/bin/gcc" if spec["compiler"] == "GCC" else "/usr/bin/clang"
                probe = row.dir + "/clock-san-check"
                code = f"""
import os, subprocess, sys
sys.path.insert(0, {row.src!r})
from tests.portability.row_driver import preload_environment
probe = {probe!r}
env = dict(os.environ, ASAN_OPTIONS='detect_leaks=0')
base = subprocess.run([probe], env=env, capture_output=True, text=True)
env.update(preload_environment(probe, {library!r}), P6_FAKE_TIME_OFFSET={str(FAKE_OFFSET)!r})
host = subprocess.run([probe], env=env, capture_output=True, text=True)
assert base.returncode == host.returncode == 0, base.stderr + host.stderr
assert not base.stderr and not host.stderr, base.stderr + host.stderr
a, b = (dict((k, int(v)) for k, v in (item.split('=') for item in p.stdout.split())) for p in (base, host))
for key in ('time', 'gettimeofday', 'clock_gettime'):
    assert abs(b[key] - a[key] - {FAKE_OFFSET}) <= 5, (key, a, b)
assert 0 <= b['monotonic'] - a['monotonic'] <= 5, (a, b)
print('sanitizer startup and all clocks PASS')
"""
                command = (f"{compiler} -fsanitize=address,undefined -fno-sanitize-recover=all -DFAKETIME_CHECK "
                           f"{row.src}/tests/portability/faketime.c -o {probe} && python3 -I -c {shlex.quote(code)}")
                log = row.host_dir / "clock-san-check.log"
                try:
                    status, _ = row.run(command, log, 120)
                    output = log.read_text()
                    self.assertEqual(status, 0, output)
                    self.assertIn("all clocks PASS", output)
                finally:
                    (row.host_dir / "clock-san-check").unlink(missing_ok=True)
