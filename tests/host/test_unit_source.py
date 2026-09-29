"""Source contract of the video-unit return (patch 0060); the mechanism itself runs in test_cpu_rom_guard.

The CPU loop leaves at a rendered video unit through one flag raised in one place and comes back through
the same function. These checks pin what keeps that safe: one producer, every wait loop polling the flag,
no second scheduler, and the pristine build unchanged.
"""
import json
from pathlib import Path
import re
import unittest

from tests.host import ensure_generated
from tests.host.test_cpu_rom_guard import function

ROOT = Path(__file__).resolve().parents[2]
TREE = ROOT / "build/upstream"
FLAG = "SPCFLAG_UNIT_END"


def engine_sources():
    selection = json.loads((ROOT / "manifests/source-manifest.json").read_text())["selection"]
    return [TREE / name for name in selection["upstream_units"]]


def profile_view(text):
    """Source as the P6 profile compiles it: `#ifndef P6_PORTABLE` regions and `#else` of `#ifdef` dropped."""
    out, stack = [], []  # entries: (kind, keeping)
    for line in text.splitlines():
        token = line.strip()
        keeping = all(keep for _, keep in stack)
        if token.startswith("#ifndef P6_PORTABLE"):
            stack.append(("guard", False))
        elif token.startswith("#ifdef P6_PORTABLE"):
            stack.append(("guard", True))
        elif token.startswith("#if"):
            stack.append(("other", True))
        elif token.startswith("#else") and stack and stack[-1][0] == "guard":
            stack[-1] = ("guard", not stack[-1][1])
        elif token.startswith("#endif") and stack:
            stack.pop()
        elif keeping:
            out.append(line)
    return "\n".join(out)


class UnitSourceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        ensure_generated()
        cls.newcpu = (TREE / "newcpu.cpp").read_text()
        cls.header = (TREE / "include/newcpu.h").read_text()

    def test_flag_is_a_distinct_bit_and_zero_outside_the_profile(self):
        values = {name: int(value) for name, value in
                  re.findall(r"^#define (SPCFLAG_\w+) (\d+)", self.header, re.M) if name != FLAG}
        self.assertTrue(all(v & (v - 1) == 0 for v in values.values()))
        self.assertTrue("#ifdef P6_PORTABLE\n// A video unit was rendered: leave the CPU loop at the next"
                        " instruction boundary.\n#define SPCFLAG_UNIT_END 1\n#else\n#define SPCFLAG_UNIT_END 0\n#endif"
                        in self.header)
        self.assertNotIn(1, values.values())  # bit 0 is free in this WinUAE, so no other flag moves

    def test_only_the_render_produces_the_request(self):
        producers = []
        for source in engine_sources():
            for number, line in enumerate(source.read_text(errors="replace").splitlines(), 1):
                if FLAG in line and re.search(r"\bset_special(_exter)?\s*\(", line):
                    producers.append((source.name, number))
        self.assertEqual([name for name, _ in producers], ["custom.cpp"])
        custom = (TREE / "custom.cpp").read_text()
        render = function(custom, "static void vsync_display_render(void)")
        self.assertEqual(render.count(FLAG), 1)
        self.assertLess(render.index("vsync_display_rendered = true;"), render.index(FLAG))
        # The port never raises it: drawing hooks (unlockscr, reset paths) run at other times.
        for path in (ROOT / "ports/portable").rglob("*.cpp"):
            self.assertNotIn(FLAG, path.read_text(), path.name)

    def test_every_wait_loop_of_the_cpu_polls_the_request(self):
        halt = function(self.newcpu, "static bool haltloop(void)")
        self.assertIn(f"SPCFLAG_BRK | SPCFLAG_MODE_CHANGE | {FLAG}", halt)
        specialties = function(self.newcpu, "static int do_specialties (int cycles)")
        reset_wait = specialties[specialties.index("while (spcflags & SPCFLAG_CPUINRESET)"):
                                 specialties.index("if (spcflags & SPCFLAG_CHECK)")]
        self.assertIn(FLAG, reset_wait)
        tail = specialties[specialties.rindex("return 0;") - 250:]
        self.assertIn(f"if (regs.spcflags & {FLAG})\n\t\treturn 1;", tail)
        # The test-only observer sits in front of that return and is invisible to the profile build.
        self.assertLess(specialties.index("#ifdef P6_UNIT_TRACE"), specialties.rindex(f"if (regs.spcflags & {FLAG})"))
        self.assertNotIn("P6_UNIT_TRACE", (ROOT / "ports/portable/sysconfig.h").read_text())
        # Loops without a poll are excluded by configuration, not silently accepted.
        self.assertIn("m68k_reset_delay", specialties)
        manifest = json.loads((ROOT / "manifests/config-invariants.json").read_text())
        options = {item["option"]: item for item in manifest["invariants"]}
        self.assertEqual(options["reset_delay"]["require"], "0")
        self.assertEqual(options["crash_auto_reset"]["require"], "0")

    def test_m68k_go_is_replaced_by_one_resumable_function(self):
        # assertTrue, not assertIn: a failure must not dump the whole header.
        self.assertTrue("enum { M68K_UNIT_BEGIN, M68K_UNIT_APPLY, M68K_UNIT_RUN, M68K_UNIT_LEAVE };" in self.header)
        self.assertTrue("extern int m68k_go_unit (int mode);\n#else\nextern void m68k_go (int);" in self.header)
        body = function(self.newcpu, "#ifdef P6_PORTABLE\nint m68k_go_unit (int mode)")
        active = body[:body.index("#else\n\tint hardboot = 1;")] + body[body.index("\tfor (;;) {"):]
        for forbidden in ("setjmp", "longjmp", "pthread", "std::thread", "swapcontext", "sleep", "abort ("):
            self.assertNotIn(forbidden, active)
        self.assertIn("static int hardboot = 1;", body)
        self.assertLess(body.index("return M68K_UNIT_DONE;\n\t\t}\n\t\tsuspended = false;"),
                        body.rindex("custom_end_drawing();"))
        # Nothing else in the engine or the port can start the loop: the bridge is the only caller.
        callers = []
        for source in engine_sources() + list((ROOT / "ports/portable").rglob("*.cpp")):
            text = profile_view(source.read_text(errors="replace"))
            if re.search(r"\bm68k_go\s*\(", text) and source.name != "newcpu.cpp":
                callers.append(source.name)
        self.assertEqual(callers, [])

    def test_the_pristine_profile_keeps_m68k_go_and_a_zero_flag(self):
        source = (ROOT / "upstream/winuae/newcpu.cpp").read_text()
        self.assertIn("void m68k_go (int may_quit)", source)
        self.assertNotIn(FLAG, source)


if __name__ == "__main__":
    unittest.main()
