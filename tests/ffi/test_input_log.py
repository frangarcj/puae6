"""T70: the real BizHawk BK2 codec preserves every CD32 control; no guest/runtime is needed.

The unpinned managed build has separate bin/obj paths, so this test cannot replace
the pinned FFI assembly used by Waterbox tests in the same checkout.
"""
import os
from pathlib import Path
import shutil
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[2]


class InputLogTests(unittest.TestCase):
    def codec(self, command):
        dotnet = shutil.which("dotnet")
        cache = Path.home() / ".nuget/packages"
        if not dotnet or not cache.is_dir():
            self.skipTest("dotnet and the existing local NuGet package cache are required")
        env = {**os.environ, "DOTNET_CLI_HOME": str(ROOT / "build/waterbox/dotnet-home"), "DOTNET_ROLL_FORWARD": "Major",
               "NUGET_PACKAGES": str(ROOT / "build/bizhawk-core/nuget"), "DOTNET_CLI_TELEMETRY_OPTOUT": "1", "DOTNET_NOLOGO": "1"}
        built = subprocess.run([dotnet, "build", str(ROOT / "tests/ffi/Ffi.csproj"), "--nologo", "-m:1",
                                f"-p:RestoreSources={cache}", "-p:P6ManagedTestSuffix=-input-log"],
                               cwd=ROOT, env=env, text=True, capture_output=True, timeout=180)
        self.assertEqual(0, built.returncode, built.stdout[-4000:] + built.stderr[-4000:])
        out = subprocess.run([dotnet, str(ROOT / "build/ffi-input-log/bin/Debug/net8.0/Ffi.dll"), command],
                             cwd=ROOT, env=env, text=True, capture_output=True, timeout=30)
        self.assertEqual(0, out.returncode, out.stderr)
        return out.stdout.splitlines()

    def test_cd32_controls_round_trip_through_bizhawks_actual_bk2_codec(self):
        lines = self.codec("cd32log")
        expected = {f"ok {check}: P{p} CD32 pad {button}" for p in (1, 2)
                    for button in ("Up", "Down", "Left", "Right", "Red", "Blue", "Green", "Yellow", "Rewind", "Forward", "Play")
                    for check in ("CD32 has a known mnemonic", "CD32 BK2 round trip")}
        self.assertEqual(44, len(lines))
        self.assertEqual(expected, set(lines))

    def test_two_mice_buttons_and_absolute_positions_round_trip_through_the_actual_bk2_codec(self):
        lines = self.codec("mouse2log")
        expected = {f"ok mouse {check}: P{p} Mouse {button} Button" for p in (1, 2)
                    for button in ("Left", "Middle", "Right") for check in ("has a known mnemonic", "BK2 round trip")}
        expected |= {f"ok mouse BK2 axis round trip: P{p} Mouse {axis}" for p in (1, 2) for axis in ("X", "Y")}
        self.assertEqual(16, len(lines))
        self.assertEqual(expected, set(lines))

    def test_optical_coordinates_trigger_and_offscreen_round_trip_through_the_actual_bk2_codec(self):
        lines = self.codec("pointerlog")
        expected = {f"ok optical {device}:{port} {check} P{port + 1} Lightgun {button}" for device in (4, 5) for port in (0, 1)
                    for check in ("mnemonic", "button") for button in ("Trigger", "Offscreen Shot")}
        expected |= {f"ok optical {device}:{port} axis P{port + 1} Lightgun {axis}" for device in (4, 5) for port in (0, 1) for axis in ("X", "Y")}
        self.assertEqual(24, len(lines)); self.assertEqual(expected, set(lines))

    def test_parallel_joysticks_round_trip_through_the_actual_bk2_codec(self):
        lines = self.codec("parallellog")
        expected = {f"ok parallel {check} P{p} Joystick {button}" for p in (3, 4) for button in ("Up", "Down", "Left", "Right", "Button 1") for check in ("mnemonic", "round trip")}
        expected.add("ok parallel ABI v1/v2 sizes")
        self.assertEqual(21, len(lines)); self.assertEqual(expected, set(lines))


if __name__ == "__main__":
    unittest.main()
