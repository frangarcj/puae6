"""T17e: the media manifest grows only at its end (p6_media_append), and a native state is bound to the PREFIX of the manifest it was taken with (STATE01, STATE02, DET02, FUZZ01).

The header of a state stores media_manifest_hash, the hash of the manifest the machine had when it was saved; that hash includes the count of media, so the state names exactly one
prefix of the manifest. A state loads in an image whose manifest starts with that prefix (the image may have appended more media since), and is INCOMPATIBLE in one whose manifest
does not. The media a state names (P6BR: the drives and the queue of events; P6DK: the CRC in each drive, through the manifest) have to be inside its own prefix, which only the
structure can say: a header whose count was forged lower than the media its bodies name is refused as CORRUPT_STATE, before the machine is touched.

What is compared: a state is saved after a medium was appended and loaded back in the same process and in a new one (the appended medium registered there the same way, or passed to
p6_load), and the machine of the original goes on unit by unit as the loaded one does (every digest, the PCM and the picture); a state saved BEFORE the append loads after it and goes on
the same; a rewind to before an addition leaves the drives as they were, the added medium stays registered and can be mounted again, and that second mount goes on as it does in a machine
that had the medium from the start. The bound of the state does not move with the manifest.
"""
import hashlib
import struct
import unittest

from tests.state import envelope as e
from tests.state.harness import ProbeCase, SUBSYSTEMS, reforge

ADF_BYTES = 901120
NAMES = [chr(ord("a") + i) for i in range(17)]            # 16 media fill the manifest, the 17th does not fit


def content_id(name):
    return hashlib.sha256(image(name)).digest()


