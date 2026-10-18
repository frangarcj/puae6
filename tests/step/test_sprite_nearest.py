"""T35d: the real nearest-sprite function and the failing SuperHires frontend.

The small compiled fixture isolates drawing.cpp's arithmetic; it is not an
emulator. The frontend tests below also run the actual complete engine.
"""
import itertools
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest

from tests.simd import frontend_rgb565 as frontend

ROOT = frontend.ROOT
BEFORE = ROOT / "tests/simd/fixtures/T35d-before-sprite-counter.json"


class NearestCounterTests(unittest.TestCase):
    def test_real_function_preserves_no_future_sprite_and_counts_real_distances(self):
        from tools.materialize_upstream import materialize
        materialize(ROOT)
        text = (ROOT / "build/upstream/drawing.cpp").read_text()
        body = text[text.index("static void spr_nearest(void)"):text.index("// arm/disarm sprite")]
        cases, expected = [], []
        positions = ((None,) * 8, (100,) + (None,) * 7, (0, 511) + (None,) * 6,
                     (None,) * 7 + (31,), (511, 100) + (None,) * 6)
        for aga, res, counts, wrap, sprites in itertools.product((0, 1), range(3), ((0, 2), (20, 24), (511, 513), (600, 610)), (0, 512), positions):
            distances = []
            for position in sprites:
                if position is None:
                    continue
                for count in counts:
                    distance = (position & 511) - count if wrap else position - count
                    if wrap and distance < 0:
                        distance = (position | wrap) - count
                    if distance >= 0:
                        distances.append(distance)
            nearest = min(distances) if distances else 0x7fffffff
            result = nearest - 2
            if aga and distances and result > 0:
                result *= 1 << res
            expected.append(result)
            cases.append([aga, res, *counts, 511, wrap, *[v for p in sprites for v in (int(p is not None), p or 0)]])
        source = '''#include <iostream>
struct denise_spr { int armed; int xpos_lores; };
constexpr int MAX_SPRITES = 8;
static denise_spr dspr[MAX_SPRITES];
static int aga_mode, hresolution, denise_hcounter_new, denise_hcounter;
static int denise_xposmask_lores, denise_xposmask_mask_lores, denise_spr_nearestcnt;
''' + body + '''
int main() {
    while (std::cin >> aga_mode >> hresolution >> denise_hcounter_new >> denise_hcounter >> denise_xposmask_lores >> denise_xposmask_mask_lores) {
        for (auto &sprite : dspr) std::cin >> sprite.armed >> sprite.xpos_lores;
        spr_nearest();
        std::cout << denise_spr_nearestcnt << "\\n";
    }
}
'''
        with tempfile.TemporaryDirectory(prefix="p6-nearest-sprite-") as work:
            path = Path(work)
            (path / "probe.cpp").write_text(source)
            compiled = subprocess.run(["/usr/bin/clang++", "-std=c++17", "-O1", "-fsanitize=address,undefined", "-fno-sanitize-recover=all",
                                      str(path / "probe.cpp"), "-o", str(path / "probe")], capture_output=True, text=True, timeout=90)
            self.assertEqual(compiled.returncode, 0, compiled.stderr)
            ran = subprocess.run([str(path / "probe")], input="\n".join(" ".join(map(str, c)) for c in cases),
                                 capture_output=True, text=True, timeout=90, env={**os.environ, "ASAN_OPTIONS": "detect_leaks=0"})
            self.assertEqual(ran.returncode, 0, ran.stderr)
            self.assertEqual(list(map(int, ran.stdout.split())), expected)


class FrontendSpriteTests(unittest.TestCase):
    def test_actual_sanitized_frontend_and_restores_include_the_failing_aga_superhires_case(self):
        f = frontend.Frontend("macos-arm64-appleclang", ROOT / "build/simd/frontend/macos-arm64-appleclang", True)
        f.build()
        f.verify()

    def test_corrected_outputs_equal_the_pre_patch_observations_without_changing_golden(self):
        self.assertTrue(BEFORE.is_file(), "missing the published pre-patch observation fixture")
        old = json.loads(BEFORE.read_text())["cases"]
        f = frontend.Frontend("macos-arm64-appleclang", ROOT / "build/simd/frontend/macos-arm64-appleclang")
        f.build()
        f.verify()
        new = json.loads((f.tree / "verification.json").read_text())["cases"]
        self.assertEqual(set(old), set(new))
        for name in old:
            self.assertEqual(old[name]["runs"], new[name]["runs"], name)


class WaterboxSpriteTests(unittest.TestCase):
    def test_aga_superhires_reproducer_restores_in_same_new_sandbox_and_process(self):
        import build_waterbox as wb
        if not wb.runtime_receipt_path().is_file() or not shutil.which("dotnet") or os.environ.get("P6_TEST_WATERBOX") == "0":
            self.skipTest("real Waterbox runtime required; native cannot replace this test")
        receipt = json.loads((wb.BUILD / "puae6/puae6-receipt.json").read_text())
        wb.check_guest_matches_trees(wb.BUILD / "puae6/puae6-receipt.json")
        with tempfile.TemporaryDirectory(prefix="p6-nearest-wbx-") as work:
            path = Path(work)
            rom = path / "aga.rom"
            rom.write_bytes(frontend.corpus.build_rom(frontend.corpus.programs()["aga-planes8-fmode3-pal"]))
            state = path / "six.state"
            def session(lines):
                script = path / "program.txt"
                script.write_text("\n".join(lines) + "\n")
                result = wb.run_ffi("session", "--runtime", "libwaterboxhost.dylib", "--guest", str(wb.BUILD / "puae6/release/puae6.wbx"),
                                    "--sha", receipt["guest"]["sha256"], "--firmware", str(rom), "--program", str(script))
                self.assertEqual(result.returncode, 0, result.stdout[-2000:] + result.stderr[-2000:])
                return result.stdout
            core = lambda name: f"core {name} machine=a1200 vres=SuperHires"
            out = session([core("A"), "buffer A", "run A 6", f"savefile A {state}", "save A s", "run A 4", "digest A first",
                           "load A s", "run A 4", "digest A again", core("B"), f"loadfile B {state}", "run B 4", "digest B other"])
            self.assertRegex(out, r"(?m)^buffer A 1520x576$")
            def units(text, name):
                return re.findall(rf"^u {name} (.*)$", text, re.M)
            a, b = units(out, "A"), units(out, "B")
            self.assertEqual(len(a), 14)
            self.assertEqual(a[6:10], a[10:14])
            self.assertEqual(a[6:10], b)
            digests = re.findall(r"^digest [AB] (?:first|again|other) sha=(\w+)", out, re.M)
            self.assertEqual(len(digests), 3)
            self.assertEqual(len(set(digests)), 1)
            fresh = session([core("C"), f"loadfile C {state}", "run C 4"])
            self.assertEqual(a[6:10], units(fresh, "C"))


if __name__ == "__main__":
    unittest.main()
