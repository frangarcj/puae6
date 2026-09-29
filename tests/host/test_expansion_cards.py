"""Run the real expansion card naming and metadata path in reference and P6."""
from pathlib import Path
import os
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]


def braced(source, keyword):
    start = source.index(keyword)
    opening = source.index("{", start)
    depth, end = 1, opening + 1
    while depth:
        depth += (source[end] == "{") - (source[end] == "}")
        end += 1
    return source[start:end] + "\n"


def extracted(source):
    struct = braced(source, "struct card_data") + ";\n"
    body = braced(source, "static void expansion_parse_cards(")
    defines = "\n".join(line for line in source.splitlines()
                         if line.startswith("#define CARD_FLAG_CHILD ") or
                         line.startswith("#define MAX_EXPANSION_BOARD_SPACE "))
    return "\n".join((defines, struct,
        "#define rom_card 0x10\n#define add_memory 0x20\n#define zorroII 0xc0\n#define zorroIII 0x80",
        "static struct card_data cards_set[MAX_EXPANSION_BOARD_SPACE];",
        "static struct card_data *cards[MAX_EXPANSION_BOARD_SPACE];",
        "static int cardno;", "static void reset_ac_data(struct uae_prefs *);",
        "static void expansion_parse_autoconfig(struct card_data *, const uae_u8 *);",
        "static int get_order(struct uae_prefs *, struct card_data *);",
        "static uae_u8 autoconfig_read(const uae_u8 *, int);",
        "const struct expansionromtype *get_device_expansion_rom(int);",
        "bool expamem_z3hack(struct uae_prefs *);",
        "void write_log(const TCHAR *, ...);", body))


class ExpansionCardsTests(unittest.TestCase):
    def test_real_card_names_callbacks_and_metadata(self):
        subprocess.run(["python3", "tools/generate_cpu.py"], cwd=ROOT, check=True,
                       stdout=subprocess.PIPE, timeout=120)
        original = (ROOT / "upstream/winuae/expansion.cpp").read_text()
        patched = (ROOT / "build/upstream/expansion.cpp").read_text()
        sources = [extracted(original), extracted(patched)]
        common = ["clang++", "-std=c++17", "-UNDEBUG", "-fsanitize=address,undefined",
                  "-fno-sanitize-recover=all", "-I{tmp}", "-I" + str(ROOT / "ports/portable"),
                  "-I" + str(ROOT / "build/generated"),
                  "-I" + str(ROOT / "build/upstream/include"),
                  "-I" + str(ROOT / "build/upstream")]
        with tempfile.TemporaryDirectory(prefix="p6-expansion-cards-") as directory:
            tmp = Path(directory)
            for optimization in ("-O0", "-O2"):
                outputs = []
                for reference, body in ((True, sources[0]), (False, sources[1])):
                    (tmp / "expansion_cards_under_test.h").write_text(body)
                    flags = ["-D_stprintf=sprintf"] if reference else ["-DP6_PORTABLE"]
                    exe = tmp / "probe"
                    args = [arg.format(tmp=tmp) for arg in common]
                    subprocess.run([*args, optimization, *flags,
                                    str(ROOT / "tests/host/expansion_cards_probe.cpp"),
                                    "-o", str(exe)], check=True, timeout=90)
                    result = subprocess.run([str(exe)], capture_output=True,
                                            text=True, timeout=30,
                                            env={**os.environ, "ASAN_OPTIONS": "detect_leaks=0"})
                    self.assertEqual(result.returncode, 0, result.stderr)
                    outputs.append(result.stdout)
                self.assertEqual(outputs[0], outputs[1])


if __name__ == "__main__":
    unittest.main()
