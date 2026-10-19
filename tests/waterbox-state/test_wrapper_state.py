"""T21b: the state of the PUAE6 core in BizHawk's Waterbox host, proved with the real sandbox snapshot (never the native serializer).

A state is an envelope (what it is valid for, its length and its checksum) around the wrapper's own fields and the snapshot of the sandbox. The tests drive several cores
in one process with a small program language (tests/ffi/Session.cs) and compare what BizHawk sees: per unit the ticks, the picture's hash, the audio and its hash, lag, drive
light and the media events, and the SHA-256 of the whole state (hardware and wrapper) as the digest of the machine.

WB03  save at N, go on M units, load, go on M again: the same units and the same digest, at moments that matter (a disk read by DMA, a swap pending, a key held).
WB04  a state made by A loaded in a new sandbox B, in a third C, and the three taking turns: the same units and digest; buffers and sandboxes live at other addresses.
WB05  the same around the disk motor and DMA, audio, a held key and a pending swap, with a disk in the drive and with the swap button held.
WB06  every field of the wrapper that has a causal effect changes between the save and the load and has its effect back after the load (the mouse reference, the Caps Lock
      latch and the held-button flags, the selected drive and slot, the held disk buttons, the media sequence, the counters).
WB07  states that are cut, bent or oversized are refused without reading past what they declare, and an accepted state ignores bytes after its end.
WB11  a state of another build, heaps, settings, Kickstart, disks, of garbage, of nothing, truncated or altered anywhere is refused with its reason, and the instance that
      refused it goes on exactly as one that was never asked.
WB14  pokes through the memory domains into pages nothing wrote since the seal, and into dirty ones, are undone by a restore.
WB15  a state written by one process is loaded by another, new one, and the units that follow are those of the process that wrote it.

Needs the Waterbox runtime and the guest (tools/build_waterbox.py --puae6-run build/waterbox/boot.rom), dotnet and the local NuGet cache; skips without them.
    python3 -m unittest discover -s tests/waterbox-state -p "test_wrapper_state.py"
"""
import importlib.util
import json
from pathlib import Path
import re
import struct
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests/waterbox-smoke"))
import build_waterbox  # noqa: E402
from tests.host.test_bridge_lifecycle import TOOLS, builder  # noqa: E402
from tests.input.test_input import CAPS, ESC, FIXTURES, JOYSTICK, KEY_Q, LEFT, MOUSE, RIGHT, UP, B1, line  # noqa: E402
from tests.input.test_cd32 import ROM as CD32_ROM  # noqa: E402
from tests.input.test_parallel import ROM as PARALLEL_ROM  # noqa: E402
from tests.input.test_pointer import ROM as POINTER_ROM  # noqa: E402
from tests.input.test_mouse2 import ROM as MOUSE2_ROM  # noqa: E402
from tests.audio.test_drive_sound import MECHANICAL  # noqa: E402
from tests.scenarios.disk import disk  # noqa: E402
from test_puae6 import runtime_available  # noqa: E402
multidrive = importlib.import_module("tests.libretro-host.test_multidrive")

GUEST = build_waterbox.BUILD / "puae6/release/puae6.wbx"
RECEIPT = build_waterbox.BUILD / "puae6/puae6-receipt.json"
IDLE = dict(p0=(MOUSE, 0, 0, 0), p1=(JOYSTICK, 0, 0, 0))
SWAP = f"{disk.MOUNT_UNIT - 1}:Next_Slot;{disk.MOUNT_UNIT}:Insert_Disk"   # underscores stand for spaces in a program line
UNIT = re.compile(r"^u (\S+) (\d+) ticks=(\d+) hash=(\w+) audio=(\d+) pcm=(\w+) lag=(\d) light=(\d) events=(\d+)$")


def program_text(lines):
    return "\n".join(lines) + "\n"


