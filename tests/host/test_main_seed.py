"""Check upstream PRNG sequences with P6's deterministic zero-seed policy."""
from pathlib import Path
import os
import platform
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]


def prng_block(source):
    start, end = "static uae_u32 randseed;", "void my_trim(TCHAR *s)"
    assert source.count(start) == source.count(end) == 1
    return start + source.split(start, 1)[1].split(end, 1)[0]


class MainSeedTests(unittest.TestCase):
    def test_deterministic_seed_sequences_and_rng_restore(self):
        subprocess.run(["python3", "tools/generate_cpu.py"], cwd=ROOT, check=True,
                       stdout=subprocess.PIPE, timeout=120)
        original = (ROOT / "upstream/winuae/main.cpp").read_text()
        patched = (ROOT / "build/upstream/main.cpp").read_text()
        self.assertIn("uaesetrandseed(t)", original)
        self.assertIn("uaesetrandseed(currprefs.seed);", patched)
        sources = [prng_block(original), prng_block(patched)]
        common = ["clang++", "-std=c++17", "-UNDEBUG", "-fsanitize=address,undefined",
                  "-fno-sanitize-recover=all", "-I{tmp}", "-I" + str(ROOT / "ports/portable"),
                  "-I" + str(ROOT / "build/generated"), "-I" + str(ROOT / "build/upstream/include"),
                  "-I" + str(ROOT / "build/upstream")]
        strip = "-Wl,-dead_strip" if platform.system() == "Darwin" else "-Wl,--gc-sections"
        with tempfile.TemporaryDirectory(prefix="p6-main-seed-") as directory:
            tmp = Path(directory)
            for optimization in ("-O0", "-O2"):
                outputs=[]
                for reference, body in ((True,sources[0]),(False,sources[1])):
                    (tmp / "main_seed_under_test.h").write_text(body)
                    exe=tmp / "probe"
                    include=[arg.format(tmp=tmp) for arg in common]
                    flags=["-DTEST_REFERENCE"] if reference else ["-DP6_PORTABLE"]
                    subprocess.run([*include,optimization,*flags,strip,
                                    str(ROOT / "tests/host/main_seed_probe.cpp"),"-o",str(exe)],
                                   check=True,timeout=60)
                    for tz in ("UTC","Pacific/Honolulu","Europe/Madrid"):
                        result=subprocess.run([str(exe)],check=True,capture_output=True,text=True,
                                              timeout=20,env={**os.environ,"TZ":tz,
                                              "ASAN_OPTIONS":"detect_leaks=0"})
                        outputs.append(result.stdout)
                self.assertTrue(outputs)
                self.assertEqual(len(set(outputs)),1)


if __name__ == "__main__":
    unittest.main()
