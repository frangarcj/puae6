"""What the Waterbox tests of the write overlay (T28) share: the guest and its receipt, a session of the managed harness (tests/ffi/Session.cs), and the parsing of its lines.

Needs the Waterbox runtime and the guest (tools/build_waterbox.py --puae6-run build/waterbox/boot.rom), dotnet and the local NuGet cache; the tests skip without them.
"""
import hashlib
import json
from pathlib import Path
import re
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
for entry in (ROOT / "tools", ROOT, ROOT / "tests/waterbox-smoke", ROOT / "tests/media-cow", Path(__file__).resolve().parent):
    if str(entry) not in sys.path:
        sys.path.insert(0, str(entry))
import build_waterbox  # noqa: E402
import med02  # noqa: E402
from tests.host.test_bridge_lifecycle import TOOLS, builder  # noqa: E402
from tests.scenarios.disk import disk  # noqa: E402
from test_puae6 import runtime_available  # noqa: E402

GUEST = build_waterbox.BUILD / "puae6/release/puae6.wbx"
RECEIPT = build_waterbox.BUILD / "puae6/puae6-receipt.json"
AVAILABLE = runtime_available() and TOOLS
SKIP = "Waterbox runtime or puae6.wbx not built (tools/build_waterbox.py --puae6-run build/waterbox/boot.rom)"

UNIT = re.compile(r"^u (\S+) (\d+) ticks=(\d+) hash=(\w+) audio=(\d+) pcm=(\w+) lag=(\d) light=(\d) events=(\d+)$")
MARKS, LOG, CAPTURES = disk.MARK_BASE, disk.LOG_BASE, med02.CAPTURE


def sha(data):
    return hashlib.sha256(data).hexdigest()


def tree(path):
    """(size, mtime_ns, sha256) of every file under `path`, by relative name: what 'no file changed' is compared with."""
    return {str(p.relative_to(path)): (p.stat().st_size, p.stat().st_mtime_ns, sha(p.read_bytes())) for p in sorted(path.rglob("*")) if p.is_file()}


class Sessions(unittest.TestCase):
    """A directory with the micro-ROMs and disks, and sessions of the harness."""

    FIRMWARE = {"med02": lambda: med02.rom_source(embedded=True)}
    count = 0

    @classmethod
    def setUpClass(cls):
        cls.guest_sha = json.loads(RECEIPT.read_text())["guest"]["sha256"]
        cls.tmp = tempfile.TemporaryDirectory(prefix="p6-overlay-")
        cls.dir = Path(cls.tmp.name)
        cls.media = cls.dir / "media"           # the files the core is given: they must be the same after anything the core does
        cls.out = cls.dir / "out"               # what the tests themselves write
        cls.media.mkdir()
        cls.out.mkdir()
        for name, source in cls.FIRMWARE.items():
            (cls.media / f"{name}.rom").write_bytes(builder.build(source()))
        cls.adf = {"A": disk.make_adf(med02.LABEL_A), "B": disk.make_adf(med02.LABEL_B), "C": disk.make_adf(0x43)}
        for name, image in cls.adf.items():
            (cls.media / f"{name}.adf").write_bytes(image)
        cls.files_before = tree(cls.media)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def tearDown(self):
        self.assertEqual(self.files_before, tree(self.media), "no file the core was given changed")

    def adfs(self, names="AB"):
        return ",".join(str(self.media / f"{n}.adf") for n in names)

    def core(self, name, adf="AB", writes=True, **options):
        words = [f"core {name}"]
        if adf:
            words.append(f"adf={self.adfs(adf)}")
        if writes:
            words.append("writes")
        words += [f"{k}={v}" if v is not True else k for k, v in options.items()]
        return " ".join(words)

    def session(self, lines, firmware="med02", expect=0, timeout=3600):
        type(self).count += 1
        path = self.out / f"program-{self.count}.txt"
        path.write_text("\n".join(lines) + "\n")
        result = build_waterbox.run_ffi("session", "--runtime", "libwaterboxhost.dylib", "--guest", str(GUEST), "--sha", self.guest_sha, "--firmware",
                                        str(self.media / f"{firmware}.rom"), "--program", str(path), timeout=timeout)
        self.assertEqual(expect, result.returncode, f"{result.stdout[-2500:]}\n{result.stderr[-2500:]}")
        return result.stdout

    # --- parsing -----------------------------------------------------------------------------------------------------------------------

    @staticmethod
    def units(out, name):
        return [m.groups() for m in map(UNIT.match, out.splitlines()) if m and m[1] == name]

    @staticmethod
    def digests(out, name):
        return {m[1]: m[2] for m in re.finditer(rf"^digest {name} (\S*) sha=(\w+) ", out, re.M)}

    @staticmethod
    def exports(out, name):
        """[(media index, result, bytes, sha)] in the order they were asked."""
        return [(int(m[1]), int(m[2]), int(m[3]), m[4]) for m in re.finditer(rf"^export {name} (\d+) result=(-?\d+) bytes=(\d+) sha=(\S+)", out, re.M)]

    @staticmethod
    def statuses(out, name):
        return [dict(index=int(m[1]), result=int(m[2]), capacity=int(m[3]), used=int(m[4]), medium=int(m[5]), refused=int(m[6]), raw=int(m[7]))
                for m in re.finditer(rf"^status {name} (\d+) result=(-?\d+) capacity=(\d+) used=(\d+) medium=(\d+) refused=(\d+) raw=(\d+)", out, re.M)]

    @staticmethod
    def peeks(out, name):
        return [bytes.fromhex(m[1]) for m in re.finditer(rf"^peek {name} \S+ \S+ (\w*)$", out, re.M)]

    @staticmethod
    def rejections(out, name):
        return [(m[1], m[2]) for m in re.finditer(rf"^rejected {name} \S+ reason=(\w+) message=(.*)$", out, re.M)]

    @staticmethod
    def saverams(out, name):
        """[(tag, bytes or None, sha or None, modified before)] in the order asked."""
        rows = []
        for m in re.finditer(rf"^saveram {name} (\S+) (?:bytes=(\d+) sha=(\w+)|null) modified=(\d)$", out, re.M):
            rows.append((m[1], int(m[2]) if m[2] else None, m[3], int(m[4])))
        return rows

    @staticmethod
    def modified(out, name):
        return [int(m[1]) for m in re.finditer(rf"^modified {name} (\d)$", out, re.M)]

    @staticmethod
    def saveram_rejections(out, name):
        return [(m[1], m[2]) for m in re.finditer(rf"^saveram-rejected {name} \S+ reason=(\w+) message=(.*)$", out, re.M)]

    @staticmethod
    def sizes(out):
        return {m[1]: int(m[2]) for m in re.finditer(r"^saved (\S+) bytes=(\d+) ", out, re.M)}


def watch(name, size):
    """The program lines that print Chip RAM that tell what the guest did: the markers, the drive-line log and the three captured tracks."""
    return [f"peek {name} Chip_RAM {MARKS:x} {2 * len(med02.MARKERS)}", f"peek {name} Chip_RAM {LOG:x} {size}"] + [
        f"peek {name} Chip_RAM {CAPTURES[c]:x} {2 * med02.CAPTURE_WORDS}" for c in ("c1", "c2", "c3")]
