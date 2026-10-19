"""T65c2: the actual managed loader, then the authorized game in an existing WBX."""
import importlib
import hashlib
import json
import os
from pathlib import Path
import subprocess
import unittest

from tests.whdload import test_bundle_csharp as bc
from tests.whdload import support
from whdload import bundle, ffs
ROOT, DOTNET, archive = bc.ROOT, bc.DOTNET, bc.archive
ARTIFACTS = Path(os.environ.get("P6_WHDLOAD_TEST_ARTIFACTS", ROOT / "build/waterbox"))
GUEST = ARTIFACTS / "puae6/release/puae6.wbx"


@unittest.skipUnless(DOTNET and bc.CACHE.is_dir(), "dotnet and the existing local NuGet cache are required")
class LoaderCSharpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        bc.BundleCSharpTests.setUpClass.__func__(cls)

    def resolve(self, files, *, success=True, **options):
        source, out = self.work / "input.zip", self.work / "resolved"
        source.write_bytes(archive(files))
        args = ["whdresolve", "--archive", str(source), "--out", str(out)]
        for key, value in options.items():
            args += ["--" + key, str(value)]
        result = subprocess.run([DOTNET, str(self.dll), *args], cwd=ROOT, env=self.env, capture_output=True, text=True, timeout=60)
        self.assertEqual(success, result.returncode == 0, result.stdout + result.stderr)
        return result

    def test_zero_slaves_and_disabled_keep_the_generic_volume_and_firmware(self):
        for files, options in (({"data": b"x"}, {}), ({"A.slave": b"x", "B.slave": b"y"}, {"whdload": "Disabled"})):
            result = self.resolve(files, **options)
            self.assertIn("firmware A500", result.stdout)
            self.assertIn("resolved machine=A500 hard=1 volumes=0", result.stdout)

    def test_ambiguity_mixture_and_missing_provider_fail_before_firmware(self):
        for files, options in (({"A.slave": b"x", "B.slave": b"y"}, {}), ({"A.slave": b"x"}, {"mixed": "true"}), ({"A.slave": b"x"}, {})):
            result = self.resolve(files, success=False, **options)
            self.assertNotIn("firmware ", result.stdout)
            self.assertIn("WHDLoad", result.stderr)

    @unittest.skipUnless(support.INPUTS, "authorized WHDLoad provider is required")
    def test_auto_forces_a1200_and_uses_only_the_named_provider_before_firmware(self):
        files = {"Game/A.slave": b"x", "Game/data": b"y"}
        result = self.resolve(files, provider=support.WHDLOAD, buttonwait="true")
        self.assertIn("firmware A1200", result.stdout)
        self.assertIn("resolved machine=A1200 hard=3 volumes=0", result.stdout)
        prefs = ffs.read_volume((self.work / "resolved/hard-1.hdf").read_bytes()).files["S/WHDLoad.prefs"]
        self.assertEqual(bundle.PREFERENCES + bundle.BUTTON_WAIT_LINE, prefs)
        missing = self.work / "empty-provider-directory"
        missing.mkdir()
        refused = self.resolve(files, providerpath=missing, success=False)
        self.assertNotIn("firmware ", refused.stdout)
        self.assertIn("WHDLoad_usr.lha", refused.stderr)
        provider_dir = Path(support.WHDLOAD).parent
        self.assertIn("firmware A1200", self.resolve(files, providerpath=provider_dir).stdout)


