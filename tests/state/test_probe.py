"""T14b: the canonical state probe (bridge/state_probe.cpp, target p6_state_probe).

One digest per subsystem, field by field, for what can be reached from outside the engine. What it has to be: the same in every process and
with the heap scribbled on (nothing hashed is an address, padding or uninitialized memory), the same after a reload as in a clean process from
the first unit on, different when the machine is different, and honest in the inventory about what each digest sees.
"""
import ast
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from tests.host.test_bridge_lifecycle import TOOLS, build_probe, builder  # noqa: E402
from tests.scenarios.a500.display import Display, rom_source as display_source  # noqa: E402
from tests.scenarios.audio.audio import Tone, rom_source as tone_source  # noqa: E402

BUILD = ROOT / "build/link-audit"
FIXTURES = ROOT / "tests/fixtures/microrom"
SOURCE = ROOT / "bridge/state_probe.cpp"
INVENTORY = json.loads((ROOT / "manifests/state-inventory.json").read_text())["savestate"]
DIGEST = re.compile(r"^digest (\d+) (\w+) ([0-9a-f]{64}) (\d+)$", re.M)
PASS = re.compile(r"^pass (\d+)$")


@unittest.skipUnless(TOOLS, "toolchain unavailable")
class ProbeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.exe = build_probe(BUILD, False, "p6_state_probe")
        cls.tmp = tempfile.TemporaryDirectory(prefix="p6-probe-")
        cls.roms = {}
        sources = {name: ((FIXTURES / f"{name}.s").read_text(encoding="utf-8"), defines) for name, defines in
                   (("boot", None), ("vsync", None), ("input_all", None), ("long_ops", {"PAD": "3"}))}
        sources["lace"] = (display_source(Display("hires-lace-fields", mode="hires", lace=True, bg=0x008, cycle_fg=True)), None)
        sources["tone"] = (tone_source(Tone("probe", channels=(0, 1), period=200, volume=64)), None)
        for name, (text, defines) in sources.items():
            path = Path(cls.tmp.name) / f"{name}.rom"
            path.write_bytes(builder.build(text, defines) if defines else builder.build(text))
            cls.roms[name] = path

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def run_probe(self, rom, *arguments, env=None):
        result = subprocess.run([str(self.exe), str(self.roms[rom]), *arguments], capture_output=True, text=True, timeout=900,
                                env={**os.environ, **(env or {})})
        self.assertEqual(0, result.returncode, result.stderr[-400:])
        passes, current = {}, 0
        for line in result.stdout.splitlines():
            if PASS.match(line):
                current = int(PASS.match(line)[1])
            elif DIGEST.match(line):
                unit, subsystem, digest, size = DIGEST.match(line).groups()
                passes.setdefault(current, {})[(int(unit), subsystem)] = digest
        return passes

    def test_the_probe_lists_the_subsystems_the_inventory_says_it_has(self):
        listed = subprocess.run([str(self.exe), "--list"], capture_output=True, text=True, check=True).stdout.split()
        self.assertEqual(INVENTORY["probes"]["subsystems"], listed)

    def test_the_digests_do_not_depend_on_the_process_or_on_what_the_heap_held(self):
        first = self.run_probe("input_all", "--units", "4")[0]
        second = self.run_probe("input_all", "--units", "4")[0]
        self.assertEqual(first, second)
        self.assertEqual(10 * 5, len(first))
        if sys.platform == "darwin":        # freshly allocated memory filled with 0xAA and freed memory with 0x55: no digest may care
            scribbled = self.run_probe("input_all", "--units", "4", env={"MallocScribble": "1", "MallocPreScribble": "1"})[0]
            self.assertEqual(first, scribbled, "a digest depends on memory the engine never initialized")

    def test_the_probe_reads_no_memory_it_does_not_name(self):
        text = SOURCE.read_text()
        body = text[text.index("void cpu(Canon &c)"):text.index("const Probe kProbes")]
        raws = re.findall(r"\.raw\(([^,]+),", body)
        allowed = {"chipmem_bank.baseaddr", "bogomem_bank.baseaddr", "fastmem_bank[0].baseaddr", "ac", "buffer", "first + size_t(y) * size_t(stride)", "s.firmware_sha256", "s.media_content_id[i]", "section.data()",
                   "z3fastmem_bank[0].baseaddr",  # Z3 RAM, bounded by its allocated_size.
                   "r",                         # uint8_t[4] filled by p6_mbres_get.
                   "ricoh"}                     # uae_u8[35] filled by p6_rtc_ricoh_get.
        self.assertEqual([], [r for r in raws if r.strip() not in allowed], "raw() of something the audit did not name")
        self.assertNotRegex(body, r"memcpy|sizeof\s*\(\s*regs\s*\)|sizeof\s*\(\s*eventtab|reinterpret_cast<const uint8_t \*>\(&")

    def test_each_subsystem_with_a_probe_sees_the_machine_change(self):
        runs = {name: self.run_probe(name, "--units", "6")[0] for name in self.roms}
        runs["boot-ntsc"] = self.run_probe("boot", "--units", "6", "--ntsc")[0]
        for subsystem in ("cpu", "scheduler", "ram", "rtc", "cia", "chipset", "audio", "video", "bridge"):
            with self.subTest(subsystem=subsystem):
                self.assertGreater(len({run[(6, subsystem)] for run in runs.values()}), 1, "the digest is the same for every fixture")
        # T15c3: the input digest moves with what the host sends (a port with a mouse in motion, a key held), and with nothing else: the fixtures alone do not move it.
        moved = self.run_probe("input_all", "--units", "6", "--port", "0:2:1:5:3", "--port", "1:1:65:0:0")[0]
        held = self.run_probe("vsync", "--units", "6", "--key", "2", "0x21", "down")[0]
        # A program that reads the input registers (input_all) moves it by itself: readinput brings prev_input_frame and prev_input_vpos up to the reading.
        self.assertEqual(1, len({run[(6, "input")] for name, run in runs.items() if name != "input_all"}), "a fixture moved the input digest without reading the input")
        self.assertNotEqual(runs["input_all"][(6, "input")], runs["vsync"][(6, "input")])
        self.assertNotEqual(runs["input_all"][(6, "input")], moved[(6, "input")])
        self.assertNotEqual(runs["vsync"][(6, "input")], held[(6, "input")])

    def test_a_reload_gives_the_digests_of_a_clean_process_from_the_first_unit_on(self):
        # Unit 0 is the machine before the power-on reset head has run: a reload still holds what the last session left there (the audio
        # registers, the chipset latches, the scheduler's dead events), which the head rebuilds; it is not compared (T13b).
        cases = (("boot", ()), ("vsync", ()), ("long_ops", ()), ("tone", ()), ("lace", ()),
                 ("input_all", ("--port", "0:2:1:5:3", "--port", "1:1:65:0:0")))
        for name, extra in cases:
            with self.subTest(rom=name):
                clean = self.run_probe(name, "--units", "5", *extra)[0]
                loads = self.run_probe(name, "--units", "5", "--loads", "3", *extra)
                for number in (1, 2, 3):
                    different = sorted(key for key in clean if key[0] >= 1 and clean[key] != loads[number][key])
                    self.assertEqual([], different, f"load {number} differs from a clean process at {different[:6]}")

    def test_what_the_inventory_says_the_probe_covers_is_in_the_probe(self):
        source = SOURCE.read_text()
        listed = set(INVENTORY["probes"]["subsystems"])
        tasks = {t["id"]: t for t in json.loads((ROOT / "plan/tasks.json").read_text())["tasks"]}
        private = {
            "cartridge-hrtmon-native": ("T69b", "HrtMonTests", "test_complete_native_state_cuts_command_return_cold_owners_and_refusals"),
            "cartridge-ar23-native": ("T69d2", "ArStateTests", "test_complete_ar23_semantic_cold_state_and_refusals"),
            "cartridge-ar1-native": ("T69e1", "Ar1StateTests", "test_complete_ar1_private_owner_wait_pc_cold_state_and_refusals"),
            "cartridge-ar1200-native": ("T69f1", "Ar1200StateTests", "test_private_ar1200_owner_freeze_and_cold_state"),
        }
        test_path = "tests/cartridge/test_hrtmon.py"
        tree = ast.parse((ROOT / test_path).read_text())
        methods = {node.name: {item.name for item in node.body if isinstance(item, ast.FunctionDef)}
                   for node in tree.body if isinstance(node, ast.ClassDef)}
        sys.path.insert(0, str(ROOT / "tools"))
        import check as dispatcher
        self.assertRegex((ROOT / "CMakeLists.txt").read_text(), r"add_executable\(p6_hrtmon_probe\s+tests/cartridge/hrtmon_probe\.cpp\b")
        self.assertTrue((ROOT / "tests/cartridge/hrtmon_probe.cpp").is_file())
        seen = {"implemented": 0, "partial": 0, "pending": 0}
        private_seen = set()
        for c in INVENTORY["components"]:
            probe = c["probe"]
            if probe is None:
                continue
            seen[probe["status"]] += 1
            with self.subTest(component=c["id"]):
                if c["id"] in private:
                    task, cls, method = private[c["id"]]
                    private_seen.add(c["id"])
                    self.assertEqual(("implemented", task), (probe["status"], probe["task"]))
                    self.assertTrue(probe["probe"].startswith("private p6_hrtmon_probe"))
                    self.assertEqual("done", tasks[task]["status"])
                    self.assertEqual((task, "done"), (c["proof"]["task"], c["proof"]["status"]))
                    self.assertTrue({"STATE01", "DET02"}.issubset(c["proof"]["checks"]))
                    self.assertTrue(c["proof"]["what"].strip())
                    native = c["native_state"]
                    self.assertEqual((task, "P6RM", "bridge/state.cpp", "visit_ram"),
                                     (native["task"], native["chunk"], native["file"], native["visitor"]))
                    self.assertTrue(native["proves"].startswith(test_path + ":" + cls))
                    self.assertIn(method, methods[cls])
                    endpoint = "tests.cartridge.test_hrtmon." + cls
                    for gate in ("STATE01", "DET02"):
                        self.assertTrue(any(route in (endpoint, endpoint + "." + method)
                                            for route in dispatcher.TASK_SUITES[(gate, task)].values()), gate)
                    self.assertIn("visit_ram", (ROOT / native["file"]).read_text())
                    self.assertTrue(probe["covers"] and all(isinstance(field, str) and field.strip() for field in probe["covers"]))
                    continue  # Dedicated full-state/future assertions; never claimed by generic ram().
                if probe["status"] == "pending":
                    self.assertIsNone(probe["probe"])
                    self.assertIn(probe["task"], tasks)
                    if c["id"] == "cdtv-controller-transport-audio":
                        self.assertEqual("T67b2", probe["task"])
                        self.assertEqual(probe["task"], c["native_state"]["task"])
                    self.assertTrue(probe["why"].strip())
                else:
                    self.assertIn(probe["probe"], listed)
                    self.assertEqual(c["subsystem"], probe["probe"], "a component is seen by the probe of its own subsystem")
                if probe["status"] == "implemented":
                    self.assertTrue(probe["covers"])
                    for field in probe["covers"]:
                        self.assertRegex(source, r"(?<!\w)" + re.escape(field).replace(r"\.", r"\s*\.\s*") + r"\b", f"{field} is not in the probe")
                if probe["status"] == "partial":
                    self.assertTrue(probe["why"].strip())
        self.assertGreater(seen["implemented"], 15)
        self.assertGreater(seen["partial"], 20)
        self.assertGreater(seen["pending"], 5)
        self.assertEqual(set(private), private_seen)


if __name__ == "__main__":
    unittest.main()
