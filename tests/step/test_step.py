"""STEP01 in bridge/headless: N p6_run_frame calls equal the same engine advancing N units without returning.

The engine is built in its own tree with P6_UNIT_TRACE, which adds an observer and a swallow counter at the
one place where the video boundary is decided (patch 0062); no profile or release build has it. For every
micro-ROM scenario the harness prints the full-state digest at each boundary in two modes and the outputs
must be identical, repeat identically in a second process, and satisfy the guest-side invariants. A set of
mutants of the mechanism (compiled from edited copies of the patched sources) must each break the comparison,
which proves the comparison can fail. Opt-in mutants: P6_TEST_MUTANTS=1.
"""
from concurrent.futures import ThreadPoolExecutor
import importlib.util
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import tempfile
import unittest

from tests.build_support import build_targets

ROOT = Path(__file__).resolve().parents[2]
BUILD = ROOT / "build/step-trace"
FIXTURES = ROOT / "tests/fixtures/microrom"
TOOLS = all(shutil.which(t) for t in ("cmake", "ninja", "clang", "clang++"))
UNITS = (1, 2, 3, 7)

spec = importlib.util.spec_from_file_location("microrom_builder", FIXTURES / "build_rom.py")
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)

LINE = re.compile(r"^unit (\d+) pc=([0-9a-f]+) op=([0-9a-f]+) d0=([0-9a-f]+) d1=([0-9a-f]+) sr=([0-9a-f]+) line=(-?\d+) "
                  r"hpos=(\d+) cycles=(-?\d+) halted=(-?\d+) stopped=(\d+) video=(\d+)/(\d+) size=(\d+)x(\d+)/(\d+) "
                  r"a0=([0-9a-f]+) digest=([0-9a-f]+)$")


def scenarios():
    """(name, source file, defines) for every ROM the matrix runs."""
    rows = [("counter", "boot.s", {}), ("vsync", "vsync.s", {}), ("stop", "stop.s", {}), ("halt", "halt.s", {})]
    rows += [(f"copper-hp{hp}", "copper_irq.s", {"HP": hp}) for hp in range(0, 9)]
    rows += [(f"longops-pad{pad}", "long_ops.s", {"PAD": pad}) for pad in range(0, 6)]
    return rows


def make_rom(directory, name, source, defines):
    path = Path(directory) / f"{name}.rom"
    text = (FIXTURES / source).read_text(encoding="utf-8")
    path.write_bytes(builder.build(text, defines) if defines else builder.build(text))
    return path


def configure_trace_tree(directory=None, sanitizers=False):
    directory = directory or BUILD
    return build_targets(directory, ["p6_step_probe"], sanitizers=sanitizers, trace=True)["p6_step_probe"]


def run_probe(exe, rom, units, mode, seed=1, timeout=120, extra=(), env=None):
    try:
        result = subprocess.run([str(exe), str(rom), str(units), mode, str(seed), *extra], capture_output=True,
                                text=True, timeout=timeout, env=None if env is None else {**os.environ, **env})
    except subprocess.TimeoutExpired:
        return None, "timeout"
    return result.returncode, result.stdout + result.stderr


def parse(output):
    rows = [m.groups() for m in map(LINE.match, output.splitlines()) if m]
    return rows


