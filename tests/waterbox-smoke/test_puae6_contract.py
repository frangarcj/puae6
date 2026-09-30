"""T19 closure: what puae6.wbx is, as opposed to what it does (that is test_puae6*.py).

It advances the machine only through p6_run_frame, like the headless and libretro adapters; it is a different artifact from the old uae.wbx
and from the smoke guest, and is found by hash, never by a fallback name; the libretro adapter is not in it.
"""
import json
from pathlib import Path
import re
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import build_waterbox  # noqa: E402

GUEST = build_waterbox.BUILD / "puae6/release/puae6.wbx"
RECEIPT = build_waterbox.BUILD / "puae6/puae6-receipt.json"
ADAPTER_SOURCES = sorted((ROOT / "adapters/waterbox").glob("*.cpp")) + sorted((ROOT / "adapters/waterbox").glob("*.h"))
SCHEDULER_NAMES = re.compile(r"\b(m68k_go\w*|m68k_run\w*|umain|real_main\w*|retro_\w+|start_program|leave_program)\b")


class Puae6ContractTests(unittest.TestCase):
    def test_no_source_of_the_waterbox_adapter_names_a_scheduler_or_the_libretro_api(self):
        self.assertTrue(ADAPTER_SOURCES)
        for source in ADAPTER_SOURCES:
            text = re.sub(r"//[^\n]*|/\*.*?\*/", "", source.read_text(), flags=re.S)
            self.assertIsNone(SCHEDULER_NAMES.search(text), f"{source.name} must advance the machine only through p6_run_frame")
        self.assertIn("p6_run_frame(", (ROOT / "adapters/waterbox/puae6_guest.cpp").read_text())

    @unittest.skipUnless(RECEIPT.is_file(), "puae6.wbx has not been built (tools/build_waterbox.py --puae6)")
    def test_the_guest_reaches_the_engine_only_through_the_p6_abi_and_carries_no_frontend(self):
        tc = build_waterbox.toolchain()
        nm = tc["paths"]["clang"].parent / "llvm-nm"
        objects = [build_waterbox.BUILD / "puae6/release" / f"{source}.o" for source in build_waterbox.puae6_sources() if source.startswith("adapters/waterbox/")]
        self.assertEqual(2, len(objects))
        for obj in objects:
            undefined = {line.split()[-1] for line in subprocess.run([str(nm), "--undefined-only", str(obj)], check=True, capture_output=True, text=True).stdout.splitlines()
                         if len(line.split()) == 2}
            self.assertFalse({name for name in undefined if SCHEDULER_NAMES.fullmatch(name.lstrip("_"))}, obj.name)
            if obj.name.startswith("puae6_guest"):
                self.assertTrue({"p6_init", "p6_load", "p6_run_frame", "p6_unload", "p6_shutdown"} <= undefined)
        defined = subprocess.run([str(nm), "--defined-only", str(GUEST)], check=True, capture_output=True, text=True).stdout
        self.assertNotRegex(defined, r"(?m)\s_?retro_\w+$", "no libretro entry point is linked into the guest")
        self.assertNotIn("libretro", json.dumps(sorted(json.loads(RECEIPT.read_text())["sources"])))

    @unittest.skipUnless(RECEIPT.is_file(), "puae6.wbx has not been built (tools/build_waterbox.py --puae6)")
    def test_the_artifact_is_named_and_hashed_as_puae6_and_is_not_the_smoke_guest_or_the_old_uae(self):
        receipt = json.loads(RECEIPT.read_text())["guest"]
        self.assertEqual("puae6.wbx", Path(receipt["path"]).name)
        self.assertEqual(build_waterbox.sha256(GUEST), receipt["sha256"])
        smoke = build_waterbox.BUILD / "guest/release/p6-smoke.wbx"
        if smoke.is_file():
            self.assertNotEqual(build_waterbox.sha256(smoke), receipt["sha256"])
        self.assertEqual("EM_AARCH64", build_waterbox.validate_elf(GUEST)["machine"])


if __name__ == "__main__":
    unittest.main()
