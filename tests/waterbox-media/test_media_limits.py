"""T28, WB12: the worst case of the write overlay in the Waterbox sandbox, measured.

The guest is the adversarial writer of wb12.py: it rewrites every track of DF0 and of DF1 (3520 sectors, the whole pool of the overlay), then waits for the host to put a third
disk in DF0 and tries to write that one too. What is checked:

* the disks hold what the guest wrote, track by track, and the third disk, which the pool has no room for, is reported write protected by the drive and left alone (nothing
  is stored, nothing is claimed): the exhaustion is a clear state, not a stub that succeeds;
* the snapshot grows with what the guest wrote and by no more than the pool the state bound already includes; a state taken with the pool full restores in the same sandbox and
  in a new one, and a state taken early takes the whole pool back;
* the 17th and 18th non-AmigaDOS track the guest writes do not fit the 16 raw slots: they are counted as refused writes, the disk cannot be exported as an ADF (UNSUPPORTED,
  said so) and the SaveRam leaves it out;
* the heaps of the sandbox hold it: tools/build_waterbox.py's Smoke harness (--floppy-writes --drives 2) measures the committed and dirty bytes of every heap, recorded in
  manifests/waterbox-memory.json under measured_a500_floppy_writes_full, and the mmap heap of a core with FloppyWrites is the one the manifest says.

    python3 -m unittest discover -s tests/waterbox-media -p "test_media_limits.py"
    python3 tests/waterbox-media/test_media_limits.py --record      # writes the measurement into manifests/waterbox-memory.json (review the diff)
"""
import json
import re
import subprocess
import sys
import unittest

from support import AVAILABLE, SKIP, GUEST, RECEIPT, ROOT, Sessions, build_waterbox, disk, med02, sha
import wb12

CHUNK, UNITS = 100, 5600
POOL, SECTORS_PER_DISK = 3520, disk.TRACKS * disk.SECTORS
MANIFEST = ROOT / "manifests/waterbox-memory.json"
MEASURED = "measured_a500_floppy_writes_full"
PHASES = ("after_seal", "after_first_unit", "after_last_unit")


def smoke_command(directory, units=UNITS):
    """The command of the Smoke harness for the full-pool run (tools/build_waterbox.py's run_puae6 does not know the flags of T28)."""
    dotnet, env = build_waterbox.build_managed()
    result = directory / "smoke.json"
    command = [dotnet, str(build_waterbox.BUILD / "managed/bin/Debug/net8.0/Smoke.dll"), "--puae6", "--runtime", str(build_waterbox.runtime_receipt_path()), "--guest", str(RECEIPT),
               "--manifest", str(MANIFEST), "--firmware", str(directory / "wb12.rom"), "--units", str(units), "--result", str(result), "--media", str(directory / "A.adf"),
               "--media", str(directory / "B.adf"), "--drives", "2", "--floppy-writes"]
    return command, env, result


def measure(directory, units=UNITS):
    command, env, result = smoke_command(directory, units)
    process = subprocess.run(command, env=env, cwd=build_waterbox.MANAGED, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=3600)
    assert process.returncode == 0 and result.is_file(), process.stdout[-1500:] + process.stderr[-1500:]
    return json.loads(result.read_text())


