"""Execute the pinned CCR provider; no CPU instruction/timing claim."""
import hashlib
import json
from pathlib import Path
import platform
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
from materialize_upstream import materialize


class M68kFlagsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        key = hashlib.sha256((ROOT / "patches/winuae/series.json").read_bytes()).hexdigest()
        _, cls.tree = materialize(ROOT, "build/t06-events/" + key)
        cls.unit = cls.tree / "od-win32/machdep/m68k.cpp"
        cls.probe = ROOT / "tests/host/m68k_flags_probe.cpp"
        cls.common = ["clang++", "-std=c++17", "-I" + str(ROOT / "ports/portable"),
                      "-I" + str(cls.tree / "include"), "-I" + str(cls.tree)]

    def test_ccr_truth_table_preservation_and_imports(self):
        manifest = json.loads((ROOT / "manifests/source-manifest.json").read_text())
        reuse = manifest["portable_reuse"]["od-win32/machdep/m68k.cpp"]
        for key, name in (("source_sha256", "m68k.cpp"), ("header_sha256", "m68k.h")):
            self.assertEqual(hashlib.sha256((self.unit.parent / name).read_bytes()).hexdigest(), reuse[key])
        with tempfile.TemporaryDirectory(prefix="p6-ccr-") as tmp:
            obj, exe, dep = (Path(tmp) / name for name in ("ccr.o", "probe", "ccr.d"))
            for optimization in ("-O0", "-O2"):
                flags = [optimization, "-fsanitize=address,undefined", "-fno-sanitize-recover=all"]
                subprocess.run([*self.common, *flags, "-MMD", "-MF", str(dep),
                                "-c", str(self.unit), "-o", str(obj)], check=True)
                subprocess.run([*self.common, *flags, str(self.probe), str(obj), "-o", str(exe)], check=True)
                subprocess.run([str(exe)], check=True, timeout=20)
                # No Windows config or general backend directory on the include path.
                dependencies = dep.read_text().replace("\\\n", " ").split()[1:]
                windows = {p for p in dependencies if "/od-win32/" in p}
                self.assertEqual(windows, {str(self.unit), str(self.unit.with_suffix('.h'))})
                self.assertIn(str(ROOT / "ports/portable/sysconfig.h"), dependencies)
            subprocess.run([*self.common, "-O0", "-c", str(self.unit), "-o", str(obj)], check=True)
            result = subprocess.run(["nm", "-u", str(obj)], text=True, capture_output=True, check=True)
            imports = {line.split()[-1].lstrip("_") for line in result.stdout.splitlines() if line.strip()}
            self.assertEqual(imports, {"regflags"})

    @unittest.skipUnless(platform.system() == "Darwin", "Darwin cross-architecture syntax check")
    def test_x86_64_layout_compile_only(self):
        # Real compiler target checks upstream's wider struct, without executing
        # that architecture or claiming cross-host determinism.
        subprocess.run([*self.common, "-arch", "x86_64", "-fsyntax-only", str(self.probe),
                        str(self.unit)], check=True)


if __name__ == "__main__":
    unittest.main()
