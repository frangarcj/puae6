"""T14a: manifests/state-inventory.json (key `savestate`) says, for every causal component of the A500 profile, who owns it, what upstream saves,
what it leaves out, what must never be serialized and which test will prove its restore. It is only useful while it stays true to the code
and to the plan, and while nothing the upstream serializer knows is missing from it.

Nothing here runs the emulator; the claims about the code are checked against the materialized upstream tree (skipped when it is not there).
"""
import json
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "manifests/state-inventory.json"
ADR = ROOT / "docs/adr/0009-state-format.md"
UPSTREAM = ROOT / "build/upstream"
FIELDS = {"id", "subsystem", "component", "owner", "chunk", "save", "restore", "reset", "in_profile", "upstream_compiled", "policy", "saved_fields",
          "omitted", "pointers", "rebuild", "evidence", "proof", "probe", "waterbox"}
SUBSYSTEMS = {"cpu", "scheduler", "ram", "rtc", "chipset", "video", "audio", "cia", "disk", "input", "bridge"}
REQUIRED = {"cpu", "scheduler", "chipset", "cia", "ram", "disk", "input", "audio", "video", "bridge"}     # docs/tasks T14 step 1
CAUSAL = {"serialize", "serialize_extended", "extension", "rebuild"}
DEFINED = {"P6_PORTABLE": 1, "CPUEMU_13": 1}          # what the profile defines; every other macro is undefined (SAVESTATE, DEBUGGER, FPUEMU...)


def plan_tasks():
    data = json.loads((ROOT / "plan/tasks.json").read_text())
    return {t["id"]: t for t in (data["tasks"] if isinstance(data, dict) else data)}


def evaluate(condition):
    """The value of a #if/#elif expression under DEFINED; an expression that cannot be evaluated counts as true."""
    text = re.sub(r"defined\s*\(\s*(\w+)\s*\)|defined\s+(\w+)", lambda m: "1" if (m[1] or m[2]) in DEFINED else "0", condition)
    text = re.sub(r"[A-Za-z_]\w*", lambda m: str(DEFINED.get(m[0], 0)), text)
    text = text.replace("&&", " and ").replace("||", " or ")
    text = re.sub(r"!(?!=)", " not ", text)
    if not re.fullmatch(r"[0-9\s()<>=!andornt+\-]*", text):
        return True
    try:
        return bool(eval(text, {"__builtins__": {}}))       # noqa: S307 (digits, operators and parentheses only)
    except Exception:
        return True


def active_lines(path):
    """Set of 1-based line numbers of `path` that the preprocessor keeps under DEFINED."""
    active = set()
    stack = []          # per #if: [parent_active, branch_taken, this_branch_active]
    text = re.sub(r"/\*.*?\*/", lambda m: "\n" * m[0].count("\n"), path.read_text(encoding="utf-8", errors="replace"), flags=re.S)
    live = True
    for number, line in enumerate(text.splitlines(), 1):
        stripped = line.strip()
        match = re.match(r"#\s*(ifdef|ifndef|if|elif|else|endif)\b(.*)", stripped)
        if match:
            kind, rest = match[1], re.sub(r"//.*", "", match[2]).strip()
            if kind in ("ifdef", "ifndef", "if"):
                value = (rest in DEFINED) if kind == "ifdef" else (rest not in DEFINED) if kind == "ifndef" else evaluate(rest)
                stack.append([live, value, live and value])
                live = live and value
            elif kind == "elif":
                parent, taken, _ = stack[-1]
                value = (not taken) and evaluate(rest)
                stack[-1] = [parent, taken or value, parent and value]
                live = parent and value
            elif kind == "else":
                parent, taken, _ = stack[-1]
                stack[-1] = [parent, True, parent and not taken]
                live = parent and not taken
            else:
                parent, _, _ = stack.pop()
                live = parent
            continue
        if live:
            active.add(number)
    return active


def first_identifier(text):
    match = re.match(r"\s*([A-Za-z_]\w*)", text or "")
    return match[1] if match else None


def first_path(text):
    match = re.match(r"\s*((?:build/upstream|build/generated|bridge|ports/portable|adapters)/[\w./+-]+?)(?=[\s,;:()]|$)", text)
    return match[1] if match else None


