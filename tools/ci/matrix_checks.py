"""Shared observation oracle for local Docker and distributed hosted matrices."""
from tests.portability.row_driver import PROBE_SCHEMA


def check_observations(expected, baseline, host, label="matrix"):
    """Check complete current observations; no files, saved verdicts or execution."""
    def require(condition, message):
        if not condition:
            raise AssertionError(f"{label}: {message}")

    require(bool(expected["cases"]), "empty golden corpus")
    require(type(expected.get("probe_schema")) is int and expected["probe_schema"] == PROBE_SCHEMA,
            "unsupported golden observation schema")
    for variant, record in (("baseline", baseline), ("host", host)):
        require(type(record.get("probe_schema")) is int and record["probe_schema"] == PROBE_SCHEMA,
                "unsupported corpus observation schema")
        require(record["errors"] == {}, f"{variant} corpus errors: {record['errors']}")
        require(set(record["cases"]) == set(expected["cases"]), f"{variant} missing/extra corpus cases")
    for case_id, wanted in expected["cases"].items():
        got, shifted = baseline["cases"][case_id], host["cases"][case_id]
        require(all(type(item.get("probe_schema")) is int and item["probe_schema"] == PROBE_SCHEMA
                    for item in (wanted, got, shifted)), "unsupported case observation schema")
        for field in ("hardware", "av"):
            require(got[field] == wanted[field], f"{case_id} {field} golden mismatch")
            require(shifted[field] == got[field], f"DET06 {case_id} {field} host mismatch")
