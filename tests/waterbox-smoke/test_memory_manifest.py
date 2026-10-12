"""T48f: manifests/waterbox-memory.json is re-recorded as a whole or not at all. Static: it needs no runtime, no guest and no dotnet.

Every `measured*` key is one run of one guest in one Waterbox runtime on one fixture. A partial re-record (T72 only redid `measured`, T51 changed the firmware of the
hard-file keys) leaves numbers of different guests side by side, and the tests that compare each key with its budget keep passing. So this test asks that
  - the set of keys is exactly the one the code produces: one per machine (build_waterbox.MEASURED_KEYS), one per memory configuration of test_puae6.MEMORY_CONFIGS
    (named by build_waterbox.measured_key), the hard-file keys and the full floppy overlay of WB12;
  - all of them carry the same guest_sha256 and runtime_sha256 (and, when puae6.wbx is built here, the guest's is the built file's);
  - each one carries the fixture it documents: the machine and memory it names, the firmware it says (boot.s for the machines, tests.hdf.hdf_rom.loop_rom() for the hard
    files, the WB12 writer for the floppy overlay) by sha256, and the size of the hard files its key names.
The recipe to re-record everything is in docs/03-waterbox.md ("Re-registro completo de waterbox-memory.json").
"""
import hashlib
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
for entry in (ROOT / "tools", ROOT, ROOT / "tests/waterbox-media", Path(__file__).resolve().parent):
    if str(entry) not in sys.path:
        sys.path.insert(0, str(entry))
import build_waterbox  # noqa: E402
from test_puae6 import MANIFEST, MEASURED, MEMORY_CONFIGS, microrom  # noqa: E402
from tests.hdf import hdf_rom  # noqa: E402
from tests.host.test_bridge_lifecycle import TOOLS, builder  # noqa: E402

REPLAY_HINT = "re-record every measured* key with the same guest and runtime (docs/03-waterbox.md, 'Re-registro completo')"
HARD_FILES = {"measured_a500_hdf2048": [2 << 20], "measured_a500_hdf32768": [32 << 20]}       # the sizes the hard-file keys document (tests/waterbox-smoke/test_puae6_hdf.py: 64 cylinders; the big one)
FLOPPY_KEY = "measured_a500_floppy_writes_full"
BOOT_UNITS = 60


def sha(data):
    return hashlib.sha256(data).hexdigest()


FIXTURE_NAMES = ("chip_kib", "slow_kib", "fast_kib", "fpu_model", "hard_file_bytes", "floppy_writes", "drives", "media")


def expected_fixtures():
    """{key: (the fixture fields the key documents, sha256 of its firmware)} for all the keys of the code."""
    boot, loop = sha(microrom("boot.s")), sha(hdf_rom.loop_rom())
    expected = {}
    for machine, key in MEASURED:
        expected[key] = ({"machine": machine, "units": BOOT_UNITS}, boot)
    for _label, key, machine, memory in MEMORY_CONFIGS:
        expected[key] = ({"machine": machine, "units": BOOT_UNITS, **memory}, boot)
    for key, sizes in HARD_FILES.items():
        expected[key] = ({"machine": "A500", "units": BOOT_UNITS, "hard_file_bytes": sizes}, loop)
    import wb12
    from test_media_limits import UNITS
    expected[FLOPPY_KEY] = ({"machine": "A500", "units": UNITS, "floppy_writes": True, "drives": 2, "media": ["A", "B"]}, sha(builder.build(wb12.rom_source())))
    return expected


def produced_keys():
    return {key for _, key in MEASURED} | {key for _, key, _, _ in MEMORY_CONFIGS} | set(HARD_FILES) | {FLOPPY_KEY}


def key_problems(manifest):
    produced = produced_keys()
    measured = {key for key in manifest if key.startswith("measured")}
    return ([f"{key}: recorded but nobody asks for it" for key in sorted(measured - produced)]
            + [f"{key}: not recorded" for key in sorted(produced - measured)])


def identity_problems(manifest, built=None):
    """The keys must share guest_sha256 and runtime_sha256; guest_sha256 must also be the built guest's if given. The runtime is not compared with the built libwaterboxhost.dylib:
    its bytes carry the paths of the checkout it was compiled in (a worktree builds another hash from the same sources), the guest is the same in any checkout."""
    problems = []
    measured = {key: value for key, value in manifest.items() if key.startswith("measured")}
    for field in ("guest_sha256", "runtime_sha256"):
        by_value = {}
        for key, value in measured.items():
            by_value.setdefault(value.get(field), []).append(key)
        if len(by_value) != 1 or None in by_value:
            problems.append(f"{field} differs between keys: " + "; ".join(f"{str(value)[:8]}: {keys}" for value, keys in by_value.items()))
        if field in (built or {}) and set(by_value) != {built[field]}:
            problems.append(f"{field} is not the built one ({built[field][:8]}) in {sorted(key for value, keys in by_value.items() if value != built[field] for key in keys)}")
    return problems


