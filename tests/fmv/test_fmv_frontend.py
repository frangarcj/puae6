"""FMV-F1: public schema, frontend firmware transport and non-freezer controls."""
import hashlib
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest

from tests.build_support import ROOT, build_targets

sys.path.insert(0, str(ROOT / "tests/libretro-host"))
import test_libretro as lr  # noqa: E402


class FmvFrontendContractTests(unittest.TestCase):
    def test_schema_libretro_and_bizhawk_share_one_non_freezer_fmv_value(self):
        schema = __import__("json").loads((ROOT / "config/schema/options.json").read_text())
        cartridge = next(option for option in schema["options"] if option["id"] == "cartridge")
        fmv = next(value for value in cartridge["libretro"]["extra_values"] if value["value"] == "fmv")
        self.assertEqual(fmv, {"value": "fmv", "abi": 7, "label": "CD32 Full Motion Video module", "const": "Fmv", "wrapper_name": "Fmv"})
        firmware = (ROOT / "adapters/libretro/firmware.h").read_text()
        mapper = (ROOT / "adapters/libretro/mapper.h").read_text()
        wrapper = (ROOT / "integration/bizhawk/core/Puae6/Puae6Core.cs").read_text()
        emuhawk = (ROOT / "integration/bizhawk/core/EmuHawk/Puae6Core.EmuHawk.cs").read_text()
        self.assertIn("kCartridgeFmv, P6_CARTRIDGE_CD32_FMV, 262144, 524288, false", firmware)
        self.assertIn("cartridge && cartridge->freeze", mapper)
        self.assertIn("Puae6Cartridge.Fmv => 32", wrapper)
        self.assertIn("IsFreezerCartridge(_syncSettings.Cartridge)", wrapper)
        self.assertIn('new(VSystemID.Raw.Amiga, "CD32FMV")', emuhawk)


