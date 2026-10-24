"""Own non-executable byte fixtures; only the opt-in class launches real EmuHawk."""
import base64
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
import zipfile
from unittest import mock
from tools import emuhawk_selfcheck as s


class BootstrapTests(unittest.TestCase):
    def setUp(self):
        (s.ROOT / "build").mkdir(exist_ok=True)
        temporary = tempfile.TemporaryDirectory(prefix="emuhawk-fixture-", dir=s.ROOT / "build")
        self.addCleanup(temporary.cleanup); self.root = Path(temporary.name)
        (self.root / "build").mkdir(); self.tree = self.root / "build/key-emuhawk"
        self.output = self.tree / "output"; (self.output / "dll").mkdir(parents=True)
        for name in ("EmuHawk.exe", "EmuHawk.exe.config", "dll/BizHawk.Emulation.Cores.dll", "dll/Virtu.dll", "dll/Other.dll", "dll/Newtonsoft.Json.dll"):
            (self.output / name).write_bytes(b"own data, never executed: " + name.encode())
        self.dotnet = self.root / "own-dotnet"; self.dotnet.write_bytes(b"own host fixture, never executed")
        self.files = s.builder.output_closure(self.output)
        self.receipt = self.output / "bootstrap-receipt.json"
        self.receipt.write_text(json.dumps({"tree_key": "key", "commit": "a" * 40, "files": self.files}))
        for patch in (mock.patch.object(s, "ROOT", self.root), mock.patch.object(s.builder, "TREE_EMUHAWK", self.tree),
                      mock.patch.object(s.builder, "tree_key", return_value="key"), mock.patch.object(s.builder, "pinned_commit", return_value="a" * 40),
                      mock.patch.object(s.builder, "check_source"), mock.patch.object(s.shutil, "which", return_value=str(self.dotnet))):
            patch.start(); self.addCleanup(patch.stop)
        self.asset_patch = mock.patch.object(s, "runtime_json_asset", return_value=b"own modern JSON asset")
        self.asset = self.asset_patch.start()
        self.process = mock.patch.object(s.subprocess, "run", side_effect=self.execute).start()
        self.addCleanup(mock.patch.stopall); self.stages = []

    def record(self, stage):
        return {"schema_version": 1, "kind": "emuhawk-cli-bootstrap", "runtime": "10.0.13",
            "entry_name": "BizHawk.Client.EmuHawk", "entry_type": "BizHawk.Client.EmuHawk.Program",
            "entry_path": str(stage / "EmuHawk.exe"), "entry_sha256": self.files["EmuHawk.exe"],
            "cores_path": str(stage / "dll/BizHawk.Emulation.Cores.dll"), "cores_sha256": self.files["dll/BizHawk.Emulation.Cores.dll"],
            "framework": s.FRAMEWORK, "cores_framework": s.FRAMEWORK, "types_count": 2,
            "types_sha256": "b" * 64, "amiga": s.AMIGA}

    def execute(self, command, **kwargs):
        stage = Path(kwargs["cwd"]); self.stages.append(stage)
        self.assertEqual(command, [str(self.dotnet), "exec", "--runtimeconfig", str(stage / "EmuHawk.runtimeconfig.json"),
                                  str(stage / "EmuHawk.exe"), "--puae6-self-check"])
        self.assertEqual(json.loads((stage / "EmuHawk.runtimeconfig.json").read_text())["runtimeOptions"]["rollForward"], "Disable")
        self.assertEqual((stage / "dll/Other.dll").read_bytes(), (self.output / "dll/Other.dll").read_bytes())
        self.assertNotIn("DOTNET_STARTUP_HOOKS", kwargs["env"])
        self.assertEqual((stage / "dll/Newtonsoft.Json.dll").read_bytes(), b"own modern JSON asset")
        return subprocess.CompletedProcess(command, 0, s.MARKER + json.dumps(self.record(stage)) + "\n", "")

    def test_actual_exe_contract_copies_complete_closure_identifies_bytes_and_cleans_staging(self):
        with mock.patch.dict(os.environ, {"DOTNET_STARTUP_HOOKS": "/foreign"}): result = s.run()
        self.assertEqual(result["status"], "PASS"); self.assertEqual(result["scope"], "bootstrap-only")
        self.assertEqual(result["files"], self.files)
        self.assertEqual(result["dotnet_sha256"], hashlib.sha256(self.dotnet.read_bytes()).hexdigest())
        self.assertFalse(self.stages[0].exists()); self.assertEqual(s.builder.output_closure(self.output), self.files)

    def test_receipt_changed_binary_and_symlink_fail_before_process(self):
        for mutate, restore in ((lambda: self.receipt.write_text("{}"), lambda: self.receipt.write_text(json.dumps({"tree_key":"key","commit":"a"*40,"files":self.files}))),
                                (lambda: (self.output / "EmuHawk.exe").write_bytes(b"foreign host"), lambda: (self.output / "EmuHawk.exe").write_bytes(b"own data, never executed: EmuHawk.exe"))):
            mutate()
            with self.assertRaises(ValueError): s.run()
            restore()
        (self.output / "dll/Other.dll").unlink(); (self.output / "dll/Other.dll").symlink_to(self.dotnet)
        with self.assertRaises(s.builder.RecipeError): s.run()
        self.process.assert_not_called()

    def test_wrong_entry_framework_runtime_or_partial_inventory_cannot_pass(self):
        stage = self.root / "stage"; valid = self.record(stage)
        for key, value in (("entry_type", "OtherHost.Program"), ("entry_path", "/foreign/EmuHawk.exe"), ("runtime", "8.0.0"),
                           ("framework", "net10.0"), ("types_count", 0), ("types_count", True), ("amiga", s.AMIGA[:1]), ("types_sha256", "bad")):
            with self.subTest(key=key), self.assertRaises(ValueError): s.validate({**valid, key:value}, stage, self.files)

    def test_failed_process_duplicate_result_and_source_drift_clean_staging(self):
        for mode in ("failed", "duplicate", "drift"):
            def execute(command, **kwargs):
                result = self.execute(command, **kwargs)
                if mode == "failed": result.returncode = 2; result.stderr = "ReflectionTypeLoadException: Virtu"
                if mode == "duplicate": result.stdout *= 2
                if mode == "drift": (Path(kwargs["cwd"]) / "dll/Other.dll").write_bytes(b"changed")
                return result
            self.process.side_effect = execute
            with self.subTest(mode=mode), self.assertRaises(ValueError): s.run()
            self.assertFalse(self.stages[-1].exists())

    def test_missing_virtu_is_only_an_explicit_negative_never_partial_success(self):
        def reject(command, **kwargs):
            stage = Path(kwargs["cwd"]); self.stages.append(stage)
            self.assertFalse((stage / "dll/Virtu.dll").exists())
            return subprocess.CompletedProcess(command, 2, "", "ReflectionTypeLoadException LOADER missing Virtu")
        self.process.side_effect = reject
        self.assertEqual(s.run(without_virtu=True)["status"], "EXPECTED_REJECTION")
        self.assertFalse(self.stages[-1].exists()); self.assertTrue((self.output / "dll/Virtu.dll").exists())

    def test_runtime_asset_authenticates_archive_and_does_not_install_or_modify_inputs(self):
        self.asset_patch.stop()
        data = io.BytesIO()
        with zipfile.ZipFile(data, "w") as package:
            package.writestr("lib/net6.0/Newtonsoft.Json.dll", b"own modern JSON asset")
        raw = data.getvalue(); archive = self.root / "build/hosted-managed-feed/newtonsoft.json.13.0.3.nupkg"
        archive.parent.mkdir(); archive.write_bytes(raw)
        manifest = self.root / "tools/ci/managed-inputs.json"; manifest.parent.mkdir(parents=True)
        manifest.write_text(json.dumps({"packages": [{"id": "newtonsoft.json", "version": "13.0.3",
            "bytes": len(raw), "archive_sha512_base64": base64.b64encode(hashlib.sha512(raw).digest()).decode()}]}))
        self.assertEqual(s.runtime_json_asset(), b"own modern JSON asset")
        self.assertEqual(archive.read_bytes(), raw)
        archive.write_bytes(raw + b"tamper")
        with self.assertRaisesRegex(ValueError, "SHA512"): s.runtime_json_asset()
        self.process.assert_not_called()

    def test_skip_chd_is_explicit_and_default_still_builds_provider(self):
        self.process.side_effect = None; self.process.return_value = subprocess.CompletedProcess([], 0)
        with mock.patch.object(s.builder, "build_chd_host") as provider:
            s.builder.build("a"*64, self.tree, "own.csproj", "output/EmuHawk.exe", chd_host=False)
            provider.assert_not_called()
            s.builder.build("a"*64, self.tree, "own.csproj", "output/EmuHawk.exe")
            provider.assert_called_once_with(self.output)

    def test_normal_core_export_contains_every_existing_target_of_the_patch_queue(self):
        for patch in s.builder.PATCHES:
            for line in patch.read_text().splitlines():
                if line.startswith("--- a/"):
                    target = line[6:]
                    self.assertTrue(any(target == p or target.startswith(p + "/") for p in s.builder.EXPORT), target)


@unittest.skipUnless(os.environ.get("P6_TEST_EMUHAWK_SELFCHECK") == "1", "requires the actual current complete EmuHawk EXE")
class RealBootstrapTests(unittest.TestCase):
    def test_real_exe_loads_all_core_types_and_rejects_missing_virtu(self):
        result = s.run(); self.assertEqual(result["status"], "PASS")
        print(json.dumps(result, sort_keys=True))
        self.assertEqual(s.run(without_virtu=True)["status"], "EXPECTED_REJECTION")
