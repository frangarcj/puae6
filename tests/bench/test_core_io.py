"""PERF01 (T34): the two things the BizHawk core did at every unit that were not the engine and not the sandbox, and that cost something measurable.

The core asked the controller for ~110 buttons by a name it built with string interpolation each time (~4.7 KB of managed garbage per unit) and copied the picture with a scalar loop
that ORs the alpha into 434 112 pixels. integration/bizhawk/core/Puae6 now has the names in a table (Controllers.KeyButtons and the like) and PixelCopy.XrgbToArgb with vectors.

tests/ffi/IoBench.cs (`Ffi.dll iobench`) keeps the old form of each next to the new one, on the same data, and these tests assert what must stay true:

  * the names are exactly the strings the interpolation made, and every one is a button or an axis of the controller definition;
  * XrgbToArgb is `target[i] = source[i] | 0xFF000000` for every length and offset, writes nothing outside its span and refuses a target that is too short;
  * the picture of 756 x 574 pixels and the packet of keys are the same as the old forms made;
  * the new form of the questions allocates nothing, the old one did (the exact number of bytes is not asserted; it is the length of the names);
  * the host is the Release build (the managed code BizHawk ships, which tools/bench.py measures): the picture copy is faster than the loop it replaced.

    python3 -m unittest tests.bench.test_core_io
"""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
import bench  # noqa: E402

AVAILABLE = bench.GUEST_RECEIPT.is_file()       # the Release host is built with the SHA-256 of the guest it belongs with; the sandbox itself is not entered
ITERATIONS = 300


@unittest.skipUnless(AVAILABLE, "no Waterbox guest (tools/build_waterbox.py --puae6)")
class CoreIoTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory(prefix="p6-core-io-")
        out = Path(cls.directory.name) / "iobench.json"
        bench.build_ffi_release()
        env = {**bench.dotnet_env(), "NUGET_PACKAGES": str(ROOT / "build/bizhawk-core/nuget")}
        result = subprocess.run(["dotnet", str(ROOT / "build/ffi/bin/Release/net8.0/Ffi.dll"), "iobench", "--out", str(out), "--iterations", str(ITERATIONS)], env=env, text=True,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=600)
        cls.stdout, cls.stderr = result.stdout, result.stderr
        cls.returncode = result.returncode
        cls.report = json.loads(out.read_text()) if out.is_file() else None

    @classmethod
    def tearDownClass(cls):
        cls.directory.cleanup()

    def test_the_host_ran_every_equivalence_check(self):
        self.assertEqual(self.returncode, 0, self.stderr)
        self.assertIsNotNone(self.report)
        lines = [line for line in self.stdout.splitlines() if line.startswith("ok ")]
        self.assertEqual(len(lines), len(self.report["checks"]) + 1, self.stdout)         # one line per check and the final one
        self.assertGreaterEqual(len(self.report["checks"]), 9)

    def test_the_names_the_picture_and_the_packet_are_what_the_old_forms_made(self):
        checks = " | ".join(self.report["checks"])
        for fragment in ("key buttons are", "joystick buttons are", "mouse buttons and axes", "XrgbToArgb sets the alpha", "756 x 574", "same packet"):
            self.assertIn(fragment, checks)

    def test_a_target_that_is_too_short_is_refused(self):
        self.assertIn("a target shorter than the source is refused", " | ".join(self.report["checks"]))

    def test_the_questions_asked_of_the_controller_no_longer_allocate(self):
        self.assertEqual(self.report["keys_new_bytes_per_call"], 0)
        self.assertGreater(self.report["keys_reference_bytes_per_call"], 1000)             # ~110 names a unit were built and thrown away

    def test_the_picture_copy_is_faster_in_the_release_host(self):
        self.assertEqual(self.report["managed_build"], "Release")
        reference = bench.describe(self.report["picture_reference_ns"])["p50"]
        new = bench.describe(self.report["picture_new_ns"])["p50"]
        self.assertLess(new, reference)


if __name__ == "__main__":
    unittest.main()
