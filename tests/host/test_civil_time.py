"""Integer clock primitives; actual scheduler/RTC/TOD integration remains T06/T07."""
from datetime import datetime, timedelta
import hashlib
import os
from pathlib import Path
import random
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
from materialize_upstream import materialize


class CivilTimeTests(unittest.TestCase):
    def test_calendar_accounting_seed_and_restore(self):
        key = hashlib.sha256((ROOT / "patches/winuae/series.json").read_bytes()).hexdigest()
        _, tree = materialize(ROOT, "build/t05-sysdeps/" + key)
        source = (tree / "main.cpp").read_text()
        # Original PRNG and seeding functions, not a copied algorithm in the port.
        start, end = "static uae_u32 randseed;", "void my_trim(TCHAR *s)"
        self.assertEqual(source.count(start), 1)
        self.assertEqual(source.count(end), 1)
        reference = source.split(start)[1].split(end)[0]
        epoch = datetime(1970, 1, 1)
        dates = [datetime(y, m, d, 23, 59, 59)
                 for y in (1, 4, 100, 400, 1600, 1900, 1969, 1970, 2000, 2038, 2100, 2400, 9999)
                 for m, d in ((1, 1), (2, 28), (3, 1), (12, 31))]
        dates += [datetime(y, 2, 29) for y in (4, 400, 1600, 2000, 2400)]
        rng = random.Random(0)
        dates += [datetime.fromordinal(rng.randrange(1, datetime.max.toordinal() + 1))
                  + timedelta(seconds=rng.randrange(86400)) for _ in range(1000)]
        cases = [(int((date - epoch).total_seconds()), 0, date) for date in dates]
        cases += [(0, adjustment, epoch + timedelta(seconds=adjustment))
                  for adjustment in (-2147483648, -86401, -1, 1, 86401, 2147483647)]
        payload = "".join(f"{seconds} {offset}\n" for seconds, offset, _ in cases)
        expected = [(d.year, d.month, d.day, d.hour, d.minute, d.second, (d.weekday() + 1) % 7,
                     (d - datetime(d.year, 1, 1)).days) for _, _, d in cases]
        locales = set(subprocess.run(["locale", "-a"], text=True, capture_output=True, check=True).stdout.splitlines())
        with tempfile.TemporaryDirectory(prefix="p6-time-") as directory:
            directory = Path(directory)
            (directory / "rng_original.inc").write_text(start + reference)
            flags = ["-O2", "-Wall", "-Wextra", "-Werror", "-fsanitize=address,undefined", "-fno-sanitize-recover=all",
                     "-I", str(ROOT / "ports/portable"), "-I", str(tree / "include"), "-I", str(directory)]
            for language, standard, compiler in (("c", "c11", "clang"), ("c++", "c++17", "clang++")):
                obj, exe = directory / "clock.o", directory / "probe"
                subprocess.run([compiler, "-x", language, "-std=" + standard, *flags, "-c",
                                str(ROOT / "ports/portable/civil_time.c"), "-o", str(obj)], check=True)
                subprocess.run(["clang++", "-std=c++17", *flags, str(ROOT / "tests/host/civil_time_probe.cpp"),
                                str(obj), "-o", str(exe)], check=True)
                for tz, locale in (("UTC", "C"), ("Pacific/Honolulu", "en_US.UTF-8"), ("Europe/Madrid", "es_ES.UTF-8")):
                    locale = locale if locale in locales else "C"
                    env = dict(os.environ, TZ=tz, LC_ALL=locale, LANG=locale)
                    subprocess.run([str(exe)], env=env, check=True)
                    result = subprocess.run([str(exe), "calendar"], input=payload, env=env, text=True, capture_output=True, check=True)
                    self.assertEqual([tuple(map(int, line.split())) for line in result.stdout.splitlines()], expected)

    def test_no_clock_locale_entropy_dependencies(self):
        with tempfile.TemporaryDirectory(prefix="p6-time-symbols-") as directory:
            obj = Path(directory) / "clock.o"
            subprocess.run(["clang", "-std=c11", "-O2", "-c", str(ROOT / "ports/portable/civil_time.c"), "-o", str(obj)], check=True)
            result = subprocess.run(["nm", "-u", str(obj)], text=True, capture_output=True, check=True)
            symbols = {line.split()[-1].lstrip("_") for line in result.stdout.splitlines() if line.strip()}
            self.assertLessEqual(symbols, {"memcpy", "memset", "bzero", "stack_chk_fail", "stack_chk_guard"})


if __name__ == "__main__":
    unittest.main()
