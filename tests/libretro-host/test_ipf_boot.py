"""T58: a free boot block and the owner's IPF boot and restore through libretro.

Opt-in: P6_KICKSTART_ROM (1.3), P6_IPF_FILE (raw IPF or ZIP). Missing assets skip,
so the T58 gate is BLOCKED. The default owner's run reaches Amiga Soccer's title.
"""
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_libretro as lr
import test_state as state
from tests.build_support import build_targets
from tests.media import ipf_boot as fixture


@unittest.skipUnless(lr.TOOLS and fixture.ROM and fixture.OWNER, "T58 needs P6_KICKSTART_ROM and P6_IPF_FILE")
class IpfBootTests(lr.LibretroHarness):
    @classmethod
    def setUpClass(cls):
        artifacts = build_targets(lr.ROOT / "build/state-opt", ("p6_libretro_host", "puae6_libretro"), optimized=True)
        cls.host, cls.core = artifacts["p6_libretro_host"], artifacts["puae6_libretro"]
        cls.tmp = tempfile.TemporaryDirectory(prefix="p6-ipf-boot-")
        cls.counter = 0
        cls.system_dir = Path(cls.tmp.name)
        (cls.system_dir / "kick34005.A500").symlink_to(Path(fixture.ROM).resolve(strict=True))

    def boot_restore(self, content, n, m, total, marker=False):
        path = self.work() / "disk.state"
        out = self.frontend("boot", "--runs", total + m, "--do", f"{n}:savefile:{path}",
                            "--do", f"{n + m}:loadfile:{path}", "--do", f"{n + m}:save:restored",
                            "--do", f"{total + m}:peek:0x{fixture.MARK:x}:4", content=content, system=self.system_dir).stdout
        self.assertEqual([m[3] for m in state.LOAD.finditer(out)], ["1"])
        state.StateTests.assert_replays(self, out, n + 1, n + m)
        saves = state.StateTests.saved(self, out)
        self.assertTrue(saves[str(path)]["ok"] and saves["restored"]["ok"])
        self.assertEqual(saves[str(path)]["hash"], saves["restored"]["hash"])
        first = [state.view(r) for r in self.runs(out)]
        other = self.frontend("boot", "--runs", m, "--do", f"0:loadfile:{path}:pos={n}", content=content, system=self.system_dir).stdout
        self.assertEqual([m[3] for m in state.LOAD.finditer(other)], ["1"])
        self.assertEqual([state.view(r) for r in self.runs(other)], first[n:n + m])
        self.assertGreater(len({r["px"] for r in self.runs(out)}), 3, "boot changes the display")
        if marker:
            self.assertIn(f"peek 0x{fixture.MARK:x} 4 {fixture.MAGIC.hex()}", out[-1000:])

    def by_unit(self, out, picture=True):
        return state.StateTests.by_unit(self, out, picture)

    def test_free_ipf_executes_its_boot_block_and_restores(self):
        path = self.work() / "free.ipf"
        path.write_bytes(fixture.boot_image())
        self.boot_restore(path, 600, 100, 800, marker=True)

    def test_owner_ipf_boots_and_restores_in_the_same_and_a_new_process(self):
        self.boot_restore(fixture.owner_image(self.work()), 1000, 200, 2800)


if __name__ == "__main__":
    unittest.main()