def fixture_problems(manifest, expected):
    problems = []
    for key, (fixture, firmware_sha) in expected.items():
        recorded = manifest.get(key, {}).get("fixture", {})
        problems += [f"{key}: fixture.{name} is {recorded.get(name)!r}, not {value!r}" for name, value in fixture.items() if recorded.get(name) != value]
        if recorded.get("firmware_sha256") != firmware_sha:
            problems.append(f"{key}: firmware {str(recorded.get('firmware_sha256'))[:8]} is not the documented one ({firmware_sha[:8]})")
        if {name for name in FIXTURE_NAMES if name in fixture} != {name for name in FIXTURE_NAMES if name in recorded}:
            problems.append(f"{key}: the fixture names memory or media the key does not document")
    return problems


def built_identity():
    guest_receipt = build_waterbox.BUILD / "puae6/puae6-receipt.json"
    if not guest_receipt.is_file():
        return None
    return {"guest_sha256": json.loads(guest_receipt.read_text())["guest"]["sha256"]}


@unittest.skipUnless(TOOLS, "no 68k assembler (the firmware shas cannot be recomputed)")
class MemoryManifestRecordedAsAWholeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.manifest = json.loads(MANIFEST.read_text())
        cls.expected = expected_fixtures()

    def assertNoProblems(self, problems):
        self.assertEqual([], problems, REPLAY_HINT)

    def test_the_keys_are_exactly_the_ones_the_recording_code_produces(self):
        self.assertEqual(set(build_waterbox.MEASURED_KEYS.values()), {key for _, key in MEASURED}, "test_puae6.MEASURED follows build_waterbox.MEASURED_KEYS")
        for machine, key in MEASURED:
            self.assertEqual(key, build_waterbox.measured_key(machine))
        for _label, key, machine, memory in MEMORY_CONFIGS:
            self.assertEqual(key, build_waterbox.measured_key(machine, memory.get("chip_kib", 0), memory.get("slow_kib", 0), memory.get("fast_kib", 0), memory.get("fpu_model", 0)))
        for key, sizes in HARD_FILES.items():
            self.assertEqual(key, build_waterbox.measured_key("A500", hard_file_kib=sum(sizes) // 1024))
        self.assertEqual(16, len(produced_keys()))
        self.assertNoProblems(key_problems(self.manifest))

    def test_every_key_was_recorded_by_the_same_guest_and_the_same_runtime(self):
        self.assertNoProblems(identity_problems(self.manifest))

    def test_each_key_has_the_fixture_it_documents(self):
        self.assertEqual(produced_keys(), set(self.expected))
        self.assertNoProblems(fixture_problems(self.manifest, self.expected))

    def test_the_recorded_guest_is_the_built_one_when_it_is_built_here(self):
        built = built_identity()
        if built is None:
            self.skipTest("puae6.wbx is not built here (tools/build_waterbox.py --puae6 / --puae6-run build/waterbox/boot.rom): only the keys are compared with each other")
        self.assertNoProblems(identity_problems(self.manifest, built))

    def mutated(self, key, **changes):
        manifest = json.loads(json.dumps(self.manifest))
        for name, value in changes.items():
            target = manifest[key]
            *path, last = name.split(".")
            for step in path:
                target = target[step]
            target[last] = value
        return manifest

    def test_a_partial_re_record_fails(self):
        """The failures this test exists for: T72 re-recorded `measured` only, T51 changed the firmware of the hard-file keys."""
        other = "f" * 64
        self.assertNoProblems(identity_problems(self.manifest) + fixture_problems(self.manifest, self.expected) + key_problems(self.manifest))
        self.assertTrue(identity_problems(self.mutated("measured", guest_sha256=other)), "one key of another guest")
        self.assertTrue(identity_problems(self.mutated("measured_a1200", runtime_sha256=other)), "one key of another runtime")
        self.assertTrue(identity_problems(self.manifest, {"guest_sha256": other}), "all keys of a guest that is not the built one")
        self.assertTrue(fixture_problems(self.mutated("measured_a500_hdf2048", **{"fixture.firmware_sha256": other}), self.expected), "hard-file key on another firmware")
        self.assertTrue(fixture_problems(self.mutated("measured_a500_hdf32768", **{"fixture.hard_file_bytes": [2 << 20]}), self.expected), "hard-file key with the size of the other")
        self.assertTrue(fixture_problems(self.mutated(FLOPPY_KEY, **{"fixture.firmware_sha256": other}), self.expected), "floppy key on another writer")
        self.assertTrue(fixture_problems(self.mutated("measured_a600", **{"fixture.machine": "A500"}), self.expected), "a key recorded with another machine")
        manifest = json.loads(json.dumps(self.manifest))
        del manifest["measured_a2000"]
        self.assertTrue(key_problems(manifest), "a missing key")
        manifest = json.loads(json.dumps(self.manifest))
        manifest["measured_a500_hdf4096"] = manifest["measured_a500_hdf2048"]
        self.assertTrue(key_problems(manifest), "a key nobody asks for")


if __name__ == "__main__":
    unittest.main()
