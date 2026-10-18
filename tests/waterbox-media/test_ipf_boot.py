"""T58: real Kickstart boot and snapshot restore of free/owner IPFs in the BizHawk core.

P6_KICKSTART_ROM and P6_IPF_FILE are opt-in assets, never committed. Uses the
actual managed Puae6Core and Waterbox sandbox, including a fresh process restore.
"""
from pathlib import Path
import os
import tempfile
import unittest

from support import AVAILABLE, GUEST, RECEIPT, Sessions, build_waterbox
from tests.media import ipf_boot as fixture
import json


@unittest.skipUnless(AVAILABLE and fixture.ROM and fixture.OWNER, "T58 needs Waterbox, P6_KICKSTART_ROM and P6_IPF_FILE")
class IpfBootSandboxTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="p6-ipf-wbx-")
        self.addCleanup(self.tmp.cleanup)
        self.directory = Path(self.tmp.name)
        self.rom = Path(fixture.ROM).resolve(strict=True)
        self.guest_sha = json.loads(RECEIPT.read_text())["guest"]["sha256"]
        self.count = 0

    def session(self, lines):
        self.count += 1
        program = self.directory / f"program-{self.count}.txt"
        program.write_text("\n".join(lines) + "\n")
        result = build_waterbox.run_ffi("session", "--runtime", "libwaterboxhost.dylib", "--guest", str(GUEST), "--sha", self.guest_sha,
                                        "--firmware", str(self.rom), "--program", str(program))
        self.assertEqual(0, result.returncode, result.stdout[-1500:] + result.stderr[-1500:])
        self.assertNotIn("rejected ", result.stdout)
        return result.stdout

    def boot_restore(self, image, n, m, total, marker=False):
        state = self.directory / "disk.state"
        picture_dir = Path(os.environ.get("P6_IPF_PICTURE_DIR", self.directory))
        picture_dir.mkdir(parents=True, exist_ok=True)
        picture = picture_dir / ("free.bin" if marker else "owner.bin")
        core = lambda name: f"core {name} adf={image}"
        out = self.session([core("A"), f"run A {n}", f"savefile A {state}", "save A s", f"run A {m}", "digest A first",
                            "load A s", f"run A {m}", "digest A again", f"run A {total - n - m}",
                            f"peek A Chip_RAM {fixture.MARK:x} 4", f"picture A {picture}", core("B"), "load B s", f"run B {m}", "digest B other"])
        self.assertIn("loaded A s", out)
        self.assertIn("loaded B s", out)
        units = Sessions.units(out, "A")
        self.assertEqual(total + m, len(units))
        first = [u[1:] for u in units[n:n + m]]
        self.assertEqual(first, [u[1:] for u in units[n + m:n + 2 * m]])
        self.assertEqual(first, [u[1:] for u in Sessions.units(out, "B")])
        digests = Sessions.digests(out, "A")
        self.assertEqual(digests["first"], digests["again"])
        self.assertEqual(digests["first"], Sessions.digests(out, "B")["other"])
        other = self.session([core("C"), f"loadfile C {state}", f"run C {m}", "digest C new"])
        self.assertIn("loaded C ", other)
        self.assertEqual(first, [u[1:] for u in Sessions.units(other, "C")])
        self.assertEqual(digests["first"], Sessions.digests(other, "C")["new"])
        self.assertGreater(len({u[3] for u in units}), 3, "boot changes the display")
        pixels = picture.read_bytes()[16:]
        self.assertGreater(len({pixels[i:i + 4] for i in range(0, len(pixels), 4)}), 8, "the booted display has content")
        if marker:
            self.assertEqual([fixture.MAGIC], Sessions.peeks(out, "A"))

    def test_free_ipf_executes_its_boot_block_and_restores(self):
        image = self.directory / "free.ipf"
        image.write_bytes(fixture.boot_image())
        self.boot_restore(image, 600, 100, 800, marker=True)

    def test_owner_ipf_boots_and_restores_in_the_same_and_new_sandbox_and_process(self):
        self.boot_restore(fixture.owner_image(self.directory), 1000, 200, 2800)


if __name__ == "__main__":
    unittest.main()
