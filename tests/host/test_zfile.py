"""Compile the real materialized zfile.cpp and pinned public/internal headers."""
import binascii
import hashlib
import json
import re
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
from materialize_upstream import materialize
from check_host_surface import mask


class ZfileTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        key = hashlib.sha256((ROOT / "patches/winuae/series.json").read_bytes()).hexdigest()
        _, cls.tree = materialize(ROOT, "build/t05-sysdeps/" + key)

    def test_real_zfile_asset_api(self):
        self.run_probe("zfile_probe.cpp")

    def test_ram_temporaries(self):
        self.run_probe("zfile_ram_probe.cpp")

    def test_views_and_duplicates(self):
        self.run_probe("zfile_views_probe.cpp")

    def test_data_crc_and_text(self):
        self.run_probe("zfile_helpers_probe.cpp")

    def run_probe(self, probe):
        with tempfile.TemporaryDirectory(prefix="p6-zfile-") as directory:
            directory = Path(directory)
            blob, zfile = directory / "blob.o", directory / "zfile.o"
            flags = ["-O2", "-Wall", "-Wextra", "-Werror", "-fsanitize=address,undefined", "-fno-sanitize-recover=all",
                     "-I", str(ROOT / "ports/portable"), "-I", str(self.tree / "include")]
            subprocess.run(["clang", "-std=c11", *flags, "-c", str(ROOT / "ports/portable/blob_io.c"), "-o", str(blob)], check=True)
            crc = directory / "crc.o"
            subprocess.run(["clang++", "-std=c++17", *flags, "-c", str(self.tree / "crc32.cpp"), "-o", str(crc)], check=True)
            for injected in (False, True):
                subprocess.run(["clang++", "-std=c++17", *flags, *(["-Dcalloc=p6_test_calloc"] + (["-Dmalloc=p6_test_malloc", "-Dfree=p6_test_free"] if probe != "zfile_probe.cpp" else []) if injected else []), "-MMD", "-MF", str(directory / "deps"),
                                "-c", str(self.tree / "zfile.cpp"), "-o", str(zfile)], check=True)
                deps = (directory / "deps").read_text()
                for name in ("zfile.h", "zarchive.h"):
                    self.assertIn(str(self.tree / "include" / name), deps)
                self.assertIn(str(ROOT / "ports/portable/zfile_port.inc"), deps)
                self.assertNotIn("archivers/", deps)
                self.assertNotIn("options.h", deps)
                exe = directory / "probe"
                subprocess.run(["clang++", "-std=c++17", *flags, *(["-DP6_ZFILE_ALLOC_TEST"] if injected else []), str(ROOT / "tests/host" / probe),
                                str(blob), str(zfile), str(crc), "-o", str(exe)], check=True)
                subprocess.run([str(exe)], check=True)

    def test_joint_portable_providers_and_full_hash_unit(self):
        with tempfile.TemporaryDirectory(prefix="p6-host-link-") as directory:
            directory = Path(directory)
            for optimization in ("-O0", "-O2"):
                flags = [optimization, "-Wall", "-Wextra", "-Werror", "-fsanitize=address,undefined",
                         "-fno-sanitize-recover=all", "-I", str(ROOT / "ports/portable"), "-I", str(self.tree / "include")]
                providers = [ROOT / "ports/portable" / name for name in
                             ("blob_io.c", "civil_time.c", "diagnostics.cpp", "gui.cpp")]
                providers += [self.tree / "zfile.cpp", self.tree / "crc32.cpp"]
                objects, defined, imported = [], set(), set()
                for source in providers:
                    obj = directory / (source.stem + ".o")
                    cpp = source.suffix == ".cpp"
                    subprocess.run(["clang++" if cpp else "clang", "-std=c++17" if cpp else "-std=c11", *flags,
                                    "-c", str(source), "-o", str(obj)], check=True)
                    objects.append(str(obj))
                    symbols = subprocess.run(["nm", "-g", str(obj)], text=True, capture_output=True, check=True)
                    for line in symbols.stdout.splitlines():
                        fields = line.split()
                        if len(fields) >= 2:
                            (imported if fields[-2] == "U" else defined).add(fields[-1])
                # Sanitizers are test runtime; the remaining imports must be plain
                # memory/formatting/errno. Provider-to-provider imports must close.
                external = {name.lstrip("_") for name in imported - defined}
                external = {name for name in external if not name.startswith(("asan_", "ubsan_", "sanitizer_"))}
                self.assertLessEqual(external, {"malloc", "calloc", "free", "memcpy", "memmove", "memset", "bzero",
                                               "strlen", "strcmp", "strcpy", "strcpy_chk", "strchr", "vsnprintf", "error",
                                               "errno_location", "stack_chk_fail", "stack_chk_guard"})
                exe = directory / "joint"
                subprocess.run(["clang++", "-std=c++17", *flags, str(ROOT / "tests/host/portable_services_probe.cpp"),
                                *objects, "-o", str(exe)], check=True)
                subprocess.run([str(exe)], check=True)
                for length in (0, 1, 3, 55, 56, 63, 64, 65, 119, 120, 127, 128, 129, 511, 512, 513, 8193):
                    data = bytes((i * 37 + 11) % 256 for i in range(length))
                    result = subprocess.run([str(exe), "hashes"], input=data, capture_output=True, check=True)
                    expected = f"{binascii.crc32(data):08X} {binascii.crc_hqx(data, 0xffff):04X} {hashlib.sha1(data).hexdigest().upper()}"
                    self.assertEqual(result.stdout.decode().strip(), expected)

    def test_selected_api_inventory(self):
        implementation = json.loads((ROOT / "manifests/host-surface.json").read_text())["contracts"]["vfs"]["implementation"]
        selection = json.loads((ROOT / "manifests/source-manifest.json").read_text())["selection"]
        callers = {}
        for filename in selection["upstream_units"] + selection["generated_units"]:
            if filename == "zfile.cpp":
                continue  # Its legacy body is excluded by the boundary patch.
            code = (ROOT / "upstream/winuae" / filename).read_text(errors="replace")
            code = mask(re.sub(r"\\\r?\n", "", code), True)
            for name in set(re.findall(r"\b(zfile_\w+)\s*\(", code)):
                callers.setdefault(name, []).append(filename)
        self.assertEqual(callers, implementation["selected_callers"])
        code = (ROOT / "ports/portable/zfile_port.inc").read_text()
        defined = set(re.findall(r"^[A-Za-z_][\w ]*\**(zfile_\w+)\(", code, re.M))
        self.assertEqual(defined, set(implementation["implemented"]))
        excluded = set(implementation["excluded_pending_link"])
        self.assertFalse(defined & excluded)
        self.assertEqual(set(callers) - defined, excluded)

    def test_no_host_backing_symbols(self):
        with tempfile.TemporaryDirectory(prefix="p6-zfile-symbols-") as directory:
            obj = Path(directory) / "zfile.o"
            subprocess.run(["clang++", "-std=c++17", "-O2", "-c", "-I", str(ROOT / "ports/portable"),
                            "-I", str(self.tree / "include"), str(self.tree / "zfile.cpp"), "-o", str(obj)], check=True)
            result = subprocess.run(["nm", "-u", str(obj)], text=True, capture_output=True, check=True)
            symbols = {line.split()[-1].lstrip("_") for line in result.stdout.splitlines() if line.strip()}
            allowed = {"calloc", "malloc", "free", "memmove", "strcpy", "strcpy_chk", "strchr", "memcpy", "memset", "bzero",
                       "strlen", "Z9get_crc32Pvi", "error", "errno_location", "stack_chk_fail", "stack_chk_guard"}
            allowed.update("p6_blob_"+name for name in ("open", "close", "stat", "read", "seek", "exists"))
            self.assertLessEqual(symbols, allowed)


if __name__ == "__main__":
    unittest.main()
