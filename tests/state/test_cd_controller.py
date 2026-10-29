"""The neutral selector preserves zero ABI identity and IDE has its own future.

T84e1 originally exercised the physical port before ATAPI existed. In the
integrated T84e contract IDE has a fixed source before load; ejecting it proves
the same empty-tray topology without weakening the owner requirement.
"""
import subprocess

from tests.state import test_det02 as d
from tests.state import test_state_a600 as a600
from tests.state.harness import ProbeCase


class CdControllerIdentityTests(ProbeCase):
    ROMS = {"vsync": d.Det02Tests.ROMS["vsync"]}

    def raw(self, machine, *arguments):
        flag = ["--machine", machine] if machine != "a500" else []
        result = subprocess.run([str(self.exe), str(self.roms["vsync"]), *flag, "--units", "4", "--every", "1", *arguments],
                                capture_output=True, text=True, timeout=1800)
        self.assertEqual(0, result.returncode, result.stderr[-1000:])
        return result.stdout

    def state(self, name, machine, *arguments):
        path = self.dir / name
        self.probe_on(machine, "vsync", "--units", "3", "--save-at", "3", str(path), *arguments)
        return path.read_bytes()

    def test_zero_auto_and_explicit_historical_none_are_byte_exact(self):
        self.assertEqual(self.raw("a500"), self.raw("a500", "--cd-controller", "auto"))
        self.assertEqual(self.raw("a500"), self.raw("a500", "--cd-controller", "none"))
        self.assertEqual(self.state("a500-auto.state", "a500"), self.state("a500-none.state", "a500", "--cd-controller", "none"))
        self.assertEqual(self.state("a600-auto.state", "a600"), self.state("a600-none.state", "a600", "--cd-controller", "none"))

    def test_ide_is_a_distinct_effective_topology_in_config_identity(self):
        auto = self.state("a600-auto-hash.state", "a600")
        ide = self.state("a600-ide-hash.state", "a600", "--cd-controller", "ide",
                         "--ide-cd-fixture", "--ide-cd-ejected")
        self.assertNotEqual(auto[a600.CONFIG_HASH], ide[a600.CONFIG_HASH])


class Det02IdeCdTopologyTests(d.Det02Case):
    MACHINE = "a600"
    ROMS = {"vsync": d.Det02Tests.ROMS["vsync"]}

    def arguments(self, extra):
        return ["--cd-controller", "ide", "--ide-cd-fixture", "--ide-cd-ejected",
                *super().arguments(extra)]


d.populate(Det02IdeCdTopologyTests,
           {"ide-vsync": ("vsync", [], (3,), (d.SAME, d.NEW))}, {})


if __name__ == "__main__":
    import unittest
    unittest.main()
