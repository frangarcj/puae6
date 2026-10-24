"""T72 (AV01, WB03): the resolution and the line mode on the three surfaces. headless takes --resolution and --line-mode, the BizHawk core the VideoResolution and VideoLineMode sync
settings, libretro the puae6_video_resolution and puae6_video_line_mode options.

The options are p6_config.video_resolution and video_line_mode (config/schema/options.json names every spelling; this file reads the schema and has no table of its own). What is decided here:
PICTURE    the core's pictures are the headless runner's (the same size and the same bytes: the hash over the active pixels, which are the whole of the buffer BizHawk is told,
           is the same function in both) and its ticks are the same, at every raster;
WB03       save at N, go on, load, repeat: the same units and digest at every raster;
ID         a state or a movie of one raster is refused by a core with any other (OtherConfig, before the sandbox is touched) and accepted by the same one; a core at the profile's raster has
           the identity it always had (the text of the identity names a setting only when it is off the default).

Needs the Waterbox runtime and the guest (tools/build_waterbox.py --puae6), dotnet and the local NuGet cache for the wrapper part, and the native build for headless.
    python3 -m unittest discover -s tests/waterbox-state -p "test_wrapper_raster.py"
"""
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests/waterbox-smoke"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_waterbox  # noqa: E402
import generate_options as gen  # noqa: E402
from tests.host.test_bridge_lifecycle import TOOLS, build_probe, builder  # noqa: E402
from tests.scenarios.a500.display import rom_source  # noqa: E402
from tests.step.test_display import CASES  # noqa: E402
from test_puae6 import runtime_available  # noqa: E402

GUEST = build_waterbox.BUILD / "puae6/release/puae6.wbx"
RECEIPT = build_waterbox.BUILD / "puae6/puae6-receipt.json"
UNIT = re.compile(r"^u (\S+) (\d+) ticks=(\d+) hash=(\w+) audio=(\d+) pcm=(\w+) lag=(\d) light=(\d) events=(\d+)$")
SCHEMA = gen.load_schema()
RESOLUTION = next(o for o in SCHEMA["options"] if o["id"] == "video_resolution")
LINE_MODE = next(o for o in SCHEMA["options"] if o["id"] == "video_line_mode")
RASTERS = [(r["value"], r.get("wrapper_name", r["value"]), RESOLUTION["headless"]["names"][r["value"]], l["value"], l.get("wrapper_name", l["value"]), LINE_MODE["headless"]["names"][l["value"]])
           for r in RESOLUTION["values"] for l in LINE_MODE["values"]]
UNITS = 8


