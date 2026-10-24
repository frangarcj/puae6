"""T20c: the PUAE6 core as BizHawk drives it, built from BizHawk's pinned sources and run in a real Waterbox host (tests/ffi/CoreCommands.cs).

IN01  the controller of BizHawk (buttons and absolute mouse axes) reaches the machine as the p6_input packet of docs/02-contracts.md: the same scripts
      that tests/input plays through p6_run_frame natively are played through IController, and what the input_all fixture logs in Chip RAM is the same,
      word for word. Mouse movement past 120 counts per unit is carried over, not lost; Caps Lock is a latch toggled by a press; Power resets
      (and the unit counter and the ticks of the caller keep going).
A/V   picture size, aspect, cadence, picture hash and audio frames per unit are those of p6_headless; the PCM equals the one of the Waterbox harness;
      silencing picture and sound for a stretch does not change what follows; lag and drive light follow the machine.
WB04  two machines of the same guest in one process, taking turns with different input, each do what they do alone; one can go on after the other
      is disposed.
T24f  the same lines on the A1200 (Machine = A1200, --machine a1200) and its domains: 2 MiB of Chip RAM and the ROM.
T23e  the same lines on the A600 (Machine = A600, --machine a600): the picture, ticks and PCM of p6_headless --machine a600; the domains are 1 MiB of Chip RAM and the ROM.
T50   the same lines on the A500 OG, the A500+, the A1000, the A2000 OG and the A2000 (--machine a500og, a500plus, a1000, a2000og, a2000) and their domains.
WB07  the memory domains are the machine's (big endian, 16-bit words, function hooks): pokes before the first unit go through the guest into pages nothing
      has written, the ROM takes none, out-of-range accesses throw, and nothing is a temporary pointer or an empty region.

Needs the Waterbox runtime and the guest (tools/build_waterbox.py --puae6-run build/waterbox/boot.rom), dotnet and the local NuGet cache; skips without them.
    python3 -m unittest discover -s tests/ffi -p "test_core.py"
"""
import importlib.util
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests/waterbox-smoke"))
import build_waterbox  # noqa: E402
from tests.host.test_bridge_lifecycle import build_probe, builder  # noqa: E402
from tests.input.test_input import B1, B2, B3, CAPS, DOWN, ESC, FIXTURES, JOYSTICK, KEY_Q, KEY_W, LEFT, MOUSE, RIGHT, UP, line  # noqa: E402
from tests.lifecycle.test_reset import Run  # noqa: E402
from tests.scenarios.a500.display import Display, rom_source  # noqa: E402
from tests.scenarios.ecs import ecs  # noqa: E402
from tests.step.test_step import TOOLS, configure_trace_tree  # noqa: E402
from test_puae6 import runtime_available  # noqa: E402

spec = importlib.util.spec_from_file_location("disk_fixture", ROOT / "tests/scenarios/disk/disk.py")
disk = importlib.util.module_from_spec(spec)
spec.loader.exec_module(disk)

GUEST = build_waterbox.BUILD / "puae6/release/puae6.wbx"
RECEIPT = build_waterbox.BUILD / "puae6/puae6-receipt.json"
LACE = Display("hires-lace-fields", mode="hires", lace=True, bg=0x008, cycle_fg=True)
IDLE = dict(p0=(MOUSE, 0, 0, 0), p1=(JOYSTICK, 0, 0, 0))     # the core always has a mouse in port 1 and a joystick in port 2