@unittest.skipUnless(TOOLS, "toolchain unavailable")
class StepTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.exe = configure_trace_tree()
        cls.tmp = tempfile.TemporaryDirectory(prefix="p6-step-")
        cls.roms = {name: make_rom(cls.tmp.name, name, src, defs) for name, src, defs in scenarios()
                    if (FIXTURES / src).exists()}

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def runs(self, name, units, mode, seed=1):
        code, out = run_probe(self.exe, self.roms[name], units, mode, seed)
        self.assertEqual(code, 0, f"{name} {mode} x{units}: {out[-400:]}")
        return out

    def test_every_scenario_needs_its_rom(self):
        self.assertEqual({n for n, s, _ in scenarios()}, set(self.roms))

    def test_n_calls_equal_continuous_advance(self):
        def compare(job):
            name, units = job
            return job, self.runs(name, units, "steps"), self.runs(name, units, "continuous")
        jobs = [(name, units) for name in self.roms for units in UNITS]
        with ThreadPoolExecutor(max_workers=max(1, (os.cpu_count() or 2) - 2)) as pool:
            for (name, units), steps, continuous in pool.map(compare, jobs):
                with self.subTest(scenario=name, units=units):
                    self.assertEqual(len(parse(steps)), units)
                    self.assertEqual(steps, continuous)

    def test_two_processes_agree(self):
        for name in ("vsync", "stop", "halt", "copper-hp3", "longops-pad2"):
            with self.subTest(scenario=name):
                self.assertEqual(self.runs(name, 5, "steps"), self.runs(name, 5, "steps"))

    def test_seed_changes_the_start_and_only_the_start(self):
        one, two = parse(self.runs("vsync", 4, "steps", 1)), parse(self.runs("vsync", 4, "steps", 2))
        self.assertNotEqual([r[8] for r in one], [r[8] for r in two])  # different start-up delay => other cycles
        self.assertNotEqual(one[0][-1], two[0][-1])

    def test_counter_rom_counts_and_every_unit_advances_time(self):
        rows = parse(self.runs("counter", 5, "steps"))
        d0 = [int(r[3], 16) for r in rows]
        cycles = [int(r[8]) for r in rows]
        self.assertTrue(all(b > a for a, b in zip(d0, d0[1:])))
        self.assertTrue(all(b > a for a, b in zip(cycles, cycles[1:])))
        # Each unit lasts one non-interlaced PAL frame (312 lines of 227 CCK); the boundary falls at an
        # instruction boundary a few CCK after the render point, so lengths differ by that slop only.
        for a, b in zip(cycles, cycles[1:]):
            self.assertLessEqual(abs((b - a) - 312 * 227 * 512), 16 * 512, (a, b))

    def test_vsync_handler_runs_once_per_unit(self):
        rows = parse(self.runs("vsync", 7, "steps"))
        handled = [int(r[4], 16) for r in rows]
        self.assertTrue(all(b - a == 1 for a, b in zip(handled, handled[1:])), handled)
        self.assertTrue(all(int(r[9]) == 0 for r in rows))

    def test_stop_and_halt_scenarios_reach_their_states(self):
        stop = parse(self.runs("stop", 5, "steps"))
        self.assertTrue(any(int(r[10]) for r in stop))
        halt = parse(self.runs("halt", 4, "steps"))
        self.assertTrue(all(int(r[9]) != 0 for r in halt))
        self.assertTrue(all(b > a for a, b in zip([int(r[8]) for r in halt], [int(r[8]) for r in halt][1:])))

    def test_boundary_covers_long_instructions_and_interrupt_entry(self):
        opcodes = set()
        for name in [n for n in self.roms if n.startswith("longops")]:
            opcodes |= {int(r[2], 16) for r in parse(self.runs(name, 7, "steps"))}
        self.assertTrue(any(op & 0xF1C0 == 0x80C0 for op in opcodes), "no boundary inside a DIVU")
        self.assertTrue(any(op & 0xFB80 == 0x4880 for op in opcodes), "no boundary inside a MOVEM")
        entered = 0
        for name in [n for n in self.roms if n.startswith("copper")]:
            entered += sum(1 for r in parse(self.runs(name, 5, "steps")) if 0xF80100 <= int(r[1], 16) < 0xF80200)
        self.assertGreater(entered, 0, "no boundary coincided with the interrupt handler")


FRAME = re.compile(r"^frame (\d+) ticks=(\d+) video=(\d) bytes=(\d+) format=(\d+) size=(\d+)x(\d+) stride=(\d+) "
                   r"active=(\d+),(\d+),(\d+),(\d+) aspect=(\d+)/(\d+) cadence=(\d+)/(\d+) parity=(\d+) flags=(\d+) "
                   r"outside=(zero|dirty) pixels=([0-9a-f]+) audio=(-?)(\d+)$")