class FmvLibretroTests(lr.LibretroHarness):
    @classmethod
    def setUpClass(cls):
        downloads = Path.home() / "Downloads"
        main = Path(os.environ.get("P6_CD32_ROM_MAIN", downloads / "kick40060.CD32"))
        ext = Path(os.environ.get("P6_CD32_ROM_EXT", downloads / "kick40060.CD32.ext"))
        fmv = Path(os.environ.get("P6_CD32_FMV_ROM", downloads / "FMV.CD32-2.rom"))
        if not all(path.is_file() for path in (main, ext, fmv)):
            raise unittest.SkipTest("BLOCKED: authorized CD32 main/ext and P6_CD32_FMV_ROM required")
        raw = fmv.read_bytes()
        if len(raw) not in (262144, 524288):
            raise AssertionError(f"FMV ROM has unsupported container size: {fmv}")
        canonical = raw[:262144]
        if hashlib.sha1(canonical).hexdigest() not in (
            "03ca81c7a7b259cf64bc9582863eca0f6529f435",
            "94e469a76030dcb299ebc7520aaeef9db54284cf",
        ):
            raise AssertionError(f"wrong authorized FMV cartridge: {fmv}")
        if len(raw) == 524288 and raw[262144:] != canonical:
            raise AssertionError(f"FMV container halves differ: {fmv}")
        built = build_targets(ROOT / "build/state-opt", ["p6_libretro_host", "puae6_libretro"], optimized=True)
        cls.host, cls.core = built["p6_libretro_host"], built["puae6_libretro"]
        cls.tmp = tempfile.TemporaryDirectory(prefix="fmv-frontend-", dir=ROOT / "build")
        cls.counter = 0
        cls.main, cls.ext, cls.fmv = main, ext, fmv
        cls.canonical = canonical
        cls.cannon = Path(os.environ.get(
            "P6_CANNON_FODDER_CUE",
            downloads / "Cannon Fodder (Europe)" / "Cannon Fodder (Europe).cue",
        ))
        cls.iso = Path(cls.tmp.name) / "public.iso"
        cls.iso.write_bytes(bytes(512 * 2048))

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def system(self, name="cd32-fmv.rom", contents=None):
        directory = self.work()
        (directory / "kick40060.CD32").symlink_to(self.main.resolve())
        (directory / "kick40060.CD32.ext").symlink_to(self.ext.resolve())
        target = directory / name
        if contents is None:
            target.symlink_to(self.fmv.resolve())
        else:
            target.write_bytes(contents)
        return directory

    def run_fmv(self, system, *options, content=True, expect=0):
        result = self.frontend("boot", "--runs", 2, "--set", "puae6_cartridge=fmv", *options,
                               content=self.iso if content else None, system=system, expect=expect)
        self.assertIn("vfs closed live=0", result.stdout)
        return result.stdout

    def test_raw_and_doubled_known_roms_reach_model32_only_with_cd32_content(self):
        doubled = self.run_fmv(self.system())
        self.assertIn("machine CD32 (puae6_machine)", doubled)
        self.assertIn("cartridge cd32-fmv.rom copied through VFS", doubled)
        raw = self.run_fmv(self.system("amiga-cd32-fmv-4030.rom", self.canonical))
        self.assertIn("cartridge amiga-cd32-fmv-4030.rom copied through VFS", raw)

        missing = self.system(); (missing / "cd32-fmv.rom").unlink()
        self.assertIn("missing cartridge ROM cd32-fmv.rom", self.run_fmv(missing, expect=3))
        invalid = bytearray(self.canonical + self.canonical); invalid[-1] ^= 1
        refused = self.run_fmv(self.system(contents=invalid), expect=3)
        self.assertIn("512 KiB FMV container halves differ", refused)
        no_cd = self.run_fmv(self.system(), "--set", "puae6_machine=A500", content=False, expect=3)
        self.assertIn("CD32 FMV requires CD32 machine with direct CD content", no_cd)

    @staticmethod
    def view(run):
        return tuple(run[key] for key in (
            "size", "pitch", "dupe", "hash", "px", "audio", "ahash", "polls", "states", "unit", "ticks"
        ))

    def real(self, *options, runs, fmv=True):
        if not self.cannon.is_file():
            self.skipTest("BLOCKED: authorized P6_CANNON_FODDER_CUE required")
        selected = ("--set", "puae6_cartridge=fmv") if fmv else ()
        return self.frontend("boot", "--runs", runs, *selected, *options,
                             content=self.cannon, system=self.system()).stdout

    def p6fm(self, path):
        data = path.read_bytes()
        self.assertLess(len(data), 18 * 1024 * 1024)
        self.assertEqual(data[:8], b"P6LRSTAT")
        adapter = struct.unpack_from("<I", data, 24)[0]
        native = 64 + adapter
        self.assertEqual(data[native:native + 8], b"P6NATIVE")
        header = struct.unpack_from("<I", data, native + 12)[0]
        payload = struct.unpack_from("<Q", data, native + 40)[0]
        chunks = struct.unpack_from("<I", data, native + 48)[0]
        at = native + header
        found = None
        for _ in range(chunks):
            tag = data[at:at + 4]
            version = struct.unpack_from("<I", data, at + 4)[0]
            length = struct.unpack_from("<Q", data, at + 8)[0]
            self.assertLessEqual(at + 16 + length, native + header + payload)
            if tag == b"P6FM":
                found = (version, data[at + 16:at + 16 + length])
            at += 16 + length
        self.assertEqual(at, native + header + payload)
        self.assertIsNotNone(found)
        version, body = found
        self.assertEqual(version, 1)
        self.assertGreater(len(body), 5 * 1024 * 1024)
        # Fixed prefix through CL450: ROM/RAM/register files precede the IRQ mask/status.
        mask = struct.unpack_from("<I", body, 789336)[0]
        pending = struct.unpack_from("<I", body, 789340)[0]
        self.assertNotEqual(mask & pending, 0)
        return len(data), len(body), mask, pending

    def test_cannon_fodder_live_fmv_saves_at_arbitrary_cuts_and_replays_in_place_and_fresh(self):
        state = self.work() / "cannon-fmv.state"
        out = self.real(
            "--do", "350:save:s350", "--do", "400:save:s400", "--do", "426:save:s426",
            "--do", "430:save:s430", "--do", f"430:savefile:{state}",
            "--do", "440:save:s440", "--do", "440:load:s430", "--do", "440:save:again",
            runs=445,
        )
        saves = {match[1]: match for match in __import__("re").finditer(
            r"^state save (\S+) ok=(\d) size=(\d+) used=(\d+) hash=(\w+) section=(\w+)$", out, __import__("re").M
        )}
        for name in ("s350", "s400", "s426", "s430", "s440", "again"):
            self.assertEqual(saves[name][2], "1", name)
        self.assertEqual(saves["again"][5], saves["s430"][5])
        self.assertEqual(saves["again"][4], saves["s430"][4])
        self.assertLess(int(saves["s430"][4]), int(saves["s430"][3]))

        seen = {}
        for run in self.runs(out):
            seen.setdefault(run["unit"], []).append(self.view(run))
        for unit in range(431, 436):
            self.assertEqual(len(seen[unit]), 2)
            self.assertEqual(seen[unit][0], seen[unit][1], unit)
        active = [run for run in self.runs(out) if 426 <= run["unit"] <= 435][:10]
        self.assertTrue(active)
        self.assertTrue(all(run["size"] == (756, 576) and run["audio"] > 0 for run in active))
        self.assertTrue(any(run["px"] != "00000000" for run in active))
        self.assertGreater(len({run["hash"] for run in active}), 2)
        self.assertGreater(len({run["ahash"] for run in active}), 2)

        baseline = {run["unit"]: run for run in self.runs(self.real(runs=431, fmv=False))}
        live430 = next(run for run in active if run["unit"] == 430)
        self.assertNotEqual((live430["hash"], live430["px"], live430["ahash"]),
                            (baseline[430]["hash"], baseline[430]["px"], baseline[430]["ahash"]))

        fresh = self.real("--do", f"0:loadfile:{state}:pos=430", "--do", "0:save:loaded", runs=5)
        self.assertIn(" ok=1 ", next(line for line in fresh.splitlines() if line.startswith("state load ")))
        loaded = next(match for match in __import__("re").finditer(
            r"^state save loaded ok=(\d) size=(\d+) used=(\d+) hash=(\w+)", fresh, __import__("re").M
        ))
        self.assertEqual(loaded[1], "1")
        self.assertEqual(loaded[4], saves["s430"][5])
        original = {run["unit"]: self.view(run) for run in self.runs(out) if 431 <= run["unit"] <= 435}
        repeated = {run["unit"]: self.view(run) for run in self.runs(fresh)}
        self.assertEqual(repeated, original)
        self.p6fm(state)


if __name__ == "__main__":
    unittest.main()