def combo_script(units=14):
    """tests/input's combo_script with the devices the core has plugged in from the first unit on."""
    script = {4: line(p0=(MOUSE, 0, 5, -3), p1=(JOYSTICK, RIGHT, 0, 0)), 5: line(p0=(MOUSE, 0, 120, 0), p1=(JOYSTICK, RIGHT | UP, 0, 0)),
              6: line(p0=(MOUSE, B1, -7, 0), p1=(JOYSTICK, LEFT, 0, 0)), 7: line(p0=(MOUSE, B2, 0, 0), p1=(JOYSTICK, DOWN | LEFT, 0, 0)),
              8: line(p0=(MOUSE, B3, 0, 0), p1=(JOYSTICK, B1, 0, 0)), 9: line(p0=(MOUSE, B1 | B2 | B3, 0, 0), p1=(JOYSTICK, B2, 0, 0)),
              10: line(p0=(MOUSE, 0, 0, 0), p1=(JOYSTICK, B3 | UP, 0, 0)), 11: line(p0=(MOUSE, 0, 0, 0), p1=(JOYSTICK, DOWN, 0, 0))}
    return [script.get(u, line(**IDLE)) for u in range(1, units + 1)]


def joystick_script(units=14):
    """Both ports are joysticks: every direction and button on port 1, a few on port 2."""
    rows = {4: (RIGHT, 0), 5: (RIGHT | UP, LEFT), 6: (LEFT, B1), 7: (DOWN | LEFT, B2), 8: (B1, B3), 9: (B2, UP | B1), 10: (B3 | UP, DOWN), 11: (DOWN | B1 | B2, RIGHT)}
    return [line(p0=(JOYSTICK, rows.get(u, (0, 0))[0], 0, 0), p1=(JOYSTICK, rows.get(u, (0, 0))[1], 0, 0)) for u in range(1, units + 1)]


def image_of(run, units, resets=()):
    """The words the guest's log holds after `units` units, from what each unit wrote: the log starts again at $1000 after a reset."""
    image, at = {}, 0
    for unit in range(1, units + 1):
        if unit in resets and unit > 1:
            at = 0
        for word in run.logged[unit]:
            image[at] = word
            at += 1
    return [image.get(i, 0) for i in range(max(image) + 1)]


