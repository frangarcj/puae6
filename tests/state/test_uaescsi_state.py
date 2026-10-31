"""T84g2: estado causal y transaccional de uaescsi.device cooperativo."""
import unittest

from tests.state.harness import FieldTable, ProbeCase


DISK = 8
CORRUPT = 9


class UaeScsiStateTests(ProbeCase):
    ROMS = {"vsync": ("vsync", None, None)}

    def snapshot(self):
        path = self.dir / "uaescsi.state"
        run = self.probe("vsync", "--uaescsi-fixture", "--units", "1",
                         "--save-fields-at", "1", str(path))
        self.assertFalse(run.save_errors)
        self.assertEqual(1, len(run.saves))
        return path, FieldTable(path.read_bytes(), DISK, run.fields), run

    def test_pending_guest_request_roundtrips_in_a_fresh_process(self):
        source, fields, saved = self.snapshot()
        self.assertEqual(1, fields.get("ide.cd.opens"))
        self.assertEqual(1, fields.get("uaescsi.opencnt"))
        self.assertEqual(1, fields.get("uaescsi.open.active[0]"))
        self.assertEqual(1, fields.get("uaescsi.request.active[0]"))
        self.assertEqual(0x4000, fields.get("uaescsi.request.address[0]"))
        self.assertEqual(10, fields.get("uaescsi.request.type[0]"))
        self.assertEqual(0x4100, fields.get("uaescsi.request.data[0]"))

        restored = self.dir / "uaescsi-restored.state"
        run = self.probe("vsync", "--uaescsi-fixture", "--units", "1",
                         "--public-load-at", "1", str(source),
                         "--public-save-at", "1", str(restored))
        self.assertEqual([(1, 0, "")], run.loads)
        self.assertFalse(run.save_errors)
        self.assertEqual(source.read_bytes(), restored.read_bytes())
        self.assertEqual(saved.saves[0][2], run.saves[0][2])

    def test_reset_aborts_requests_and_closes_guest_owner_without_leaking_backend(self):
        path = self.dir / "uaescsi-reset.state"
        run = self.probe("vsync", "--uaescsi-fixture", "--units", "2",
                         "--reset-at", "1", "--save-fields-at", "2", str(path))
        self.assertFalse(run.save_errors)
        fields = FieldTable(path.read_bytes(), DISK, run.fields)
        self.assertEqual(0, fields.get("ide.cd.opens"))
        self.assertEqual(0, fields.get("uaescsi.opencnt"))
        self.assertEqual(0, fields.get("uaescsi.open.active[0]"))
        self.assertEqual(0, fields.get("uaescsi.request.active[0]"))

    def test_bad_catalog_guest_addresses_and_requests_are_refused_before_mutation(self):
        _, fields, _ = self.snapshot()
        attempts = [
            ("catalog", fields.with_values({"ide.cd.ordinal": 2}), CORRUPT, "uaescsi"),
            ("backend", fields.with_values({"uaescsi.backend.present": 2}), CORRUPT, "uaescsi"),
            ("request address", fields.with_values({"uaescsi.request.address[0]": 1}), CORRUPT, "uaescsi"),
            ("request type", fields.with_values({"uaescsi.request.type[0]": 99}), CORRUPT, "uaescsi"),
            ("request data", fields.with_values({"uaescsi.request.data[0]": 0x5000}), CORRUPT, "uaescsi"),
            ("open slot", fields.with_values({"uaescsi.open.active[1]": 2}), CORRUPT, "uaescsi"),
        ]
        self.refusals(attempts, extra=("--uaescsi-fixture",), unit=2, units=4)


if __name__ == "__main__":
    unittest.main()
