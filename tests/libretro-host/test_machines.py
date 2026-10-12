"""T50: the A500 OG, A500+, A1000, A2000 OG and A2000 through the libretro core (LR01, STATE01, DET02 through libretro), run by the minimal frontend.

`puae6_machine` offers the five machines (a restart option; the value names are the ones of config/schema/options.json); each looks for the Kickstart of its own family in the system
directory (adapters/libretro/firmware.h): the A500 OG, the A1000 and the A2000 OG the 1.3 of the A500 (the A1000 also the 1.2 and the 1.1), the A500+ the 2.04 and the A2000 the 3.1 r40.063,
and none boots with a name of another family. What is checked:
  * the core's session is p6_headless --machine <name> unit by unit (ticks, audio, picture hash) with the three micro-ROMs in PAL and NTSC;
  * every known file name of each machine is found, through the VFS and with stdio;
  * a Kickstart of another family is a clear failure that names what is missing;
  * the memory the options may ask of the machine (the A1000 has the memory of its preset: the bridge says so);
  * a save in one unit and a load in a later one replay the same units, and the state of each machine is the same size in every session, as the A500's.
"""
from pathlib import Path
import re
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_libretro as lr  # noqa: E402
from test_libretro import TICKS  # noqa: E402

NEW = {
    # option value -> (headless --machine, Kickstart version of the message, the names the machine looks for, a name of another family)
    "A500OG": ("a500og", "1.3", lr.KICKSTART_NAMES, lr.KICKSTART_NAMES_A600[0]),
    "A500PLUS": ("a500plus", "2.04", ["kick37175.A500", "amiga-os-204.rom", "Kickstart v2.04 rev 37.175 (1991)(Commodore)(A500+)[!].rom",
                                       "Kickstart v2.04 rev 37.175 (1991)(Commodore)(A500+).rom", "Kickstart v2.04 r37.175 (1991-05)(Commodore)(A500+)[!].rom"], lr.KICKSTART_NAMES[0]),
    "A1000": ("a1000", "1.3", [*lr.KICKSTART_NAMES, "kick33180.A500", "amiga-os-120.rom", "Kickstart v1.2 rev 33.180 (1986)(Commodore)(A500-A2000)[!].rom",
                               "Kickstart v1.2 rev 33.180 (1986)(Commodore)(A500-A1000-A2000).rom", "kick32034.A1000", "amiga-os-110-pal.rom",
                               "Kickstart v1.1 rev 32.34 (1986)(Commodore)(A1000)(PAL).rom", "kick31034.A1000", "amiga-os-110-ntsc.rom"], lr.KICKSTART_NAMES_A600[0]),
    "A2000OG": ("a2000og", "1.3", lr.KICKSTART_NAMES, lr.KICKSTART_NAMES_A600[0]),
    "A2000": ("a2000", "3.1", ["kick40063.A600", "amiga-os-310-a600.rom", "Kickstart v3.1 rev 40.63 (1993)(Commodore)(A500-A600-A2000)[!].rom",
                               "Kickstart v3.1 rev 40.63 (1993)(Commodore)(A500-A600-A2000).rom", "Kickstart v3.1 r40.063 (1993-07)(Commodore)(A500-A600-A2000)[!].rom"],
               lr.KICKSTART_NAMES[0]),
}
SAVE = re.compile(r"^state save (\S+) ok=(\d) size=(\d+) used=(\d+) hash=(\w+) section=(\w+)$", re.M)


def option(machine):
    return ("--set", f"puae6_machine={machine}")