@unittest.skipUnless(runtime_available() and TOOLS, "Waterbox runtime or puae6.wbx not built (tools/build_waterbox.py --puae6-run build/waterbox/boot.rom)")
class WrapperStateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sha = json.loads(RECEIPT.read_text())["guest"]["sha256"]
        cls.tmp = tempfile.TemporaryDirectory(prefix="p6-state-")
        cls.dir = Path(cls.tmp.name)
        for name, source in (("disk", disk.rom_source()), ("input_all", (FIXTURES / "input_all.s").read_text(encoding="utf-8")),
                             ("chipcell", (ROOT / "tests/waterbox-smoke/chipcell.s").read_text(encoding="utf-8")), ("cd32", CD32_ROM),
                             ("four", multidrive.rom_source()), ("mouse2", MOUSE2_ROM), ("pointer", POINTER_ROM), ("mechanical", MECHANICAL), ("parallel", PARALLEL_ROM)):
            (cls.dir / f"{name}.rom").write_bytes(builder.build(source))
        for name, label in (("A", 0x41), ("B", 0x42), ("C", 0x43), ("D", 0x44)):
            (cls.dir / f"{name}.adf").write_bytes(disk.make_adf(label))
        cls.count = 0

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    # --- helpers --------------------------------------------------------------------------------------------------------------------

    def rom(self, name):
        return str(self.dir / f"{name}.rom")

    def adfs(self, names="A,B"):
        return ",".join(str(self.dir / f"{n}.adf") for n in names.split(",") if n)

    def script(self, name, lines):
        path = self.dir / f"{name}.txt"
        path.write_text(program_text(lines))
        return str(path)

    def session(self, lines, firmware="disk"):
        WrapperStateTests.count += 1
        path = self.dir / f"program-{WrapperStateTests.count}.txt"
        path.write_text(program_text(lines))
        result = build_waterbox.run_ffi("session", "--runtime", "libwaterboxhost.dylib", "--guest", str(GUEST), "--sha", self.sha, "--firmware", self.rom(firmware),
                                        "--program", str(path))
        self.assertEqual(0, result.returncode, f"{result.stdout[-2500:]}\n{result.stderr[-2500:]}")
        return result.stdout

    @staticmethod
    def units(out, name):
        return [m.groups() for m in map(UNIT.match, out.splitlines()) if m and m[1] == name]

    @staticmethod
    def digests(out, name):
        return {m[1]: m[2] for m in re.finditer(rf"^digest {name} (\S*) sha=(\w+) ", out, re.M)}

    def core(self, name, adf="A,B", **options):
        extra = "".join(f" {k}={v}" if v is not True else f" {k}" for k, v in options.items())
        return f"core {name} adf={self.adfs(adf)}{extra}" if adf else f"core {name}{extra}"

    # --- WB03 and WB05 -------------------------------------------------------------------------------------------------------------

    def held_keys(self):
        """A key held from unit 50 on and the mouse moving: input that a state has to carry along."""
        return self.script("held", [line(keys=[ESC] if u >= 50 else [], p0=(MOUSE, 0, 1 if u % 7 == 0 else 0, 0), p1=(JOYSTICK, 0, 0, 0)) for u in range(1, 400)])

    def test_save_go_on_load_and_repeat_gives_the_same_units_and_the_same_machine(self):
        script = self.held_keys()
        # a disk read by DMA (units 26-52), the swap pending (80-180), a read after the swap (about 190), the motor on throughout, a key held from 50
        for n in (10, 40, 55, 85, 130, 185):
            with self.subTest(saved_at=n):
                m = 100
                out = self.session([self.core("A"), f"run A {n} script={script} press={SWAP}", "save A s", f"run A {m} script={script} press={SWAP}", "digest A first",
                                    "load A s", f"run A {m} script={script} press={SWAP}", "digest A second"])
                units = self.units(out, "A")
                self.assertEqual(n + 2 * m, len(units))
                first, second = units[n:n + m], units[n + m:]
                self.assertEqual([u[1:] for u in first], [u[1:] for u in second], "every unit after the load is the one that followed the save")
                digests = self.digests(out, "A")
                self.assertEqual(digests["first"], digests["second"], "and so is the machine at the end (hardware and wrapper)")
                self.assertEqual(n + m, int(re.search(r"^digest A first .* frame=(\d+)", out, re.M)[1]))

    def test_the_second_load_of_a_state_is_as_good_as_the_first_after_a_hundred_cycles(self):
        script = self.held_keys()
        lines = [self.core("A"), f"run A 60 script={script} press={SWAP}", "save A s"]
        for _ in range(100):
            lines += [f"run A 3 script={script} press={SWAP}", "load A s"]
        lines += [f"run A 40 script={script} press={SWAP}", "digest A last", "load A s", f"run A 40 script={script} press={SWAP}", "digest A again"]
        out = self.session(lines)
        digests = self.digests(out, "A")
        self.assertEqual(digests["last"], digests["again"])
        units = self.units(out, "A")
        self.assertEqual(units[-80:-40], units[-40:], "after a hundred alternations the state still takes the machine back")

    # --- WB04 -----------------------------------------------------------------------------------------------------------------------

    def test_a_state_of_one_sandbox_runs_in_a_new_one_and_in_a_third_as_in_the_first_and_they_can_take_turns(self):
        script = self.held_keys()
        n, m = 85, 60
        lines = [self.core("A"), f"run A {n} script={script} press={SWAP}", "save A s",
                 # B and C are created after A ran, and C runs 17 units of its own before it loads.
                 self.core("B"), "load B s", self.core("C"), "run C 17", "load C s",
                 f"run A {m} script={script} press={SWAP}", f"run B {m} script={script} press={SWAP}", f"run C {m} script={script} press={SWAP}",
                 "digest A a", "digest B b", "digest C c", "load B s", "load C s", "load A s"]
        for turn in range(4):                                        # taking turns, six units each
            for name in "ABC":
                lines.append(f"run {name} 6 script={script} press={SWAP}")
        lines += ["digest A a2", "digest B b2", "digest C c2"]
        out = self.session(lines)
        a = [u[1:] for u in self.units(out, "A")]
        b, c = [u[1:] for u in self.units(out, "B")], [u[1:] for u in self.units(out, "C")][17:]
        self.assertEqual(a[n:n + m], b[:m], "B, a new sandbox that loaded A's state, continues as A does")
        self.assertEqual(a[n:n + m], c[:m], "so does C, which had run units of its own before it loaded")
        digests = {**self.digests(out, "A"), **self.digests(out, "B"), **self.digests(out, "C")}
        self.assertEqual({digests["a"], digests["b"], digests["c"]}, {digests["a"]})
        self.assertEqual({digests["a2"], digests["b2"], digests["c2"]}, {digests["a2"]})
        self.assertEqual(a[n + m:], b[m:], "taking turns changes nothing")
        self.assertEqual(a[n + m:], c[m:])

    def test_a_state_made_before_the_first_unit_goes_to_a_new_sandbox_too(self):
        out = self.session([self.core("A"), "save A start", self.core("B"), "load B start", "run A 30", "run B 30", "digest A a", "digest B b"])
        self.assertEqual([u[1:] for u in self.units(out, "A")], [u[1:] for u in self.units(out, "B")])
        self.assertEqual(self.digests(out, "A")["a"], self.digests(out, "B")["b"])

    def test_drive_volume_is_live_host_preference_and_restore_keeps_mechanical_phase(self):
        movie = self.dir / "drive-sound.bk2"
        out = self.session([self.core("A", adf="", drivevolume=100), "run A 5 record", "save A motor", "run A 9 record", "digest A first", f"endmovie A {movie}",
                            "peek A Chip_RAM 0 1024", "drivevolume A 0", "load A motor", "drivevolume A", "run A 4", "drivevolume A 100", "run A 5", "digest A returned",
                            "peek A Chip_RAM 0 1024", "drivevolume A 101", "drivevolume A -1", "load A motor", "run A 9", "digest A replay",
                            self.core("B", adf="", drivevolume=50), "load B motor", "drivevolume B", f"moviecheck B {movie}", "drivevolume B 100", "run B 9", "digest B fresh"], firmware="mechanical")
        a, b = ([u[1:] for u in self.units(out, n)] for n in "AB")
        self.assertEqual(32, len(a))
        # PCM is field 4; ticks, picture, cadence, lag and events agree while muted.
        self.assertEqual([u[:4] + u[5:] for u in a[5:14]], [u[:4] + u[5:] for u in a[14:23]])
        self.assertTrue(any(u[4] != v[4] for u, v in zip(a[5:9], a[14:18])))
        self.assertEqual(a[9:14], a[18:23], "returning to 100 keeps continuous sample phase")
        self.assertEqual(a[5:14], a[23:])
        self.assertEqual(a[5:14], b)
        self.assertEqual(["0", "0", "100", "100", "100"], re.findall(r"^volume A (\d+)$", out, re.M), "load and invalid updates preserve the current setting")
        self.assertIn("volume B 50", out)
        self.assertEqual(2, out.count("rejected volume A"))
        self.assertIn("movie accepted B", out, "volume is outside BK2 identity")
        peeks = re.findall(r"^peek A Chip_RAM 0 (\w+)$", out, re.M)
        self.assertEqual(2, len(peeks))
        self.assertEqual(peeks[0], peeks[1])
        digests = {**self.digests(out, "A"), **self.digests(out, "B")}
        self.assertEqual(4, len(digests))
        self.assertEqual(1, len(set(digests.values())), "same volume and phase give same complete state")

    def test_four_distinct_boot_disks_restore_reads_and_the_complete_wrapper_in_a_new_sandbox(self):
        n, end = multidrive.SAVE, multidrive.UNITS

        def captures(name):
            return [f"peek {name} Chip_RAM 6000 8"] + [
                f"peek {name} Chip_RAM {multidrive.BASE + drive * multidrive.STEP:x} {2 * multidrive.td.CAPTURE_WORDS}"
                for drive in range(4)]

        out = self.session([self.core("A", adf="A,B,C,D", drives=4), f"run A {n}", "save A four", f"run A {end - n}", "digest A first",
                            *captures("A"),
                            # Change the selected drive, eject DF3 and change the slot
                            # before restoring all four drive identities and held edges.
                            "run A 8 press=221:Next_Drive;223:Next_Drive;225:Next_Drive;227-228:Eject_Disk;227-228:Next_Slot",
                            "load A four", f"run A {end - n}", "digest A replay", *captures("A"),
                            self.core("B", adf="A,B,C,D", drives=4), "load B four", f"run B {end - n}", "digest B fresh", *captures("B")], firmware="four")
        a = [u[1:] for u in self.units(out, "A")]
        self.assertEqual(end + 8 + end - n, len(a))
        self.assertEqual(a[n:end], a[end + 8:], "same sandbox: every unit, picture, PCM and media event")
        self.assertEqual(a[n:end], [u[1:] for u in self.units(out, "B")], "new sandbox: every unit, picture, PCM and media event")
        digests = {**self.digests(out, "A"), **self.digests(out, "B")}
        self.assertEqual({"first", "replay", "fresh"}, set(digests))
        self.assertEqual(1, len(set(digests.values())), "whole sandbox and wrapper, including DF2/DF3, selection and held edges")
        peeks = re.findall(r"^peek [AB] Chip_RAM (\w+) ([0-9a-f]+)$", out, re.M)
        self.assertEqual(15, len(peeks))
        self.assertEqual(peeks[:5], peeks[5:10])
        self.assertEqual(peeks[:5], peeks[10:])
        self.assertEqual(bytes.fromhex(peeks[0][1]), b"\xca\xfe" * 4, "all four DMA reads completed")
        for drive, (_, raw) in enumerate(peeks[1:5]):
            found = disk.decode_capture(struct.unpack(f">{multidrive.td.CAPTURE_WORDS}H", bytes.fromhex(raw)))
            self.assertGreaterEqual(len(found), 10, f"DF{drive} did not read a track")
            image = disk.make_adf(0x41 + drive)
            for (track, sector), payload in found.items():
                self.assertEqual(track, 0)
                self.assertEqual(payload, disk.adf_sector(image, track, sector), f"DF{drive} read its own disk")

    # --- WB06 -----------------------------------------------------------------------------------------------------------------------

    def effect(self, prefix, divergence, suffix, save_at, units, firmware="disk", core=None, **core_options):
        """A runs the prefix up to `save_at`, saves, runs 25 units of the divergence, loads and runs `units` units of the suffix. The control K runs the same prefix and the same
        suffix and was never saved or loaded. Each of prefix, divergence and suffix is a (script, press) pair that says what is pressed in each unit by the unit's number, so
        after the load, which goes back to `save_at`, the suffix is played from there. What the load restores shows as A equal to K: in every unit and in the machine."""
        core = core or self.core

        def run(name, spec, count):
            script, press = spec
            return f"run {name} {count}" + (f" script={script}" if script else "") + (f" press={press}" if press else "")
        a = [core("A", **core_options), run("A", prefix, save_at), "save A s", run("A", divergence, 25), "load A s", run("A", suffix, units), "digest A a"]
        k = [core("K", **core_options), run("K", prefix, save_at), run("K", suffix, units), "digest K k"]
        out = self.session(a + k, firmware=firmware)
        got = [u[1:] for u in self.units(out, "A")][save_at + 25:]
        want = [u[1:] for u in self.units(out, "K")][save_at:]
        self.assertEqual(units, len(got))
        self.assertEqual(want, got)
        digests = {**self.digests(out, "A"), **self.digests(out, "K")}
        self.assertEqual(digests["k"], digests["a"], "the machine and the wrapper are the control's, byte for byte")
        return out, got

    def test_the_mouse_reference_is_part_of_the_state(self):
        def mouse(moves):
            return self.script(f"mouse-{abs(hash(tuple(moves.items())))}", [line(p0=(MOUSE, 0, moves.get(u, 0), 0), p1=(JOYSTICK, 0, 0, 0)) for u in range(1, 120)])
        prefix = (mouse({3: 20, 4: 20}), None)                        # the pointer ends 40 counts from the middle
        divergence = (mouse({3: 20, 4: 20, 12: 120, 13: 120, 14: 120, 15: 120}), None)    # and then goes far away
        suffix = (mouse({3: 20, 4: 20, 30: 25}), None)                # a move of 25 from where the state was saved
        self.effect(prefix, divergence, suffix, save_at=10, units=40, firmware="input_all", core=lambda n, **o: self.core(n, adf="", **o))

    def test_the_caps_lock_latch_and_the_button_held_across_a_load_are_part_of_the_state(self):
        caps = "Key_Caps_Lock"
        for name, prefix_press, divergence_press, suffix_press, at in (
                ("latch", f"5:{caps}", f"5:{caps};15:{caps}", f"5:{caps};20:{caps}", 10),      # saved latched; the divergence unlatched it; the suffix presses again
                ("held", f"8-14:{caps}", f"8-11:{caps}", f"8-14:{caps}", 11)):                 # saved with the button down; the divergence released it
            with self.subTest(what=name):
                out, units = self.effect((None, prefix_press), (None, divergence_press), (None, suffix_press), save_at=at, units=40, firmware="input_all",
                                         core=lambda n, **o: self.core(n, adf="", **o))

    def test_the_selected_drive_and_slot_and_the_held_disk_buttons_are_part_of_the_state(self):
        # selected slot: B was selected when the state was saved, the divergence selected A again, the suffix inserts: B must be what goes in
        _, units = self.effect((None, "60:Next_Slot"), (None, "60:Next_Slot;70:Next_Slot"), (None, "60:Next_Slot;100:Insert_Disk"), save_at=65, units=140)
        self.assertEqual("2", units[-1][-1], "the boot disk and the insert")
        # selected drive (two drives): DF1 was selected, the divergence moved to DF0, the suffix ejects: it is DF1 that must be emptied, not the disk the guest is reading
        self.effect((None, "60:Next_Drive"), (None, "60:Next_Drive;70:Next_Drive"), (None, "60:Next_Drive;100:Eject_Disk"), save_at=65, units=140, drives=2)
        # held buttons: down when the state was saved, released by the divergence, down again in the suffix: a button that is held does not act again. The ones that only select
        # are followed by an action that uses the selection, so that a flag that was lost shows in what the action does.
        held = (("Insert_Disk", "79:Next_Slot;80-120:Insert_Disk", "79:Next_Slot;80-84:Insert_Disk"), ("Eject_Disk", "80-120:Eject_Disk", "80-84:Eject_Disk"),
                ("Next_Slot", "79-120:Next_Slot;130:Insert_Disk", "79-84:Next_Slot;130:Insert_Disk"), ("Next_Drive", "79-120:Next_Drive;130:Eject_Disk", "79-84:Next_Drive;130:Eject_Disk"))
        for button, down, released in held:
            with self.subTest(held=button):
                self.effect((None, down), (None, released), (None, down), save_at=85, units=140, drives=2)

    def test_the_media_sequence_and_the_counters_are_part_of_the_state(self):
        out, units = self.effect((None, SWAP), (None, f"{SWAP};105:Eject_Disk;110:Insert_Disk"),      # two more events between the save and the load
                                 (None, f"{SWAP};120:Eject_Disk;125:Insert_Disk"), save_at=100, units=160)
        self.assertEqual("4", units[-1][-1], "boot disk, insert, eject, insert: the sequence goes on from the saved one")

    # --- WB11 -----------------------------------------------------------------------------------------------------------------------

    def test_a_state_of_another_build_heaps_settings_firmware_or_disks_is_refused_and_the_instance_goes_on_as_if_never_asked(self):
        script = self.held_keys()
        base = [self.core("A"), f"run A 70 script={script} press={SWAP}", "save A s"]
        cases = [
            ("other build of the guest", ["mutate s bad flip:10"], "OtherGuest", self.core("T")),
            ("other heaps", ["mutate s bad flip:44"], "OtherHeaps", self.core("T")),
            ("other region", [], "OtherConfig", self.core("T", ntsc=True)),
            ("other seed", [], "OtherConfig", self.core("T", seed=2)),
            ("other drives", [], "OtherConfig", self.core("T", drives=2)),
            ("other ports", [], "OtherConfig", self.core("T", port1="joystick")),
            ("other disks", [], "OtherMedia", self.core("T", adf="B,A")),
            ("fewer disks", [], "OtherMedia", self.core("T", adf="A")),
            ("no disks", [], "OtherMedia", self.core("T", adf="")),
            ("another firmware", [], "OtherFirmware", f"core T firmware={self.rom('chipcell')} adf={self.adfs()}"),
        ]
        for what, edits, reason, target in cases:
            with self.subTest(what=what):
                lines = base + edits + [target, f"run T 12 script={script} press={SWAP}", "digest T before",
                                        f"load T {'bad' if edits else 's'}", f"run T 25 script={script} press={SWAP}", "digest T after",
                                        # the twin never saw the state
                                        target.replace("core T", "core W"), f"run W 12 script={script} press={SWAP}", f"run W 25 script={script} press={SWAP}", "digest W twin"]
                out = self.session(lines)
                self.assertRegex(out, rf"rejected T (bad|s) reason={reason} message=", what)
                t, w = [u[1:] for u in self.units(out, "T")], [u[1:] for u in self.units(out, "W")]
                self.assertEqual(w, t, "the instance that refused the state is the instance that never saw it")
                d = {**self.digests(out, "T"), **self.digests(out, "W")}
                self.assertEqual(d["twin"], d["after"])

    def test_states_that_are_cut_bent_oversized_empty_or_not_ours_are_refused_with_their_reason_and_nothing_changes(self):
        script = self.held_keys()
        header = 4 + 4 + 32 + 20 + 32 + 32 + 32 + 8 + 32
        base = [self.core("A"), f"run A 70 script={script} press={SWAP}", "save A s"]
        cases = [("empty", ["mutate s bad truncate:0"], "NotAState"), ("three bytes", ["mutate s bad truncate:3"], "NotAState"),
                 ("magic", ["mutate s bad magic"], "NotAState"), ("garbage", ["garbage bad 4096"], "NotAState"), ("big garbage", ["garbage bad 3000000"], "NotAState"),
                 ("version", ["mutate s bad version:7"], "WrongVersion"), ("cut in the header", ["mutate s bad truncate:60"], "Truncated"),
                 ("cut after the header", ["mutate s bad truncate:%d" % (header + 10)], "Truncated"),
                 ("one byte short", ["mutate s bad truncate:-1"], "Truncated"),
                 ("length too big", ["mutate s bad length:9999999999"], "TooLarge"), ("length one more than there is", ["mutate s bad length:LEN+1"], "Truncated"),
                 ("a bit of the payload", ["mutate s bad flip:%d" % (header + 300)], "Corrupt"), ("a bit of the checksum", ["mutate s bad flip:%d" % (header - 5)], "Corrupt"),
                 ("the declared length is shorter", ["mutate s bad length:LEN-1"], "Corrupt"), ("a bit of the last byte", ["mutate s bad flip:LAST"], "Corrupt")]
        for what, edits, reason in cases:
            with self.subTest(what=what):
                out = self.session(base + ["digest A before"] + edits + ["load A bad", f"run A 20 script={script} press={SWAP}", "digest A after",
                                                                       "load A s", f"run A 20 script={script} press={SWAP}", "digest A back"])
                self.assertRegex(out, rf"rejected A bad reason={reason} message=", what)
                units = [u[1:] for u in self.units(out, "A")]
                self.assertEqual(units[70:90], units[90:110], "the machine went on as if nothing had been asked, and the good state still takes it back")

    def test_a_state_with_the_right_checksum_and_fields_that_cannot_be_is_refused_before_the_sandbox_is_touched(self):
        script = self.held_keys()
        # offsets in the payload: Frame 0, LagCount 4, IsLagFrame 8, CycleCount 9, BufferWidth 17, BufferHeight 21, VsyncNumerator 25, VsyncDenominator 29, AspectNum 33,
        # AspectDen 37, DriveLightOn 41, MouseKnown 42, MouseX 43, MouseY 47, CapsLatched 51, CapsHeld 52, Disk0 53, Disk1 57, Disk2 61, Disk3 65, CurrentDrive 69, CurrentSlot 73
        bad = [("a negative frame", "offset=0 bytes=ffffffff"), ("a picture wider than the buffers", "offset=17 bytes=ffffff7f"), ("a cadence of zero", "offset=25 bytes=00000000"),
               ("a disk that does not exist", "offset=53 bytes=05000000"), ("a drive that does not exist", "offset=69 bytes=02000000"),
               ("a slot past the disks", "offset=73 bytes=09000000"), ("a payload that ends inside the fields", "offset=0 bytes=00 length=20")]
        for what, edit in bad:
            with self.subTest(what=what):
                out = self.session([self.core("A"), f"run A 70 script={script} press={SWAP}", "save A s", f"repack s bad {edit}", "load A bad",
                                    f"run A 20 script={script} press={SWAP}", "load A s", f"run A 20 script={script} press={SWAP}"])
                self.assertRegex(out, r"rejected A bad reason=BadFields message=")
                units = [u[1:] for u in self.units(out, "A")]
                self.assertEqual(units[70:90], units[90:110], "a refused state left the machine and the wrapper as they were")

    def test_bytes_after_the_end_of_a_state_are_not_read_and_an_accepted_state_equals_the_one_saved(self):
        script = self.held_keys()
        out = self.session([self.core("A"), f"run A 70 script={script} press={SWAP}", "save A s", "mutate s grown grow:5000", f"run A 30 script={script} press={SWAP}", "digest A one",
                            "load A grown", f"run A 30 script={script} press={SWAP}", "digest A two"])
        self.assertIn("loaded A grown", out)
        self.assertEqual(self.digests(out, "A")["one"], self.digests(out, "A")["two"])

    # --- WB14 -----------------------------------------------------------------------------------------------------------------------

    def test_pokes_through_the_memory_domains_into_clean_and_dirty_pages_are_undone_by_a_restore(self):
        out = self.session([self.core("A", adf=""), "peek A Slow_RAM 0 16", "peek A Slow_RAM 1000 16", "peek A Slow_RAM 7fff0 16", "save A start",
                            "poke A Slow_RAM 0 deadbeefdeadbeef", "poke A Slow_RAM 1000 0123456789abcdef", "poke A Slow_RAM 7fff0 cafecafe",
                            "peek A Slow_RAM 0 16", "peek A Slow_RAM 1000 16", "peek A Slow_RAM 7fff0 16",
                            "load A start", "peek A Slow_RAM 0 16", "peek A Slow_RAM 1000 16", "peek A Slow_RAM 7fff0 16",
                            "run A 15", "save A later", "peek A Chip_RAM 50000 4", "poke A Chip_RAM 50000 11223344", "peek A Chip_RAM 50000 4", "load A later", "peek A Chip_RAM 50000 4"],
                           firmware="chipcell")
        peeks = [m[3] for m in re.finditer(r"^peek A (\S+) (\S+) (\w+)$", out, re.M)]
        before, poked, restored = peeks[0:3], peeks[3:6], peeks[6:9]
        self.assertEqual(["deadbeefdeadbeef", "0123456789abcdef", "cafecafe"], [p[:len(w)] for p, w in zip(poked, ("deadbeefdeadbeef", "0123456789abcdef", "cafecafe"))])
        self.assertEqual(before, restored, "a restore brought the pages nothing had written since the seal back")
        chip_before, chip_poked, chip_restored = peeks[9:12]
        self.assertEqual("11223344", chip_poked, "a poke into Chip RAM, which the machine has written, is visible")
        self.assertEqual(chip_before, chip_restored, "and a restore takes it back")

    # --- WB15 -----------------------------------------------------------------------------------------------------------------------

    def test_a_state_written_by_one_process_is_loaded_by_another_new_one_and_continues_the_same(self):
        script = self.held_keys()
        state = self.dir / "process.state"
        first = self.session([self.core("A"), f"run A 85 script={script} press={SWAP}", f"savefile A {state}", f"run A 70 script={script} press={SWAP}", "digest A end"])
        second = self.session([self.core("B"), f"loadfile B {state}", f"run B 70 script={script} press={SWAP}", "digest B end"])
        a, b = [u[1:] for u in self.units(first, "A")][85:], [u[1:] for u in self.units(second, "B")]
        self.assertEqual(a, b)
        self.assertEqual(self.digests(first, "A")["end"], self.digests(second, "B")["end"])

    def test_cd32_partial_serial_read_restores_and_replays_a_real_bk2_in_both_ports(self):
        # Three units leave both pads partway through their serial protocol. After
        # the save, change every button/direction, independently in each port.
        masks = (0x286, 0x501, 0x139, 0x6c2, 0, 0x7ff, 0x124, 0x691, 0x482, 0x241, 0x104)
        script = self.script("cd32", [line(p0=(3, m, 0, 0), p1=(3, masks[-i - 1], 0, 0)) for i, m in enumerate(masks)])
        state, movie = self.dir / "cd32.state", self.dir / "cd32.bk2"
        core = lambda name: self.core(name, adf="", port1="cd32", port2="cd32")
        out = self.session([core("A"), f"run A 3 script={script} record", "save A partial", f"savefile A {state}",
                            f"run A 8 script={script} record", "digest A first", "peek A Chip_RAM 1000 18",
                            f"endmovie A {movie}", f"movieverify A {movie} 11 script={script}",
                            "load A partial", f"run A 8 script={script}", "digest A restored", "peek A Chip_RAM 1000 18",
                            core("B"), "load B partial", f"run B 8 script={script}", "digest B fresh", "peek B Chip_RAM 1000 18",
                            core("C"), f"run C 11 movie={movie}", f"moviefinish C {movie}", "digest C movie", "peek C Chip_RAM 1000 18"], firmware="cd32")
        a = [u[1:] for u in self.units(out, "A")]
        self.assertEqual(19, len(a))
        self.assertEqual(a[3:11], a[11:])
        self.assertEqual(a[3:11], [u[1:] for u in self.units(out, "B")])
        self.assertEqual(a[:11], [u[1:] for u in self.units(out, "C")])
        digests = {**self.digests(out, "A"), **self.digests(out, "B"), **self.digests(out, "C")}
        self.assertEqual({"first", "restored", "fresh", "movie"}, set(digests))
        self.assertEqual(1, len(set(digests.values())), "whole wrapper and sandbox, including the serial shifters and CIA clock history")
        peeks = re.findall(r"^peek [ABC] Chip_RAM 1000 ([0-9a-f]+)$", out, re.M)
        self.assertEqual(4, len(peeks))
        self.assertEqual(1, len(set(peeks)), "the guest's serial bits, not just the unchanged black picture")
        self.assertRegex(out, r"movie verified frames=11 buttons=\d+ axes=\d+ compared=\d+ entries=11")
        self.assertIn("movie finished ok frames=11 ", out)
        other = self.session([core("D"), f"loadfile D {state}", f"run D 8 script={script}", "digest D process", "peek D Chip_RAM 1000 18"], firmware="cd32")
        self.assertEqual(a[3:11], [u[1:] for u in self.units(other, "D")])
        self.assertEqual(digests["first"], self.digests(other, "D")["process"])
        self.assertIn(f"peek D Chip_RAM 1000 {peeks[0]}", other)

    def test_two_mouse_references_motion_and_bk2_restore_in_same_and_new_sandbox_and_process(self):
        # At unit3 both absolute references still owe movement (>120 counts),
        # in opposite directions. Replaying needs both references, not just RAM.
        script = self.script("mouse2", [line(p0=(2, i % 8, 150 if i == 3 else 1 if i > 3 else 0, -2 if i > 3 else 0),
                                               p1=(2, 7 - i % 8, -180 if i == 3 else -3 if i > 3 else 0, 4 if i > 3 else 0)) for i in range(1, 12)])
        state, movie = self.dir / "mouse2.state", self.dir / "mouse2.bk2"
        core = lambda name: self.core(name, adf="", port1="mouse", port2="mouse")
        out = self.session([core("A"), f"run A 3 script={script} record", "save A moving", f"savefile A {state}",
                            # Wrapper: second reference appended at89, X at90; 98bytes in total.
                            "repack moving bad offset=90 bytes=ffffffff", "load A bad",
                            "repack moving cut offset=0 bytes=03 length=97", "load A cut",
                            f"run A 8 script={script} record", "digest A first", "peek A Chip_RAM 1000 80",
                            f"endmovie A {movie}", f"movieverify A {movie} 11 script={script}",
                            "load A moving", f"run A 8 script={script}", "digest A restored", "peek A Chip_RAM 1000 80",
                            core("B"), "load B moving", f"run B 8 script={script}", "digest B fresh", "peek B Chip_RAM 1000 80",
                            core("C"), f"run C 11 movie={movie}", f"moviefinish C {movie}", "digest C movie", "peek C Chip_RAM 1000 80"], firmware="mouse2")
        a = [u[1:] for u in self.units(out, "A")]
        self.assertEqual(19, len(a))
        self.assertEqual(a[3:11], a[11:])
        self.assertEqual(a[3:11], [u[1:] for u in self.units(out, "B")])
        self.assertEqual(a[:11], [u[1:] for u in self.units(out, "C")])
        digests = {**self.digests(out, "A"), **self.digests(out, "B"), **self.digests(out, "C")}
        self.assertEqual({"first", "restored", "fresh", "movie"}, set(digests))
        self.assertEqual(1, len(set(digests.values())))
        peeks = re.findall(r"^peek [ABC] Chip_RAM 1000 ([0-9a-f]+)$", out, re.M)
        self.assertEqual(4, len(peeks))
        self.assertEqual(1, len(set(peeks)))
        self.assertRegex(out, r"movie verified frames=11 buttons=\d+ axes=4 compared=\d+ entries=11")
        self.assertIn("movie finished ok frames=11 ", out)
        self.assertRegex(out, r"rejected A bad reason=BadFields message=")
        self.assertRegex(out, r"rejected A cut reason=BadFields message=")
        other = self.session([core("D"), f"loadfile D {state}", f"run D 8 script={script}", "digest D process", "peek D Chip_RAM 1000 80"], firmware="mouse2")
        self.assertEqual(a[3:11], [u[1:] for u in self.units(other, "D")])
        self.assertEqual(digests["first"], self.digests(other, "D")["process"])
        self.assertIn(f"peek D Chip_RAM 1000 {peeks[0]}", other)

    def test_optical_levels_beam_and_bk2_restore_in_same_and_new_sandbox_and_process(self):
        for device, port, name in ((4, 1, "lightgun"), (5, 0, "lightpen")):
            with self.subTest(device=name):
                frames = []
                for i in range(11):
                    pointer = (device, (1, 0, 2, 3)[(i // 3) % 4], -18000 if i < 5 else 18000, -8000 if i < 5 else 8000)
                    frames.append(line(p0=pointer if port == 0 else (0, 0, 0, 0), p1=pointer if port == 1 else (0, 0, 0, 0)))
                script = self.script(name, frames)
                state, movie = self.dir / f"{name}.state", self.dir / f"{name}.bk2"
                core = lambda n: self.core(n, adf="", port1=name if port == 0 else "none", port2=name if port == 1 else "none")
                out = self.session([core("A"), f"run A 3 script={script} record", "save A aiming", f"savefile A {state}",
                                    f"run A 8 script={script} record", "digest A first", "peek A Chip_RAM 1000 60",
                                    f"endmovie A {movie}", f"movieverify A {movie} 11 script={script}",
                                    "load A aiming", f"run A 8 script={script}", "digest A restored", "peek A Chip_RAM 1000 60",
                                    core("B"), "load B aiming", f"run B 8 script={script}", "digest B fresh", "peek B Chip_RAM 1000 60",
                                    core("C"), f"run C 11 movie={movie}", f"moviefinish C {movie}", "digest C movie", "peek C Chip_RAM 1000 60"], firmware="pointer")
                a = [u[1:] for u in self.units(out, "A")]
                self.assertEqual(19, len(a)); self.assertEqual(a[3:11], a[11:])
                self.assertEqual(a[3:11], [u[1:] for u in self.units(out, "B")])
                self.assertEqual(a[:11], [u[1:] for u in self.units(out, "C")])
                digests = {**self.digests(out, "A"), **self.digests(out, "B"), **self.digests(out, "C")}
                self.assertEqual({"first", "restored", "fresh", "movie"}, set(digests))
                self.assertEqual(1, len(set(digests.values())))
                peeks = re.findall(r"^peek [ABC] Chip_RAM 1000 ([0-9a-f]+)$", out, re.M)
                self.assertEqual(4, len(peeks)); self.assertEqual(1, len(set(peeks)))
                self.assertRegex(out, r"movie verified frames=11 buttons=\d+ axes=2 compared=\d+ entries=11")
                self.assertIn("movie finished ok frames=11 ", out)
                other = self.session([core("D"), f"loadfile D {state}", f"run D 8 script={script}", "digest D process", "peek D Chip_RAM 1000 60"], firmware="pointer")
                self.assertEqual(a[3:11], [u[1:] for u in self.units(other, "D")])
                self.assertEqual(digests["first"], self.digests(other, "D")["process"])
                self.assertIn(f"peek D Chip_RAM 1000 {peeks[0]}", other)

    def test_four_players_parallel_pins_and_bk2_restore_in_same_and_new_sandbox_and_process(self):
        frames = []
        for i in range(11):
            a, b = (9, 81) if i < 5 else (33, 17)
            frames.append(line(p0=(1, 9, 0, 0), p1=(1, 65, 0, 0)) + f" 1 {a} 0 0 1 {b} 0 0")
        script = self.script("parallel", frames)
        state, movie = self.dir / "parallel.state", self.dir / "parallel.bk2"
        core = lambda name: self.core(name, adf="", port1="joystick", port2="joystick", port3="joystick", port4="joystick")
        out = self.session([core("A"), f"run A 3 script={script} record", "save A playing", f"savefile A {state}",
                            f"run A 8 script={script} record", "digest A first", "peek A Chip_RAM 1000 120",
                            f"endmovie A {movie}", f"movieverify A {movie} 11 script={script}",
                            "load A playing", f"run A 8 script={script}", "digest A restored", "peek A Chip_RAM 1000 120",
                            core("B"), "load B playing", f"run B 8 script={script}", "digest B fresh", "peek B Chip_RAM 1000 120",
                            core("C"), f"run C 11 movie={movie}", f"moviefinish C {movie}", "digest C movie", "peek C Chip_RAM 1000 120"], firmware="parallel")
        a = [u[1:] for u in self.units(out, "A")]
        self.assertEqual(19, len(a)); self.assertEqual(a[3:11], a[11:])
        self.assertEqual(a[3:11], [u[1:] for u in self.units(out, "B")])
        self.assertEqual(a[:11], [u[1:] for u in self.units(out, "C")])
        digests = {**self.digests(out, "A"), **self.digests(out, "B"), **self.digests(out, "C")}
        self.assertEqual({"first", "restored", "fresh", "movie"}, set(digests)); self.assertEqual(1, len(set(digests.values())))
        peeks = re.findall(r"^peek [ABC] Chip_RAM 1000 ([0-9a-f]+)$", out, re.M)
        self.assertEqual(4, len(peeks)); self.assertEqual(1, len(set(peeks)))
        words = struct.unpack(">60H", bytes.fromhex(peeks[0]))[-5:]
        self.assertEqual((0xdb, 2), (words[3] & 255, words[4] & 7))
        self.assertRegex(out, r"movie verified frames=11 buttons=\d+ axes=0 compared=\d+ entries=11")
        self.assertIn("movie finished ok frames=11 ", out)
        other = self.session([core("D"), f"loadfile D {state}", f"run D 8 script={script}", "digest D process", "peek D Chip_RAM 1000 120"], firmware="parallel")
        self.assertEqual(a[3:11], [u[1:] for u in self.units(other, "D")])
        self.assertEqual(digests["first"], self.digests(other, "D")["process"])
        self.assertIn(f"peek D Chip_RAM 1000 {peeks[0]}", other)


if __name__ == "__main__":
    unittest.main()