@unittest.skipUnless(AVAILABLE, SKIP)
class AdversarialWritesTests(Sessions):
    FIRMWARE = {"wb12": wb12.rom_source, "raw": lambda: raw_rom_source()}

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.cache = {}

    def disks(self):
        return self.adfs("ABC")

    def core(self, name, **options):
        return f"core {name} adf={self.disks()} writes drives=2"

    def pressure(self):
        """The whole run without the third disk, chunk by chunk (the markers and the PRA samples), states at some points, and then the disks and the overlay at the end, the full
        state restored in the same sandbox over an early one and over itself, and in a new sandbox. Sections of the output start with a line "@name"."""
        if "pressure" not in self.cache:
            checkpoints = (1, 10, 20, 30, 40, 50, UNITS // CHUNK)
            lines = [self.core("A")]
            for chunk in range(1, UNITS // CHUNK + 1):
                lines += [f"run A {CHUNK} silent=1-100000", f"peek A Chip_RAM {disk.MARK_BASE:x} {2 * len(wb12.MARKERS)}", f"peek A Chip_RAM {wb12.PRA_BASE:x} {len(wb12.PRA_SAMPLES)}"]
                if chunk in checkpoints:
                    lines += [f"save A u{chunk * CHUNK}"]
            snap = lambda name: [f"export {name} {i}" for i in range(3)] + [f"status {name} {i}" for i in range(3)]
            lines += ["echo @end", *snap("A"), "digest A end", "save A full", "saveram A sr",
                      "echo @early", "load A u1000", *snap("A"),
                      "echo @full", "load A full", *snap("A"), "digest A restored",
                      "echo @new", self.core("B"), "load B full", *snap("B"), "digest B restored"]
            out = self.session(lines, firmware="wb12", timeout=7200)
            self.cache["pressure"] = out
        return self.cache["pressure"]

    @staticmethod
    def section(out, label):
        return re.search(rf"^@{label}$(.*?)(?=^@|\Z)", out, re.M | re.S)[1]

    def disk_state(self, text, name):
        exports, statuses = self.exports(text, name), self.statuses(text, name)
        return [e[3] for e in exports], statuses

    def test_the_guest_writes_every_track_of_both_disks_and_the_pool_is_exactly_full(self):
        out = self.pressure()
        peeks = self.peeks(out, "A")[:2 * (UNITS // CHUNK)]
        marks, samples = peeks[0::2], peeks[1::2]
        done = disk.MARK_DONE.to_bytes(2, "big")
        reached = [next((i + 1 for i, m in enumerate(marks) if m[2 * n:2 * n + 2] == done), None) for n in range(len(wb12.MARKERS))]
        self.assertNotIn(None, reached, "the guest finished inside the run")
        self.assertEqual(sorted(reached), reached, "DF0, DF1, the hold and the attempt, in that order")
        shas, statuses = self.disk_state(self.section(out, "end"), "A")
        self.assertEqual([sha(wb12.expected_full(None))] * 2 + [sha(self.adf["C"])], shas, "A and B hold what the guest wrote on every track, C is untouched")
        self.assertEqual([0, 0, 0], [e[1] for e in self.exports(self.section(out, "end"), "A")])
        self.assertEqual([(POOL, POOL)] * 3, [(s["used"], s["capacity"]) for s in statuses], "the pool is exactly the two disks")
        self.assertEqual([SECTORS_PER_DISK, SECTORS_PER_DISK, 0], [s["medium"] for s in statuses], "every sector of A and B differs from its base, none of C")
        self.assertEqual([(0, 0)] * 3, [(s["refused"], s["raw"]) for s in statuses],
                         "a well behaved guest is stopped by /WPRO before it can be refused: its last write attempt, on the full pool, stored nothing (no raw track either)")
        # the drive lines the guest sampled: DF0 writable while the pool was half full, DF1 protected as soon as fewer than 11 sectors are free, and so on
        sample = samples[reached[-1] - 1]
        protected = [disk.pra_state(b).write_protected for b in sample]
        self.assertEqual([False, True, True, True], protected, f"/WPRO at the samples {wb12.PRA_SAMPLES}")

    def test_the_snapshot_grows_with_the_writes_by_no_more_than_the_pool_and_the_full_state_restores_here_and_in_a_new_sandbox(self):
        out = self.pressure()
        by_unit = sorted((int(k[1:]), v) for k, v in self.sizes(out).items() if k.startswith("u"))
        sizes = [v for _, v in by_unit]
        self.assertEqual(sorted(sizes), sizes, f"a snapshot does not shrink as the guest writes more: {by_unit}")
        bound = 12 + POOL * (1 + 2 + 512) + 4 + 16 * (6 + 12784)       # the pool and the raw tracks the state bound counts (docs/02-contracts.md)
        growth = sizes[-1] - sizes[0]
        self.assertLessEqual(growth, bound + 4 * 16384, f"{growth} bytes of growth for an overlay whose bound is {bound}")
        self.assertGreater(growth, POOL * 512 // 2, "and it does grow: the writes are in the snapshot")
        digests = {**self.digests(out, "A"), **self.digests(out, "B")}
        self.assertEqual(digests["end"], digests["restored"], "the full state over an early one and back: the same machine")
        full = self.disk_state(self.section(out, "end"), "A")
        self.assertEqual(full, self.disk_state(self.section(out, "full"), "A"), "and the same disks and overlay")
        self.assertEqual(full, self.disk_state(self.section(out, "new"), "B"), "in a new sandbox too")
        early_shas, early = self.disk_state(self.section(out, "early"), "A")
        self.assertLess(early[0]["used"], POOL, "a state taken early takes the pool back")
        self.assertNotEqual(early_shas[1], full[0][1])

    def test_a_third_disk_put_in_after_the_pool_is_full_is_reported_protected_and_left_alone(self):
        # the unit of the run when the second drive was done comes from the pressure run (it is deterministic): the swap goes inside the guest's hold
        peeks = self.peeks(self.pressure(), "A")[:2 * (UNITS // CHUNK)]
        done = disk.MARK_DONE.to_bytes(2, "big")
        second = next(i + 1 for i, m in enumerate(peeks[0::2]) if m[2:4] == done) * CHUNK
        swap = ";".join(f"{second + 10 + 2 * i}:{button}" for i, button in enumerate(wb12.SWAP_ORDER))
        out = self.session([self.core("A"), f"run A {second + wb12.HOLD + 200} silent=1-100000 press={swap}", f"peek A Chip_RAM {disk.MARK_BASE:x} {2 * len(wb12.MARKERS)}",
                            f"peek A Chip_RAM {wb12.PRA_BASE:x} {len(wb12.PRA_SAMPLES)}", "export A 0", "export A 1", "export A 2", "status A 0", "status A 1", "status A 2"],
                           firmware="wb12", timeout=7200)
        marks, samples = self.peeks(out, "A")
        self.assertEqual([done] * 4, [marks[2 * i:2 * i + 2] for i in range(4)], "the guest got to its attempt on the third disk")
        self.assertEqual([False, True, True, True], [disk.pra_state(b).write_protected for b in samples], "the third disk is write protected for the guest, before and after its attempt")
        shas, statuses = self.disk_state(out, "A")
        self.assertEqual([sha(wb12.expected_full(None))] * 2 + [sha(self.adf["C"])], shas, "A and B as written, C as it was given: the attempt stored nothing")
        self.assertEqual([(POOL, POOL)] * 3, [(s["used"], s["capacity"]) for s in statuses])
        self.assertEqual([SECTORS_PER_DISK, SECTORS_PER_DISK, 0], [s["medium"] for s in statuses])
        self.assertEqual([0, 0, 0], [s["refused"] for s in statuses], "the engine did not even try: the drive said no first")

    def test_a_disk_with_more_non_amigados_tracks_than_the_raw_slots_says_so(self):
        raw = med02.raw_buffer_bytes().hex()
        out = self.session([self.core("A"), "run A 1 silent=1-100000", f"poke A Chip_RAM {med02.RAW_ADDR:x} {raw}", "run A 330 silent=1-100000", "status A 0", "export A 0", "saveram A sr",
                            "modified A", "save A raw", f"run A 1", "status A 0"], firmware="raw")
        status = self.statuses(out, "A")[0]
        self.assertEqual((16, 2), (status["raw"], status["refused"]), "16 raw slots: the 17th and 18th track were not kept, and the machine counts them")
        self.assertEqual(0, status["used"])
        export = self.exports(out, "A")[0]
        self.assertEqual((4, 0), (export[1], export[2]), "an ADF cannot hold them: UNSUPPORTED, nothing written")
        self.assertIn(("sr", None, None, 0), self.saverams(out, "A"), "the SaveRam leaves the disk out (and the frontend is told)")

    # --- the heaps ----------------------------------------------------------------------------------------------------------------------

    def test_the_heaps_hold_the_full_pool_with_the_margin_of_the_manifest_and_the_core_asks_for_the_same(self):
        manifest = json.loads(MANIFEST.read_text())
        rule = manifest["memory_budget"]
        key = manifest[MEASURED]
        self.assertEqual(self.guest_sha, key["guest_sha256"], "the measurement is the one of this guest: rerun test_media_limits.py --record")
        heaps = build_waterbox.run_ffi("heaps", "--writes")
        self.assertEqual(0, heaps.returncode, heaps.stdout + heaps.stderr)
        mmap_kib = int(re.search(r"^heaps (.*)$", heaps.stdout, re.M)[1].split()[4])
        self.assertEqual(manifest["layout"]["mmap"] // 1024 + rule["mmap_extra_factor"] * rule["floppy_writes_overlay_kib"], mmap_kib)
        budget = mmap_kib * 1024
        for phase in PHASES:
            self.assertLessEqual(2 * key[phase]["mmap"]["committed_bytes"], budget, f"{phase}: the budget is at least twice what the full pool needed")

    def test_the_measurement_in_the_manifest_is_the_one_a_run_produces_now(self):
        directory = self.dir / "smoke"
        directory.mkdir()
        for name in ("wb12.rom", "A.adf", "B.adf"):
            (directory / name).write_bytes((self.media / name).read_bytes())
        result = measure(directory)
        recorded = json.loads(MANIFEST.read_text())[MEASURED]
        for field in (*PHASES, "snapshot_bytes"):
            self.assertEqual(recorded[field], result[field], f"{field} changed: python3 tests/waterbox-media/test_media_limits.py --record, and review manifests/waterbox-memory.json")
        for phase in PHASES:
            for heap, size in result["layout"].items():
                self.assertLessEqual(result[phase][heap]["committed_bytes"], size, f"{heap} exceeds its budget")


def raw_rom_source():
    """A guest that writes 18 tracks that are not AmigaDOS (the buffer of med02.raw_buffer_bytes, poked by the host at unit 1), one after the other from cylinder 0: more than the
    16 raw slots of the overlay."""
    head = med02.rom_source().split("    MOVE.B #$77,$BFD100.L        ; select DF0 (bit 3 low): the motor latches at the select edge\n")[0].splitlines()
    a = list(head)
    a += ["    MOVE.B #$%02X,$BFD100.L        ; select DF0" % wb12.DF0]
    wb12.waits(a, wb12.SPINUP)
    for track in range(18):
        cylinder, side = divmod(track, 2)
        a += ["    MOVE.B #$%02X,$BFD100.L        ; cylinder %d, side %d" % (wb12.DF0 if side == 0 else wb12.DF0 & ~0x04, cylinder, side)]
        wb12.waits(a, wb12.SETTLE)
        med02._write(a, "raw")
        if side == 1:
            a += ["    MOVE.B #$%02X,$BFD100.L        ; side 0" % wb12.DF0]
            for edge in (wb12.DF0 & ~3, wb12.DF0 & ~2):
                a += ["    MOVE.B #$%02X,$BFD100.L        ; /STEP" % edge]
            wb12.waits(a, wb12.STEP_WAIT)
    a += ["done:", "    STOP #$2000", "    BRA.S done",
          "hvert:", "    MOVE.W #$0020,$DFF09C.L      ; INTREQ: clear VERTB", "    RTE",
          "hdsk:", "    MOVE.W #$0002,$DFF09C.L      ; INTREQ: clear DSKBLK", "    RTE"]
    return "\n".join(a) + "\n"


def record():
    import tempfile
    with tempfile.TemporaryDirectory(prefix="p6-wb12-") as tmp:
        from pathlib import Path
        directory = Path(tmp)
        (directory / "wb12.rom").write_bytes(build_wb12_rom())
        for name, label in (("A", med02.LABEL_A), ("B", med02.LABEL_B)):
            (directory / f"{name}.adf").write_bytes(disk.make_adf(label))
        result = measure(directory)
        manifest = json.loads(MANIFEST.read_text())
        manifest[MEASURED] = {
            "fixture": {"machine": "A500", "floppy_writes": True, "drives": 2, "media": ["A", "B"], "firmware": "tests/waterbox-media/wb12.py", "firmware_sha256": sha((directory / "wb12.rom").read_bytes()),
                        "firmware_bytes": (directory / "wb12.rom").stat().st_size, "units": UNITS,
                        "note": "the adversarial writer: every track of both disks is rewritten, 3520 sectors, the whole pool of the overlay (WB12)"},
            "guest_sha256": result["guest_sha256"], "runtime_sha256": result["runtime_sha256"], "after_seal": result["after_seal"], "after_first_unit": result["after_first_unit"],
            "after_last_unit": result["after_last_unit"], "snapshot_bytes": result["snapshot_bytes"]}
        MANIFEST.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")


def build_wb12_rom():
    from support import builder
    return builder.build(wb12.rom_source())


if __name__ == "__main__":
    if "--record" in sys.argv:
        record()
    else:
        unittest.main()
