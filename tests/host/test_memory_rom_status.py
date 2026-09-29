"""Run actual ROM loader/lifecycle bodies with observed device boundaries."""
from pathlib import Path
import subprocess
import tempfile
import unittest

from tests.host.test_cpu_rom_guard import function

from tests.host import ensure_generated

ROOT = Path(__file__).resolve().parents[2]


class MemoryRomStatusTests(unittest.TestCase):
    def test_load_errors_recovery_and_lifecycle(self):
        ensure_generated()
        tree = ROOT / "build/upstream"
        source = (tree / "memory.cpp").read_text()
        rommgr = (tree / "rommgr.cpp").read_text()
        bodies = source[source.index("static int rom_load_status"):
                        source.index("#endif", source.index("static int rom_load_status"))]
        bodies += "\n".join(line for line in source.splitlines()
                            if line.startswith("#define EXTENDED_ROM_")) + "\n"
        bodies += function(source, "static const uae_u8 romend[20]") + ";\n"
        for signature in ("static struct zfile *get_kickstart_filehandle(",
                          "static int read_kickstart(", "static int load_kickstart (void)",
                          "static void restore_roms(void)", "bool read_kickstart_version(",
                          "void reload_roms(void)", "static void kickmem_init(void)",
                          "void memory_init (void)", "void memory_cleanup (void)",
                          "void set_roms_modified(void)"):
            bodies += function(source, signature)
        # Reset cannot silently clear causal status without invoking a load/init.
        self.assertNotIn("rom_load_status", function(source, "void memory_reset (void)"))
        with tempfile.TemporaryDirectory(prefix="p6-memory-rom-status-") as directory:
            tmp = Path(directory)
            (tmp / "memory_rom_status_under_test.h").write_text(bodies)
            (tmp / "rom_checksum_under_test.h").write_text(
                function(rommgr, "static int kickstart_checksum_do (") +
                function(rommgr, "int kickstart_checksum ("))
            common = ["-UNDEBUG", "-fsanitize=address,undefined", "-fno-sanitize-recover=all",
                      "-I" + str(tmp), "-I" + str(ROOT / "ports/portable"),
                      "-I" + str(ROOT / "build/generated"),
                      "-I" + str(tree / "include"), "-I" + str(tree)]
            for optimization in ("-O0", "-O2"):
                blob, exe = tmp / "blob.o", tmp / "probe"
                subprocess.run(["clang", "-std=c11", *common, optimization, "-c",
                                str(ROOT / "ports/portable/blob_io.c"), "-o", str(blob)],
                               check=True, timeout=30)
                subprocess.run(["clang++", "-std=c++17", *common, optimization,
                                str(ROOT / "tests/host/memory_rom_status_probe.cpp"),
                                str(tree / "zfile.cpp"), str(tree / "crc32.cpp"),
                                str(ROOT / "ports/portable/diagnostics.cpp"), str(blob),
                                "-o", str(exe)], check=True, timeout=90)
                subprocess.run([str(exe)], check=True, timeout=30)


if __name__ == "__main__":
    unittest.main()
