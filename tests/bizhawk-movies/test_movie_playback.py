"""T22b: movies of the A500 played, sought and branched with the real core in a real Waterbox host, with render and sound alternated and the host's environment changed.

WB02  a movie of 10000 units is recorded by one process and played by two new ones at the same time: the same units (ticks, picture hash, audio and its hash, lag, drive light and
      media events) and the same machine at the end; the cycle count and the lag count of the movie's header are those of the end of the play, and a play that disagrees fails
      with a non-zero exit code (the first unit that differs is named).
WB09  seeks and branches, repeated: states are saved on the way, the movie is sought to many frames (forwards and backwards, in an order that is not the one of the movie) by loading the
      state before and playing on, and every seek lands on the unit lines and the machine of the continuous play; a branch (other inputs from a saved state) goes its own way, and loading
      the state again and going back to the movie converges to the continuous play. The interlaced fixture saves states in the middle of both fields.
WB08  render and sound switched off for stretches of the play change nothing of the machine that follows (compared without the wrapper's own picture fields, which do not exist when
      no picture was asked for) nor the units that still deliver a picture and sound.
DET06 the same play under another time zone, locale, language, working directory, home and temporary directory is the same play.

Needs the Waterbox runtime and the guest (tools/build_waterbox.py --puae6-run build/waterbox/boot.rom), dotnet and the local NuGet cache; skips without them.
    python3 -m unittest discover -s tests/bizhawk-movies -p "test_movie_playback.py"
"""
from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import random
import re
import shutil
import unittest

from harness import AVAILABLE, B1, B2, CAPS, ESC, JOYSTICK, KEY_Q, KEY_W, LEFT, MOUSE, RIGHT, UP, Harness, line

LONG_UNITS = 10000
SHORT_UNITS = 720
STATE_EVERY = 60
CHECK_EVERY = 20


def random_inputs(h, name, units, seed, device_ports=True):
    """A reproducible pseudo-random script: keys held for a few units, the pointer moving, the joysticks, and a press list with resets and disk changes at fixed distances."""
    rng = random.Random(seed)
    lines, held = [], {}
    for u in range(1, units + 1):
        for key in (ESC, KEY_Q, KEY_W, 0x38, 0x39, 0x44):
            if key in held:
                held[key] -= 1
                if held[key] <= 0:
                    del held[key]
            elif rng.random() < 0.01:
                held[key] = rng.randint(1, 12)
        mouse = (MOUSE, B1 if rng.random() < 0.05 else 0, rng.randint(-4, 4) if rng.random() < 0.3 else 0, rng.randint(-3, 3) if rng.random() < 0.2 else 0)
        joy = (JOYSTICK, (rng.choice([0, LEFT, RIGHT, UP]) if rng.random() < 0.2 else 0) | (B2 if rng.random() < 0.03 else 0), 0, 0)
        lines.append(line(keys=sorted(held), p0=mouse, p1=joy))
    return h.script(name, lines)


def press_schedule(units, spacing=1500):
    """A reset every `spacing` units and a disk swap (select the next disk, insert it, later eject and insert again) between them."""
    items = []
    for start in range(spacing, units, spacing):
        items.append(f"{start}:Power")
    for start in range(300, units, 700):
        items += [f"{start - 1}:Next_Slot", f"{start}-{start + 3}:Insert_Disk", f"{start + 200}:Eject_Disk", f"{start + 210}-{start + 212}:Insert_Disk"]
    return ";".join(items)