@unittest.skipUnless(runtime_available() and TOOLS, "Waterbox runtime or puae6.wbx not built (tools/build_waterbox.py --puae6-run build/waterbox/boot.rom)")
class CoreTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sha = json.loads(RECEIPT.read_text())["guest"]["sha256"]
        cls.headless = build_probe(ROOT / "build/link-audit", False, "p6_headless")
        cls.trace = configure_trace_tree()
        cls.tmp = tempfile.TemporaryDirectory(prefix="p6-core-")
        cls.dir = Path(cls.tmp.name)
        for name, source in (("input_all", (FIXTURES / "input_all.s").read_text(encoding="utf-8")), ("chipcell", (Path(__file__).parents[1] / "waterbox-smoke/chipcell.s").read_text(encoding="utf-8")),
                             ("lace", rom_source(LACE)), ("disk", disk.rom_source()),
                             ("to-ntsc", ecs.rom_source(ecs.beam_display_for("to-ntsc", "pal")))):
            (cls.dir / f"{name}.rom").write_bytes(builder.build(source))

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def core(self, command, *args):
        result = build_waterbox.run_ffi(command, "--runtime", "libwaterboxhost.dylib", "--guest", str(GUEST), "--sha", self.sha, *args)
        self.assertEqual(0, result.returncode, f"{result.stdout[-2500:]}\n{result.stderr[-2500:]}")
        self.assertNotIn("FAIL", result.stdout)
        return result.stdout

    def play(self, script, units, rom="input_all", standard="pal", port1="mouse", power_at=(), seed=None):
        path = self.dir / "script.txt"
        path.write_text("\n".join(script) + "\n")
        args = ["--firmware", str(self.dir / f"{rom}.rom"), "--script", str(path), "--units", str(units), "--port1", port1]
        if standard == "ntsc":
            args.append("--ntsc")
        if power_at:
            args += ["--power-at", ",".join(map(str, power_at))]
        if seed is not None:
            args += ["--seed", str(seed)]
        out = self.core("play", *args)
        words = [int(w, 16) for w in re.search(r"^log (.*)$", out, re.M)[1].split()]
        ticks = [int(m) for m in re.findall(r"^unit \d+ ticks=(\d+)", out, re.M)]
        return words, ticks

    def native(self, script, units, standard="pal", resets=()):
        return Run(self.trace, self.dir / "input_all.rom", script, units, list(resets), standard=standard)

    def assertSameMachine(self, script, units, standard="pal", port1="mouse", native_script=None, resets=(), power_at=()):
        words, ticks = self.play(script, units, standard=standard, port1=port1, power_at=power_at)
        run = self.native(native_script or script, units, standard, resets)
        expected = image_of(run, units, resets)
        self.assertGreater(len(expected), 40)
        self.assertEqual(expected, words[:len(expected)], "what the guest logged is what it logs through p6_run_frame")
        self.assertEqual(run.ticks, ticks[-1], "the same ticks at the end")
        self.assertEqual(units, len(ticks))
        self.assertEqual(sorted(ticks), ticks)

    def test_the_heaps_of_the_core_are_the_budgets_of_the_memory_manifest(self):
        layout = json.loads((ROOT / "manifests/waterbox-memory.json").read_text())["layout"]
        expected = [layout[name] // 1024 for name in ("sbrk", "sealed", "invisible", "plain", "mmap")]
        out = build_waterbox.run_ffi("heaps").stdout
        self.assertEqual(f"heaps {' '.join(map(str, expected))}", out.strip())

    # --- IN01 ---------------------------------------------------------------------------------------------------------------------------

    def test_mouse_and_joysticks_reach_the_guest_as_they_do_through_the_native_packet(self):
        for standard in ("pal", "ntsc"):
            with self.subTest(standard=standard):
                self.assertSameMachine(combo_script(), 14, standard)

    def test_two_joysticks_with_every_direction_and_button(self):
        self.assertSameMachine(joystick_script(), 14, port1="joystick")

    def test_keys_are_pressed_once_and_released_and_caps_lock_is_a_latch_that_a_press_toggles(self):
        keys = [line(keys=[ESC] if 30 <= u <= 39 else [KEY_Q, KEY_W] if u == 50 else [CAPS] if 20 <= u <= 27 else [], **IDLE) for u in range(1, 61)]
        self.assertSameMachine(keys, 60)

    def test_mouse_movement_past_120_counts_is_carried_over_and_not_lost(self):
        wide = [line(p0=(MOUSE, 0, 300 if u == 5 else 0, -150 if u == 5 else 0), p1=(JOYSTICK, 0, 0, 0)) for u in range(1, 15)]
        spread = [line(p0=(MOUSE, 0, {5: 120, 6: 120, 7: 60}.get(u, 0), {5: -120, 6: -30}.get(u, 0)), p1=(JOYSTICK, 0, 0, 0)) for u in range(1, 15)]
        self.assertSameMachine(wide, 14, native_script=spread)

    def test_power_is_a_hard_reset_that_keeps_the_counters_going(self):
        script = [line(**IDLE)] * 20
        self.assertSameMachine(script, 20, resets=[9], power_at=[8])
        words, ticks = self.play(script, 20, power_at=[8])
        self.assertTrue(all(b > a for a, b in zip(ticks, ticks[1:])), "master ticks keep growing through the reset")

    # --- A/V ----------------------------------------------------------------------------------------------------------------------------

    def av(self, rom, units, *extra):
        out = self.core("av", "--firmware", str(self.dir / f"{rom}.rom"), "--units", str(units), *extra)
        rows = [dict(re.findall(r"(\w+)=(\S+)", row)) | {"unit": row.split()[1]} for row in out.splitlines() if row.startswith("av ")]
        return rows, out

    def headless_lines(self, rom, units, *extra):
        result = subprocess.run([str(self.headless), "--run", str(self.dir / f"{rom}.rom"), "--units", str(units), *extra], capture_output=True, text=True, timeout=3600)
        self.assertEqual(0, result.returncode, result.stderr)
        pattern = re.compile(r"unit (\d+) ticks=(\d+) video=(\d) active=(\d+)x(\d+) parity=(\d+) aspect=(\d+)/(\d+) cadence=(\d+)/(\d+) hash=(\w+) audio=(\d+)")
        return [m.groups() for m in map(pattern.match, result.stdout.splitlines()) if m]

    def test_picture_audio_and_timing_per_unit_are_those_of_headless_in_both_standards(self):
        for standard, flag in (("pal", []), ("ntsc", ["--ntsc"])):
            with self.subTest(standard=standard):
                units = 40
                rows, out = self.av("lace", units, *flag)
                native = self.headless_lines("lace", units, *flag)
                self.assertEqual(units, len(rows))
                for row, (unit, ticks, video, w, h, parity, an, ad, cn, cd, picture, audio) in zip(rows, native):
                    self.assertEqual((unit, ticks, f"{w}x{h}", f"{cn}/{cd}", picture, audio), (row["unit"], row["ticks"], row["size"], row["vsync"], row["hash"], row["audio"]), f"unit {unit}")
                    self.assertEqual(int(w) * int(an) // int(ad), int(row["virtual"].split("x")[0]), f"unit {unit}: the aspect of the pixel")
                self.assertTrue(all(row["opaque"] == "1" for row in rows), "every pixel of the picture is opaque ARGB")
                self.assertIn(f"region={'NTSC' if standard == 'ntsc' else 'PAL'} system=Amiga deterministic=True", out)

    def test_an_amiga_forever_rom_is_decrypted_with_the_rom_key_and_is_the_plain_rom(self):
        # T51: AMIROMTYPE1 + XOR with a synthetic key (the format of Cloanto's rom.key; the cipher is written here from the format, not taken from the core). The decrypted image is the
        # firmware, so the core runs exactly the units of the plain ROM; without the key or with another one the creation of the core fails and says why.
        plain = (self.dir / "lace.rom").read_bytes()
        key = bytes((i * 37 + 11) % 251 for i in range(1584))
        (self.dir / "lace-encrypted.rom").write_bytes(b"AMIROMTYPE1" + bytes(b ^ key[i % len(key)] for i, b in enumerate(plain)))
        (self.dir / "rom.key").write_bytes(key)
        (self.dir / "wrong.key").write_bytes(key[::-1])
        expected, _ = self.av("lace", 12)
        opened, _ = self.av("lace-encrypted", 12, "--romkey", str(self.dir / "rom.key"))
        self.assertEqual(expected, opened)
        for label, extra, text in (("no key", [], "no rom.key was given"), ("wrong key", ["--romkey", str(self.dir / "wrong.key")], "does not decrypt it")):
            with self.subTest(case=label):
                result = build_waterbox.run_ffi("av", "--runtime", "libwaterboxhost.dylib", "--guest", str(GUEST), "--sha", self.sha, "--firmware", str(self.dir / "lace-encrypted.rom"),
                                                "--units", "2", *extra)
                self.assertNotEqual(0, result.returncode)
                self.assertIn(text, result.stdout + result.stderr)

    def test_picture_audio_and_timing_per_unit_are_those_of_headless_on_the_a600_in_both_standards(self):
        # T23e: the same lines on the other machine (ECS, 1 MiB of Chip RAM): the core with Machine = A600 is p6_headless --machine a600.
        for standard, flag in (("pal", []), ("ntsc", ["--ntsc"])):
            with self.subTest(standard=standard):
                units = 40
                rows, out = self.av("lace", units, "--machine", "a600", *flag)
                native = self.headless_lines("lace", units, "--machine", "a600", *flag)
                self.assertEqual(units, len(rows))
                for row, (unit, ticks, video, w, h, parity, an, ad, cn, cd, picture, audio) in zip(rows, native):
                    self.assertEqual((unit, ticks, f"{w}x{h}", f"{cn}/{cd}", picture, audio), (row["unit"], row["ticks"], row["size"], row["vsync"], row["hash"], row["audio"]), f"unit {unit}")
                self.assertIn(f"region={'NTSC' if standard == 'ntsc' else 'PAL'} system=Amiga deterministic=True", out)

    def test_picture_audio_and_timing_per_unit_are_those_of_headless_on_the_a1200_in_both_standards(self):
        # T24f: the A1200 (68EC020, AGA, 2 MiB of Chip RAM): the core with Machine = A1200 is p6_headless --machine a1200.
        for standard, flag in (("pal", []), ("ntsc", ["--ntsc"])):
            with self.subTest(standard=standard):
                units = 40
                rows, out = self.av("lace", units, "--machine", "a1200", *flag)
                native = self.headless_lines("lace", units, "--machine", "a1200", *flag)
                self.assertEqual(units, len(rows))
                for row, (unit, ticks, video, w, h, parity, an, ad, cn, cd, picture, audio) in zip(rows, native):
                    self.assertEqual((unit, ticks, f"{w}x{h}", f"{cn}/{cd}", picture, audio), (row["unit"], row["ticks"], row["size"], row["vsync"], row["hash"], row["audio"]), f"unit {unit}")
                self.assertIn(f"region={'NTSC' if standard == 'ntsc' else 'PAL'} system=Amiga deterministic=True", out)

    T50_MACHINES = {"a500og": {"Chip RAM": 524288}, "a500plus": {"Chip RAM": 1048576}, "a1000": {"Chip RAM": 524288}, "a2000og": {"Chip RAM": 524288, "Slow RAM": 524288}, "a2000": {"Chip RAM": 1048576}}

    def test_picture_audio_and_timing_per_unit_are_those_of_headless_on_the_t50_machines_in_both_standards(self):
        # T50: the core with Machine = A500OG, A500PLUS, A1000, A2000OG or A2000 is p6_headless --machine <name>.
        for machine in self.T50_MACHINES:
            for standard, flag in (("pal", []), ("ntsc", ["--ntsc"])):
                with self.subTest(machine=machine, standard=standard):
                    units = 30
                    rows, out = self.av("lace", units, "--machine", machine, *flag)
                    native = self.headless_lines("lace", units, "--machine", machine, *flag)
                    self.assertEqual(units, len(rows))
                    for row, (unit, ticks, video, w, h, parity, an, ad, cn, cd, picture, audio) in zip(rows, native):
                        self.assertEqual((unit, ticks, f"{w}x{h}", f"{cn}/{cd}", picture, audio), (row["unit"], row["ticks"], row["size"], row["vsync"], row["hash"], row["audio"]), f"unit {unit}")
                    self.assertIn(f"region={'NTSC' if standard == 'ntsc' else 'PAL'} system=Amiga deterministic=True", out)

    def test_the_t50_machines_have_the_memory_domains_of_their_preset(self):
        for machine, sizes in self.T50_MACHINES.items():
            with self.subTest(machine=machine):
                out = self.core("domains", "--firmware", str(self.dir / "chipcell.rom"), "--machine", machine)
                names = ",".join([*sizes, "ROM", "Waterbox PageData"])
                self.assertIn(f"domains {names}", out)
                for name, size in sizes.items():
                    self.assertIn(f"domain {name} size={size} writable=True", out)
                if "Slow RAM" not in sizes:
                    self.assertNotIn("Slow RAM size=", out)
                self.assertIn("domain ROM size=524288 writable=False", out)
                self.assertIn("chip-marker 50364d57", out)
                self.assertEqual(4, out.count("ok an out-of-range access throws"))

    def test_a_guest_that_programs_ntsc_timing_on_a_pal_a600_changes_the_vsync_the_core_reports(self):
        # T23f: BizHawk takes the frame rate from every unit that carries a picture, so the rate of the beam the guest programs (262 lines of 227.5 clocks
        # on the PAL clock, 3546895 / 59605 Hz) reaches VsyncNumerator / VsyncDenominator, unit for unit as p6_headless reports it.
        units = 14
        rows, _ = self.av("to-ntsc", units, "--machine", "a600")
        native = self.headless_lines("to-ntsc", units, "--machine", "a600")
        self.assertEqual(units, len(rows))
        self.assertEqual([f"{cn}/{cd}" for (_, _, _, _, _, _, _, _, cn, cd, _, _) in native], [row["vsync"] for row in rows])
        # The program writes BEAMCON0 before the first picture, so every unit is already at the NTSC beam: 59.51 Hz, not the 50.08 Hz of the PAL configuration.
        self.assertEqual({row["vsync"] for row in rows}, {"709379/11921"})
        self.assertNotEqual(rows[0]["vsync"], "3546895/70824")

    def test_the_pcm_is_the_one_the_waterbox_harness_delivers(self):
        units = 40
        _, out = self.av("lace", units)
        _, result = build_waterbox.run_puae6(self.dir / "lace.rom", units)
        total = re.search(r"^audio frames=(\d+) hash=(\w+)$", out, re.M)
        self.assertEqual((result["audio"]["frames"], result["audio"]["fnv"]), (int(total[1]), total[2]))

    def test_silencing_picture_and_sound_for_a_stretch_changes_nothing_that_follows(self):
        units = 60
        loud, _ = self.av("lace", units)
        quiet, out = self.av("lace", units, "--silent", "20-40")
        for a, b in zip(loud, quiet):
            unit = int(a["unit"])
            self.assertEqual(a["ticks"], b["ticks"], f"unit {unit}")
            if 20 <= unit <= 40:
                self.assertEqual("0", b["audio"], f"unit {unit}: no sound was asked for")
            else:
                self.assertEqual((a["hash"], a["audio"], a["pcm"]), (b["hash"], b["audio"], b["pcm"]), f"unit {unit}")

    def test_lag_follows_the_guests_reads_and_the_drive_light_follows_the_motor(self):
        rows, _ = self.av("lace", 6)                         # reads no input register and has no motor on
        self.assertTrue(all(r["lag"] == "1" and r["light"] == "0" for r in rows))
        self.assertEqual(["1", "2", "3", "4", "5", "6"], [r["lagcount"] for r in rows])
        rows, _ = self.av("disk", 6)                         # reads PRA at every vertical blank and switches the motor on
        self.assertTrue(all(r["lag"] == "0" and r["light"] == "1" for r in rows[1:]))
        self.assertEqual("0", rows[-1]["lagcount"])

    # --- WB04 ---------------------------------------------------------------------------------------------------------------------------

    def test_two_machines_taking_turns_do_what_each_does_alone_and_one_goes_on_without_the_other(self):
        script = combo_script(14)
        path = self.dir / "two.txt"
        path.write_text("\n".join(script) + "\n")
        out = self.core("two", "--firmware", str(self.dir / "input_all.rom"), "--script", str(path), "--units", "14")
        # A plays the script with seed 1, B has seed 7 and nothing pressed; each is compared with the same machine alone.
        a_alone, a_ticks = self.play(script, 14)
        b_alone, b_ticks = self.play([line(**IDLE)] * 14, 14, seed=7)
        b_longer, b_longer_ticks = self.play([line(**IDLE)] * 17, 17, seed=7)

        def words(label):
            return [int(w, 16) for w in re.search(rf"^{label} (.*)$", out, re.M)[1].split()]

        self.assertEqual(a_alone, words("log-a"), "A, with B next to it, is A alone")
        self.assertEqual(b_alone, words("log-b"), "B, with A next to it and its input, is B alone")
        self.assertNotEqual(words("log-a"), words("log-b"), "B did not see A's input")
        self.assertEqual(b_longer, words("log-b-after"), "B goes on, three units after A was disposed, as B alone does")
        self.assertEqual(a_ticks, [int(m) for m in re.findall(r"^two \d+ a=(\d+)", out, re.M)])
        self.assertEqual(b_longer_ticks, [int(m) for m in re.findall(r"^two \d+ (?:a=\d+ )?b=(\d+)", out, re.M)])

    # --- WB07 ---------------------------------------------------------------------------------------------------------------------------

    def test_the_memory_domains_are_the_machines_and_pokes_before_the_first_unit_go_through_the_guest(self):
        out = self.core("domains", "--firmware", str(self.dir / "chipcell.rom"))
        self.assertIn("domains Chip RAM,Slow RAM,ROM,Waterbox PageData", out)
        self.assertIn("domain Chip RAM size=524288 writable=True", out)
        self.assertIn("domain Slow RAM size=524288 writable=True", out)
        self.assertIn("domain ROM size=524288 writable=False", out)
        self.assertIn("chip-marker 50364d57", out)
        self.assertEqual(4, out.count("ok an out-of-range access throws"))

    def test_the_a600_has_one_mebibyte_of_chip_ram_and_the_rom_and_no_slow_ram_domain_at_all(self):
        out = self.core("domains", "--firmware", str(self.dir / "chipcell.rom"), "--machine", "a600")
        self.assertIn("domains Chip RAM,ROM,Waterbox PageData", out)
        self.assertNotIn("Slow RAM size=", out)
        self.assertIn("domain Chip RAM size=1048576 writable=True", out)
        self.assertIn("domain ROM size=524288 writable=False", out)
        self.assertIn("scratch Chip RAM base=524288", out, "the pokes before the first unit went to the half of Chip RAM above 512 KiB, which the fixture leaves alone")
        self.assertIn("chip-marker 50364d57", out)
        self.assertEqual(4, out.count("ok an out-of-range access throws"))

    def test_the_a1200_has_two_mebibytes_of_chip_ram_and_the_rom_and_no_slow_ram_domain_at_all(self):
        out = self.core("domains", "--firmware", str(self.dir / "chipcell.rom"), "--machine", "a1200")
        self.assertIn("domains Chip RAM,ROM,Waterbox PageData", out)
        self.assertNotIn("Slow RAM size=", out)
        self.assertIn("domain Chip RAM size=2097152 writable=True", out)
        self.assertIn("domain ROM size=524288 writable=False", out)
        self.assertIn("scratch Chip RAM base=524288", out)
        self.assertIn("chip-marker 50364d57", out)
        self.assertEqual(4, out.count("ok an out-of-range access throws"))

    def test_the_fast_ram_board_and_the_other_sizes_are_domains_of_the_machine_that_has_them(self):
        # T49: the Zorro II board is a domain of its own at its real size, between Slow RAM and the ROM; the pokes before the first unit go through the guest into its clean pages.
        cases = ((("--machine", "a1200", "--fast", "MB8"), "domains Chip RAM,Fast RAM,ROM,Waterbox PageData", {"Chip RAM": 2097152, "Fast RAM": 8388608}),
                 (("--machine", "a600", "--chip", "MB2", "--fast", "MB8"), "domains Chip RAM,Fast RAM,ROM,Waterbox PageData", {"Chip RAM": 2097152, "Fast RAM": 8388608}),
                 (("--machine", "a500", "--fast", "MB2", "--slow", "MB1"), "domains Chip RAM,Slow RAM,Fast RAM,ROM,Waterbox PageData",
                  {"Chip RAM": 524288, "Slow RAM": 1048576, "Fast RAM": 2097152}),
                 (("--machine", "a500", "--slow", "None", "--chip", "MB1"), "domains Chip RAM,ROM,Waterbox PageData", {"Chip RAM": 1048576}))
        for flags, names, sizes in cases:
            with self.subTest(flags=flags):
                out = self.core("domains", "--firmware", str(self.dir / "chipcell.rom"), *flags)
                self.assertIn(names, out)
                for name, size in sizes.items():
                    self.assertIn(f"domain {name} size={size} writable=True", out)
                if "Fast RAM" in sizes:
                    self.assertIn("scratch Fast RAM base=0", out)
                self.assertIn("chip-marker 50364d57", out)
                self.assertEqual(4, out.count("ok an out-of-range access throws"))


if __name__ == "__main__":
    unittest.main()
