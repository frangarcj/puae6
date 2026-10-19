"""T74b: live presentation gain, with continuous mechanical phase and native restore."""
import importlib
from pathlib import Path
import re

lr = importlib.import_module("tests.libretro-host.test_libretro")
from tests.audio.test_drive_sound import MECHANICAL


class DriveVolumeTests(lr.LibretroHarness):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        rom = Path(cls.tmp.name) / "mechanical.rom"
        rom.write_bytes(lr.builder.build(MECHANICAL))
        cls.roms["mechanical"] = rom

    def test_live_mute_preserves_hardware_phase_and_state_replay(self):
        args = ("--runs", 14, "--set", "puae6_drive_sound_volume=100", "--do", "14:save:end")
        baseline = self.frontend("mechanical", *args).stdout
        changed = self.frontend("mechanical", *args, "--set-at", "6:puae6_drive_sound_volume=0", "--set-at", "10:puae6_drive_sound_volume=100").stdout
        a, b = self.runs(baseline), self.runs(changed)
        hardware = ("hash", "ticks", "unit", "audio")
        self.assertEqual([tuple(u[k] for k in hardware) for u in a], [tuple(u[k] for k in hardware) for u in b])
        self.assertTrue(any(u["ahash"] != v["ahash"] for u, v in zip(a[5:9], b[5:9])), "volume changes audible PCM")
        self.assertEqual([u["ahash"] for u in a[9:]], [u["ahash"] for u in b[9:]], "unmuting retains phase")
        state_hash = lambda text: re.search(r"state save end ok=1 .* hash=(\w+)", text)[1]
        self.assertEqual(state_hash(baseline), state_hash(changed), "presentation is outside native state")
        replay = self.frontend("mechanical", "--runs", 23, "--set", "puae6_drive_sound_volume=100", "--do", "5:save:s", "--do", "14:load:s").stdout
        units = self.runs(replay)
        self.assertEqual([u["ahash"] for u in units[5:14]], [u["ahash"] for u in units[14:]])
        self.assertEqual([u["hash"] for u in units[5:14]], [u["hash"] for u in units[14:]])
