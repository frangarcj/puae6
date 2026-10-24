"""T52: the clock of the CPU (p6_config.cpu_multiplier) in the native state and in the identity.

What is decided here (docs/adr/0009-state-format.md, validation = hash + identity + structure):
  * the multiplier is a restart option and part of the machine: it is not in the body of any chunk (the cycle unit is derived from the configuration when the machine starts), so a state of
    any clock has the chunks, the bodies and the bound of the machine's own, and what tells the clocks apart is the configuration hash;
  * the machine's own clock (2 on the machines with a 68000, 4 on the A1200), named or not, has the hash it always had; any other clock derives it from the hash so far and the resolved
    multiplier (domain p6/config/cpu/v1), together with the CPU model, made here from the envelope's definition (tests/state/envelope.py) and not read from the probe;
  * a state of one clock is INCOMPATIBLE in another by the hash: the bodies are the same, so the hash is the only guard, and the machine that loads a state of its own clock is judged by DET02
    (tests/state/test_det02_clock.py).
"""
import unittest

from tests.state import envelope as e
from tests.state import test_state_a600 as a600
from tests.state.harness import ProbeCase, parse_chunks

INCOMPATIBLE = 10


class ClockProbeCase(ProbeCase):
    ROMS = {"vsync": ("vsync", None, None)}

    def state(self, name, machine, *options):
        path = self.dir / f"{name}.state"
        self.probe_on(machine, "vsync", "--units", "3", "--save-at", "3", str(path), *options)
        return path.read_bytes()


class ClockIdentityTests(ClockProbeCase):
    def test_the_machines_own_clock_has_the_hash_it_had(self):
        base = e.config_hash(1, 1, 48000, 0, 1, 946684800)
        self.assertEqual(base, self.state("own", "a500")[a600.CONFIG_HASH])
        self.assertEqual(base, self.state("named", "a500", "--cpu-multiplier", "2")[a600.CONFIG_HASH])
        self.assertEqual(base, self.state("named-cpu", "a500", "--cpu", "68000", "--cpu-multiplier", "2")[a600.CONFIG_HASH])
        a1200 = e.config_hash(3, 1, 48000, 0, 1, 946684800)
        self.assertEqual(a1200, self.state("a1200", "a1200")[a600.CONFIG_HASH])
        self.assertEqual(a1200, self.state("a1200-named", "a1200", "--cpu-multiplier", "4")[a600.CONFIG_HASH])

    def test_any_other_clock_derives_the_hash_from_the_resolved_multiplier_and_the_cpu(self):
        base = e.config_hash(1, 1, 48000, 0, 1, 946684800)
        seen = {base}
        for multiplier in (1, 4, 8, 10, 12, 16):
            with self.subTest(multiplier=multiplier):
                found = self.state(f"m{multiplier}", "a500", "--cpu-multiplier", str(multiplier))[a600.CONFIG_HASH]
                self.assertEqual(found, e.config_hash_cpu(base, 0, multiplier))
                self.assertNotIn(found, seen)
                seen.add(found)
        # a 68010 at its own clock is the CPU alone, at another the two
        self.assertEqual(e.config_hash_cpu(base, 1, 0), self.state("c10", "a500", "--cpu", "68010")[a600.CONFIG_HASH])
        self.assertEqual(e.config_hash_cpu(base, 1, 8), self.state("c10m8", "a500", "--cpu", "68010", "--cpu-multiplier", "8")[a600.CONFIG_HASH])
        a1200 = e.config_hash(3, 1, 48000, 0, 1, 946684800)
        for multiplier in (1, 2, 8, 16):
            with self.subTest(machine="a1200", multiplier=multiplier):
                self.assertEqual(e.config_hash_cpu(a1200, 2, multiplier), self.state(f"a{multiplier}", "a1200", "--cpu-multiplier", str(multiplier))[a600.CONFIG_HASH])

    def test_the_clock_is_not_in_any_body_only_in_the_hash(self):
        own = parse_chunks(self.state("own", "a500"))
        for multiplier in ("1", "8", "16"):
            with self.subTest(multiplier=multiplier):
                fast = parse_chunks(self.state(f"f{multiplier}", "a500", "--cpu-multiplier", multiplier))
                self.assertEqual([(tag, version, len(body)) for tag, version, body in own], [(tag, version, len(body)) for tag, version, body in fast])

    def test_the_audio_the_cpu_and_the_clock_derive_one_after_the_other(self):
        base = e.config_hash(1, 1, 48000, 0, 1, 946684800)
        audio = e.config_hash_audio(base, 10, 1, 1, 0)
        found = self.state("all", "a500", "--sound-sep", "11", "--cpu", "68010", "--cpu-multiplier", "4")[a600.CONFIG_HASH]
        self.assertEqual(found, e.config_hash_cpu(audio, 1, 4))

    def test_a_state_of_one_clock_is_incompatible_in_another(self):
        states = {m: self.state(f"s{m}", "a500", "--cpu-multiplier", str(m)) for m in (1, 2, 8)}
        for mine in states:
            for theirs in states:
                if mine == theirs:
                    continue
                with self.subTest(machine=mine, state=theirs):
                    path = self.dir / "foreign.state"
                    path.write_bytes(states[theirs])
                    run = self.probe_on("a500", "vsync", "--units", "4", "--load-at", "3", str(path), "--cpu-multiplier", str(mine))
                    self.assertEqual([(3, INCOMPATIBLE)], [(unit, code) for unit, code, _ in run.loads])
                    self.assertIn("config_hash: differs from the loaded machine", run.loads[0][2])


if __name__ == "__main__":
    unittest.main()
