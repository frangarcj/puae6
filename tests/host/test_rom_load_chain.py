"""Exercise the real explicit ROM-reader to A500-memory-loader chain."""
from pathlib import Path
import os
import subprocess
import tempfile
import unittest

from tests.host.test_cpu_rom_guard import function

from tests.host import ensure_generated

ROOT = Path(__file__).resolve().parents[2]


class RomLoadChainTests(unittest.TestCase):
    def test_real_rommgr_to_memory_loader(self):
        ensure_generated()
        tree = ROOT / "build/upstream"
        memory = (tree / "memory.cpp").read_text()
        rommgr = (tree / "rommgr.cpp").read_text()
        memory_bodies = memory[memory.index("static int rom_load_status"):
                                memory.index("#endif", memory.index("static int rom_load_status"))]
        memory_bodies += "\n".join(line for line in memory.splitlines()
                                     if line.startswith("#define EXTENDED_ROM_")) + "\n"
        memory_bodies += function(memory, "static const uae_u8 romend[20]") + ";\n"
        for signature in (
            "static struct zfile *get_kickstart_filehandle(", "static int read_kickstart(",
            "static int load_kickstart (void)", "static void restore_roms(void)",
            "bool read_kickstart_version(", "void reload_roms(void)",
            "static void kickmem_init(void)", "void memory_init (void)",
            "void memory_cleanup (void)", "void set_roms_modified(void)"):
            memory_bodies += function(memory, signature)
        rom_checksum = (function(rommgr, "static int kickstart_checksum_do (") +
                        function(rommgr, "int kickstart_checksum ("))

        rom_support = rommgr[rommgr.index("#define ROM_KEY_NUM 4"):
                             rommgr.index("void addkeyfile", rommgr.index("#define ROM_KEY_NUM 4"))]
        rom_parts = [rom_support]
        for signature in (
            "void addkeyfile (const TCHAR *path)", "int get_keyring (void)",
            "int load_keyring (struct uae_prefs *p, const TCHAR *path)",
            "void free_keyring (void)", "static int cmpsha1 (",
            "static struct romdata *checkromdata (", "static int kickstart_checksum_more_do (",
            "int decode_cloanto_rom_do (", "static void byteswap(",
            "static void wordbyteswap (", "static void mergecd32 (",
            "struct zfile *rom_fopen(", "static struct zfile *rom_fopen2(",
            "struct zfile *read_rom_name("):
            rom_parts.append(function(rommgr, signature))
        rom_input = "\n".join(rom_parts)

        with tempfile.TemporaryDirectory(prefix="p6-rom-load-chain-") as directory:
            tmp = Path(directory)
            (tmp / "memory_rom_status_under_test.h").write_text(memory_bodies)
            (tmp / "rom_checksum_under_test.h").write_text(rom_checksum)
            (tmp / "rom_input_under_test.h").write_text(rom_input)
            common = ["-UNDEBUG", "-fsanitize=address,undefined", "-fno-sanitize-recover=all",
                      "-I" + str(tmp), "-I" + str(ROOT / "ports/portable"),
                      "-I" + str(ROOT / "build/generated"), "-I" + str(tree / "include"),
                      "-I" + str(tree)]
            for optimization in ("-O0", "-O2"):
                blob, exe = tmp / "blob.o", tmp / "probe"
                subprocess.run(["clang", "-std=c11", *common, optimization, "-c",
                                str(ROOT / "ports/portable/blob_io.c"), "-o", str(blob)],
                               check=True, timeout=30)
                subprocess.run(["clang++", "-std=c++17", *common, optimization,
                                str(ROOT / "tests/host/rom_load_chain_probe.cpp"),
                                str(tree / "zfile.cpp"), str(tree / "crc32.cpp"),
                                str(ROOT / "ports/portable/diagnostics.cpp"), str(blob),
                                "-o", str(exe)], check=True, timeout=120)
                subprocess.run([str(exe)], check=True, timeout=60,
                               env={**os.environ, "ASAN_OPTIONS": "detect_leaks=0"})


if __name__ == "__main__":
    unittest.main()
