"""Warning baseline for the A500 engine units; it does not run an emulator.

Every engine translation unit is compiled with -fsyntax-only and its warnings are
counted per (file, warning kind) against manifests/engine-warnings.json. A new warning
kind or a higher count fails (an upstream update or a patch introduced something to
review); a lower count fails too, so fixed warnings leave the list. Each entry carries
a written disposition: the warnings are upstream code that the profile cannot reach or
that is harmless, and none is silenced.
"""
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import tempfile
import unittest
from tests.build_support import _compiler_options

ROOT = Path(__file__).resolve().parents[2]
BUILD = ROOT / "build/link-audit"
WARNING = re.compile(r"^(?P<file>[^\s:][^:]*):(?P<line>\d+):\d+: warning: (?P<message>.*?) \[(?P<flag>-W[\w-]+)\]$")


def engine_commands():
    if not (BUILD / "compile_commands.json").exists():
        subprocess.run(["cmake", "-S", str(ROOT), "-B", str(BUILD), "-G", "Ninja",
                        *_compiler_options(BUILD),
                        "-DCMAKE_BUILD_TYPE=Debug", "-DP6_SANITIZERS=OFF"],
                       check=True, capture_output=True, text=True, timeout=120)
    commands = json.loads((BUILD / "compile_commands.json").read_text())
    return [c for c in commands if "p6_engine.dir/" in c["command"]]


def syntax_only(entry):
    args = shlex.split(entry["command"])
    cleaned, skip = [], 0
    for arg in args:
        if skip:
            skip -= 1
        elif arg in ("-o", "-MF", "-MT", "-MQ"):
            skip = 1
        elif arg in ("-MD", "-MMD"):
            continue
        else:
            cleaned.append(arg)
    result = subprocess.run([*cleaned, "-fsyntax-only"], cwd=entry["directory"],
                            capture_output=True, text=True, timeout=300)
    found = set()
    for line in result.stderr.splitlines():
        match = WARNING.match(line)
        if match:
            name = match["file"].rsplit("/", 1)[-1]
            found.add((name, int(match["line"]), match["flag"], match["message"]))
    return result.returncode, found


def collect():
    with ThreadPoolExecutor(max_workers=max(1, (os.cpu_count() or 2) - 2)) as pool:
        results = list(pool.map(syntax_only, engine_commands()))
    counts = {}
    for code, found in results:
        assert code == 0, "an engine unit failed to compile"
        for name, _line, flag, _message in found:
            counts[(name, flag)] = counts.get((name, flag), 0) + 1
    return counts


@unittest.skipUnless(all(shutil.which(t) for t in ("cmake", "clang", "clang++")), "toolchain unavailable")
class EngineWarningTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        subprocess.run(["python3", "tools/generate_cpu.py"], cwd=ROOT, check=True,
                       stdout=subprocess.PIPE, timeout=300)
        cls.manifest = json.loads((ROOT / "manifests/engine-warnings.json").read_text())
        cls.counts = collect()

    def test_manifest_entries_are_reviewed(self):
        seen = set()
        for entry in self.manifest["warnings"]:
            key = (entry["file"], entry["flag"])
            self.assertNotIn(key, seen)
            seen.add(key)
            with self.subTest(entry=key):
                self.assertGreater(entry["count"], 0)
                self.assertIn(entry["disposition"], {"dead_under_profile", "harmless", "revisit"})
                self.assertGreater(len(entry["note"]), 30)

    def test_warnings_match_the_baseline_exactly(self):
        expected = {(e["file"], e["flag"]): e["count"] for e in self.manifest["warnings"]}
        self.assertEqual(self.counts, expected,
                         "review new warnings; lower counts must be removed from the manifest")

    def test_period_max_narrowing_is_the_same_as_a_32_bit_long(self):
        # audio.cpp stores PERIOD_MAX (ULONG_MAX) and PERIOD_MAX - 1 into an int period. The
        # narrowing gives -1 and -2 on LP64 exactly as on the LLP64 build that upstream
        # targets, and comparing back as unsigned long recovers the sentinel in both.
        source = r'''
#include "sysconfig.h"
#include "sysdeps.h"
#include "audio.h"
#include <cassert>
int main()
{
    int period = PERIOD_MAX, next = PERIOD_MAX - 1;
    assert(period == -1 && next == -2);
    assert((unsigned long)period == PERIOD_MAX && (unsigned long)next == PERIOD_MAX - 1);
    assert((int)0xffffffffu == -1 && (int)0xfffffffeu == -2);
    return 0;
}
'''
        tree = ROOT / "build/upstream"
        with tempfile.TemporaryDirectory(prefix="p6-period-") as directory:
            exe = Path(directory) / "probe"
            subprocess.run(["clang++", "-std=c++17", "-UNDEBUG", "-Wno-constant-conversion",
                            "-I" + str(ROOT / "ports/portable"), "-I" + str(ROOT / "build/generated"),
                            "-I" + str(tree / "include"), "-I" + str(tree), "-x", "c++", "-",
                            "-o", str(exe)], input=source, text=True, check=True,
                           capture_output=True, timeout=120)
            subprocess.run([str(exe)], check=True, timeout=30)


if __name__ == "__main__":
    unittest.main()