@unittest.skipUnless(lr.TOOLS, "toolchain unavailable")
class MachineLibretroTests(lr.LibretroHarness):
    @staticmethod
    def messages(text):
        return re.findall(r"(?m)^message (.*)$", text)

    def compare_with_headless(self, out, rom, units, *flags):
        """Every unit of a session of the core has the ticks, audio and picture that p6_headless gives the same ROM with the same machine."""
        headless = self.headless_units(rom, units, *flags)
        ticks = {int(m[1]): int(m[2]) for m in TICKS.finditer(out)}
        runs = {r["n"]: r for r in self.runs(out)}
        self.assertEqual(sorted(runs), sorted(headless))
        for n, expected in headless.items():
            self.assertEqual((ticks[n], runs[n]["audio"]), (expected["ticks"], expected["audio"]), f"unit {n}")
            if expected["video"]:
                self.assertEqual((runs[n]["size"], runs[n]["hash"]), (expected["size"], expected["hash"]), f"unit {n}")
            else:
                self.assertTrue(runs[n]["dupe"], f"unit {n}")

    def test_each_machine_selected_by_option_is_the_machine_headless_runs(self):
        for machine, (flag, _, names, _) in NEW.items():
            for rom, units in (("boot", 16), ("long_frame", 14), ("identify", 8)):
                for standard in ((), ("--set", "puae6_video_standard=NTSC")):
                    with self.subTest(machine=machine, rom=rom, standard=standard):
                        out = self.frontend(rom, "--runs", units, *option(machine), *standard, name=names[0]).stdout
                        self.assertIn(f"machine {machine} (puae6_machine)", out)
                        self.compare_with_headless(out, rom, units, "--machine", flag, *(["--ntsc"] if standard else []))

    def test_every_machine_announces_the_buffer_height_of_its_agnus(self):
        # T72a: SET_SYSTEM_AV_INFO announces the bridge's buffer, and the bridge decides by the Agnus of the preset (OCS: A500, A500 OG, A1000, A2000 OG keep 486 rows in NTSC; ECS and AGA:
        # A500+, A600, A1200, A2000 reserve 576), halved by single lines; no picture of the run is larger than that.
        every = {"A500": lr.KICKSTART_NAMES[0], "A600": lr.KICKSTART_NAMES_A600[0], "A1200": lr.KICKSTART_NAMES_A1200[0], **{m: v[2][0] for m, v in NEW.items()}}
        ocs = {"A500", "A500OG", "A1000", "A2000OG"}
        for machine, name in every.items():
            for standard in ("PAL", "NTSC"):
                for resolution, lines in (("hires", "double"), ("lores", "single"), ("superhires", "double")):
                    with self.subTest(machine=machine, standard=standard, resolution=resolution, lines=lines):
                        out = self.frontend("boot", "--runs", 3, *option(machine), "--set", f"puae6_video_standard={standard}", "--set", f"puae6_video_resolution={resolution}",
                                            "--set", f"puae6_video_line_mode={lines}", name=name).stdout
                        found = re.search(r"^av base=(\d+)x(\d+) max=(\d+)x(\d+) ", out, re.M)
                        self.assertIsNotNone(found, out[-400:])
                        rows = (486 if standard == "NTSC" and machine in ocs else 576) >> (0 if lines == "double" else 1)
                        self.assertEqual((int(found[3]), int(found[4])), (380 << {"lores": 0, "hires": 1, "superhires": 2}[resolution], rows))
                        runs = self.runs(out)
                        self.assertTrue(runs)
                        self.assertTrue(all(r["size"][0] <= int(found[3]) and r["size"][1] <= int(found[4]) for r in runs))

    def test_each_known_file_name_of_each_machine_is_found_with_the_vfs_and_with_stdio(self):
        for machine, (_, _, names, _) in NEW.items():
            reference = None
            for name in names:
                for options in ((), ("--no-vfs",)):
                    with self.subTest(machine=machine, name=name, options=options):
                        out = self.frontend("boot", "--runs", 4, *option(machine), *options, name=name).stdout
                        self.assertIn(f"Kickstart {name}, read " + ("with stdio" if options else "through the frontend VFS"), out)
                        runs = [(r["hash"], r["audio"]) for r in self.runs(out)]
                        reference = reference or runs
                        self.assertEqual(runs, reference)

    def test_a_kickstart_of_another_family_is_a_clear_failure_and_never_boots(self):
        for machine, (_, version, names, other) in NEW.items():
            with self.subTest(machine=machine, other=other):
                out = self.frontend("boot", *option(machine), name=other, expect=3).stdout
                self.assertIn("load refused", out)
                text = " ".join(self.messages(out))
                self.assertIn(f"no Kickstart {version} ROM in the system directory ", text)
                self.assertIn(f"(looked for {names[0]}, ...) for the {machine} chosen by puae6_machine", text)
                self.assertNotIn("run 1 ", out)

    def test_a_directory_with_a_kickstart_for_each_family_boots_each_machine_with_its_own(self):
        everything = self.system_for("boot", lr.KICKSTART_NAMES[0], lr.KICKSTART_NAMES_A600[0], lr.KICKSTART_NAMES_A1200[0], NEW["A500PLUS"][2][0], NEW["A2000"][2][0])
        for machine, (_, _, names, _) in NEW.items():
            with self.subTest(machine=machine):
                self.assertIn(f"Kickstart {names[0]},", self.frontend("boot", "--runs", 3, *option(machine), system=everything).stdout)

    def test_the_memory_options_follow_what_each_machine_can_have(self):
        for machine, options, accepted in (
                ("A500OG", ("--set", "puae6_chip_size=MB2"), False),             # OCS Agnus: 512 KiB or 1 MiB
                ("A500OG", ("--set", "puae6_chip_size=MB1"), True),
                ("A500OG", ("--set", "puae6_slow_size=KB512"), True),
                ("A500PLUS", ("--set", "puae6_chip_size=MB2"), True),             # ECS Agnus: 2 MiB too
                ("A2000", ("--set", "puae6_chip_size=MB2"), True),
                ("A2000OG", ("--set", "puae6_chip_size=MB2"), False),
                ("A2000", ("--set", "puae6_fast_size=MB8"), True),
                ("A1000", ("--set", "puae6_chip_size=MB1"), False),               # the A1000 has the memory of its preset
                ("A1000", ("--set", "puae6_slow_size=KB512"), False),
                ("A1000", ("--set", "puae6_fast_size=MB1"), False),
                ("A1000", ("--set", "puae6_chip_size=KB512"), True)):             # naming the preset's own size is the preset
            with self.subTest(machine=machine, options=options):
                names = NEW[machine][2]
                result = self.frontend("boot", "--runs", 3, *option(machine), *options, name=names[0], expect=0 if accepted else 3)
                if not accepted:
                    self.assertIn("chip memory size is not supported for this machine" if machine != "A1000" else "the A1000 has the memory of its preset", result.stdout)
                    self.assertNotIn("run 1 ", result.stdout)

    def test_a_machine_changed_while_it_runs_is_reported_and_applies_at_the_next_load(self):
        both = self.system_for("identify", lr.KICKSTART_NAMES[0], NEW["A500PLUS"][2][0])
        out = self.frontend("identify", "--runs", 14, "--set-at", "6:puae6_machine=A500PLUS", system=both).stdout
        self.assertEqual(self.messages(out), ["PUAE6: puae6_machine is now A500PLUS but the machine runs with A500: reload the content to apply it"])
        cycles = self.frontend("identify", "--runs", 14, "--set-at", "6:puae6_machine=A500PLUS", "--cycles", 2, system=both).stdout
        self.assertRegex(cycles, r"machine A500 \(puae6_machine\)(?s:.*)machine A500PLUS \(puae6_machine\)")

    def test_the_schema_value_names_are_exactly_these(self):
        for value in ("A500+", "A2000 OG", "a1000", "A1000 "):
            with self.subTest(value=value):
                result = self.frontend("boot", "--set", f"puae6_machine={value}", expect=3)
                self.assertIn(f'the option puae6_machine has the value "{value}", which this core does not support (allowed: auto, A500, A600, A1200, A500OG, A500PLUS, A1000, A2000OG, A2000)',
                              result.stdout)
                self.assertNotIn("run 1 ", result.stdout)


