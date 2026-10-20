"""Pending preference checks are causal state, including before their first vsync."""
from pathlib import Path
import struct
import tempfile
import unittest

from tests.state import harness as h, test_det02 as d, test_state_upstream as u


class PendingConfigTests(d.Det02Case):
    MACHINE = "a1200"

    @classmethod
    def setUpClass(cls):
        cls.exe = h.built("p6_state_probe")
        cls.tmp = tempfile.TemporaryDirectory(prefix="pending-config-", dir=h.ROOT / "build")
        cls.dir = Path(cls.tmp.name)
        cls.roms = {}
        for name in ("vsync", "long_frame"):
            path = cls.dir / (name + ".rom")
            path.write_bytes(h.builder.build((h.FIXTURES / (name + ".s")).read_text()))
            cls.roms[name] = path

    @staticmethod
    def beam(blob):
        data = u.upstream_bodies(blob)[b"P6CH"]["BEAM"]
        return dict(zip(u.BEAM_WORDS, struct.unpack(">" + "i" * len(u.BEAM_WORDS), data)))

    def test_early_and_changed_mode_cuts_keep_the_pending_check_and_complete_future(self):
        for rom in ("vsync", "long_frame"):
            # On AGA this existing scenario adopts its shorter raster in unit 6;
            # the following two boundaries still hold the mode-change request.
            points = (1, 2) if rom == "vsync" else (6, 7)
            paths = {n: self.dir / f"{rom}.{n}" for n in points}
            final = self.dir / (rom + ".end")
            ops = [arg for n, path in paths.items() for arg in ("--public-save-at", str(n), str(path))]
            original = self.probe(rom, "--units", "20", "--every", "1", *ops, "--public-save-at", "20", str(final))
            pending = {n: self.beam(p.read_bytes())["config_changed"] for n, p in paths.items()}
            self.assertTrue(set(pending.values()) <= {0, 1, 2})
            if rom == "vsync":
                self.assertIn(pending[1], (1, 2), "the first cut must still hold the startup request")
                cuts = (1, 2)
            else:
                self.assertEqual([1, 2], [pending[n] for n in points], "the mode-change request must still be pending")
                for path in paths.values():
                    geom = u.upstream_bodies(path.read_bytes())[b"P6VD"]["GEOM"]
                    self.assertNotEqual(312, struct.unpack_from(">i", geom, 12)[0], "the raster must have changed from PAL reset")
                cuts = points
            for n in cuts:
                for offset in (0, 22):  # Fresh process and an owner with later history.
                    with self.subTest(rom=rom, cut=n, owner=offset):
                        end = self.dir / "restored.end"
                        run = self.probe(rom, "--units", str(offset + 20 - n), "--every", "1",
                                         "--public-load-at", str(offset), str(paths[n]),
                                         "--public-save-at", str(offset + 20 - n), str(end))
                        self.assertEqual(run.loads, [(offset, 0, "")])
                        for j in range(1, 21 - n):
                            self.assertEqual([], self.differing(original, run, n, offset, j), f"future unit {n + j}")
                        self.assertEqual(final.read_bytes(), end.read_bytes(), "all native state must return")

    def test_countdown_and_complete_flag_word_roundtrip_and_reach_the_oracle(self):
        original = self.state_of("original", "vsync", 3)
        digests = []
        for countdown, flags in ((0, 0), (1, 0), (2, 0), (2, 0x80000004)):
            def edit(subs):
                for sub in subs:
                    if sub[0] == "BEAM":
                        data = bytearray(sub[1])
                        struct.pack_into(">II", data, 4 * u.BEAM_WORDS.index("config_changed"), countdown, flags)
                        sub[1] = bytes(data)
            source, again = self.dir / "candidate", self.dir / "again"
            source.write_bytes(u.with_subs(original, b"P6CH", edit))
            run = self.probe("vsync", "--units", "0", "--public-load-at", "0", str(source), "--public-save-at", "0", str(again))
            self.assertEqual([(0, 0, "")], run.loads)
            self.assertEqual(source.read_bytes(), again.read_bytes())
            digests.append(run.chip_after[0]["timing"])
        self.assertEqual(4, len(set(digests)), "the oracle must observe both pending-check words")


if __name__ == "__main__":
    unittest.main()
