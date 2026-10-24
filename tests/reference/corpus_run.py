"""What the three kinds of corpus test (REF01, DET02, DET03) have in common: the media the entries name, written to a directory, and the probe arguments an entry stands for.

An entry's `extra` is a list of probe arguments or a key of corpus.MEDIA:
  disk          disk A in DF0 from boot (tests/state/test_det02.py's);
  swap          disks A and B, A in DF0 from boot, ejected after unit 9, B mounted after unit 11 (the change lands 100 frames later);
  hdf           the hard file of tests/hdf (an RDB with an empty FFS partition), as tests/state/test_det02_hdf.py mounts it;
  floppy-write  the writable disks A and B of tests/media-cow/med02.py with the three track buffers poked into Chip RAM at unit 1, as tests/media-cow/test_med02.py does.
"""
import importlib.util
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from tests.scenarios.corpus import corpus  # noqa: E402


def _med02():
    if "med02" in sys.modules:
        return sys.modules["med02"]
    spec = importlib.util.spec_from_file_location("med02", ROOT / "tests/media-cow/med02.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["med02"] = module
    spec.loader.exec_module(module)
    return module


class Assets:
    """The files of the media keys, written once into `directory`."""

    def __init__(self, directory):
        from tests.hdf import hdf_image
        from tests.scenarios.disk import disk
        med02 = _med02()
        self.dir = Path(directory)
        self.a, self.b = self.dir / "a.adf", self.dir / "b.adf"
        self.a.write_bytes(corpus.adf_bytes("a"))
        self.b.write_bytes(corpus.adf_bytes("b"))
        self.hdf = self.dir / "d.hdf"
        self.hdf.write_bytes(hdf_image.rdb_image(cylinders=64))
        self.fa, self.fb = self.dir / "fa.adf", self.dir / "fb.adf"
        self.fa.write_bytes(disk.make_adf(med02.LABEL_A))
        self.fb.write_bytes(disk.make_adf(med02.LABEL_B))
        self.buffers = {}
        for name, address in med02.BUF_ADDR.items():
            path = self.dir / f"{name}.bin"
            path.write_bytes(med02.buffer_bytes(name))
            self.buffers[name] = (address, path)

    def arguments(self, entry):
        return self.expand(entry.extra)

    def expand(self, extra):
        if isinstance(extra, (list, tuple)):
            return list(extra)
        if extra == "disk":
            return ["--adf", str(self.a), "--event-at", "0", "0", "0", "1"]
        if extra == "swap":
            return ["--adf", str(self.a), "--adf", str(self.b), "--event-at", "0", "0", "0", "1", "--event-at", "9", "0", "-1", "2", "--event-at", "11", "0", "1", "3"]
        if extra == "hdf":
            return ["--hdf", str(self.hdf)]
        if extra == "floppy-write":
            args = ["--writable", "--adf", str(self.fa), "--adf", str(self.fb), "--event-at", "0", "0", "0", "1"]
            for address, path in self.buffers.values():
                args += ["--ram-at", "1", hex(address), str(path)]
            return args
        raise ValueError(f"unknown media key {extra!r}")


def machine_arguments(entry):
    return [] if entry.machine == "a500" else ["--machine", entry.machine]


def probe_arguments(entry, assets, units=None):
    """The arguments after the ROM of one run of the entry: machine, length and the entry's inputs, events and media."""
    return [*machine_arguments(entry), "--units", str(units or entry.units), *assets.arguments(entry)]
