"""config/profiles/a500-ocs.json against the preferences the bridge really builds.

(test_a600_profile.py runs the same checks, with the probe told to build the A600, against a600-ecs.json.)

The probe builds the A500 twice in one process: the way upstream does (default_prefs + built_in_prefs
model 0 + fixup_prefs) and the way the bridge does. Every field the profile pins must have the stated
value for PAL and NTSC, and every byte of struct uae_prefs that differs from upstream must belong to a
field the profile declares as an override or as derived from p6_config. A preference that changes
without being declared, in either direction, fails here.
"""
import bisect
import json
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import unittest

from tests.host.test_bridge_lifecycle import ROOT, TOOLS, build_probe

PROFILE = ROOT / "config/profiles/a500-ocs.json"
SOURCES = {"upstream", "pin", "override", "config"}


def top_level(name):
    return re.split(r"[.\[]", name)[0]


def layout_fields(build_dir):
    """(offset, member) of the top-level members of struct uae_prefs and its size, from clang's own layout."""
    entry = next(e for e in json.loads((build_dir / "compile_commands.json").read_text())
                 if e["file"].endswith("a500_profile_probe.cpp"))
    args, cleaned, skip = shlex.split(entry["command"]), [], 0
    for arg in args:
        if skip:
            skip -= 1
        elif arg in ("-o", "-MF", "-MT", "-MQ"):
            skip = 1
        elif arg not in ("-MD", "-MMD"):
            cleaned.append(arg)
    result = subprocess.run([*cleaned, "-fsyntax-only", "-Xclang", "-fdump-record-layouts-complete"],
                            cwd=entry["directory"], capture_output=True, text=True, check=True)
    block = next(b for b in result.stdout.split("*** Dumping AST Record Layout")
                 if "struct uae_prefs" in b.split("\n")[1])
    fields = []
    for line in block.splitlines():
        match = re.match(r"^ *(\d+)(?::\d+-\d+)? \| {3}(?! )(.+)$", line)
        if match:
            fields.append((int(match[1]), match[2].split()[-1]))
    size = int(re.search(r"sizeof=(\d+)", block)[1])
    return fields, size


class ProfileChecks:
    """The checks of one machine's profile; a subclass names the machine, the file and the argument of the probe."""
    MACHINE = "A500"
    PROBE_ARG = "a500"
    PROFILE = PROFILE

    @classmethod
    def setUpClass(cls):
        cls.build_dir = ROOT / "build/link-audit"
        exe = build_probe(cls.build_dir, False, "p6_profile_probe")
        out = subprocess.run([str(exe), cls.PROBE_ARG], capture_output=True, text=True, timeout=120, check=True).stdout
        cls.pinned = {"pal": {}, "ntsc": {}}
        cls.upstream = {"pal": {}, "ntsc": {}}
        cls.diffs = {"pal": [], "ntsc": []}
        for line in out.splitlines():
            parts = line.split()
            if parts[0] == "size":
                cls.size = int(parts[1])
            elif parts[0] == "pinned":
                cls.pinned[parts[1]][parts[2]] = " ".join(parts[3:])
            elif parts[0] == "upstream":
                cls.upstream[parts[1]][parts[2]] = " ".join(parts[3:])
            elif parts[0] == "diff":
                cls.diffs[parts[1]].append((int(parts[2]), int(parts[3])))
        cls.profile = json.loads(cls.PROFILE.read_text(encoding="utf-8"))
        cls.layout, cls.layout_size = layout_fields(cls.build_dir)

    def test_profile_file_is_well_formed(self):
        fields = self.profile["fields"]
        names = [f["name"] for f in fields]
        self.assertEqual(len(names), len(set(names)))
        self.assertEqual(self.profile["profile"], "deterministic-a500")
        self.assertEqual(self.profile["machine"], self.MACHINE)
        for f in fields:
            with self.subTest(field=f["name"]):
                self.assertIn(f["source"], SOURCES)
                self.assertGreaterEqual(len(f["why"]), 5)
                self.assertTrue(isinstance(f["value"], (int, str)) or set(f["value"]) == {"pal", "ntsc"})
        self.assertEqual(self.profile["test_config"], {"audio_rate_hz": 48000, "random_seed": "0x0123456789abcdef"})

    def test_pinned_values_are_what_the_bridge_builds_for_pal_and_ntsc(self):
        expected_names = {f["name"] for f in self.profile["fields"]}
        for standard in ("pal", "ntsc"):
            self.assertEqual(set(self.pinned[standard]), expected_names)
            for f in self.profile["fields"]:
                with self.subTest(standard=standard, field=f["name"]):
                    value = f["value"][standard] if isinstance(f["value"], dict) else f["value"]
                    actual = self.pinned[standard][f["name"]]
                    self.assertEqual(actual, str(value))

    def test_each_source_agrees_with_the_value_upstream_has(self):
        # "upstream" and "pin" are values the preset shares with upstream's own: T50 judges them by the comparator and not only by the bytes that differ in a member (an "override" may coincide
        # with upstream's value inside a member that differs elsewhere, jports and floppyslots, so that one is not constrained).
        for standard in ("pal", "ntsc"):
            for f in self.profile["fields"]:
                if f["source"] in ("upstream", "pin"):
                    with self.subTest(standard=standard, field=f["name"], source=f["source"]):
                        self.assertEqual(self.pinned[standard][f["name"]], self.upstream[standard][f["name"]])

    def test_every_change_against_upstream_is_declared(self):
        assert self.size == self.layout_size, "probe and layout disagree about struct uae_prefs"
        starts = [offset for offset, _ in self.layout]
        declared = {f["source"]: set() for f in self.profile["fields"]}
        for f in self.profile["fields"]:
            declared[f["source"]].add(top_level(f["name"]))
        for standard in ("pal", "ntsc"):
            changed = set()
            for offset, length in self.diffs[standard]:
                for byte in (offset, offset + length - 1):
                    changed.add(self.layout[bisect.bisect_right(starts, byte) - 1][1])
            with self.subTest(standard=standard):
                self.assertLessEqual(changed, declared.get("override", set()) | declared.get("config", set()),
                                     "preferences changed against upstream without a declaration")
                self.assertLessEqual(declared.get("override", set()), changed,
                                     "an override that changes nothing must be declared as a pin")

    def test_the_profile_file_is_not_in_the_engine_or_the_bridge_by_accident(self):
        # The file documents and checks the preset; the code owns it. Both must name the same fixed choices.
        config = (ROOT / "bridge/config.cpp").read_text()
        for f in self.profile["fields"]:
            if f["source"] in ("override", "pin") and f["name"] not in ("romfile",):
                with self.subTest(field=f["name"]):
                    compound = top_level(f["name"]) != f["name"]
                    # "via" names the chipset table that sets the value (T50): the bridge applies the table, it has no assignment of its own.
                    self.assertTrue(f["via"] in config and "built_in_chipset_prefs(&currprefs)" in config if "via" in f else
                                    (f"currprefs.{top_level(f['name'])}" if compound else f"currprefs.{f['name']} =") in config)


@unittest.skipUnless(TOOLS, "toolchain unavailable")
class A500ProfileTests(ProfileChecks, unittest.TestCase):
    pass


if __name__ == "__main__":
    unittest.main()
