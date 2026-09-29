"""Run the patched upstream vertical-sync timing function in isolation."""
from pathlib import Path
import subprocess
import tempfile
import unittest
from tests.host.test_audio_math import function

ROOT = Path(__file__).resolve().parents[2]


class CustomTimingTests(unittest.TestCase):
    def test_patched_framesync_keeps_physical_audio_clock_and_beam_geometry(self):
        subprocess.run(["python3", "tools/generate_cpu.py"], cwd=ROOT, check=True,
                       stdout=subprocess.PIPE, timeout=120)
        source = (ROOT / "build/upstream/custom.cpp").read_text()
        vsync = function(source, "void compute_vsynctime(void)")
        framesync = function(source, "void compute_framesync(void)")
        self.assertIn("devices_update_sound", vsync)
        self.assertIn("devices_update_sync", vsync)
        self.assertIn("compute_vsynctime();", framesync)
        # The probe includes real upstream declarations before these exact bodies.
        # P6_PORTABLE removes monitor negotiation and host pacing at compilation.
        header = vsync + framesync
        with tempfile.TemporaryDirectory(prefix="p6-custom-timing-") as tmp:
            tmp = Path(tmp)
            (tmp / "custom_timing_under_test.h").write_text(header)
            common = ["clang++", "-std=c++17", "-UNDEBUG", "-fno-fast-math",
                      "-fsanitize=address,undefined", "-fno-sanitize-recover=all",
                      "-I" + str(tmp),
                      "-I" + str(ROOT / "ports/portable"),
                      "-I" + str(ROOT / "build/generated"),
                      "-I" + str(ROOT / "build/upstream/include"),
                      "-I" + str(ROOT / "build/upstream")]
            for optimization in ("-O0", "-O2"):
                exe = tmp / "probe"
                subprocess.run([*common, optimization,
                                str(ROOT / "tests/host/custom_timing_probe.cpp"),
                                "-o", str(exe)], check=True, timeout=60)
                imports = subprocess.run(["nm", "-u", str(exe)], text=True,
                                         capture_output=True, check=True).stdout
                for host_policy in ("target_adjust_vblank_hz", "target_getcurrentvblankrate",
                                    "get_chipset_refresh", "getvsyncrate",
                                    "vsync_switchmode", "isvsync_chipset"):
                    self.assertNotIn(host_policy, imports)
                deps = subprocess.run([*common, "-M",
                                       str(ROOT / "tests/host/custom_timing_probe.cpp")],
                                      text=True, capture_output=True, check=True).stdout
                self.assertNotIn("od-win32", deps)
                subprocess.run([str(exe)], check=True, timeout=30)


if __name__ == "__main__":
    unittest.main()
