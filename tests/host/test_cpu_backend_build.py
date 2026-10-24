"""T55c1 BUILD01: actual linked native and WBX artifacts, never generation alone.

The WBX is built beforehand by tools/build_waterbox.py --puae6 --jobs 2. Missing
artifact means BLOCKED; a stale receipt or missing table/export means failure.
No sandbox is executed here and this is not a WB03 snapshot test.
"""
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import unittest

from tools import build_waterbox as wb
from tests.build_support import build_targets
from tests.host.test_bridge_lifecycle import TOOLS, run_probe

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
RECEIPT = wb.BUILD / "puae6/puae6-receipt.json"


class BackendBuildTests(unittest.TestCase):
    def assert_backend_symbols(self, output):
        for model in (0, 1):
            self.assertRegex(output, rf"(?m)\b_?op_smalltbl_{model}$")
        self.assertIn("flush_cpu_caches_040", output)

    @unittest.skipUnless(TOOLS and shutil.which("nm"), "native toolchain unavailable")
    def test_native_tables_link_and_public_lifecycle_runs(self):
        exe = build_targets(ROOT / "build/state-opt", ["p6_lifecycle_probe"], optimized=True)["p6_lifecycle_probe"]
        names = subprocess.run(["nm", "-g", str(exe)], check=True, capture_output=True, text=True).stdout
        self.assert_backend_symbols(names)
        result = run_probe(exe)
        self.assertEqual(0, result.returncode, result.stdout[-1000:] + result.stderr[-1000:])
        self.assertIn("bridge lifecycle ok", result.stdout)

    @unittest.skipUnless(RECEIPT.is_file(), "build full WBX with tools/build_waterbox.py --puae6 --jobs 2")
    def test_current_waterbox_whole_objects_link_and_preserve_all_exports(self):
        receipt = json.loads(RECEIPT.read_text())
        self.assertEqual("BUILD01_PASS_GUEST_NOT_EXECUTED", receipt["status"])
        wb.check_guest_matches_trees(RECEIPT)
        self.assertEqual(set(wb.puae6_sources()), set(receipt["sources"]))
        for source, digest in receipt["sources"].items():
            self.assertEqual(digest, wb.sha256(ROOT / source), source)
        guest = wb.BUILD / "puae6/release/puae6.wbx"
        self.assertEqual(str(guest), receipt["guest"]["path"])
        self.assertEqual(receipt["guest"]["sha256"], wb.sha256(guest))
        self.assertEqual("EM_AARCH64", wb.validate_elf(guest, 16384)["machine"])
        wb.check_no_compiler_tls(guest)
        paths, _ = wb.locked_tools(json.loads(wb.LOCK.read_text()))
        environment = wb.hosted_environment(paths)["direct_env"]
        nm = paths["clang"].parent / "llvm-nm"
        names = subprocess.run([str(nm), "--defined-only", str(guest)], check=True, capture_output=True, text=True, env=environment).stdout
        self.assert_backend_symbols(names)
        exports = set()
        for source in (ROOT / "adapters/waterbox").glob("*.cpp"):
            exports.update(re.findall(r"ECL_EXPORT\s+\w+\s+(p6w_\w+)\s*\(", source.read_text()))
        self.assertGreater(len(exports), 10)
        symbols = {line.split()[-1] for line in names.splitlines() if line.split()}
        self.assertLessEqual(exports, symbols)
        objects = [wb.BUILD / "puae6/release" / f"{source}.o" for source in receipt["sources"]]
        self.assertEqual([], wb.audit_imports({"paths": paths, "env": environment}, objects)["unexpected"])