def frames_of(output):
    return [tuple(int(v) if v.isdigit() else v for v in m.groups()) for m in map(FRAME.match, output.splitlines()) if m]


@unittest.skipUnless(TOOLS, "toolchain unavailable")
class VideoMetadataTests(unittest.TestCase):
    """What p6_run_frame reports about the picture, per video standard (AV01 metadata half)."""

    @classmethod
    def setUpClass(cls):
        cls.exe = configure_trace_tree()
        cls.tmp = tempfile.TemporaryDirectory(prefix="p6-video-")
        cls.rom = make_rom(cls.tmp.name, "vsync", "vsync.s", {})

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def frames(self, units, standard, mode="frames"):
        code, out = run_probe(self.exe, self.rom, units, mode, 1, timeout=180, extra=[standard])
        self.assertEqual(code, 0, out[-400:])
        return out, frames_of(out)

    def test_geometry_aspect_and_cadence_per_standard(self):
        expected = {"pal": (576, 574, (26, 25), (3546895, 70824)), "ntsc": (486, 484, (43, 50), (715909, 11921))}
        for standard, (height, active_h, aspect, cadence) in expected.items():
            _, rows = self.frames(4, standard)
            self.assertEqual(len(rows), 4)
            for row in rows:
                with self.subTest(standard=standard, unit=row[0]):
                    (_, _, video, nbytes, fmt, w, h, stride, ax, ay, aw, ah, an, ad, cn, cd, parity, flags, outside, *_) = row
                    self.assertEqual((video, fmt, w, h, stride), (1, 1, 760, height, 7296))
                    self.assertEqual(nbytes, stride * h)
                    self.assertEqual((ax, ay, aw, ah), (0, 0, 756, active_h))
                    self.assertEqual((an, ad), aspect)
                    self.assertEqual((cn, cd), cadence)
                    self.assertEqual((parity, flags & ~1, outside), (0, 0, "zero"))   # bit 0 is the lag flag (T10b)
            self.assertEqual([r[0] for r in rows], [1, 2, 3, 4])
            ticks = [r[1] for r in rows]
            self.assertTrue(all(b > a for a, b in zip(ticks, ticks[1:])))

    def test_cadence_constants_are_the_clock_over_the_measured_unit_length(self):
        from fractions import Fraction
        pal, ntsc = 3546895, 3579545
        table = {(3546895, 70824): Fraction(pal, 70824), (1418758, 28375): Fraction(pal, 1) / Fraction(141875, 2),
                 (3546895, 71051): Fraction(pal, 71051), (715909, 11921): Fraction(ntsc, 59605),
                 (1431818, 23933): Fraction(ntsc, 1) / Fraction(119665, 2), (2863636, 47775): Fraction(ntsc, 1) / Fraction(238875, 2) * 2}
        for (num, den), exact in table.items():
            with self.subTest(rate=f"{num}/{den}"):
                self.assertEqual(Fraction(num, den), exact)

    def test_render_off_delivers_no_picture_and_changes_nothing(self):
        on, rows_on = self.frames(6, "pal")
        off, rows_off = self.frames(6, "pal", "frames-norender")
        self.assertEqual(on.splitlines()[-1], off.splitlines()[-1])   # same final state digest and ticks
        for a, b in zip(rows_on, rows_off):
            self.assertEqual((a[0], a[1]), (b[0], b[1]))               # same unit index and master ticks
            self.assertEqual((b[2], b[3], b[19], b[17] & ~1), (0, 0, 0, 2))  # no pointer, no bytes, NO_VIDEO
            self.assertEqual(a[5:17], b[5:17])                         # geometry is reported regardless

    def guest(self, name, units, standard):
        rom = make_rom(self.tmp.name, name, name + ".s", {})
        code, out = run_probe(self.exe, rom, units, "frames", 1, timeout=300, extra=[standard])
        self.assertEqual(code, 0, out[-400:])
        return frames_of(out)

    def test_a_guest_that_sticks_the_long_field_bit_grows_the_active_raster_inside_the_same_buffer(self):
        for standard, (height, normal, long) in {"pal": (576, 574, 576), "ntsc": (486, 484, 486)}.items():
            rows = self.guest("lof_stuck", 9, standard)
            with self.subTest(standard=standard):
                heights = [r[11] for r in rows]                       # active height
                self.assertEqual(heights[0], normal)
                self.assertEqual(heights[-1], long)
                self.assertTrue(set(heights) <= {normal, long}, heights)
                self.assertEqual({(r[5], r[6], r[7]) for r in rows}, {(760, height, 7296)})   # the buffer never changes
                self.assertTrue(all(r[18] == "zero" for r in rows))
                self.assertEqual(rows[-1][16], 0)                                             # still progressive
                cadence = {"pal": (3546895, 71051), "ntsc": (1431818, 23933)}[standard]
                self.assertEqual((rows[-1][14], rows[-1][15]), cadence)

    def test_a_frame_longer_than_the_buffer_is_refused_without_stopping_or_overflowing(self):
        rows = self.guest("long_frame", 16, "pal")
        self.assertEqual(len(rows), 16)
        refused = [r for r in rows if r[17] & 2]
        self.assertTrue(refused, "the oversize mode was never refused")
        self.assertTrue(all(r[2] == 0 and r[3] == 0 for r in refused))
        self.assertTrue(all(r[18] == "zero" for r in rows))
        ticks = [r[1] for r in rows]
        self.assertTrue(all(b > a for a, b in zip(ticks, ticks[1:])))

    def test_many_units_run_without_anyone_reading_the_audio(self):
        _, rows = self.frames(40, "pal")
        self.assertEqual(len(rows), 40)
        self.assertEqual({r[3] for r in rows}, {7296 * 576})