@unittest.skipUnless(MANIFEST.is_file(), "no manifest")
class StateComponentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = json.loads(MANIFEST.read_text(encoding="utf-8"))["savestate"]
        cls.components = cls.data["components"]
        cls.tasks = plan_tasks()

    def test_every_component_has_the_same_fields_a_known_policy_and_a_unique_id(self):
        ids = [c["id"] for c in self.components]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(set(self.data["policy_values"]), CAUSAL | {"noncausal", "out_of_profile"})
        for c in self.components:
            with self.subTest(component=c["id"]):
                self.assertEqual(set(c), FIELDS)
                self.assertIn(c["subsystem"], SUBSYSTEMS)
                self.assertIn(c["policy"], self.data["policy_values"])
                self.assertIsInstance(c["in_profile"], bool)
                self.assertIsInstance(c["upstream_compiled"], bool)
                self.assertIsInstance(c["evidence"], list)
                self.assertTrue(c["evidence"], "a claim without the lines that support it")
                for field in ("component", "owner", "saved_fields", "waterbox"):
                    self.assertTrue((c[field] or "").strip(), field)

    def test_every_subsystem_of_the_plan_has_components_in_the_profile(self):
        present = {c["subsystem"] for c in self.components if c["in_profile"]}
        self.assertTrue(REQUIRED <= present, REQUIRED - present)

    def test_no_causal_component_lacks_an_owner_a_restore_policy_and_a_test_that_exists_in_the_plan(self):
        for c in self.components:
            if c["policy"] not in CAUSAL or not c["in_profile"]:
                continue
            with self.subTest(component=c["id"]):
                self.assertTrue(first_path(c["owner"]) or c["owner"].startswith(("bridge", "ports", "build")), c["owner"])
                proof = c["proof"]
                self.assertIsNotNone(proof, "a causal component needs the test that proves its restore")
                self.assertIn(proof["task"], self.tasks)
                self.assertTrue(proof["checks"])
                self.assertTrue(set(proof["checks"]) <= set(self.tasks[proof["task"]]["checks"]),
                                f"{proof['checks']} are not checks of {proof['task']}")
                self.assertIn(proof["status"], {"pending", "done"})
                self.assertTrue(proof["what"].strip())
                self.assertIsNotNone(c["probe"], "a probe of the subsystem (T14b) or the task that will add its accessor")
                self.assertIn(c["probe"]["task"], self.tasks)

    def test_a_component_that_upstream_does_not_fully_save_names_what_it_leaves_out(self):
        for c in self.components:
            if c["policy"] in ("serialize_extended", "extension"):
                with self.subTest(component=c["id"]):
                    self.assertTrue((c["omitted"] or "").strip(), "an extension needs the list of variables it adds")
            if c["policy"] == "rebuild":
                with self.subTest(component=c["id"]):
                    self.assertTrue((c["rebuild"] or "").strip(), "a derived component says how it is rebuilt")
                    self.assertIn("DET02", c["proof"]["checks"])
            if c["policy"] == "noncausal":
                with self.subTest(component=c["id"]):
                    why = " ".join((c[k] or "").strip() for k in ("saved_fields", "pointers", "rebuild", "omitted"))
                    self.assertGreater(len(why), 40, "a noncausal component says why it is not state")

    def test_what_may_leave_a_waterbox_snapshot_has_its_poison_test_identified(self):
        for c in self.components:
            if "candidate to leave out" in c["waterbox"]:
                with self.subTest(component=c["id"]):
                    self.assertIn("T36", c["waterbox"])
        self.assertIn("T36", self.tasks)

    def test_findings_and_evidence_point_at_things_that_exist(self):
        for finding in self.data["findings"]:
            with self.subTest(finding=finding["id"]):
                self.assertIn(finding["task"], self.tasks)
                self.assertTrue(finding["finding"].strip() and finding["consequence"].strip())
        for c in self.components:
            for ref in c["evidence"]:
                with self.subTest(component=c["id"], evidence=ref):
                    path, _, line = ref.partition(":")
                    file = ROOT / path
                    if path.startswith(("build/upstream", "build/generated")) and not UPSTREAM.is_dir():
                        continue
                    self.assertTrue(file.is_file(), path)
                    if line:
                        self.assertLessEqual(int(line), len(file.read_text(encoding="utf-8", errors="replace").splitlines()))


