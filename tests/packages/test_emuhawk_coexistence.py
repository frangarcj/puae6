"""Own data fixtures; actual EXE/guest execution requires the explicit opt-in."""
import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import struct
import tempfile
import unittest
from unittest import mock
from tools import emuhawk_coexistence as c
from tests.packages import test_emuhawk_selfcheck as bootstrap


class CoexistenceTests(unittest.TestCase):
    @staticmethod
    def native():
        return {name: {"path": "/own/" + name, "sha256": "a" * 64} for name in ("libwaterboxhost.dylib", "puae6.wbx", "uae.wbx")}

    def record(self):
        return {"runtime": "10.0.13", "same_process": True, "native": {n: v["sha256"] for n, v in self.native().items()},
            "cases": [{"format": f, "core": core, "frames": 12, "replayed": 12, "isolation": True,
                "video_sha256": "b" * 64, "audio_sha256": "c" * 64, "state_sha256": "d" * 64} for f, core in sorted(c.CASES)]}

    def test_exact_four_cases_counts_runtime_and_native_identity(self):
        c.validate(self.record(), self.native())
        mutations = [("runtime", "8.0.0"), ("same_process", False), ("native", {}), ("cases", self.record()["cases"][:3])]
        for key, value in mutations:
            with self.subTest(key=key), self.assertRaises(ValueError): c.validate({**self.record(), key: value}, self.native())
        for key, value in (("frames", True), ("replayed", 11), ("isolation", False), ("state_sha256", "bad"), ("core", "Other")):
            record = self.record(); record["cases"][0][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError): c.validate(record, self.native())
        record = self.record(); record["cases"][1] = copy.deepcopy(record["cases"][0])
        with self.assertRaises(ValueError): c.validate(record, self.native())

    def test_prepare_own_micro_rom_database_and_complete_native_copy(self):
        with tempfile.TemporaryDirectory(dir=c.ROOT / "build", prefix="coexist-fixture-") as tmp:
            root = Path(tmp); stage = root / "stage"; (stage / "dll").mkdir(parents=True)
            native = self.native()
            for name, item in native.items():
                path = root / name; path.write_bytes(b"own non-executable fixture " + name.encode())
                item.update(path=str(path), sha256=c.sha(path))
            with mock.patch.object(c, "inputs", return_value=native): self.assertEqual(c.prepare(stage), native)
            for name, item in native.items(): self.assertEqual(c.sha(stage / "dll" / name), item["sha256"])
            for name in ("Firmware/Own.rom", "Own.adf"):
                self.assertIn(hashlib.sha1((stage / name).read_bytes()).hexdigest().upper(), (stage / "gamedb/gamedb.txt").read_text())
            self.assertEqual((stage / "gamedb/mame_machines.txt").read_bytes(), b"")
            link = root / "linked"; link.symlink_to(root / "uae.wbx")
            with self.assertRaises(ValueError): c.sha(link)

    def test_inputs_require_both_derived_guests_and_track_authenticated_receipts(self):
        from tools import build_legacy_guest as legacy, build_waterbox as wb
        with tempfile.TemporaryDirectory(dir=c.ROOT / "build", prefix="coexist-inputs-") as tmp:
            root = Path(tmp); (root / "build/waterbox/puae6/release").mkdir(parents=True)
            (root / "build/legacy-guest").mkdir()
            source = root / "own.cpp"; source.write_bytes(b"own source fixture")
            paths = {"libwaterboxhost.dylib": root / "build/own-runtime.dylib",
                     "puae6.wbx": root / "build/waterbox/puae6/release/puae6.wbx",
                     "uae.wbx": root / "build/legacy-guest/uae.wbx"}
            for name, path in paths.items():
                data = bytearray(64)
                if name.endswith("dylib"): struct.pack_into("<II", data, 0, 0xfeedfacf, 0x100000c)
                else: data[:7] = b"\x7fELF\x02\x01\x01"; struct.pack_into("<H", data, 18, 183)
                path.write_bytes(data + name.encode())
            runtime_path = root / "build/runtime-receipt.json"
            guest_path = root / "build/waterbox/puae6/puae6-receipt.json"
            legacy_path = root / "build/legacy-guest/receipt.json"
            runtime = {"runtime_profile": "release", "runtime_dylib": {"path": str(paths["libwaterboxhost.dylib"]), "sha256": c.sha(paths["libwaterboxhost.dylib"])}}
            guest = {"manual_inputs": {"own": "pin"}, "sources": {"own.cpp": c.sha(source)},
                     "guest": {"sha256": c.sha(paths["puae6.wbx"])}, "guest_abi": {"variant": "own-derived"}}
            reference = {"guest": {"sha256": c.sha(paths["uae.wbx"])}}
            runtime_path.write_text(json.dumps(runtime)); legacy_path.write_text(json.dumps(reference))
            def write(): guest_path.write_text(json.dumps(guest))
            def abi(receipt):
                if receipt.get("guest_abi") != {"variant": "own-derived"}: raise ValueError("Guest ABI missing/stale")
            write()
            with mock.patch.object(c, "ROOT", root), mock.patch.object(wb, "runtime_receipt_path", return_value=runtime_path), \
                    mock.patch.object(wb, "read_runtime_receipt", return_value=runtime), mock.patch.object(wb, "puae6_manual_inputs", return_value={"own": "pin"}), \
                    mock.patch.object(wb, "check_puae6_guest_abi", side_effect=abi, create=True) as check, mock.patch.object(legacy, "verify", return_value=reference) as verify:
                before = c.inputs(); check.assert_called_once_with(guest); verify.assert_called_once_with(root / "build/legacy-guest")
                self.assertEqual(before["uae.wbx"]["path"], str(paths["uae.wbx"]))
                guest["provenance"] = "changed receipt only"; write(); after = c.inputs()
                self.assertNotEqual(before, after); self.assertEqual(before["puae6.wbx"]["sha256"], after["puae6.wbx"]["sha256"])
                for value in (None, {"variant": "base"}):
                    guest["guest_abi"] = value; write()
                    with self.assertRaisesRegex(ValueError, "ABI missing/stale"): c.inputs()
                guest["guest_abi"] = {"variant": "own-derived"}; guest["manual_inputs"] = {}; write()
                with self.assertRaisesRegex(ValueError, "sources required"): c.inputs()
                guest["manual_inputs"] = {"own": "pin"}; write(); verify.side_effect = ValueError("Legacy base/stale receipt")
                with self.assertRaisesRegex(ValueError, "Legacy base/stale"): c.inputs()
                verify.side_effect = None; reference["guest"]["sha256"] = "f" * 64
                with self.assertRaisesRegex(ValueError, "artifact mismatch"): c.inputs()

    def test_staging_context_failure_duplicate_and_input_drift_never_accept_partial_result(self):
        fixture = bootstrap.BootstrapTests(); fixture.setUp(); self.addCleanup(fixture.doCleanups)
        def execute(command, **kwargs):
            stage = Path(kwargs["cwd"]); fixture.stages.append(stage)
            self.assertEqual(command[-1], "--puae6-coexistence")
            for name in ("TMPDIR", "TMP", "TEMP"): self.assertEqual(Path(kwargs["env"][name]), stage / "tmp")
            self.assertEqual(kwargs["env"]["HOME"], str(stage))
            return subprocess.CompletedProcess(command, 0, c.s.MARKER + json.dumps(fixture.record(stage)) + "\n" + c.MARKER + json.dumps(self.record()) + "\n", "")
        with mock.patch.object(c, "prepare", return_value=self.native()), mock.patch.object(c, "inputs", return_value=self.native()):
            fixture.process.side_effect = execute
            self.assertEqual(c.s.run(coexistence=True)["scope"], "same-process-coexistence")
            for mode in ("failed", "duplicate", "drift", "receipts"):
                def invalid(command, **kwargs):
                    result = execute(command, **kwargs)
                    if mode == "failed": result.returncode = 2
                    if mode == "duplicate": result.stdout += c.MARKER + json.dumps(self.record()) + "\n"
                    if mode == "drift": (Path(kwargs["cwd"]) / "dll/Other.dll").write_bytes(b"changed")
                    if mode == "receipts": c.inputs.return_value = {**self.native(), "uae.wbx": {**self.native()["uae.wbx"], "receipt_sha256": "changed"}}
                    return result
                fixture.process.side_effect = invalid
                with self.subTest(mode=mode), self.assertRaises(ValueError): c.s.run(coexistence=True)
            self.assertTrue(all(not stage.exists() for stage in fixture.stages))


@unittest.skipUnless(os.environ.get("P6_TEST_EMUHAWK_COEXISTENCE") == "1", "requires the actual current complete EmuHawk EXE, runtime and both guests")
class RealCoexistenceTests(unittest.TestCase):
    def test_real_exe_records_and_replays_both_cores_in_one_process(self):
        result = c.s.run(coexistence=True); self.assertEqual(result["status"], "PASS")
        print(json.dumps(result, sort_keys=True))