@unittest.skipUnless(AVAILABLE, "Waterbox runtime or puae6.wbx not built (tools/build_waterbox.py --puae6-run build/waterbox/boot.rom)")
class MoviePlaybackTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.h = Harness("p6-movie-play-")
        h = cls.h
        cls.long_script = random_inputs(h, "long", LONG_UNITS, seed=22)
        cls.long_press = press_schedule(LONG_UNITS)
        cls.long_movie = h.dir / "long.bk2"
        record = h.session([h.core("R"), f"run R {LONG_UNITS} script={cls.long_script} press={cls.long_press} record", f"endmovie R {cls.long_movie}", "digest R end machine"]).stdout
        cls.long_units = h.units(record, "R")
        cls.long_digest = h.digests(record, "R")["end"]
        cls.short = {}
        for name, firmware, adf in (("disk", "disk", "A,B"), ("lace", "lace", "")):
            script = random_inputs(h, f"short-{name}", SHORT_UNITS, seed=7 if name == "disk" else 9)
            press = press_schedule(SHORT_UNITS, spacing=250) if name == "disk" else ""
            movie = h.dir / f"short-{name}.bk2"
            out = h.session([h.core("R", firmware=firmware, adf=adf), f"run R {SHORT_UNITS} script={script} press={press} record", f"endmovie R {movie}"]).stdout
            cls.short[name] = dict(firmware=firmware, adf=adf, script=script, press=press, movie=movie, units=h.units(out, "R"))

    @classmethod
    def tearDownClass(cls):
        cls.h.close()

    # --- WB02 -----------------------------------------------------------------------------------------------------------------------

    def play_long(self, tag):
        h = self.h
        out = h.session([h.core(tag), f"run {tag} {LONG_UNITS} movie={self.long_movie}", f"moviefinish {tag} {self.long_movie}", f"digest {tag} end machine"]).stdout
        return h.units(out, tag), h.digests(out, tag)["end"], out

    def first_difference(self, expected, got):
        for i, (a, b) in enumerate(zip(expected, got)):
            if a[1:] != b[1:]:
                return f"unit {i + 1}: {a} != {b}"
        return f"lengths {len(expected)} and {len(got)}"

    def test_a_movie_of_ten_thousand_units_played_twice_at_once_by_new_processes_is_the_one_that_was_recorded(self):
        self.assertEqual(LONG_UNITS, len(self.long_units))
        with ThreadPoolExecutor(2) as pool:
            first, second = pool.map(self.play_long, ["P1", "P2"])
        for tag, (units, digest, out) in (("P1", first), ("P2", second)):
            self.assertEqual([u[1:] for u in self.long_units], [u[1:] for u in units], f"{tag}: {self.first_difference(self.long_units, units)}")
            self.assertEqual(self.long_digest, digest)
            self.assertIn("movie finished ok", out, "the cycle count and the lag count of the header are those of the end of the play")
        # a play that disagrees with the header fails, and says what disagrees
        bent = self.h.rewrite_movie(self.long_movie, self.h.dir / "bent-cycles.bk2", {"CycleCount": "12345"})
        result = self.h.session([self.h.core("D"), f"run D {LONG_UNITS} movie={bent}", f"moviefinish D {bent}"], expect=1)
        self.assertIn("CycleCount", result.stderr)
        self.assertIn("12345", result.stderr)

    def test_a_movie_that_ends_before_the_core_does_is_an_error(self):
        result = self.h.session([self.h.core("D"), f"run D {SHORT_UNITS + 5} movie={self.short['disk']['movie']}"], expect=1)
        self.assertIn(f"the movie ends at frame {SHORT_UNITS}", result.stderr)

    # --- WB09 -----------------------------------------------------------------------------------------------------------------------

    def main_pass(self, name, movie_core):
        """The continuous play with a state every STATE_EVERY units and the machine's digest every CHECK_EVERY: the program lines and what to compare with."""
        f = self.short[name]
        lines = [movie_core, "save M start"]
        for i in range(SHORT_UNITS // STATE_EVERY):
            for c in range(STATE_EVERY // CHECK_EVERY):
                frame = i * STATE_EVERY + (c + 1) * CHECK_EVERY
                lines += [f"run M {CHECK_EVERY} movie={f['movie']}", f"digest M main{frame}"]
            lines.append(f"save M s{i}")
        return lines

    def seek_program(self, name, rng):
        f = self.short[name]
        targets = [rng.randrange(CHECK_EVERY, SHORT_UNITS + 1, CHECK_EVERY) for _ in range(40)]
        lines, expect = [], []
        for n, t in enumerate(targets):
            saved = t // STATE_EVERY                      # states saved after 60, 120, ...: s0 is frame 60; "start" is frame 0
            tag, base = ("start", 0) if saved == 0 else (f"s{saved - 1}", saved * STATE_EVERY)
            lines += [f"load M {tag}", f"run M {t - base} movie={f['movie']}", f"digest M seek{n}_{t}"]
            expect.append((t, base))
        return lines, expect

    def test_seeks_to_many_frames_land_on_the_continuous_play_forwards_backwards_and_in_the_middle_of_both_fields(self):
        h = self.h
        for name in ("disk", "lace"):
            with self.subTest(movie=name):
                f = self.short[name]
                rng = random.Random(11)
                program = self.main_pass(name, h.core("M", firmware=f["firmware"], adf=f["adf"]))
                seeks, expect = self.seek_program(name, rng)
                out = h.session(program + seeks).stdout
                units = [u[1:] for u in h.units(out, "M")]
                main_units = units[:SHORT_UNITS]
                self.assertEqual([u[1:] for u in f["units"]], main_units, "the continuous play is the recording")
                digests = h.digests(out, "M")
                cursor = SHORT_UNITS
                for n, (t, base) in enumerate(expect):
                    count = t - base
                    self.assertEqual(main_units[base:t], units[cursor:cursor + count], f"seek {n} to frame {t}: the units that were played again are the ones of the first play")
                    self.assertEqual(digests[f"main{t}"], digests[f"seek{n}_{t}"], f"seek {n} to frame {t}: the machine")
                    cursor += count
                self.assertGreater(len({t for t, _ in expect}), 15, "the seeks visit many different frames")
                self.assertTrue(any(b > t for (t, _), (b, _) in zip(expect, expect[1:])), "and some of them go backwards")

    def test_a_branch_goes_its_own_way_and_going_back_to_the_movie_converges(self):
        h = self.h
        for name in ("disk", "lace"):
            with self.subTest(movie=name):
                f = self.short[name]
                core = h.core("M", firmware=f["firmware"], adf=f["adf"])
                alt = random_inputs(h, f"branch-{name}", SHORT_UNITS, seed=99)
                rounds = 6
                lines = [core, f"run M 300 movie={f['movie']}", "save M a", f"run M 200 movie={f['movie']}", "digest M mainEnd"]
                for r in range(rounds):
                    lines += ["load M a", f"run M {20 * r + 40} script={alt} press={301 + r}-{330 + 5 * r}:Key_Q",       # another way from the same state
                              f"digest M branch{r}", "load M a", f"run M 200 movie={f['movie']}", f"digest M back{r}"]              # and back to the movie from the same state
                d = h.digests(h.session(lines).stdout, "M")
                for r in range(rounds):
                    self.assertEqual(d["mainEnd"], d[f"back{r}"], f"round {r}: back on the movie from the saved state it is the continuous play")
                    self.assertNotEqual(d["mainEnd"], d[f"branch{r}"], "and the branch did not")
                self.assertEqual(rounds + 1, len({d["mainEnd"], *(d[f"branch{r}"] for r in range(rounds))}), "each branch goes somewhere else")

    # --- WB08 -----------------------------------------------------------------------------------------------------------------------

    def test_render_and_sound_off_for_stretches_change_nothing_of_the_machine_or_of_the_units_that_still_deliver(self):
        h = self.h
        for name in ("disk", "lace"):
            with self.subTest(movie=name):
                f = self.short[name]
                core = h.core("M", firmware=f["firmware"], adf=f["adf"])
                stretches = [(100, 160), (301, 301), (420, 520), (650, 700)]
                loud = h.session([core, f"run M {SHORT_UNITS} movie={f['movie']}", "digest M end machine"]).stdout
                lines = [core]
                at = 0
                for a, b in stretches:
                    lines += [f"run M {a - 1 - at} movie={f['movie']}", f"run M {b - a + 1} movie={f['movie']} silent={a}-{b}"]
                    at = b
                lines += [f"run M {SHORT_UNITS - at} movie={f['movie']}", "digest M end machine"]
                quiet = h.session(lines).stdout
                l, q = h.units(loud, "M"), h.units(quiet, "M")
                self.assertEqual(SHORT_UNITS, len(q))
                for a, b in zip(l, q):
                    unit = int(a[1])
                    silent = any(x <= unit <= y for x, y in stretches)
                    self.assertEqual((a[2], a[6], a[8]), (b[2], b[6], b[8]), f"unit {unit}: ticks, lag and media events")
                    if silent:
                        self.assertEqual("0", b[4], f"unit {unit}: no sound was asked for")
                    else:
                        self.assertEqual((a[3], a[4], a[5]), (b[3], b[4], b[5]), f"unit {unit}: picture, audio and its hash")
                self.assertEqual(h.digests(loud, "M")["end"], h.digests(quiet, "M")["end"], "the machine is the same")

    # --- DET06 ----------------------------------------------------------------------------------------------------------------------

    def test_the_play_does_not_depend_on_the_time_zone_locale_working_directory_home_or_temporary_directory(self):
        h = self.h
        f = self.short["disk"]
        program = [h.core("M", firmware=f["firmware"], adf=f["adf"]), f"run M {SHORT_UNITS} movie={f['movie']}", f"moviefinish M {f['movie']}", "digest M end"]
        runtime = Path(__import__("build_waterbox").build_core()[2]) / "libwaterboxhost.dylib"
        results = []
        environments = [{}, {"TZ": "Pacific/Kiritimati", "LANG": "tr_TR.UTF-8", "LC_ALL": "tr_TR.UTF-8"}, {"TZ": "America/St_Johns", "LANG": "ja_JP.UTF-8", "LC_ALL": "C"},
                        {"TZ": "UTC", "DOTNET_SYSTEM_GLOBALIZATION_INVARIANT": "1", "LC_ALL": "de_DE.UTF-8"}]
        for n, env in enumerate(environments):
            here = h.dir / f"cwd-{n}"
            here.mkdir()
            shutil.copy2(runtime, here / "libwaterboxhost.dylib")           # BizHawk asks dlopen for the bare name: the runtime library lives in the working directory
            home, tmp = h.dir / f"home-{n}", h.dir / f"tmp-{n}"
            home.mkdir(); tmp.mkdir()
            out = h.session(program, env={**env, "HOME": str(home), "TMPDIR": str(tmp), "TEMP": str(tmp)}, cwd=here).stdout
            results.append(([u[1:] for u in h.units(out, "M")], h.digests(out, "M")["end"]))
        for n in range(1, len(results)):
            self.assertEqual(results[0], results[n], f"environment {environments[n]}")
        self.assertEqual([u[1:] for u in f["units"]], results[0][0], "and it is the play that was recorded")


if __name__ == "__main__":
    unittest.main()
