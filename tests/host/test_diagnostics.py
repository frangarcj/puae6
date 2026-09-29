"""Native diagnostic/causal-state unit tests, not an engine or Waterbox run."""
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
from materialize_upstream import materialize
from check_host_surface import mask


class DiagnosticsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        key = hashlib.sha256((ROOT / "patches/winuae/series.json").read_bytes()).hexdigest()
        _, cls.tree = materialize(ROOT, "build/t05-sysdeps/" + key)

    def test_queue_and_actual_gui_layout(self):
        with tempfile.TemporaryDirectory(prefix="p6-diagnostics-") as directory:
            output = Path(directory) / "probe"
            result = subprocess.run([
                "clang++", "-std=c++17", "-O2", "-Wall", "-Wextra", "-Werror",
                "-fsanitize=address,undefined", "-fno-sanitize-recover=all",
                "-I", str(ROOT / "ports/portable"), "-I", str(self.tree / "include"),
                str(ROOT / "ports/portable/diagnostics.cpp"), str(ROOT / "ports/portable/gui.cpp"),
                str(ROOT / "tests/host/diagnostics_probe.cpp"), "-o", str(output)],
                text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            subprocess.run([str(output)], check=True)

    def test_c_header_and_external_dependencies(self):
        subprocess.run(["clang", "-x", "c", "-std=c11", "-Werror", "-fsyntax-only", "-"],
                       input='#include "ports/portable/diagnostics.h"\n', text=True,
                       cwd=ROOT, check=True)
        with tempfile.TemporaryDirectory(prefix="p6-diagnostic-symbols-") as directory:
            output = Path(directory) / "diagnostics.o"
            subprocess.run(["clang++", "-std=c++17", "-O2", "-c",
                            "-I", str(ROOT / "ports/portable"), "-I", str(self.tree / "include"),
                            str(ROOT / "ports/portable/diagnostics.cpp"), "-o", str(output)], check=True)
            result = subprocess.run(["nm", "-u", str(output)], check=True, text=True, capture_output=True)
            symbols = {line.split()[-1].lstrip("_") for line in result.stdout.splitlines() if line.strip()}
            self.assertIn("vsnprintf", symbols)
            # Clang lowers zero fills to bzero on Darwin (memset elsewhere).
            self.assertLessEqual(symbols, {"vsnprintf", "memset", "bzero", "memcpy", "memmove",
                                           "error", "errno_location", "stack_chk_fail", "stack_chk_guard"})

    def test_observational_gui_and_upstream_flicker(self):
        upstream = (ROOT / "upstream/winuae/od-win32/win32gui.cpp").read_text()
        oracle = upstream[upstream.index("static void gui_flicker_led2 ("):upstream.index("void gui_fps (")]
        oracle = '''namespace reference {
struct gui_info gui_data = {}; unsigned led_mask;
void gui_led(int led, int on, int) {
 if (on > 0) led_mask |= 1u << led; else if (on == 0) led_mask &= ~(1u << led);
}
''' + oracle + "\n}\n"
        with tempfile.TemporaryDirectory(prefix="p6-gui-") as directory:
            source = Path(directory) / "probe.cpp"
            source.write_text((ROOT / "tests/host/gui_probe.cpp").read_text().replace("// P6_FLICKER_REFERENCE", oracle))
            output = Path(directory) / "probe"
            subprocess.run(["clang++", "-std=c++17", "-O2", "-fsanitize=address,undefined",
                            "-fno-sanitize-recover=all", "-I", str(ROOT / "ports/portable"),
                            "-I", str(self.tree / "include"), str(source),
                            str(ROOT / "ports/portable/gui.cpp"), str(ROOT / "ports/portable/diagnostics.cpp"),
                            "-o", str(output)], check=True)
            subprocess.run([str(output)], check=True)

    def test_diagnostics_inventory_is_exhaustive(self):
        contract = json.loads((ROOT / "manifests/host-surface.json").read_text())["contracts"]["diagnostics"]
        groups = contract["implementation"]
        classified = groups["implemented"] + groups["types_or_aliases"] + groups["excluded_pending_link"]
        self.assertEqual(len(classified), len(set(classified)))
        self.assertEqual(set(contract["symbols"]), set(classified))
        # Guard the documented lexical exclusion; headers/macros/linkage still
        # belong to T06, not this primitive-level test.
        selection = json.loads((ROOT / "manifests/source-manifest.json").read_text())["selection"]
        excluded = re.compile(r"\b(?:" + "|".join(groups["excluded_pending_link"]) + r")\b")
        for filename in selection["upstream_units"] + selection["generated_units"]:
            code = (ROOT / "upstream/winuae" / filename).read_text()
            code = mask(re.sub(r"\\\r?\n", "", code), strings=True)
            self.assertIsNone(excluded.search(code), filename)
        options = (ROOT / "upstream/winuae/include/options.h").read_text()
        inputs = (ROOT / "upstream/winuae/include/inputdevice.h").read_text()
        self.assertRegex(options, r"#define MAX_JPORTS 4\b")
        self.assertRegex(inputs, r"#define JOYBUTTON_LIGHTPEN2 10\b")


if __name__ == "__main__":
    unittest.main()
