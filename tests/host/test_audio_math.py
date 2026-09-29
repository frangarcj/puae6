"""Paula coefficient math against the pinned, initialized upstream FPU wrapper."""
from pathlib import Path
import platform
import subprocess
import tempfile
import unittest

from tests.host import ensure_generated

ROOT = Path(__file__).resolve().parents[2]


def function(source, signature):
    start = source.index(signature)
    opening = source.index("{", start)
    depth = 1
    end = opening + 1
    while depth:
        depth += (source[end] == "{") - (source[end] == "}")
        end += 1
    return source[start:end] + "\n"


class AudioMathTests(unittest.TestCase):
    def test_upstream_tangent_and_paula_coefficients(self):
        ensure_generated()
        tree = ROOT / "build/upstream"
        native = (ROOT / "upstream/winuae/fpp_native.cpp").read_text()
        audio = (ROOT / "upstream/winuae/audio.cpp").read_text()
        # Extract, do not reimplement the reference's word conversions or tangent.
        reference = """#include <cstdint>
#include <cmath>
#include <initializer_list>
#include "softfloat/softfloat.h"
#define SOFTFLOAT_CONVERSIONS 1
#if __BYTE_ORDER__ == __ORDER_BIG_ENDIAN__
#define WORDS_BIGENDIAN 1
#endif
namespace original {
using uae_u32 = uint32_t;
using uae_u64 = uint64_t;
using fptype = double;
struct fpdata { double fp; };
static float_status fs{};
void init() {
    set_floatx80_rounding_precision(80, &fs);
    set_float_rounding_mode(float_round_to_zero, &fs);
}
"""
        for signature in ("static void fp_to_double(", "static void fp_from_double(",
                          "double softfloat_tan("):
            reference += function(native, signature)
        reference += function(audio, "static float rc_calculate_a0 (") + "}\n"
        common = ["clang++", "-std=c++17", "-UNDEBUG", "-fno-fast-math",
                  "-Werror=nonportable-include-path", "-I" + str(ROOT / "ports/portable"),
                  "-I" + str(ROOT / "build/generated"), "-I" + str(tree / "include"),
                  "-I" + str(tree)]
        sources = [tree / "softfloat/softfloat.cpp", tree / "softfloat/softfloat_fpsp.cpp",
                   ROOT / "ports/portable/audio_math.cpp"]
        with tempfile.TemporaryDirectory(prefix="p6-audio-math-") as tmp:
            tmp = Path(tmp)
            (tmp / "upstream_audio_math.h").write_text(reference)
            for optimization in ("-O0", "-O2"):
                for index, source in enumerate(sources):
                    obj = tmp / f"math{index}.o"
                    subprocess.run([*common, optimization, "-c", str(source), "-o", str(obj)],
                                   check=True, timeout=60)
                    imports = subprocess.run(["nm", "-u", str(obj)], text=True,
                                             capture_output=True, check=True).stdout
                    self.assertNotRegex(imports, r"(?m)\s+_?(?:tan|sin|cos)(?:f|l)?$")
                    for forbidden in ("fp_init", "fpp_", "pthread"):
                        self.assertNotIn(forbidden, imports)
                    deps = subprocess.run([*common, "-M", str(source)], text=True,
                                          capture_output=True, check=True).stdout
                    self.assertNotIn("od-win32", deps)
                    self.assertNotIn("fpp_native", deps)
                strip = "-Wl,-dead_strip" if platform.system() == "Darwin" else "-Wl,--gc-sections"
                exe = tmp / "probe"
                subprocess.run([*common, optimization, "-I" + str(tmp),
                                "-ffunction-sections", "-fdata-sections", strip,
                                "-fsanitize=address,undefined", "-fno-sanitize-recover=all",
                                str(ROOT / "tests/host/audio_math_probe.cpp"),
                                *map(str, sources), "-o", str(exe)], check=True, timeout=60)
                subprocess.run([str(exe)], check=True, timeout=30)


if __name__ == "__main__":
    unittest.main()