@unittest.skipUnless(TOOLS and os.environ.get("P6_TEST_SANITIZERS") == "1", "opt-in: full engine build with ASan/UBSan")
class VideoGuestSanitizerTests(unittest.TestCase):
    """The guests that push the renderer to its limits, under ASan and UBSan: no write outside the buffer."""

    def test_adversarial_guests_are_clean(self):
        exe = configure_trace_tree(ROOT / "build/step-trace-san", True)
        with tempfile.TemporaryDirectory(prefix="p6-video-san-") as tmp:
            from tests.scenarios.a500.display import Display, rom_source
            roms = {name: make_rom(tmp, name, name + ".s", {}) for name in ("lof_stuck", "long_frame", "vsync")}
            for name, display in {"flip-mode": Display("flip-mode", flip="mode", bg=8, bars=((64, 0xF),)),
                                  "flip-lace": Display("flip-lace", mode="hires", flip="lace", bg=8),
                                  "lace-fields": Display("lace-fields", mode="hires", lace=True, cycle_fg=True)}.items():
                path = Path(tmp) / (name + ".rom")
                path.write_bytes(builder.build(rom_source(display)))
                roms[name] = path
            for name, rom in roms.items():
                for standard in ("pal", "ntsc"):
                    with self.subTest(guest=name, standard=standard):
                        code, out = run_probe(exe, rom, 14, "frames", 1, timeout=600, extra=[standard])
                        self.assertEqual(code, 0, out[-600:])
                        self.assertNotIn("runtime error", out)
                        self.assertNotIn("AddressSanitizer", out)


