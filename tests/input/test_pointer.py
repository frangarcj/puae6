"""T70c: absolute optical input, beam latch/event and transactional native states."""
from pathlib import Path
import re
import struct
import subprocess
import tempfile
import unittest

from tests.build_support import ROOT, build_targets
from tests.state.harness import FieldTable, parse_chunks, reforge
from tests.state.test_det02 import Det02Case, SAME, NEW
from tests.state.test_state_upstream import sub_bodies, with_subs
from tests.step.test_step import builder

ROM = """; Original PUAE6 micro-ROM, MIT. Read the optical beam latch and trigger pins.
entry:
    MOVE.W #$2700,SR
    LEA $400.W,A7
    MOVE.B #3,$BFE201.L
    MOVE.B #2,$BFE001.L
    MOVE.W #0,$DFF034.L
    MOVE.W #8,$DFF100.L
    MOVE.L #vb,$6C.L
    LEA $1000.W,A0
    MOVE.W #$C020,$DFF09A.L
    MOVE.W #$2000,SR
loop:
    ADDQ.L #1,D0
    BRA.S loop
vb:
    MOVE.W #$0020,$DFF09C.L
    MOVE.W $DFF004.L,(A0)+
    MOVE.W $DFF006.L,(A0)+
    MOVE.W $DFF016.L,(A0)+
    RTE
"""


