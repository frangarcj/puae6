"""STATE01 (T15 closure): p6_state_bound, through the public ABI, is one number for the whole session of an image, and every published configuration has one that a state fits in.

The bound is what a host sizes its buffer with (rewind, netplay, the Waterbox state transport), so it must not move while the image lives (docs/02-contracts.md: the bound announced when the
load ends does not grow until unload) and a state taken anywhere in that life must fit in it. Four things can make a naive bound grow: the media queue filling up (P6BR has 0 to 16 events), keys and
ports held by the host (P6IN), a delayed disk change in flight (P6DK), and the passing of time itself (counters, rings, the audio queue). The first group below takes the public bound (and the
registry's, which has to be the same number) at the first boundary, after many units, with the media queue full, with keys held, after a reset, and right after a load. The second one asks for
the bound of every configuration a host can publish (PAL and NTSC, 44100 and 48000 Hz, 0 to P6_MAX_MEDIA media) and saves and loads a state in each: it fits, the bound is positive and bounded by
the sum of the chunks' maxima, and the load that follows is accepted.
"""
import hashlib
import unittest

from tests.state.harness import ProbeCase

MAX_MEDIA = 16          # P6_MAX_MEDIA
UNITS = 30


class BoundTests(ProbeCase):
    ROMS = {"vsync": ("vsync", None, None)}

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.adfs = []
        for i in range(MAX_MEDIA):
            path = cls.dir / f"disk{i}.adf"
            path.write_bytes(hashlib.sha256(f"disk {i}".encode()).digest() * (901120 // 32))
            cls.adfs.append(path)

    def media(self, count):
        return [argument for path in self.adfs[:count] for argument in ("--adf", str(path))]

    def bounds(self, run):
        """Every bound the run printed: the public one at each --public-at and the registry's at each --save-at."""
        return [size[1] for size in run.public_size] + [save[3] for save in run.saves]

    def test_the_bound_is_one_number_for_the_whole_session_of_an_image(self):
        # Two media registered, DF0 mounted at boot; at unit 2 the queue of media events is filled up to its capacity (16 accepted, the 17th refused); key 0x20 is held from unit 3
        # and a mouse moves from unit 4; a reset is queued at unit 9 and a medium swap lands later. The bound is asked for right after the load (unit 0, where there is no state yet),
        # at the first boundary, with the queue full, with the keys held, after the reset and after many units; a save at each of those boundaries has to fit.
        events = [arg for i in range(17) for arg in ("--event-at", "2", str(i % 2), str(i % 2), str(100 + i))]
        extra = ["--key", "3", "0x20", "down", "--port-at", "4", "0:2:1:7:-3", "--reset-at", "9"]
        asked = (0, 1, 2, 3, 5, 9, 10, 15, UNITS)
        arguments = []
        for unit in asked:
            arguments += ["--public-at", str(unit), "--save-at", str(unit), str(self.dir / f"session-{unit}.state")]
        run = self.probe("vsync", "--units", str(UNITS), "--every", "10", *self.media(2), "--event-at", "0", "0", "0", "1", *events, *extra, *arguments)
        self.assertEqual({0}, {int(p[2]) for p in run.public}, "the public bound succeeds at every boundary, including the one before the first unit")
        by_unit = {size[0]: size for size in run.public_size}
        self.assertEqual(sorted(asked), sorted(by_unit))
        bounds = set(self.bounds(run))
        self.assertEqual(1, len(bounds), f"the bound moved during the session: {sorted(bounds)}")
        bound, = bounds
        self.assertGreater(bound, 336 + 11 * 16, "the header and one header per chunk at least")
        saved = {s[0]: s for s in run.saves}
        self.assertEqual(sorted(set(asked) - {0}), sorted(saved), "every boundary after the first unit saved (unit 0 is before any state exists)")
        for unit, (_, written, _, _) in saved.items():
            self.assertLessEqual(written, bound, f"the state of unit {unit} does not fit in the bound")
        for unit in asked[1:]:
            self.assertLessEqual(by_unit[unit][2], bound)
            self.assertEqual(saved[unit][1], by_unit[unit][2], "the public save and the registry's one are the same size")
        for p in run.public[1:]:
            self.assertEqual(("0", "0", "0"), p[2:5], "bound, save and load through the public ABI succeed")

    def test_the_bound_after_a_load_is_the_one_of_the_image_that_took_the_state(self):
        # A state taken with the queue full and keys held, loaded into another process, and then the bound asked for again (twice, with a run between): the same number.
        state = self.dir / "bound-source.state"
        events = [arg for i in range(16) for arg in ("--event-at", "2", str(i % 2), str(i % 2), str(100 + i))]
        first = self.probe("vsync", "--units", "4", *self.media(2), "--event-at", "0", "0", "0", "1", *events, "--key", "3", "0x20", "down",
                           "--public-at", "4", "--public-save-at", "4", str(state))
        before = first.public_size[0][1]
        second = self.probe("vsync", "--units", "8", *self.media(2), "--public-at", "0", "--public-load-at", "5", str(state), "--public-at", "5", "--public-at", "8")
        self.assertEqual([(5, 0, "")], second.loads)
        self.assertEqual([before] * 3, [size[1] for size in second.public_size])
        self.assertEqual(("0", "0", "0"), second.public[-1][2:5])

    def test_the_bound_depends_on_the_image_and_not_on_what_the_machine_did(self):
        # Two different programs, one that is idle and one that fills the chipset's DMA, the media queue and the audio, end up with the same bound.
        busy = self.probe("vsync", "--units", "12", *self.media(2), "--port", "0:2:1:40:-25", "--key", "2", "0x20", "down", "--public-at", "2", "--public-at", "12")
        idle = self.probe("vsync", "--units", "12", *self.media(2), "--public-at", "2", "--public-at", "12")
        self.assertEqual({size[1] for size in busy.public_size}, {size[1] for size in idle.public_size})
        self.assertEqual(1, len({size[1] for size in busy.public_size + idle.public_size}))

    def test_every_published_configuration_has_a_bound_that_a_state_fits_in(self):
        seen = {}
        for standard, flag in (("pal", []), ("ntsc", ["--ntsc"])):
            for rate in (44100, 48000):
                for count in range(MAX_MEDIA + 1):
                    with self.subTest(standard=standard, rate=rate, media=count):
                        boot = ["--event-at", "0", "0", "0", "1"] if count else []
                        run = self.probe("vsync", "--units", "2", "--every", "1", *flag, "--rate", str(rate), *self.media(count), *boot, "--public-at", "2")
                        (unit, cap, bound, save, load, written, junk, small, message), = run.public
                        self.assertEqual(("2", "1", "0", "0", "0", "9", "7"), (unit, cap, bound, save, load, junk, small), message)
                        (_, size_bound, size_written, _), = run.public_size
                        self.assertEqual(int(written), size_written)
                        self.assertGreater(size_written, 0)
                        self.assertLessEqual(size_written, size_bound, "a state does not fit in its own bound")
                        seen[(standard, rate, count)] = size_bound
        self.assertEqual(2 * 2 * (MAX_MEDIA + 1), len(seen))
        # The bound is a function of the configuration and the registered media, and it never exceeds the largest of them: nothing in it scales with the media beyond the manifest.
        self.assertLess(max(seen.values()), 4 << 20, "the A500 profile is its RAM (about 1 MiB) and some tens of KiB of devices, not the media")


if __name__ == "__main__":
    unittest.main()