class BoundaryContractTests(unittest.TestCase):
    """The boundary belongs to p6_run_frame alone: a frontend that stepped the core by other means, or a
    libretro-only boundary, would show up here even when the numeric comparison above still passes."""

    def sources(self, *roots):
        for root in roots:
            for path in sorted((ROOT / root).rglob("*")):
                if path.suffix in (".cpp", ".h", ".c", ".hpp"):
                    yield path, path.read_text(encoding="utf-8", errors="replace")

    def test_no_frontend_symbol_in_the_bridge_or_the_headless_adapter(self):
        for path, text in self.sources("bridge", "adapters/headless"):
            with self.subTest(file=str(path.relative_to(ROOT))):
                self.assertNotRegex(text, r"libretro|retro_(run|get|set|init|load|unload)|RETRO_")

    def test_the_waterbox_adapter_has_no_libretro_api_in_it(self):
        # It may say in a comment that it is not the libretro adapter; it must not use or include any of it.
        for path, text in self.sources("adapters/waterbox"):
            with self.subTest(file=str(path.relative_to(ROOT))):
                self.assertNotRegex(text, r"#\s*include\s*[<\"]libretro|retro_(run|get|set|init|load|unload)|RETRO_")

    def test_only_frame_step_advances_and_only_lifecycle_begins_and_leaves(self):
        callers = {"advance": set(), "begin_leave": set()}
        for path, text in self.sources("bridge", "adapters", "ports/portable"):
            name = str(path.relative_to(ROOT))
            self.assertNotRegex(text, r"\bm68k_go\s*\(", name)
            self.assertNotRegex(text, r"\bm68k_run\s*\(", name)
            if re.search(r"m68k_go_unit\s*\(\s*M68K_UNIT_(APPLY|RUN)", text):
                callers["advance"].add(name)
            if re.search(r"m68k_go_unit\s*\(\s*M68K_UNIT_(BEGIN|LEAVE)", text):
                callers["begin_leave"].add(name)
        self.assertEqual(callers, {"advance": {"bridge/frame_step.cpp"}, "begin_leave": {"bridge/lifecycle.cpp"}})

    def test_the_hook_exists_only_in_trace_builds(self):
        source = (ROOT / "build/upstream/newcpu.cpp").read_text()
        block = source[source.index("#ifdef P6_UNIT_TRACE"):]
        block = block[:block.index("#endif")]
        self.assertIn("p6_unit_observe", block)
        self.assertNotIn("P6_UNIT_TRACE", (ROOT / "CMakeLists.txt").read_text().split("option(P6_UNIT_TRACE")[0])
        cmake = (ROOT / "CMakeLists.txt").read_text()
        self.assertRegex(cmake, r'option\(P6_UNIT_TRACE "[^"]*" OFF\)')
        presets = (ROOT / "CMakePresets.json").read_text()
        self.assertNotIn("P6_UNIT_TRACE", presets)
        # An engine object of a normal build does not reference the hook.
        normal = ROOT / "build/link-audit/CMakeFiles/p6_engine.dir/build/upstream/newcpu.cpp.o"
        if normal.exists() and shutil.which("nm"):
            symbols = subprocess.run(["nm", str(normal)], capture_output=True, text=True, check=True).stdout
            self.assertNotIn("p6_unit_", symbols)


# Each mutant edits one exact text of the patched sources; it must appear once, so a refactor cannot
# silently turn a mutant into a no-op. "control" replaces a text by itself and must not be detected.
MUTANTS = {
    "control": [("newcpu.cpp", "\tstatic int hardboot = 1;", "\tstatic int hardboot = 1;")],
    # A mechanical port of the PUAE mechanism: the head is repeated on every call, with a local hardboot.
    "mechanical-port": [
        ("newcpu.cpp", "\tstatic int hardboot = 1;", "\tint hardboot = 1;"),
        ("newcpu.cpp", "if (suspended && quit_program <= 0 && !(regs.spcflags & SPCFLAG_MODE_CHANGE)) {",
         "if (false && suspended && quit_program <= 0 && !(regs.spcflags & SPCFLAG_MODE_CHANGE)) {")],
    "return-after-end-drawing": [(
        "newcpu.cpp", "\t\t\t// Leave before custom_end_drawing(): the frame in progress must stay open.\n",
        "\t\t\tcustom_end_drawing();\n")],
    "no-halt-poll": [(
        "newcpu.cpp", "SPCFLAG_BRK | SPCFLAG_MODE_CHANGE | SPCFLAG_UNIT_END)))", "SPCFLAG_BRK | SPCFLAG_MODE_CHANGE)))")],
    "request-not-cleared": [(
        "newcpu.cpp", "\t\t\tunset_special(SPCFLAG_UNIT_END);\n\t\t\treturn M68K_UNIT_DONE;", "\t\t\treturn M68K_UNIT_DONE;")],
    "no-wait-gate": [("newcpu.cpp", "if (!waiting || !do_specialties (0))", "if (true)")],
}
# A local hardboot alone is not here: it only matters at a second reset, which STEP01 cannot inject before
# p6_reset exists (T13); tests/host/test_cpu_rom_guard.py catches it at the function level.
MUTANT_SCENARIOS = ("counter", "vsync", "stop", "halt", "copper-hp3", "longops-pad2")


