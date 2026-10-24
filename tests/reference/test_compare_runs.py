"""tools/compare_runs.py on synthetic runs: it finds the first boundary and the subsystem of a difference that the test puts there, and finds none
between equal runs. The same tool on real engine output is tests/reference/test_ref01.py."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import compare_runs as cr  # noqa: E402

SUBSYSTEMS = ("cpu", "scheduler", "ram", "rtc", "cia", "chipset", "audio", "input", "video", "bridge")
GROUPS = ("beam", "counters", "irq_dma", "registers")


def h(*parts):
    return hashlib.sha256("/".join(map(str, parts)).encode()).hexdigest()


def probe_text(units=10, change=None):
    """What a probe prints for `units` boundaries; `change` maps (unit, key) to a replacement tag, applied from that unit on if the unit is a tuple's
    first element with a trailing 'from' marker."""
    change = change or {}
    lines = []
    for unit in range(units + 1):
        def value(key, base):
            for (first, name), tag in change.items():
                if name == key and unit >= first:
                    return h(base, tag)
            return h(base)
        for name in SUBSYSTEMS:
            lines.append(f"digest {unit} {name} {value('digest.' + name, name + str(unit))} 100")
            if name == "chipset":
                for group in GROUPS:
                    lines.append(f"chip {unit} {group} {value('chip.' + group, group + str(unit))}")
        if unit:
            lines.append(f"pcm {unit} {value('pcm', 'pcm' + str(unit))} 959")
            lines.append(f"picture {unit} 756x574 p0 3546895/70824 {value('picture', 'pic' + str(unit))}")
    return "\n".join(lines) + "\n"


def run(text):
    return cr.parse_probe(text)


class CompareTests(unittest.TestCase):
    def test_equal_runs_are_equal(self):
        report = cr.compare(run(probe_text()), run(probe_text()))
        self.assertTrue(report["equal"])
        self.assertEqual(11, report["boundaries"])
        self.assertEqual([], cr.explain(report, set()))

    def test_first_boundary_and_subsystem_of_an_injected_difference(self):
        report = cr.compare(run(probe_text()), run(probe_text(change={(5, "digest.cpu"): "x"})))
        self.assertFalse(report["equal"])
        self.assertEqual(5, report["first_divergent_boundary"])
        self.assertEqual(4, report["last_equal_boundary"])
        self.assertEqual("cpu", report["look_first"])
        self.assertEqual(["digest.cpu"], [item["key"] for item in report["keys"]])
        self.assertEqual(["cpu"], report["differing_subsystems"])
        # a difference that persists is one difference that starts at 5, not six
        self.assertEqual([], report["later"])

    def test_the_causal_order_picks_the_look_first(self):
        changes = {(3, "digest.video"): "a", (3, "pcm"): "b", (3, "digest.ram"): "c", (3, "digest.scheduler"): "d"}
        report = cr.compare(run(probe_text()), run(probe_text(change=changes)))
        self.assertEqual(3, report["first_divergent_boundary"])
        self.assertEqual("scheduler", report["look_first"])
        self.assertEqual(["scheduler", "ram", "video", "pcm"], report["subsystems"])

    def test_a_chip_group_is_the_chipset_and_names_its_group(self):
        report = cr.compare(run(probe_text()), run(probe_text(change={(7, "chip.beam"): "x"})))
        self.assertEqual(["chipset"], report["subsystems"])
        self.assertEqual("chip.beam", report["keys"][0]["key"])

    def test_keys_that_part_ways_later_are_listed(self):
        changes = {(2, "digest.input"): "a", (6, "picture"): "b"}
        report = cr.compare(run(probe_text()), run(probe_text(change=changes)))
        self.assertEqual("input", report["look_first"])
        self.assertEqual([("picture", 6, 5)], [(e["key"], e["first_unit"], e["units"]) for e in report["later"]])
        self.assertEqual(["input", "picture"], report["differing_subsystems"])

    def test_a_run_that_ends_early_diverges_at_its_end(self):
        report = cr.compare(run(probe_text(units=10)), run(probe_text(units=6)))
        self.assertFalse(report["equal"])
        self.assertEqual(7, report["first_divergent_boundary"])
        self.assertEqual([7], report["only_in_a"])
        self.assertEqual([], report["only_in_b"])

    def test_explain_returns_what_nobody_expected(self):
        report = cr.compare(run(probe_text()), run(probe_text(change={(4, "digest.audio"): "x", (4, "pcm"): "y", (4, "digest.cia"): "z"})))
        self.assertEqual(["cia"], cr.explain(report, {"audio", "pcm"}))
        self.assertEqual(["audio", "cia", "pcm"], cr.explain(report, set()))

    def test_a_load_section_is_compared_at_the_unit_it_was_made_in(self):
        base = probe_text(units=3) + "after-load 2\n" + "".join(f"digest 2 {name} {h('load', name)} 100\n" for name in ("cpu", "ram"))
        other = probe_text(units=3) + "after-load 2\n" + f"digest 2 cpu {h('load', 'cpu')} 100\n" + f"digest 2 ram {h('other')} 100\n"
        report = cr.compare(run(base), run(other))
        self.assertEqual(2, report["first_divergent_boundary"])
        self.assertEqual(["load1.digest.ram"], [item["key"] for item in report["keys"]])
        self.assertEqual("ram", report["look_first"])

    def test_scenario_results_are_compared_the_same_way(self):
        def result(events3):
            boundaries = []
            for unit in range(1, 6):
                hashes = {"ram": h("ram", unit), "cpu": h("cpu", unit), "events": h("events", unit, events3 if unit >= 3 else ""), "video": None, "pcm": h("pcm", unit)}
                boundaries.append({"unit": unit, "master_ticks": unit * 1000, "hashes": hashes, "samples_total": unit * 959})
            return {"boundaries": boundaries}
        report = cr.compare(cr.parse_result(result("a")), cr.parse_result(result("b")))
        self.assertEqual(3, report["first_divergent_boundary"])
        self.assertEqual(["events"], report["subsystems"])
        self.assertTrue(cr.compare(cr.parse_result(result("a")), cr.parse_result(result("a")))["equal"])

    def test_inputs_up_to_the_divergent_boundary_are_listed_in_order(self):
        args = "--units 8 --port 0:2:1:40:-25 --key 2 0x40 down --port-at 4 1:1:5:0:0 --reset-at 6 --save-at 7 /tmp/x".split()
        items = cr.inputs_from_probe_args(args)
        self.assertEqual([1, 2, 4, 6, 7], [item["unit"] for item in items])
        shown = cr.previous_inputs(items, 4, 8)
        self.assertEqual(["port", "key", "port-at"], [item["kind"] for item in shown])
        self.assertEqual("0x40 down", shown[1]["detail"])
        self.assertEqual(["key", "port-at"], [item["kind"] for item in cr.previous_inputs(items, 4, 2)])

    def test_scenario_inputs_and_events(self):
        scenario = {"inputs": [{"unit": 2, "keys": "0" * 32, "ports": [{"type": "joystick", "buttons": 1, "dx": 0, "dy": 0}, {"type": "none", "buttons": 0, "dx": 0, "dy": 0}]}],
                    "events": [{"unit": 5, "sequence": 0, "type": "mount", "drive": 0, "media_index": 0}]}
        items = cr.inputs_from_scenario(scenario)
        self.assertEqual([(2, "input"), (5, "mount")], [(i["unit"], i["kind"]) for i in items])

    def test_malformed_runs_are_refused(self):
        with self.assertRaises(cr.RunFormatError):
            cr.parse_probe("nothing here\n")
        with self.assertRaises(cr.RunFormatError):
            cr.parse_probe(probe_text(units=2) + probe_text(units=2))
        with self.assertRaises(cr.RunFormatError):
            cr.parse_result({"boundaries": []})
        with self.assertRaises(cr.RunFormatError):
            cr.parse_result({"boundaries": [{"unit": 1}]})


class CommandLineTests(unittest.TestCase):
    def invoke(self, a, b, *extra):
        with tempfile.TemporaryDirectory() as tmp:
            first, second = Path(tmp, "a.txt"), Path(tmp, "b.txt")
            first.write_text(a)
            second.write_text(b)
            return subprocess.run([sys.executable, str(ROOT / "tools/compare_runs.py"), str(first), str(second), *extra], capture_output=True, text=True)

    def test_exit_status_and_report(self):
        equal = self.invoke(probe_text(), probe_text())
        self.assertEqual((0, True), (equal.returncode, equal.stdout.startswith("EQUAL")))
        different = self.invoke(probe_text(), probe_text(change={(5, "digest.cpu"): "x"}), "--probe-args", "--key 3 0x40 down --key 9 0x41 down")
        self.assertEqual(1, different.returncode)
        self.assertIn("first divergent boundary 5", different.stdout)
        self.assertIn("look first at: cpu", different.stdout)
        self.assertIn("unit 3: key 0x40 down", different.stdout)
        self.assertNotIn("0x41", different.stdout)

    def test_json_report(self):
        different = self.invoke(probe_text(), probe_text(change={(5, "pcm"): "x"}), "--json")
        report = json.loads(different.stdout)
        self.assertEqual((1, 5, "pcm"), (different.returncode, report["first_divergent_boundary"], report["look_first"]))

    def test_unreadable_input_is_status_two(self):
        self.assertEqual(2, self.invoke("garbage\n", probe_text()).returncode)


if __name__ == "__main__":
    unittest.main()