def image(name):
    return hashlib.sha256(f"disk {name}".encode()).digest() * (ADF_BYTES // 32)


class MediaAppendTests(ProbeCase):
    ROMS = {"vsync": ("vsync", None, None)}

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.adf = {}
        for name in NAMES:
            cls.adf[name] = cls.dir / f"disk-{name}.adf"
            cls.adf[name].write_bytes(image(name))

    def adfs(self, *names):
        return [argument for name in names for argument in ("--adf", str(self.adf[name]))]

    def append(self, unit, name):
        return ["--append-at", str(unit), str(self.adf[name])]

    def loads_into(self, name, blob, extra, unit=6):
        """What loading `blob` at `unit` of a machine started with `extra` answers: [(unit, code, message)]."""
        path = self.dir / f"{name}.state"
        path.write_bytes(blob)
        try:
            return self.probe("vsync", "--units", str(unit + 1), "--every", "1", *extra, "--load-at", str(unit), str(path)).loads
        finally:
            path.unlink()

    def assert_same_future(self, original, at, run, load_at, units, skip=(), label=""):
        """The machine right after the load (unit `load_at` of `run`) is the one of unit `at` of `original`, and so are the `units` units that follow: every digest but those in `skip`, the PCM
        and the picture each unit handed over."""
        wanted = {s: original.digests[(at, s)] for s in SUBSYSTEMS if s not in skip}
        self.assertEqual(wanted, {s: v for s, v in run.after[-1].items() if s not in skip}, f"{label}: the machine right after the load")
        for j in range(1, units + 1):
            for subsystem in SUBSYSTEMS:
                if subsystem not in skip:
                    self.assertEqual(original.digests[(at + j, subsystem)], run.digests[(load_at + j, subsystem)], f"{label}: unit +{j}, {subsystem}")
            self.assertEqual(original.pcm.get(at + j), run.pcm.get(load_at + j), f"{label}: unit +{j}, pcm")
            self.assertEqual(original.pictures.get(at + j), run.pictures.get(load_at + j), f"{label}: unit +{j}, picture")

    # --- the state of a machine that appended a medium ---------------------------------------------------------------------------------------------------

    def test_a_state_taken_after_an_append_loads_in_the_same_process_and_in_a_new_one_and_goes_on_the_same(self):
        path = self.dir / "after-append.state"
        # Disk a is in the manifest from the load, b is appended before the first unit and mounted in DF0 at power-on: the state names index 1, which a manifest of one medium has not.
        boot = ["--event-at", "0", "0", "1", "1"]
        first = self.probe("vsync", "--units", "14", "--every", "1", *self.adfs("a"), *self.append(0, "b"), *boot, "--save-at", "4", str(path), "--load-at", "10", str(path))
        self.assertEqual([(0, 0, 1, "")], first.appends)
        self.assertEqual([(10, 0, "")], first.loads)
        self.assert_same_future(first, 4, first, 10, 4, label="same process")
        for name, arguments in (("both media in p6_load", self.adfs("a", "b")), ("b appended after p6_load", [*self.adfs("a"), *self.append(0, "b")])):
            with self.subTest(name):
                run = self.probe("vsync", "--units", "14", "--every", "1", *arguments, *boot, "--load-at", "10", str(path))
                self.assertEqual([(10, 0, "")], run.loads)
                self.assert_same_future(first, 4, run, 10, 4, label=name)

    def test_a_state_taken_before_an_append_loads_after_it_and_goes_on_the_same(self):
        path = self.dir / "before-append.state"
        boot = ["--event-at", "0", "0", "0", "1"]
        original = self.probe("vsync", "--units", "8", "--every", "1", *self.adfs("a"), *boot, "--save-at", "3", str(path))
        for name, arguments in (("b appended at power-on", self.append(0, "b")), ("b and c appended", [*self.append(0, "b"), *self.append(0, "c")]),
                                ("b appended in the middle of the run", self.append(5, "b"))):
            with self.subTest(name):
                run = self.probe("vsync", "--units", "14", "--every", "1", *self.adfs("a"), *boot, *arguments, "--load-at", "8", str(path))
                self.assertEqual([(8, 0, "")], run.loads)
                # The bridge digest holds the manifest (its size and ids): that is the one thing that differs.
                self.assert_same_future(original, 3, run, 8, 5, skip=("bridge",), label=name)
                self.assertNotEqual(original.digests[(3, "bridge")], run.after[-1]["bridge"])

    def test_a_rewind_to_before_an_addition_restores_the_drive_and_the_added_medium_can_be_mounted_again(self):
        # Medium a is in DF0 from power-on. State at unit 3; b is appended at 4 and mounted at 5 (a swap that is still counting down at unit 8, when the state of 3 is loaded back). After
        # the load the drive is what it was at 3; b is still registered, and mounting it again (sequence 3: the machine the state restores has seen 1) goes on as it does in a machine that
        # had b from the start and mounted it at the same point of its life.
        path = self.dir / "rewind.state"
        units = 125
        rewound = self.probe("vsync", "--units", str(units), "--every", "1", *self.adfs("a"), "--event-at", "0", "0", "0", "1", "--save-at", "3", str(path),
                             *self.append(4, "b"), "--event-at", "5", "0", "1", "2", "--load-at", "8", str(path), "--event-at", "8", "0", "1", "3")
        self.assertEqual([(4, 0, 1, "")], rewound.appends)
        self.assertEqual([(0, 0, ""), (5, 0, ""), (8, 0, "")], rewound.events)
        self.assertEqual([(8, 0, "")], rewound.loads)
        control = self.probe("vsync", "--units", str(units - 5), "--every", "1", *self.adfs("a", "b"), "--event-at", "0", "0", "0", "1", "--event-at", "3", "0", "1", "3")
        self.assertEqual([(3, 0, "")], control.events[1:])
        self.assert_same_future(control, 3, rewound, 8, units - 8, label="rewind and mount b again")
        # The first assert compares the state of unit 3 with the digests of the control at unit 3, which has the same drive (a) and the same manifest.

    def test_a_medium_that_was_never_mounted_does_not_change_the_future_of_a_state(self):
        # Saved in a manifest [a], loaded where the manifest is [a, b, c] and nothing mounts b or c: the future is the original's except for the manifest in the bridge digest.
        path = self.dir / "plain.state"
        original = self.probe("vsync", "--units", "6", "--every", "1", *self.adfs("a"), "--event-at", "0", "0", "0", "1", "--save-at", "2", str(path))
        run = self.probe("vsync", "--units", "10", "--every", "1", *self.adfs("a", "b", "c"), "--event-at", "0", "0", "0", "1", "--load-at", "6", str(path))
        self.assertEqual([(6, 0, "")], run.loads)
        self.assert_same_future(original, 2, run, 6, 4, skip=("bridge",), label="three media")

    # --- what a state refuses ---------------------------------------------------------------------------------------------------------------------------------

    def test_a_state_whose_manifest_is_not_a_prefix_of_the_live_one_is_incompatible_and_nothing_is_touched(self):
        boot = ["--event-at", "0", "0", "1", "1"]
        both = self.state_of("ab.state", "vsync", 3, *self.adfs("a", "b"), *boot)
        three = self.state_of("abc.state", "vsync", 3, *self.adfs("a", "b", "c"), *boot)
        none = self.state_of("none.state", "vsync", 3)
        for name, blob, extra in (
                ("[a, b] in a machine with [a]", both, self.adfs("a")),
                ("[a, b] in a machine with no media", both, []),
                ("[a, b] in [a, c]", both, self.adfs("a", "c")),
                ("[a, b] in [b, a]", both, self.adfs("b", "a")),
                ("[a, b] in [a, c] where c was appended", both, [*self.adfs("a"), *self.append(0, "c")]),
                ("[a, b] in [c, a, b]", both, self.adfs("c", "a", "b")),
                ("[a, b, c] in [a, b]", three, self.adfs("a", "b")),
                ("[a, b, c] in [a, b, d] where d was appended", three, [*self.adfs("a", "b"), *self.append(0, "d")]),
                ("[a] in [b]", self.state_of("a.state", "vsync", 3, *self.adfs("a")), self.adfs("b")),
        ):
            with self.subTest(name):
                self.refusals([(name, blob, 10, "media_manifest_hash")], extra=extra)
        self.assertEqual([(6, 0, "")], self.loads_into("none", none, []), "a control: the state of no media in a machine of none")

    def test_the_manifest_the_state_names_is_the_prefix_not_the_whole_live_one(self):
        # The same states, now accepted: [a, b] in [a, b], in [a, b, c] and in [a, b] with c appended; [] and [a] in [a, b].
        both = self.state_of("ab.state", "vsync", 3, *self.adfs("a", "b"), "--event-at", "0", "0", "1", "1")
        only_a = self.state_of("a.state", "vsync", 3, *self.adfs("a"), "--event-at", "0", "0", "0", "1")
        none = self.state_of("none.state", "vsync", 3)
        for name, blob, extra in (("[a, b] in [a, b]", both, self.adfs("a", "b")), ("[a, b] in [a, b, c]", both, self.adfs("a", "b", "c")),
                                  ("[a, b] in [a, b] with c appended", both, [*self.adfs("a", "b"), *self.append(0, "c")]),
                                  ("[a, b] in [a] with b appended", both, [*self.adfs("a"), *self.append(0, "b")]),
                                  ("[a] in [a, b]", only_a, self.adfs("a", "b")), ("[] in [a, b]", none, self.adfs("a", "b"))):
            with self.subTest(name):
                self.assertEqual([(6, 0, "")], self.loads_into("accepted", blob, extra))

    def test_a_forged_prefix_that_is_shorter_than_the_media_the_state_names_is_corrupt_and_nothing_is_touched(self):
        manifest = lambda *names: e.media_manifest_hash([(1, content_id(n)) for n in names])      # noqa: E731
        extra = self.adfs("a", "b")
        # DF0 holds b (index 1) in the state: a header that says the manifest had one medium (or none) cannot be right about its P6BR.
        holds_b = self.state_of("holds-b.state", "vsync", 3, *extra, "--event-at", "0", "0", "1", "1")
        # DF0 holds a and a mount of b (index 1) is queued, waiting for the next unit.
        queued = self.dir / "queued.state"
        self.probe("vsync", "--units", "4", "--every", "1", *extra, "--event-at", "0", "0", "0", "1", "--event-at", "4", "0", "1", "2", "--save-at", "4", str(queued))
        queued = queued.read_bytes()
        attempts = [
            ("drive 0 holds b but the prefix has one medium", reforge(holds_b, header={208: manifest("a")}, keep_length=True), 9, "chunk bridge"),
            ("drive 0 holds b but the prefix is empty", reforge(holds_b, header={208: manifest()}, keep_length=True), 9, "chunk bridge"),
            ("a mount of b is queued but the prefix has one medium", reforge(queued, header={208: manifest("a")}, keep_length=True), 9, "invalid media index"),
            ("a mount of b is queued but the prefix is empty", reforge(queued, header={208: manifest()}, keep_length=True), 9, "chunk bridge"),
            # A prefix that is no prefix of the live manifest is refused by name, whatever the count.
            ("a prefix of media the live manifest does not have", reforge(holds_b, header={208: manifest("a", "b", "c")}, keep_length=True), 10, "media_manifest_hash"),
            ("a prefix of other media", reforge(holds_b, header={208: manifest("c")}, keep_length=True), 10, "media_manifest_hash"),
        ]
        self.refusals(attempts, extra=extra)
        # The right count with the right prefix is the state itself, resealed.
        self.assertEqual([(6, 0, "")], self.loads_into("resealed", reforge(holds_b, header={208: manifest("a", "b")}, keep_length=True), extra))

    # --- STATE01 ---------------------------------------------------------------------------------------------------------------------------------------------------

    def test_the_bound_does_not_move_when_media_are_appended_and_a_full_manifest_loads_the_state_of_an_empty_one(self):
        early = self.dir / "early.state"
        late = self.dir / "late.state"
        appended = [argument for name in NAMES[1:16] for argument in self.append(2, name)]
        run = self.probe("vsync", "--units", "8", "--every", "1", *self.adfs("a"), "--public-at", "1", "--save-at", "1", str(early), *appended,
                         *self.append(2, "q"), "--public-at", "2", "--public-at", "5", "--save-at", "5", str(late), "--save-at", "6", str(self.dir / "m.state"),
                         "--load-at", "7", str(early), "--load-at", "8", str(late))
        self.assertEqual(list(range(1, 16)), [index for _, code, index, _ in run.appends if code == 0])
        self.assertEqual([7], [code for _, code, _, _ in run.appends if code != 0], "the 17th medium does not fit")
        self.assertEqual([(7, 0, ""), (8, 0, "")], run.loads, "the state of a manifest of 1 and the one of 16 both load into 16")
        bounds = {size[1] for size in run.public_size} | {save[3] for save in run.saves}
        self.assertEqual(1, len(bounds), f"the bound moved with the manifest: {sorted(bounds)}")
        fixed = self.probe("vsync", "--units", "2", "--every", "1", *self.adfs(*NAMES[:16]), "--public-at", "1")
        self.assertEqual(bounds, {size[1] for size in fixed.public_size}, "the bound of 16 media passed to p6_load is the same number")


if __name__ == "__main__":
    unittest.main()