class PointerTests(Det02Case):
    SAVE_OP, LOAD_OP = "--public-save-at", "--public-load-at"

    @classmethod
    def setUpClass(cls):
        cls.exe = build_targets(ROOT / "build/cd32", ["p6_state_probe"], optimized=True)["p6_state_probe"]
        cls.tmp = tempfile.TemporaryDirectory(prefix="pointer-", dir=ROOT / "build")
        cls.dir = Path(cls.tmp.name)
        cls.roms = {}
        for name, source in (("pointer", ROM), ("disable", ROM.replace("    RTE", "    .word $B1FC,$0000,$102A ; CMPA.L #$102A,A0: seventh interrupt record\n    .word $6608 ; BNE.S past the single LPEN clear\n    MOVE.W #0,$DFF100.L\n    RTE")), ("plain", ROM.replace("MOVE.W #8,$DFF100.L", "MOVE.W #0,$DFF100.L"))):
            path = cls.dir / f"{name}.rom"; path.write_bytes(builder.build(source)); cls.roms[name] = path
        cls.roms["vsync"] = cls.roms["pointer"]
        cls.cache = {}

    def pointer(self, device=4, port=1, buttons=1):
        return ("--port", f"{port}:{device}:{buttons}:8000:-6000")

    def saved(self, *extra, rom="pointer"):
        path = self.dir / "pointer.state"
        run = self.probe(rom, "--units", "8", *extra, "--save-fields-at", "8", str(path))
        self.assertEqual([], run.save_errors)
        blob = path.read_bytes()
        return blob, FieldTable(blob, 9, run.fields), run

    def test_sensor_position_and_trigger_are_independent_in_both_ports_pal_and_ntsc(self):
        for standard in ((), ("--ntsc",)):
            for device, port, buttons in ((4, 1, 0), (4, 1, 1), (5, 0, 1), (5, 0, 3)):
                with self.subTest(standard=standard, device=device, port=port, buttons=buttons):
                    path = self.dir / "beam.bin"
                    blob, table, _ = self.saved(*standard, *self.pointer(device, port, buttons), "--ramout-at", "8", "0x1000", "54", str(path))
                    vp, hp, pots = struct.unpack(">27H", path.read_bytes())[-3:]
                    cx, cy = (table.get(f"pointer.{a}", signed=True) for a in ("cx", "cy"))
                    self.assertEqual(int(not (buttons & 1)), (pots >> (8 + 4 * port)) & 1)
                    if buttons & 2:
                        self.assertEqual((-1, -1), (cx, cy))
                        self.assertEqual(2, hp & 255, "with no photodiode hit the hardware freezes at the last line, hpos1 (reads advance one clock)")
                    else:
                        self.assertGreater(cx, 23); self.assertGreater(cy, 0)
                        self.assertEqual(cx + 1, hp & 255)
                        self.assertEqual(cy, ((vp & 7) << 8) | (hp >> 8))
                    latch = dict(sub_bodies(parse_chunks(blob)[4][2]))["LPEN"]
                    self.assertEqual(16, len(latch))

    def test_optical_latch_and_levels_restore_in_same_and_new_process_even_after_detach(self):
        for extra in (self.pointer(), (*self.pointer(5, 0), "--port-at", "4", "0:0:0:0:0")):
            for mode in (SAME, NEW):
                self.assertIsNone(self.first_divergence("pointer", list(extra), 5, mode))
        plain, _, _ = self.saved(rom="plain")
        no_device, _, _ = self.saved()
        attached, _, _ = self.saved(*self.pointer())
        self.assertEqual(len(parse_chunks(plain)[4][2]), len(parse_chunks(no_device)[4][2]))
        self.assertEqual(4, len(parse_chunks(attached)[9][2]) - len(parse_chunks(no_device)[9][2]))

    def test_guest_clearing_lpen_with_a_pending_optical_event_restores_the_latch_even_while_disabled(self):
        pending, latched = self.dir / "disabled-pending.state", self.dir / "disabled-latched.state"
        original = self.probe("disable", "--units", "10", "--every", "1", *self.pointer(), "--poke-at", "5", "ev2:13:80000",
                              self.SAVE_OP, "6", str(pending), self.SAVE_OP, "7", str(latched))
        self.assertEqual([], original.save_errors)
        for n, path, flag in ((6, pending, 0), (7, latched, 1)):
            subs = dict(sub_bodies(parse_chunks(path.read_bytes())[4][2]))
            self.assertEqual(0, struct.unpack_from(">H", subs["CHIP"], 196)[0] & 8)
            self.assertEqual(flag, struct.unpack(">4i", subs["LPEN"])[3])
            for offset in (0, 10):
                history = ("--poke-at", "5", "ev2:13:80000") if offset else ()
                run = self.probe("disable", "--units", str(offset + 10 - n), "--every", "1", *self.pointer(), *history,
                                 self.LOAD_OP, str(offset), str(path))
                self.assertEqual([(offset, 0, "")], run.loads[:1])
                for j in range(1, 11 - n): self.assertEqual([], self.differing(original, run, n, offset, j))

    def test_invalid_pointer_tail_latch_and_pending_event_are_refused_transactionally(self):
        blob, table, _ = self.saved(*self.pointer())
        cut = reforge(blob, lambda c: c[9].__setitem__(2, c[9][2][:-1]))
        invalid_latch = with_subs(blob, b"P6CH", lambda subs: [sub.__setitem__(1, sub[1][:12] + struct.pack(">I", 2)) for sub in subs if sub[0] == "LPEN"])
        self.refusals([("cut coordinates", cut, 9, "the body ends before its last field"),
                       ("two sensors", table.with_values({"applied.type[0]": 5}), 9, "only one optical sensor is supported"),
                       ("optical flag", table.with_values({"applied.buttons[1]": 4}), 9, "optical pointer has unknown buttons"),
                       ("half absent", table.with_values({"pointer.cx": -1}), 9, "optical coordinates must be on screen or both absent"),
                       ("latch flag", invalid_latch, 9, "optical beam latch is out of range")], rom="pointer", extra=self.pointer(), unit=4, units=5)
        path = self.dir / "event.state"
        run = self.probe("pointer", "--units", "8", "--every", "1", *self.pointer(), "--poke-at", "5", "ev2:13", "--save-fields-at", "5", str(path))
        self.assertEqual([], run.save_errors)
        scheduler = FieldTable(path.read_bytes(), 1, run.fields)
        slot, = [i for i in range(16) if scheduler.get(f"ev2.handler[{i}]") == 13]
        self.refusals([("optical event position", scheduler.with_values({f"ev2.data[{slot}]": 256}), 9, "optical event horizontal position is out of range")],
                      rom="pointer", extra=self.pointer(), unit=4, units=5)
        # Restore a real scheduled handler, then let the emulated clock fire it.
        restored = self.probe("pointer", "--units", "3", "--every", "1", *self.pointer(), self.LOAD_OP, "0", str(path))
        self.assertEqual([(0, 0, "")], restored.loads[:1])
        for j in range(1, 4): self.assertEqual([], self.differing(run, restored, 5, 0, j))
        table_out = subprocess.run([str(self.exe), str(self.roms["pointer"]), "--units", "0", "--handler-table"], capture_output=True, text=True, check=True).stdout
        handlers = re.findall(r"^handler (\d+) (OK|BAD)$", table_out, re.M)
        self.assertEqual(17, len(handlers))
        self.assertEqual({i: "OK" for i in range(17)}, {int(i): status for i, status in handlers})
        self.assertIn("handler foreign OK", table_out)


if __name__ == "__main__":
    unittest.main()
