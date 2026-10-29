"""T84e3: semantic IDE ATAPI/CDDA state, catalog identity and refusal atomicity."""
from tests.media import ide_cd_rom
from tests.state.harness import FieldTable
from tests.state.test_det02 import Det02Case, NEW, SAME


CORRUPT = 9
INCOMPATIBLE = 10
DISK_CHUNK = 8


class IdeCdStateTests(Det02Case):
    MACHINE = "a600"
    ROMS = {"ide_cd": (ide_cd_rom.state_program(), None, None)}

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.hdf = cls.dir / "alongside.hdf"
        image = bytearray(16 * 512)
        image[:4] = b"DOS\0"
        for n in range(4, len(image)):
            image[n] = (0x43 + n * 17) & 0xFF
        cls.hdf.write_bytes(image)

    @classmethod
    def base_arguments(cls):
        # The HDF intentionally stays on the independent UAE controller while
        # the CD owns Gayle's primary master.
        return ["--cd-controller", "ide", "--ide-cd-fixture", "--hdf", str(cls.hdf)]

    def arguments(self, extra):
        return [*self.base_arguments(), *list(extra)]

    def test_command_data_and_cdda_match_locally_and_in_a_new_process(self):
        # 10: four CDB bytes received; 35: the 256 KiB response is in flight;
        # 56: PLAY AUDIO has installed an active deterministic PCM stream.
        for cut, phase in ((10, "command"), (35, "data"), (56, "cdda")):
            for mode in (SAME, NEW):
                with self.subTest(phase=phase, mode=mode):
                    self.assertIsNone(self.first_divergence("ide_cd", (), cut, mode))

    def test_selection_and_absence_replace_the_destination_history(self):
        for absent in (False, True):
            path = self.dir / f"selection-absent-{int(absent)}.state"
            source_args = [*self.base_arguments(), "--ide-cd-selected", "1"]
            if absent:
                source_args.append("--ide-cd-ejected")
            original = self.probe("ide_cd", "--units", "1", "--every", "1", *source_args,
                                  "--save-at", "1", str(path))
            restored = self.probe("ide_cd", "--units", "1", "--every", "1", *self.base_arguments(),
                                  "--load-at", "1", str(path))
            self.assertEqual([(1, 0, "")], restored.loads, restored.loads)
            self.assertEqual(original.unit(1), restored.after[-1],
                             "the saved ordinal/presence did not replace the fresh process")

    def test_another_catalog_or_topology_is_incompatible_and_untouched(self):
        path = self.dir / "identity.state"
        self.probe("ide_cd", "--units", "1", *self.base_arguments(),
                   "--ide-cd-selected", "1", "--save-at", "1", str(path))
        cases = (
            ("catalog", [*self.base_arguments(), "--ide-cd-swap-catalog"], "media_manifest_hash"),
            ("topology", ["--cd-controller", "none", "--ide-cd-fixture", "--hdf", str(self.hdf)], "config_hash"),
        )
        for name, arguments, message in cases:
            with self.subTest(name=name):
                control = self.probe("ide_cd", "--units", "3", "--every", "1", *arguments)
                refused = self.probe("ide_cd", "--units", "3", "--every", "1", *arguments,
                                     "--load-at", "1", str(path))
                self.assertEqual((1, INCOMPATIBLE), refused.loads[0][:2], refused.loads)
                self.assertIn(message, refused.loads[0][2])
                self.assertEqual(control.unit(1), refused.after[-1], "a refused identity changed the image")
                for unit in (2, 3):
                    self.assertEqual(control.unit(unit), refused.unit(unit),
                                     "the image did not continue as if the candidate had never arrived")

    def test_corrupt_transport_and_catalog_fields_are_refused_transactionally(self):
        path = self.dir / "command-fields.state"
        run = self.probe("ide_cd", "--units", "10", *self.base_arguments(),
                         "--save-fields-at", "10", str(path))
        blob = path.read_bytes()
        table = FieldTable(blob, DISK_CHUNK, run.fields)
        attempts = (
            ("packet state", table.with_values({"ide.drive_word[37]": 3}), CORRUPT, "drive can have"),
            ("ATAPI medium", table.with_values({"ide.drive_word[46]": table.get("ide.drive_word[46]") ^ 1}), CORRUPT, "ATAPI medium"),
            ("catalog ordinal", table.with_values({"ide.cd.ordinal": 2}), CORRUPT, "CD ordinal"),
        )
        self.refusals(attempts, rom="ide_cd", extra=self.base_arguments(), unit=12, units=14)


if __name__ == "__main__":
    import unittest
    unittest.main()