def compile_entry(name):
    for entry in json.loads((BUILD / "compile_commands.json").read_text()):
        if entry["file"].endswith("/build/upstream/" + name):
            return entry
    raise AssertionError(name)


def build_mutant(directory, label, edits):
    file = edits[0][0]
    assert all(edit[0] == file for edit in edits), "one mutated file per mutant"
    text = (ROOT / "build/upstream" / file).read_text(encoding="utf-8")
    for _, old, new in edits:
        assert text.count(old) == 1, f"{label}: the mutated text must occur exactly once in {file}: {old[:50]!r}"
        text = text.replace(old, new)
    source = Path(directory) / file
    source.write_text(text, encoding="utf-8")
    entry = compile_entry(file)
    args, cleaned, skip = shlex.split(entry["command"]), [], 0
    for arg in args:
        if skip:
            skip -= 1
        elif arg in ("-o", "-MF", "-MT", "-MQ"):
            skip = 1
        elif arg in ("-MD", "-MMD"):
            continue
        elif arg == entry["file"]:
            cleaned.append(str(source))
        else:
            cleaned.append(arg)
    obj = Path(directory) / f"{label}.o"
    subprocess.run([*cleaned, "-o", str(obj)], cwd=entry["directory"], check=True, capture_output=True,
                   text=True, timeout=600)
    objects = [o for o in (BUILD / "CMakeFiles/p6_engine.dir").rglob("*.o") if o.name != file + ".o"]
    probe = BUILD / "CMakeFiles/p6_step_probe.dir/tests/step/step_probe.cpp.o"
    exe = Path(directory) / label
    subprocess.run(["clang++", "-o", str(exe), str(obj), *map(str, objects), str(probe),
                    str(BUILD / "libp6_bridge.a"), str(BUILD / "libp6_portable.a")],
                   check=True, capture_output=True, text=True, timeout=600)
    return exe


@unittest.skipUnless(TOOLS and os.environ.get("P6_TEST_MUTANTS") == "1", "opt-in: compiles mutants of newcpu.cpp")
class MutantTests(unittest.TestCase):
    """The comparison has to be able to fail: each broken mechanism is caught, the untouched copy is not."""

    @classmethod
    def setUpClass(cls):
        configure_trace_tree()
        cls.tmp = tempfile.TemporaryDirectory(prefix="p6-mutants-")
        cls.roms = {name: make_rom(cls.tmp.name, name, src, defs) for name, src, defs in scenarios()
                    if name in MUTANT_SCENARIOS}

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def detected(self, exe):
        for name, rom in self.roms.items():
            outputs = []
            for mode in ("steps", "continuous"):
                code, out = run_probe(exe, rom, 3, mode, timeout=40)
                if code != 0:
                    return f"{name} {mode}: {code} {out[-120:]}"
                outputs.append(out)
            if outputs[0] != outputs[1]:
                return f"{name}: steps and continuous differ"
        return None

    def test_each_mutant_breaks_step01_and_the_control_does_not(self):
        for label, edits in MUTANTS.items():
            with self.subTest(mutant=label):
                exe = build_mutant(self.tmp.name, label, edits)
                verdict = self.detected(exe)
                if label == "control":
                    self.assertIsNone(verdict)
                else:
                    self.assertIsNotNone(verdict, "STEP01 did not notice this mutation")


if __name__ == "__main__":
    unittest.main()