@unittest.skipUnless(UPSTREAM.is_dir() and MANIFEST.is_file(), "the upstream tree is not materialized")
class UpstreamAgreementTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = json.loads(MANIFEST.read_text(encoding="utf-8"))["savestate"]
        cls.components = cls.data["components"]
        cls.units = set(json.loads((ROOT / "manifests/source-manifest.json").read_text())["selection"]["upstream_units"])
        cls.sources = {}

    def text(self, path):
        if path not in self.sources:
            self.sources[path] = path.read_text(encoding="utf-8", errors="replace")
        return self.sources[path]

    def definition(self, name, preferred):
        """(file, line) of the top-level definition of `name`, looking in the owner's file first and then in every source of the project."""
        pattern = re.compile(r"^(?!\s)(?!.*;\s*$)[\w:<>\s\*&]*\b%s\s*\(" % re.escape(name), re.M)
        candidates = ([preferred] if preferred else []) + sorted(UPSTREAM.rglob("*.cpp")) + sorted((ROOT / "bridge").rglob("*.cpp")) + \
            sorted((ROOT / "ports/portable").rglob("*.cpp")) + sorted((ROOT / "ports/portable").rglob("*.c"))
        for path in candidates:
            if path and path.is_file():
                match = pattern.search(self.text(path))
                if match:
                    return path, self.text(path)[:match.start()].count("\n") + 1
        return None, None

    def test_every_upstream_chunk_of_savestate_cpp_is_in_the_inventory_or_in_a_family_the_profile_does_not_have(self):
        text = (UPSTREAM / "savestate.cpp").read_text(encoding="utf-8", errors="replace")
        tags = set(re.findall(r'_T\("([A-Z0-9 ]{2,4}(?:%d)?)"\)', text))
        self.assertGreater(len(tags), 60)

        def family(tag):                                       # SPR0..SPR7, DSK0..DSK3, KBM1..KBM3, CDU%d: one family each
            tag = tag.replace("%d", "#")
            return tag[:-1] + "#" if tag[-1].isdigit() or tag[-1] == "#" else tag
        covered = {family(c["chunk"]) for c in self.components if c["chunk"]}
        out = {family(t) for f in self.data["out_of_profile_chunks"] for t in f["tags"]}
        self.assertEqual(set(), covered & out, "a chunk cannot be in the profile and out of it")
        missing = sorted(t for t in tags if family(t) not in covered | out)
        self.assertEqual([], missing, "upstream chunks that the audit does not mention")
        for tag in (t for f in self.data["out_of_profile_chunks"] for t in f["tags"]):
            with self.subTest(tag=tag):
                self.assertIn(tag.replace("%d", ""), text.replace("%d", ""), "an out-of-profile tag that savestate.cpp does not have")

    def test_upstream_compiled_says_whether_the_function_survives_the_preprocessor_of_the_profile(self):
        checked = 0
        for c in self.components:
            name = first_identifier(c["save"]) or first_identifier(c["restore"])
            if not name or not name.startswith(("save_", "restore_")) or name.endswith("_func"):
                continue
            owner = first_path(c["owner"])
            path, line = self.definition(name, ROOT / owner if owner else None)
            with self.subTest(component=c["id"], function=name):
                self.assertIsNotNone(path, f"{name} is not defined anywhere")
                if path.is_relative_to(UPSTREAM):
                    compiled = str(path.relative_to(UPSTREAM)) in self.units and line in active_lines(path)
                    self.assertEqual(c["upstream_compiled"], compiled,
                                     f"{name} at {path.relative_to(ROOT)}:{line}: in the compiled units and kept by the preprocessor is {compiled}")
                    checked += 1
        self.assertGreater(checked, 40)

    def test_the_ported_functions_a_component_names_exist(self):
        for c in self.components:
            for field in ("save", "restore"):
                name = first_identifier(c[field])
                if name and name.startswith("p6_"):
                    with self.subTest(component=c["id"], function=name):
                        path, _ = self.definition(name, None)
                        self.assertIsNotNone(path, f"{name} is not defined anywhere")


class EnvelopeDecisionTests(unittest.TestCase):
    """docs/adr/0009-state-format.md is what ADR01 asks of a decision, and its byte table adds up."""

    @classmethod
    def setUpClass(cls):
        cls.text = ADR.read_text(encoding="utf-8")

    def test_the_adr_has_the_parts_of_a_reviewable_decision(self):
        for heading in ("## Contexto", "## Decision", "## Evidencia", "## Alternativas descartadas", "## Costo", "## Riesgos", "## Validacion"):
            self.assertIn(heading, self.text)
        validation = self.text.split("## Validacion")[1]
        self.assertGreaterEqual(len(re.findall(r"^\d+\. ", validation, re.M)), 4, "concrete validation steps")

    def test_the_header_table_is_contiguous_and_ends_at_the_declared_header_size(self):
        rows = re.findall(r"^\s*\| (\d+) \| (\d+) \| (.+?) \|$", self.text, re.M)
        self.assertGreater(len(rows), 15)
        expected = 0
        for offset, size, name in rows:
            self.assertEqual(int(offset), expected, name)
            expected += int(size)
        self.assertEqual(336, expected)
        self.assertRegex(self.text, r"\| 12 \| 4 \| header_size \(336\) \|")

    def test_native_states_and_waterbox_snapshots_are_separate_formats_with_separate_hash_domains(self):
        for needle in ("P6NATIVE", "P6WATERB", "p6/probe/v1", "p6/config/v1", "p6/media-manifest/v1", "p6/core-build/v1", "p6/waterbox-"):
            self.assertIn(needle, self.text)
        domains = re.findall(r'"(p6/[\w-]+/v\d)"', self.text)
        self.assertEqual(len(domains), len(set(domains)), "a hash domain is used once")


if __name__ == "__main__":
    unittest.main()