@unittest.skipUnless(runtime_available() and TOOLS, "Waterbox runtime or puae6.wbx not built (tools/build_waterbox.py --puae6)")
class WrapperRasterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sha = json.loads(RECEIPT.read_text())["guest"]["sha256"]
        cls.headless = build_probe(ROOT / "build/link-audit", False, "p6_headless")
        cls.tmp = tempfile.TemporaryDirectory(prefix="p6-wrapper-raster-")
        cls.dir = Path(cls.tmp.name)
        cls.rom = cls.dir / "bars.rom"
        cls.rom.write_bytes(builder.build(rom_source(CASES["hires-bars"])))
        cls.count = 0

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def session(self, lines, expect=0):
        WrapperRasterTests.count += 1
        path = self.dir / f"program-{WrapperRasterTests.count}.txt"
        path.write_text("\n".join(lines) + "\n")
        result = build_waterbox.run_ffi("session", "--runtime", "libwaterboxhost.dylib", "--guest", str(GUEST), "--sha", self.sha, "--firmware", str(self.rom), "--program", str(path))
        self.assertEqual(expect, result.returncode, f"{result.stdout[-2500:]}\n{result.stderr[-2500:]}")
        return result.stdout

    @staticmethod
    def units(out, name):
        return [m.groups() for m in map(UNIT.match, out.splitlines()) if m and m[1] == name]

    @staticmethod
    def digests(out, name):
        return {m[1]: m[2] for m in re.finditer(rf"^digest {name} (\S*) sha=(\w+) ", out, re.M)}

    @staticmethod
    def core(name, resolution=None, lines=None):
        return f"core {name} machine=a500" + (f" vres={resolution}" if resolution else "") + (f" vlines={lines}" if lines else "")

    # --- the same pictures as headless -----------------------------------------------------------------------------------------------------

    def test_every_raster_gives_the_ticks_and_the_pictures_of_headless_with_the_same_flags(self):
        program = []
        for n, (_, resolution, _, _, lines, _) in enumerate(RASTERS):
            program += [self.core(f"C{n}", resolution, lines), f"run C{n} {UNITS}", f"dispose C{n}"]
        out = self.session(program)
        for n, (value, _, flag, mode, _, mode_flag) in enumerate(RASTERS):
            with self.subTest(resolution=value, lines=mode):
                units = self.units(out, f"C{n}")
                self.assertEqual(UNITS, len(units))
                result = subprocess.run([str(self.headless), "--run", str(self.rom), "--units", str(UNITS), "--resolution", flag, "--line-mode", mode_flag], capture_output=True, text=True,
                                        timeout=900)
                self.assertEqual(0, result.returncode, result.stderr)
                headless = re.findall(r"^unit (\d+) ticks=(\d+) video=1 active=(\d+)x(\d+) .* hash=(\w+) ", result.stdout, re.M)
                self.assertEqual([int(m[1]) for m in re.finditer(r"^unit \d+ ticks=(\d+) ", result.stdout, re.M)], [int(u[2]) for u in units])
                for unit in units[3:]:
                    hashes = [m[4] for m in headless if int(m[0]) == int(unit[1])]
                    self.assertEqual(hashes, [unit[3]], f"unit {unit[1]}")

    # --- T72a: the buffer of every machine -------------------------------------------------------------------------------------------------

    def test_every_machine_announces_before_the_first_frame_the_buffer_its_guest_delivers(self):
        # The height follows the Agnus of the preset: an OCS one (A500, A500 OG, A1000, A2000 OG) keeps 486 rows in NTSC, an ECS or AGA one (A500+, A600, A1200, A2000) reserves 576; single lines
        # halve it. The wrapper announces it at the load (`buffer` right after `core`) and refuses any frame whose buffer is another one (Puae6Core.CopyOut), so running units proves the
        # guest delivers the announced numbers; headless and libretro are checked against p6_query_video by tests/step/test_resolution.py and tests/libretro-host/test_machines.py.
        ocs = {"a500", "a500og", "a1000", "a2000og"}
        machines = ("a500", "a500og", "a500plus", "a600", "a1000", "a1200", "a2000og", "a2000")
        program, expected = [], []
        for machine in machines:
            for standard in ("pal", "ntsc"):
                for resolution, lines in (("Hires", "Double"), ("Lores", "Single"), ("SuperHires", "Double")):
                    name = f"M{len(expected)}"
                    rows = (486 if standard == "ntsc" and machine in ocs else 576) // (2 if lines == "Single" else 1)
                    width = {"Lores": 380, "Hires": 760, "SuperHires": 1520}[resolution]
                    program += [f"core {name} machine={machine} vres={resolution} vlines={lines}" + (" ntsc" if standard == "ntsc" else ""), f"buffer {name}", f"run {name} 2", f"dispose {name}"]
                    expected.append((name, machine, standard, resolution, lines, f"{width}x{rows}"))
        out = self.session(program)
        seen = dict(re.findall(r"^buffer (\S+) (\d+x\d+)$", out, re.M))
        for name, machine, standard, resolution, lines, size in expected:
            with self.subTest(machine=machine, standard=standard, resolution=resolution, lines=lines):
                self.assertEqual(size, seen.get(name))
                self.assertEqual(2, len(self.units(out, name)))

    # --- WB03 ------------------------------------------------------------------------------------------------------------------------------

    def test_save_go_on_load_and_repeat_gives_the_same_units_and_machine_at_every_raster(self):
        for value, resolution, _, mode, lines, _ in RASTERS:
            with self.subTest(resolution=value, lines=mode):
                out = self.session([self.core("A", resolution, lines), "run A 3", "save A s", "run A 5", "digest A first", "load A s", "run A 5", "digest A second"])
                units = self.units(out, "A")
                self.assertEqual(13, len(units))
                self.assertEqual([u[1:] for u in units[3:8]], [u[1:] for u in units[8:]])
                digests = self.digests(out, "A")
                self.assertEqual(digests["first"], digests["second"])

    # --- the identity ----------------------------------------------------------------------------------------------------------------------

    def test_a_state_of_a_raster_loads_in_the_same_one_and_is_refused_by_any_other_before_the_sandbox_is_touched(self):
        names = [f"{r}-{l}" for _, r, _, _, l, _ in RASTERS]
        program = []
        for (_, resolution, _, _, lines, _), name in zip(RASTERS, names):
            program += [self.core(f"S{name}", resolution, lines), f"run S{name} 3", f"save S{name} s{name}", f"digest S{name} saved", f"dispose S{name}"]
        for (_, resolution, _, _, lines, _), name in zip(RASTERS, names):
            program += [self.core(f"T{name}", resolution, lines), f"run T{name} 2", f"digest T{name} before"]
            program += [f"load T{name} s{other}" for other in names]
            program += [f"digest T{name} after"]
        out = self.session(program)
        for name in names:
            refusals = re.findall(rf"^rejected T{name} s(\S+) reason=(\w+) message=(.*)$", out, re.M)
            self.assertEqual(sorted(other for other in names if other != name), sorted(r[0] for r in refusals), f"{name} refuses every state of another raster and only those")
            for other, reason, message in refusals:
                self.assertEqual("OtherConfig", reason, f"{name} <- {other}")
                self.assertIn("other sync settings", message)
            self.assertEqual(self.digests(out, f"S{name}")["saved"], self.digests(out, f"T{name}")["after"], name)

    def test_a_movie_of_a_raster_is_refused_by_any_other_and_accepted_by_the_same_one(self):
        movies = {}
        for _, resolution, _, _, lines, _ in RASTERS[:4]:
            name = f"{resolution}-{lines}"
            movies[name] = self.dir / f"movie-{name}.bk2"
            out = self.session([self.core("R", resolution, lines), "run R 5 record", f"endmovie R {movies[name]}"])
            self.assertIn(f"movie {movies[name]} frames=5", out)
        for _, resolution, _, _, lines, _ in RASTERS[:4]:
            name = f"{resolution}-{lines}"
            with self.subTest(played=name):
                others = [other for other in movies if other != name]
                out = self.session([self.core("P", resolution, lines)] + [f"moviecheck P {movies[other]}" for other in others] + [f"moviecheck P {movies[name]}"])
                self.assertEqual(len(others), len(re.findall(r"^movie rejected P reason=OtherConfig ", out, re.M)), out[-1500:])
                self.assertEqual(1, out.count("movie accepted P"))

    def test_at_the_profile_raster_the_identity_is_the_one_it_always_was(self):
        source = (ROOT / "integration/bizhawk/core/Puae6/Puae6Core.cs").read_text(encoding="utf-8")
        self.assertIn('if (_syncSettings.VideoResolution != Puae6VideoResolution.Hires) memory += ";resolution=" + _syncSettings.VideoResolution;', source)
        self.assertIn('if (_syncSettings.VideoLineMode != Puae6VideoLineMode.Double) memory += ";linemode=" + _syncSettings.VideoLineMode;', source)
        out = self.session([self.core("A"), "run A 4", "save A s", self.core("B", "Hires", "Double"), "load B s", "run A 3", "run B 3", "digest A a", "digest B b"])
        self.assertNotIn("rejected", out)
        self.assertEqual(self.digests(out, "A")["a"], self.digests(out, "B")["b"])


if __name__ == "__main__":
    unittest.main()
