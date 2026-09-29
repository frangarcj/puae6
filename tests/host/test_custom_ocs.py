"""Full custom compilation plus executable OCS register and diagnostic probes."""
from pathlib import Path
import platform
import subprocess
import tempfile
import unittest
from tests.host.test_audio_math import function

from tests.host import ensure_generated

ROOT = Path(__file__).resolve().parents[2]


class CustomOCSTests(unittest.TestCase):
    def test_ocs_colors_without_aga_storage(self):
        ensure_generated()
        tree = ROOT / "build/upstream"
        original = (ROOT / "upstream/winuae/custom.cpp").read_text()
        patched = (tree / "custom.cpp").read_text()
        common = ["clang++", "-std=c++17", "-UNDEBUG", "-fno-fast-math",
                  "-I" + str(ROOT / "ports/portable"), "-I" + str(ROOT / "build/generated"),
                  "-I" + str(tree / "include"), "-I" + str(tree)]
        strip = "-Wl,-dead_strip" if platform.system() == "Darwin" else "-Wl,--gc-sections"
        with tempfile.TemporaryDirectory(prefix="p6-custom-ocs-") as tmp:
            tmp = Path(tmp)
            reference = ''.join(function(original, sig) for sig in
                                ("static void COLOR_WRITE(", "bool get_custom_color_reg("))
            (tmp / "reference_colors.h").write_text(reference)
            for optimization in ("-O0", "-O2"):
                outputs = []
                for baseline, source in ((True, original), (False, patched)):
                    reset = function(source, "void custom_reset(")
                    loop = function(reset, "for (int i = 0; i < 256; i++)")
                    self.assertIn("uaerand()", loop)
                    (tmp / "reset_color_loop.h").write_text("static void reset_color_loop() {\n" + loop + "}\n")
                    extra = ["-DTEST_REFERENCE"] if baseline else [str(ROOT / "ports/portable/diagnostics.cpp")]
                    exe = tmp / "probe"
                    subprocess.run([*common, optimization, "-I" + str(tmp),
                                    "-ffunction-sections", "-fdata-sections", strip,
                                    "-fsanitize=address,undefined", "-fno-sanitize-recover=all",
                                    str(ROOT / "tests/host/custom_ocs_probe.cpp"),
                                    *extra, "-o", str(exe)], check=True, timeout=60)
                    outputs.append(subprocess.run([str(exe)], check=True, capture_output=True,
                                                  text=True, timeout=30).stdout)
                self.assertEqual(outputs[0], outputs[1])
                obj = tmp / "custom.o"
                subprocess.run([*common, optimization, "-c", str(tree / "custom.cpp"),
                                "-o", str(obj)], check=True, timeout=60)
                imports = subprocess.run(["nm", "-u", str(obj)], check=True,
                                         capture_output=True, text=True).stdout
                for forbidden in ("serial_rbf_change", "specialmonitor_store_fmode", "_vsntprintf",
                                  "uae_sem_", "uae_start_thread", "COLOR_WRITE"):
                    self.assertNotIn(forbidden, imports)
                deps = subprocess.run([*common, "-M", str(tree / "custom.cpp")],
                                      check=True, capture_output=True, text=True).stdout
                # Existing audited reuse: pure-C CCR layout, no Windows APIs.
                allowed = str(tree / "od-win32/machdep/m68k.h")
                self.assertIn(allowed, deps)
                self.assertNotIn("od-win32", deps.replace(allowed, ""))
                self.assertNotIn("threaddep", deps)


if __name__ == "__main__":
    unittest.main()
