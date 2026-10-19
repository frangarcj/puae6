"""Offline image failures block before builds; loopback remains container-local."""
import json
import subprocess
import unittest
from unittest import mock

from tools.ci import nightly, retroarch_image as image


class RetroArchImageTests(unittest.TestCase):
    def inspected(self, **changes):
        spec = image.specification()
        value = dict(Id=spec["id"], Os="linux", Architecture="arm64")
        value.update(changes)
        return subprocess.CompletedProcess([], 0, json.dumps([value]), "")

    def test_absence_changed_bytes_and_wrong_isa_never_authorize_a_build(self):
        for result in (subprocess.CompletedProcess([], 1, "", "absent"), self.inspected(Id="sha256:" + "0" * 64),
                       self.inspected(Architecture="amd64"), subprocess.CompletedProcess([], 0, "{}", "")):
            with self.subTest(result=result), mock.patch.object(image.subprocess, "run", return_value=result) as execute:
                with self.assertRaises(ValueError):
                    image.verify()
                self.assertEqual(1, execute.call_count)
                self.assertEqual(["docker", "image", "inspect", "puae6-libretro-test"], execute.call_args.args[0])

    def test_runs_use_pinned_id_without_pull_network_or_host_network_override(self):
        spec = image.specification()
        argv = image.run_arguments("retroarch --version", ("--user", "1:1"), spec)
        self.assertIn("--pull=never", argv)
        self.assertIn("--network=none", argv)
        self.assertEqual(argv[-4:], [spec["id"], "bash", "-c", "retroarch --version"])
        self.assertNotIn(spec["tag"], argv)

    def test_missing_frontend_blocks_functional_before_codegen_or_preparation(self):
        from tests.portability.rows import ROWS
        from tests.simd.matrix import IMAGES
        def inspect(argv, **kwargs):
            row = next(s for s in ROWS.values() if s.get("image") == argv[-1])
            return subprocess.CompletedProcess([], 0, json.dumps([dict(Id=IMAGES[row["platform"]], Architecture=row["platform"].split("/")[1])]), "")
        with mock.patch.object(nightly.ci, "preflight"), mock.patch.object(nightly.ci, "command", side_effect=inspect), \
             mock.patch.object(image, "verify", side_effect=ValueError("required offline RetroArch image absent")), \
             mock.patch.object(nightly, "execute") as execute, mock.patch.object(nightly, "prepare") as prepare:
            result = nightly.run("functional")
        self.assertEqual("BLOCKED", result["status"])
        execute.assert_not_called()
        prepare.assert_not_called()

    def test_image_identity_is_recorded_and_rechecked_after_each_suite(self):
        before = {"puae6-libretro-test": image.specification()["id"]}
        good = dict(tests=1, failures=0, errors=0, skipped=0, expected_failures=0, unexpected_successes=0)
        with mock.patch.object(nightly, "suites", return_value=["fixture"]), mock.patch.object(nightly.ci, "preflight"), \
             mock.patch.object(nightly, "execute"), mock.patch.object(nightly, "prepare", return_value={}), \
             mock.patch.object(nightly.ci, "identity", return_value={}), \
             mock.patch.object(nightly.ci, "command", return_value=subprocess.CompletedProcess([], 0, json.dumps(good), "")), \
             mock.patch.object(nightly, "images", side_effect=[before, {"puae6-libretro-test": "changed"}]):
            result = nightly.run("functional")
        self.assertEqual("FAIL", result["status"])
        self.assertEqual(before, result["identity"]["images"])
        self.assertIn("Docker inputs changed", result["diagnostic"])
