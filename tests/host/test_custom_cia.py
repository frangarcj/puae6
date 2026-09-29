"""Custom A500 IRQ/TOD integration without BSDSOCKET or Akiko providers."""
from pathlib import Path
import platform
import subprocess
import tempfile
import unittest
from tests.host.test_audio_math import function

from tests.host import ensure_generated

ROOT = Path(__file__).resolve().parents[2]


class CustomCIATests(unittest.TestCase):
    def test_custom_cia_waveforms_and_irq_requests(self):
        ensure_generated()
        tree = ROOT / "build/upstream"
        common = ["clang++", "-std=c++17", "-UNDEBUG", "-fno-fast-math",
                  "-I" + str(ROOT / "ports/portable"), "-I" + str(ROOT / "build/generated"),
                  "-I" + str(tree / "include"), "-I" + str(tree)]
        strip = "-Wl,-dead_strip" if platform.system() == "Darwin" else "-Wl,--gc-sections"
        sources = [ROOT / "tests/host/custom_cia_probe.cpp", tree / "events.cpp", tree / "keybuf.cpp",
                   ROOT / "ports/portable/atomic.cpp", ROOT / "ports/portable/diagnostics.cpp"]
        with tempfile.TemporaryDirectory(prefix="p6-custom-cia-") as tmp:
            tmp = Path(tmp)
            for optimization in ("-O0", "-O2"):
                outputs = []
                for baseline, path in ((True, ROOT / "upstream/winuae/custom.cpp"),
                                       (False, tree / "custom.cpp")):
                    source = path.read_text()
                    bodies = ''.join(function(source, signature) for signature in
                                     ("static void cia_hsync_do(void)", "void rethink_uae_int(void)"))
                    if not baseline:
                        # Keep the real copper state layout and decision helpers.
                        begin = source.index("enum copper_states {")
                        end = source.index("static int copper_enabled_thisline;", begin)
                        bodies += source[begin:end] + "static int copper_enabled_thisline;\n"
                        for signature in ("static bool is_copper_dma(bool checksame)",
                                          "static void compute_spcflag_copper(void)\n{",
                                          "static void rtg_vsynccheck(void)",
                                          "static void hsync_handler_post(bool onvsync)"):
                            bodies += function(source, signature)
                    (tmp / "custom_cia_under_test.h").write_text(bodies)
                    exe = tmp / "probe"
                    subprocess.run([*common, optimization, "-I" + str(tmp),
                                    *(["-DTEST_REFERENCE"] if baseline else []),
                                    "-ffunction-sections", "-fdata-sections", strip,
                                    "-fsanitize=address,undefined", "-fno-sanitize-recover=all",
                                    *map(str, sources), "-o", str(exe)], check=True, timeout=60)
                    if not baseline:
                        imports = subprocess.run(["nm", "-u", str(exe)], check=True,
                                                 text=True, capture_output=True).stdout
                        for forbidden in ("uae_time", "sleep", "isvsync", "audio_pull",
                                          "audio_finish_pull", "target_get_display_scanline"):
                            self.assertNotIn(forbidden, imports)
                    outputs.append(subprocess.run([str(exe)], check=True, capture_output=True,
                                                  text=True, timeout=30).stdout)
                self.assertEqual(outputs[0], outputs[1])
                obj = tmp / "custom.o"
                subprocess.run([*common, optimization, "-c", str(tree / "custom.cpp"),
                                "-o", str(obj)], check=True, timeout=60)
                imports = subprocess.run(["nm", "-u", str(obj)], check=True,
                                         text=True, capture_output=True).stdout
                for symbol in ("bsdsock_fake_int_handler", "bsd_int_requested", "akiko_ntscmode"):
                    self.assertNotIn(symbol, imports)


if __name__ == "__main__":
    unittest.main()