@unittest.skipUnless(lr.TOOLS, "toolchain unavailable")
class MachineStateTests(lr.LibretroHarness):
    """STATE01 and DET02 through libretro on the new machines: a state taken at one unit, loaded at a later one, replays the same units and is saved again byte for byte; the size is one number."""

    def saved(self, out):
        return {m[1]: {"ok": m[2] == "1", "size": int(m[3]), "used": int(m[4]), "hash": m[5], "section": m[6]} for m in SAVE.finditer(out)}

    def test_a_load_replays_the_same_units_on_every_new_machine(self):
        for machine, (_, _, names, _) in NEW.items():
            for rom in ("boot", "long_frame"):
                with self.subTest(machine=machine, rom=rom):
                    out = self.frontend(rom, "--runs", 26, *option(machine), "--do", "8:save:a", "--do", "18:save:before", "--do", "18:load:a", "--do", "18:save:again", name=names[0]).stdout
                    runs = self.runs(out)
                    seen = {}
                    for run in runs:
                        seen.setdefault(run["unit"], []).append((run["unit"], run["ticks"], run["audio"], run["ahash"]) + ((run["hash"], run["px"]) if rom != "long_frame" else ()))
                    for unit in range(9, 17):
                        self.assertEqual(len(seen[unit]), 2, unit)
                        self.assertEqual(len(set(seen[unit])), 1, f"unit {unit} differs after the load: {seen[unit]}")
                    saves = self.saved(out)
                    self.assertEqual(saves["again"]["hash"], saves["a"]["hash"])
                    self.assertNotEqual(saves["before"]["hash"], saves["a"]["hash"])

    def test_the_size_is_one_number_per_session_and_follows_the_machine(self):
        sizes = {}
        for machine, (_, _, names, _) in NEW.items():
            with self.subTest(machine=machine):
                first = self.frontend("boot", "--runs", 12, *option(machine), "--do", "6:save:a", name=names[0]).stdout
                second = self.frontend("boot", "--runs", 4, *option(machine), "--set", "puae6_video_aspect=4:3", name=names[0]).stdout
                size = int(re.search(r"state size=(\d+)", first)[1])
                self.assertEqual(size, int(re.search(r"state size=(\d+)", second)[1]))
                self.assertEqual(self.saved(first)["a"]["size"], size)
                self.assertLess(self.saved(first)["a"]["used"], size)
                sizes[machine] = size
        a500 = int(re.search(r"state size=(\d+)", self.frontend("boot", "--runs", 3).stdout)[1])
        # The machines that count the vertical sync have the A500's chunks and the mains ones one more sub-body (patch 0240, 8 bytes and its header); the memory follows the preset.
        # The A500+ has the same total RAM (1 MiB of Chip against 512 KiB + 512 KiB of Slow), the same clock and the same worst-case picture: the A500's size.
        self.assertEqual(sizes["A500PLUS"], a500)
        self.assertEqual(sizes["A500OG"], a500 - 512 * 1024 - 31)                  # no Slow RAM and no clock body
        self.assertEqual(sizes["A1000"], sizes["A500OG"] + 16)                     # the mains TOD: CTOD, 8 bytes and its header
        self.assertEqual((sizes["A2000OG"], sizes["A2000"]), (a500 + 16, sizes["A500PLUS"] + 16))
        for machine, size in sizes.items():
            self.assertGreater(size, 1_000_000, machine)


if __name__ == "__main__":
    unittest.main()
