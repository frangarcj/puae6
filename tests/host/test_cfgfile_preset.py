"""Check full RAM-board reset and the real A500 preset path."""
from pathlib import Path
import os
import subprocess
import tempfile
import unittest

from tests.host import ensure_generated

ROOT = Path(__file__).resolve().parents[2]


def body(source, signature):
    start = source.index(signature)
    opening = source.index("{", start)
    depth, end = 1, opening + 1
    while depth:
        depth += (source[end] == "{") - (source[end] == "}")
        end += 1
    return source[start:end] + "\n"


def extract(source):
    names = (
        "static void buildin_default_host_prefs (",
        "static void buildin_default_prefs (",
        "static void set_68000_compa (",
        "static int bip_a500 (",
        "static bool has_expansion_with_rtc(",
        "int built_in_chipset_prefs (",
    )
    parts = [(source.index(name), body(source, name)) for name in names]
    parts.sort()
    return "static bool has_expansion_with_rtc(struct uae_prefs *, int);\n" + "".join(
        code for _, code in parts)


class CfgfilePresetTests(unittest.TestCase):
    def test_all_fast_z3_slots_reset_and_a500_preset(self):
        ensure_generated()
        original = (ROOT / "upstream/winuae/cfgfile.cpp").read_text()
        patched = (ROOT / "build/upstream/cfgfile.cpp").read_text()
        with tempfile.TemporaryDirectory(prefix="p6-cfgfile-preset-") as directory:
            tmp = Path(directory)
            common = ["clang++", "-std=c++17", "-UNDEBUG", "-fsanitize=address,undefined",
                      "-fno-sanitize-recover=all", "-I" + str(tmp),
                      "-I" + str(ROOT / "ports/portable"),
                      "-I" + str(ROOT / "build/generated"),
                      "-I" + str(ROOT / "build/upstream/include"),
                      "-I" + str(ROOT / "build/upstream")]
            for opt in ("-O0", "-O2"):
                executables = []
                for label, source in (("reference", original), ("patched", patched)):
                    (tmp / "cfgfile_preset_under_test.h").write_text(extract(source))
                    exe = tmp / label
                    subprocess.run([*common, opt,
                                    "-DP6_PORTABLE" if label == "patched" else "-DTEST_REFERENCE",
                                    str(ROOT / "tests/host/cfgfile_preset_probe.cpp"),
                                    "-o", str(exe)], check=True, timeout=90)
                    executables.append(exe)
                env = {**os.environ, "ASAN_OPTIONS": "detect_leaks=0"}
                ref_clean = subprocess.run([str(executables[0])], capture_output=True,
                                           text=True, env=env, timeout=20)
                self.assertEqual(ref_clean.returncode, 0, ref_clean.stderr)
                ref_dirty = subprocess.run([str(executables[0]), "dirty"], capture_output=True,
                                           text=True, env=env, timeout=20)
                self.assertNotEqual(ref_dirty.returncode, 0,
                                    "pristine helper unexpectedly cleared every populated slot")
                self.assertIn("Fast/Z3 RAM slot remains populated", ref_dirty.stderr)
                patched_clean = subprocess.run([str(executables[1])], check=True,
                                               capture_output=True, text=True,
                                               env=env, timeout=20)
                patched_dirty = subprocess.run([str(executables[1]), "dirty"], check=True,
                                               capture_output=True, text=True,
                                               env=env, timeout=20)
                patched_fail = subprocess.run([str(executables[1]), "dirty", "rom-fail"],
                                              check=True, capture_output=True, text=True,
                                              env=env, timeout=20)
                self.assertEqual(ref_clean.stdout, patched_clean.stdout)
                self.assertEqual(patched_clean.stdout, patched_dirty.stdout)
                self.assertIn("result=0", patched_fail.stdout)


if __name__ == "__main__":
    unittest.main()
