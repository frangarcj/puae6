"""T105f: PUAE6/PUAE61 expose the same validated configuration without sharing product identity."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest


ROOT = Path(__file__).resolve().parents[2]
PUAE61 = Path(os.environ.get(
    "P6_PUAE61_ROOT",
    ROOT.parent / "uae-beta21" / "build" / "update-beta21" / "integrate-main",
)).resolve()
LONGHAWK = PUAE61 / "build/longhawk-puae61"
PARITY_OPTIONS = (
    "chipset_compatible", "chipset", "chip_size", "fast_size", "z3_memory", "motherboard_fast_ram",
)
LEGACY_PRESETS = ("a600_puae5", "a1200_fast8", "cd32_fast8")


def require_tree():
    if not (PUAE61 / "config/schema/options.json").is_file():
        raise AssertionError(f"falta el worktree PUAE61 de T105f: {PUAE61}")


def load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def options(schema):
    return {option["id"]: option for option in schema["options"]}


def presets(schema):
    return {preset["id"]: preset for preset in schema["presets"]}


class CrossVersionBuildParityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        require_tree()
        cls.puae6 = load(ROOT / "config/schema/options.json")
        cls.puae61 = load(PUAE61 / "config/schema/options.json")

    def test_validated_surface_and_legacy_presets_are_identical(self):
        left, right = options(self.puae6), options(self.puae61)
        self.assertEqual({name: left[name] for name in PARITY_OPTIONS},
                         {name: right[name] for name in PARITY_OPTIONS})
        left_presets, right_presets = presets(self.puae6), presets(self.puae61)
        self.assertEqual({name: left_presets[name] for name in LEGACY_PRESETS},
                         {name: right_presets[name] for name in LEGACY_PRESETS})
        self.assertEqual(
            ["Auto", "KB512", "MB1", "MB1_5", "MB2", "MB2_5", "MB3", "MB3_5",
             "MB4", "MB4_5", "MB5", "MB5_5", "MB6", "MB6_5", "MB7", "MB7_5", "MB8"],
            [value["value"] for value in left["chip_size"]["values"]],
        )

    def test_longhawk_shares_settings_and_profiles_but_not_guest_or_movie_identity(self):
        core_dir = LONGHAWK / "src/BizHawk.Emulation.Cores/Computers/Amiga/PUAE6"
        settings = (core_dir / "Settings.cs").read_text(encoding="utf-8")
        profiles = (core_dir / "Profiles.cs").read_text(encoding="utf-8")
        emuhawk = (core_dir / "Puae6Core.EmuHawk.cs").read_text(encoding="utf-8")
        movie = (core_dir / "Movie.cs").read_text(encoding="utf-8")
        self.assertIn("public sealed class Puae61Core : Puae6Core", emuhawk)
        self.assertIn("Puae6SyncSettings", emuhawk)
        self.assertFalse(any(core_dir.glob("*Puae61*Settings*.cs")))
        for spelling in ("Puae6ChipsetCompatible", "Puae6MotherboardFastRam", "Puae6FastRam", "MB7_5", "A1200Fast8"):
            self.assertIn(spelling, settings + profiles)
        self.assertIn('["Core"] = coreName', movie)
        self.assertIn("recorded != coreName", movie)

        guest6 = LONGHAWK / "waterbox/puae6/puae6-x64.wbx"
        guest61 = LONGHAWK / "waterbox/puae61/puae61-x64.wbx"
        self.assertNotEqual(digest(guest6), digest(guest61))
        smoke6 = (LONGHAWK / "waterbox/puae6/windows-smoke.py").read_text(encoding="utf-8")
        pin6_x64 = next(line.split("'")[1] for line in smoke6.splitlines() if line.startswith("GUEST_SHA = "))
        self.assertEqual(pin6_x64, digest(guest6))
        props6 = (LONGHAWK / "waterbox/puae6/Guest.props").read_text(encoding="utf-8")
        props61 = (LONGHAWK / "waterbox/puae61/Guest.props").read_text(encoding="utf-8")
        pin6 = next(word for word in props6.replace("<", " ").replace(">", " ").split() if len(word) == 64)
        pin61 = next(word for word in props61.replace("<", " ").replace(">", " ").split() if len(word) == 64)
        self.assertNotEqual(pin6, pin61)
        self.assertIn(digest(guest61), props61)

    def test_engines_remain_distinct_pins(self):
        def winuae(root):
            lock = load(root / "plan/upstream.lock.json")
            return next(source["commit"] for source in lock["sources"] if source["id"] == "winuae")
        self.assertNotEqual(winuae(ROOT), winuae(PUAE61))


class CrossVersionStateParityTests(unittest.TestCase):
    def test_beta21_native_identity_and_restore_matrix(self):
        require_tree()
        cases = (
            "tests.state.test_state_fast.LayoutTests.test_extended_chip_ram_keeps_its_exact_state_bytes_and_identity",
            "tests.state.test_state_chipset.ChipsetStateTests",
            "tests.state.test_motherboard_fast.MotherboardFastTests",
        )
        result = subprocess.run(
            [sys.executable, "-m", "unittest", *cases], cwd=PUAE61,
            env={**os.environ, "P6_STATE_PROBE_BUILD": "optimized"},
            capture_output=True, text=True, timeout=360,
        )
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)


class CrossVersionWaterboxParityTests(unittest.TestCase):
    def test_beta21_wrapper_domains_heaps_and_cold_restore(self):
        require_tree()
        commands = (
            [sys.executable, "-m", "unittest", "discover", "-s", "tests/waterbox-state", "-p", "test_wrapper_memory.py"],
            [sys.executable, "-m", "unittest", "discover", "-s", "tests/waterbox-state", "-p", "test_wrapper_motherboard_fast.py"],
        )
        for command in commands:
            result = subprocess.run(command, cwd=PUAE61, capture_output=True, text=True, timeout=360)
            self.assertEqual(0, result.returncode, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