@unittest.skipUnless(support.AVAILABLE and GUEST.is_file() and DOTNET and bc.CACHE.is_dir(), "existing Waterbox artifacts and authorized WHDLoad/game/Kickstart inputs are required")
class LoaderWaterboxTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        bc.BundleCSharpTests.setUpClass.__func__(cls)
        cls.sha = json.loads((ARTIFACTS / "puae6/puae6-receipt.json").read_text())["guest"]["sha256"]
        cls.runtime = json.loads((ARTIFACTS / "receipt.json").read_text())["runtime_dylib"]
        if hashlib.sha256(GUEST.read_bytes()).hexdigest() != cls.sha or hashlib.sha256(Path(cls.runtime["path"]).read_bytes()).hexdigest() != cls.runtime["sha256"]:
            raise AssertionError("Waterbox artifacts differ from their receipts")
        cls.env = {**cls.env, "P6_EVICT": "0"}

    def session(self, lines):
        script = self.work / f"session-{len(list(self.work.glob('session-*.txt')))}.txt"
        script.write_text("\n".join(lines) + "\n")
        result = subprocess.run([DOTNET, str(self.dll), "session", "--runtime", self.runtime["path"], "--guest", str(GUEST), "--sha", self.sha,
                                 "--firmware", support.KICKSTART, "--program", str(script)], cwd=ROOT, env=self.env, capture_output=True, text=True, timeout=1200)
        self.assertEqual(0, result.returncode, result.stdout[-3000:] + result.stderr[-3000:])
        return result.stdout

    def test_auto_pack_real_save_restore_and_final_branch_persistence(self):
        wb = importlib.import_module("tests.waterbox-media.support").Sessions
        start, end = 900, 1700
        script = self.work / "controls.txt"
        script.write_text("\n".join("00000000 00000000 00000000 00000000 2 %d 0 0 1 %d 0 0" %
                                    (2 * int(901 <= u < 908), int(400 <= u < 404 or 1201 <= u < 1208)) for u in range(1, end + 1)) + "\n")
        core = lambda n, wait=False: f"core {n} volume={support.GAME} whdprovider={support.WHDLOAD} buttonwait={str(wait).lower()}"
        exports = lambda n, prefix: [f"export {n} {i} file={self.work / (prefix + name)}" for i, name in enumerate(bundle.NAMES)]
        before = {p: hashlib.sha256(Path(p).read_bytes()).digest() for p in (support.GAME, support.WHDLOAD)}
        saved = self.work / "final.SaveRAM"
        out = self.session([core("A"), *exports("A", "base-"), f"run A {start} script={script}", "save A s", "saveram A before keep",
                            f"run A {end - start} script={script}", "digest A first", *exports("A", "end-"), "saveram A first keep",
                            "load A s", "saveram A rewound keep", *exports("A", "rewound-"), f"run A {end - start} script={script}", "digest A again",
                            "replay A on", "saveram A suppressed keep", "replay A off", "saveram A final keep", f"writesaveram final {saved}",
                            core("B"), "load B s", f"run B {end - start} script={script}", "digest B fresh", "dispose B",
                            core("D", True), "digest D beforebad", "load D s", "digest D afterbad"])
        a, b = ([u[1:] for u in wb.units(out, n)] for n in "AB")
        self.assertEqual(a[start:end], a[end:], "same-sandbox restore changed future picture/PCM/ticks")
        self.assertEqual(a[start:end], b, "new-sandbox restore changed future picture/PCM/ticks")
        digests = {**wb.digests(out, "A"), **wb.digests(out, "B")}
        self.assertEqual(3, len(digests)); self.assertEqual(1, len(set(digests.values())))
        self.assertEqual(wb.digests(out, "D")["beforebad"], wb.digests(out, "D")["afterbad"])
        self.assertTrue(wb.rejections(out, "D"), "ButtonWait changed RASystem but its state was accepted")
        ram = {r[0]: r[1:3] for r in wb.saverams(out, "A")}
        self.assertEqual(ram["before"], ram["rewound"], "discarded writes escaped rollback")
        self.assertEqual(ram["first"], ram["final"]); self.assertIsNone(ram["suppressed"][0])
        self.assertGreater(ram["final"][0], 0)
        base = {n: (self.work / ("base-" + n)).read_bytes() for n in bundle.NAMES}
        final = {n: (self.work / ("end-" + n)).read_bytes() for n in bundle.NAMES}
        saves = ffs.read_volume(final["WHDSaves.hdf"]).files
        self.assertEqual([112], [len(v) for p, v in saves.items() if p.lower().endswith("fm1.hisc")])
        self.assertEqual(base["WHDSaves.hdf"], (self.work / "rewound-WHDSaves.hdf").read_bytes())
        self.assertEqual(ffs.read_volume(base["RASystem.hdf"]).files, ffs.read_volume(final["RASystem.hdf"]).files)
        self.assertEqual(ffs.read_volume(base["WHDLoad.hdf"]).files, ffs.read_volume(final["WHDLoad.hdf"]).files)
        # A second process imports only the final explicit SaveRAM, before running.
        persisted = self.work / "persisted.hdf"
        after = self.session([core("C"), f"readsaveram loaded {saved}", "storesaveram C loaded", "modified C", f"export C 2 file={persisted}"])
        self.assertIn("storedsaveram C loaded", after); self.assertEqual([0], wb.modified(after, "C"))
        self.assertEqual(final["WHDSaves.hdf"], persisted.read_bytes())
        self.assertEqual(before, {p: hashlib.sha256(Path(p).read_bytes()).digest() for p in before})
