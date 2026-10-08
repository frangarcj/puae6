"""T16: the core in a real libretro frontend (RetroArch), not only in the reference harness. It runs unattended.

RetroArch for macOS cannot be used for that. It is a Cocoa application whose frame loop only turns when the window server wakes its
run loop, so it runs at full speed in the foreground and crawls (16 to 64 frames in a minute) as soon as any other application,
an editor for instance, has the focus. Nothing can be done about that without a person at the keyboard.

So the real frontend is RetroArch for Linux inside a container (tests/libretro-host/docker/Dockerfile), with null video and audio:
no window, no display, no foreground. The same sources are built there for Linux arm64 (build/linux/arm64 on the host) and RetroArch loads
that core, an ADF (or an M3U of two) as content and a micro-ROM under a Kickstart file name in its system directory (T17a), which also
checks that the core builds and behaves the same outside macOS. The disks are changed with RetroArch's own network commands
(DISK_EJECT_TOGGLE, DISK_NEXT), sent by tests/libretro-host/drive_retroarch.py while it runs. Skipped when Docker is not
running or when P6_TEST_REAL_FRONTEND=0. The first run builds the image (a few minutes of downloads) and the Linux core (minutes).
    python3 -m unittest discover -s tests/libretro-host -p "test_real_frontend.py"
"""
import hashlib
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from tests.host.test_bridge_lifecycle import TOOLS, build_probe, builder  # noqa: E402

IMAGE = "puae6-libretro-test"
DOCKER_DIR = ROOT / "tests/libretro-host/docker"
LINUX_BUILD = "build/arm64"            # inside the container; on the host it is build/linux/arm64
FRAMES = 120
UNIT = re.compile(r"^\[libretro DEBUG\] \[PUAE6\] unit=(\d+) ticks=(\d+) ports=(\S+) keys=(\w+)$", re.M)
HEADLESS = re.compile(r"^unit (\d+) ticks=(\d+) video=(\d) active=(\d+)x(\d+) .* hash=(\w+) audio=(\d+)$", re.M)


def docker_ready():
    if not shutil.which("docker"):
        return False
    try:
        return subprocess.run(["docker", "info"], capture_output=True, timeout=30).returncode == 0
    except subprocess.TimeoutExpired:
        return False


def enabled():
    return os.environ.get("P6_TEST_REAL_FRONTEND") != "0" and TOOLS and docker_ready()


def container(command, work=None, timeout=300):
    """Runs a shell command in the image as the current user with the repository at /src and the scratch directory at /work."""
    # build/ is a separate host directory (build/linux), so the Linux build has its own build/upstream and build/generated and never
    # meets the macOS ones: the code generator refuses to overwrite an output made with another compiler.
    (ROOT / "build/linux").mkdir(parents=True, exist_ok=True)
    arguments = ["docker", "run", "--rm", "--user", f"{os.getuid()}:{os.getgid()}", "-e", "HOME=/work/home", "-v", f"{ROOT}:/src",
                 "-v", f"{ROOT}/build/linux:/src/build", "-w", "/src"]
    if work:
        arguments += ["-v", f"{work}:/work"]
    return subprocess.run(arguments + [IMAGE, "bash", "-c", command], capture_output=True, text=True, timeout=timeout)


@unittest.skipUnless(enabled(), "Docker is not running (or P6_TEST_REAL_FRONTEND=0)")
class RealFrontendTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if subprocess.run(["docker", "image", "inspect", IMAGE], capture_output=True).returncode:
            built = subprocess.run(["docker", "build", "-t", IMAGE, "-f", str(DOCKER_DIR / "Dockerfile"), str(DOCKER_DIR)],
                                   capture_output=True, text=True, timeout=1800)
            assert built.returncode == 0, built.stdout[-800:] + built.stderr[-800:]
        configure = (f"cmake -S /src -B /src/{LINUX_BUILD} -G Ninja -DCMAKE_C_COMPILER=clang -DCMAKE_CXX_COMPILER=clang++ "
                     "-DCMAKE_BUILD_TYPE=Debug -DP6_SANITIZERS=OFF > /dev/null")
        built = container(f"{configure} && cmake --build /src/{LINUX_BUILD} --target puae6_libretro p6_headless", timeout=3600)
        assert built.returncode == 0, built.stdout[-1500:] + built.stderr[-1500:]
        cls.tmp = tempfile.TemporaryDirectory(prefix="p6-retroarch-")
        (Path(cls.tmp.name) / "home").mkdir()
        cls.rom = Path(cls.tmp.name) / "boot.rom"
        cls.rom.write_bytes(builder.build((ROOT / "tests/fixtures/microrom/boot.s").read_text(encoding="utf-8")))
        # Disks built from a hash pattern, as tests/state/test_det02.py does; the content is the first of them or a playlist of both.
        for name in ("a", "b"):
            (Path(cls.tmp.name) / f"{name}.adf").write_bytes(hashlib.sha256(f"disk {name}".encode()).digest() * (901120 // 32))
        (Path(cls.tmp.name) / "list.m3u").write_text("a.adf|Disk A\nb.adf|Disk B\n", encoding="utf-8")
        shutil.copyfile(ROOT / "tests/libretro-host/drive_retroarch.py", Path(cls.tmp.name) / "drive_retroarch.py")
        cls.macos_headless = build_probe(ROOT / "build/link-audit", False, "p6_headless")

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def retroarch(self, name, content="a.adf", drive=False, options=None, settings=(), frames=FRAMES, features="deterministic",
                  kickstarts=("kick34005.A500",), rom=None):
        """One RetroArch process: loads the core with the micro-ROM as the Kickstart of its system directory and `content` (a file of the scratch
        directory) as the game, runs FRAMES frames and exits, or runs until tests/libretro-host/drive_retroarch.py has changed the disk and
        stops it (`drive` True drives the disk, a string names another scenario of that script: "state", "rewind"). `options` are core option values
        (key -> value) that RetroArch's own options file holds, as a person's menu choices do; `settings` are lines of RetroArch's own configuration. RetroArch
        only offers run-ahead for a core whose .info file says the save states are deterministic, so the test writes one (`features` is its savestate_features).
        Returns the log text."""
        work = Path(self.tmp.name)
        for folder in ("saves", "states", "system", "playlists", "cache", "info", "cores"):
            (work / name / folder).mkdir(parents=True)
        (work / name / "info" / "puae6_libretro.info").write_text('display_name = "PUAE6 (test)"\ncorename = "PUAE6"\nsavestate = "true"\n'
                                                                  f'savestate_features = "{features}"\n', encoding="utf-8")
        for kickstart in kickstarts:             # the micro-ROM under the Kickstart name of each machine the process may be asked for
            shutil.copyfile(rom or self.rom, work / name / "system" / kickstart)
        (work / name / "retroarch.cfg").write_text("\n".join([
            'video_driver = "null"', 'audio_driver = "null"', 'input_driver = "null"', 'menu_driver = "null"', 'libretro_log_level = "0"',
            'frontend_log_level = "0"', 'config_save_on_exit = "false"', f'savefile_directory = "/work/{name}/saves"',
            f'savestate_directory = "/work/{name}/states"', f'system_directory = "/work/{name}/system"',
            f'playlist_directory = "/work/{name}/playlists"', f'cache_directory = "/work/{name}/cache"', f'libretro_info_path = "/work/{name}/info"', f'libretro_directory = "/work/{name}/cores"', 'history_list_enable = "false"',
            'video_vsync = "false"', 'audio_sync = "false"', 'pause_nonactive = "false"', 'savestate_auto_save = "false"',
            'savestate_auto_load = "false"', 'network_cmd_enable = "true"', 'network_cmd_port = "55355"',
            'quit_press_twice = "false"', 'global_core_options = "true"', 'savestate_file_compression = "false"', *settings]) + "\n")
        # RetroArch keeps the values of the options in a file next to its configuration; the core only ever sees them through GET_VARIABLE.
        (work / name / "retroarch-core-options.cfg").write_text("".join(f'{key} = "{value}"\n' for key, value in (options or {}).items()))
        # RetroArch knows what a core promises from the .info file that sits for a core of its cores directory.
        link = f"ln -s /src/{LINUX_BUILD}/puae6_libretro.so /work/{name}/cores/puae6_libretro.so; "
        start = f"retroarch -c /work/{name}/retroarch.cfg -L /work/{name}/cores/puae6_libretro.so /work/{content} --verbose --log-file /work/{name}/log.txt"
        if drive:
            # The script stops RetroArch when it is done; the timeout keeps a script that failed from leaving the container running.
            command = (f"{link}timeout 200 {start} --max-frames 200000 & player=$!; python3 /work/drive_retroarch.py /work/{name}/log.txt {drive if isinstance(drive, str) else 'disk'}; "
                       "wait $player; echo retroarch-exit=$?")
        else:
            command = f"{link}{start} --max-frames {frames}; echo retroarch-exit=$?"
        result = container(command, work=work, timeout=300)
        self.assertIn("retroarch-exit=0", result.stdout, result.stdout[-600:] + result.stderr[-600:])
        if drive:
            self.assertIn("drive-ok", result.stdout, result.stdout[-800:] + result.stderr[-800:])
        return (work / name / "log.txt").read_text(encoding="utf-8", errors="replace")

    def test_a_real_frontend_loads_runs_and_unloads_the_core_one_unit_per_frame(self):
        text = self.retroarch("first")
        self.assertIn("Kickstart kick34005.A500, read through the frontend VFS", text)             # RetroArch offers a VFS and the core used it
        self.assertIn(f"medium 0 sha256={hashlib.sha256(hashlib.sha256(b'disk a').digest() * (901120 // 32)).hexdigest()}", text)
        self.assertIn("DF0: medium 0 inserted", text)
        self.assertIn("SET_PIXEL_FORMAT: XRGB8888", text)                                          # negotiated with the real frontend
        self.assertRegex(text, r"Geometry: 756x574, Aspect: 1\.370, FPS: 50\.08, Sample rate: 48000\.00 Hz")
        units = [(int(m[1]), int(m[2])) for m in UNIT.finditer(text)]
        self.assertGreaterEqual(len(units), FRAMES)
        self.assertEqual([u[0] for u in units], list(range(1, len(units) + 1)))                   # one unit per frame, none skipped or repeated
        self.assertTrue(all(b[1] > a[1] for a, b in zip(units, units[1:])))
        self.assertEqual(set(m[3] for m in UNIT.finditer(text)), {"2:0:0:0,1:0:0:0"})              # its input polls reach the bridge: nothing pressed
        self.assertNotRegex(text, r"(?m)^\[libretro (ERROR|WARN)\]")                               # nothing the core itself complained about
        frontend_errors = [line for line in text.splitlines() if line.startswith("[ERROR]")]
        # What RetroArch reports about its own null audio driver, menu and MIDI in a container is not about the core.
        self.assertEqual([line for line in frontend_errors if not re.search(r"audio driver|menu driver|MIDI", line)], [])
        self.assertIn("Unloading game", text)
        self.assertIn("Unloading core", text)

    def test_the_frontend_accepts_the_controller_info_and_input_descriptors_and_the_core_polls_a_keyboard_that_nobody_presses(self):
        # T17c. RetroArch is started with input_driver "null": there is no device to press, so no key, mouse movement or button can be injected from
        # outside (its network commands carry no input), and what the real frontend can show is that it takes the declared types (mouse in port 1 only,
        # joystick-only in port 1 and keyboard-only in both, besides the RetroPad and None it adds itself) and the descriptors, and that the core polls every unit without anything
        # arriving. The mapping itself is tested with the minimal frontend (tests/libretro-host/test_input.py).
        text = self.retroarch("input", frames=20)
        info = text[text.index("SET_CONTROLLER_INFO."):text.index("SET_INPUT_DESCRIPTORS")]
        first, second = info.split("Port 2:")
        self.assertIn('"Mouse only (Amiga port 1)" (2)', first)
        self.assertIn('"Joystick only (Amiga port 2)" (257)', first)                                  # the subclass of the RetroPad that frees the mouse port
        self.assertIn('"Keyboard only (port empty)" (3)', first)
        self.assertIn('"Keyboard only (port empty)" (3)', second)
        self.assertNotIn("(2)", second)                                                            # the contract has no mouse in player 2
        self.assertIn("SET_INPUT_DESCRIPTORS", text)
        self.assertEqual(set(m[3] for m in UNIT.finditer(text)), {"2:0:0:0,1:0:0:0"})
        self.assertEqual(set(m[4] for m in UNIT.finditer(text)), {"0" * 32})                      # the 128-bit keyboard bitmap: nothing pressed
        self.assertNotRegex(text, r"(?m)^\[libretro (ERROR|WARN)\]")

    def test_the_frontend_sees_the_same_ticks_as_the_headless_adapter_on_both_systems_and_in_every_process(self):
        native = subprocess.run([str(self.macos_headless), "--run", str(self.rom), "--units", str(FRAMES)], capture_output=True, text=True, timeout=600)
        whole = {int(m[1]): m.groups()[1:] for m in HEADLESS.finditer(native.stdout)}         # ticks, video, size, hash, audio per unit
        expected = {unit: int(values[0]) for unit, values in whole.items()}
        self.assertEqual(len(expected), FRAMES)
        linux = container(f"/src/{LINUX_BUILD}/p6_headless --run /work/boot.rom --units {FRAMES}", work=self.tmp.name)
        self.assertEqual({int(m[1]): m.groups()[1:] for m in HEADLESS.finditer(linux.stdout)}, whole,
                         "Linux and macOS disagree on the ticks, the picture or the audio of a unit")
        first = {int(m[1]): int(m[2]) for m in UNIT.finditer(self.retroarch("ticks-a"))}
        second = {int(m[1]): int(m[2]) for m in UNIT.finditer(self.retroarch("ticks-b"))}
        for unit, ticks in expected.items():
            self.assertEqual(first[unit], ticks, f"unit {unit}")
            self.assertEqual(second[unit], ticks, f"unit {unit} in a second process")

    def test_the_options_retroarch_stores_select_the_machine_headless_runs_with_the_same_settings(self):
        # T17b: RetroArch gets the options from the core (v2, with categories), keeps them in its options file and hands them back at load.
        text = self.retroarch("ntsc", options={"puae6_video_standard": "NTSC", "puae6_audio_rate": "44100"})
        self.assertRegex(text, r"Geometry: 756x484, Aspect: 1\.343, FPS: 60\.05, Sample rate: 44100\.00 Hz")
        native = subprocess.run([str(self.macos_headless), "--run", str(self.rom), "--units", str(FRAMES), "--ntsc"], capture_output=True, text=True,
                                timeout=600)
        expected = {int(m[1]): int(m[2]) for m in HEADLESS.finditer(native.stdout)}
        ticks = {int(m[1]): int(m[2]) for m in UNIT.finditer(text)}
        for unit, value in expected.items():
            self.assertEqual(ticks[unit], value, f"unit {unit}")
        self.assertNotRegex(text, r"(?m)^\[libretro (ERROR|WARN)\]")
        default = self.retroarch("pal")
        self.assertRegex(default, r"Geometry: 756x574, Aspect: 1\.370, FPS: 50\.08, Sample rate: 48000\.00 Hz")

    def test_the_model_retroarch_stores_selects_the_a600_headless_runs_and_finds_its_kickstart_by_its_own_name(self):
        # T23d: the machine is a restart option like the others. The system directory has the Kickstart of the A600 only under the A600's name.
        text = self.retroarch("a600", options={"puae6_machine": "A600"}, kickstarts=("kick37350.A600",))
        self.assertIn("machine A600 (puae6_machine)", text)
        self.assertIn("Kickstart kick37350.A600, read through the frontend VFS", text)
        native = subprocess.run([str(self.macos_headless), "--run", str(self.rom), "--machine", "a600", "--units", str(FRAMES)], capture_output=True, text=True,
                                timeout=600)
        expected = {int(m[1]): int(m[2]) for m in HEADLESS.finditer(native.stdout)}
        ticks = {int(m[1]): int(m[2]) for m in UNIT.finditer(text)}
        self.assertEqual(len(expected), FRAMES)
        for unit, value in expected.items():
            self.assertEqual(ticks[unit], value, f"unit {unit}")
        self.assertNotRegex(text, r"(?m)^\[libretro (ERROR|WARN)\]")
        default = self.retroarch("a500", kickstarts=("kick34005.A500", "kick37350.A600"))
        self.assertIn("machine A500 (puae6_machine)", default)
        self.assertIn("Kickstart kick34005.A500,", default)

    def test_retroarch_follows_the_rate_of_an_a600_guest_that_programs_ntsc_timing_on_a_pal_machine(self):
        # T23f: the program of tests/scenarios/ecs writes BEAMCON0 with the PAL bit clear. The core announces the machine's nominal rate at the load and
        # SET_SYSTEM_AV_INFO with the rate of the NTSC beam on the PAL clock (3546895 / 59605 Hz) once the units run at it; RetroArch applies it and says so.
        from tests.scenarios.ecs import ecs
        rom = Path(self.tmp.name) / "to-ntsc.rom"
        rom.write_bytes(builder.build(ecs.rom_source(ecs.beam_display_for("to-ntsc", "pal"))))
        text = self.retroarch("beam", options={"puae6_machine": "A600"}, kickstarts=("kick37350.A600",), rom=rom)
        self.assertRegex(text, r"Core\]: Geometry: 756x574, Aspect: 1\.370, FPS: 50\.08, Sample rate: 48000\.00 Hz")           # the load: the nominal PAL rate
        changes = re.findall(r"SET_SYSTEM_AV_INFO: (\d+x\d+), Aspect: ([\d.]+), FPS: ([\d.]+), Sample rate: ([\d.]+) Hz", text)
        self.assertEqual([(c[2], c[3]) for c in changes], [("59.51", "48000.00")], changes)         # exactly once, the rate of 262 lines of 227.5 clocks
        self.assertNotRegex(text, r"(?m)^\[libretro (ERROR|WARN)\]")

    def test_retroarch_changes_the_disk_through_disk_control_and_the_playlist_is_read_by_the_frontend_vfs(self):
        text = self.retroarch("disks", content="list.m3u", drive=True)
        self.assertRegex(text, r"(?m)^\[libretro INFO\] \[PUAE6\] disk 1: medium 0, label \"Disk A\"$")
        self.assertRegex(text, r"(?m)^\[libretro INFO\] \[PUAE6\] disk 2: medium 1, label \"Disk B\"$")
        events = re.findall(r"(?m)^\[libretro INFO\] \[PUAE6\] (DF0: .*)$", text)
        self.assertEqual(events, ["DF0: medium 0 inserted", "DF0: ejected", "DF0: medium 1 inserted"])
        self.assertNotRegex(text, r"(?m)^\[libretro (ERROR|WARN)\]")

    # --- T18: states, rewind and runahead in the real frontend -------------------------------------------------------------------------------------

    def expected_ticks(self, units):
        native = subprocess.run([str(self.macos_headless), "--run", str(self.rom), "--units", str(units)], capture_output=True, text=True, timeout=600)
        expected = {int(m[1]): int(m[2]) for m in HEADLESS.finditer(native.stdout)}
        self.assertEqual(len(expected), units)
        return expected

    def assert_converges(self, text, expected):
        """Every unit the core made, in whatever order and however often (speculative runs, replays after a load), has the ticks of the plain run."""
        units = [(int(m[1]), int(m[2])) for m in UNIT.finditer(text)]
        self.assertGreater(len(units), 20)
        for unit, ticks in units:
            self.assertEqual(ticks, expected[unit], f"unit {unit}")
        return [u for u, _ in units]

    def test_runahead_in_the_real_frontend_runs_speculative_units_and_converges_with_the_plain_run(self):
        expected = self.expected_ticks(200)
        for frames in (1, 2, 3):
            with self.subTest(frames=frames):
                text = self.retroarch(f"ahead{frames}", frames=60, settings=['run_ahead_enabled = "true"', f"run_ahead_frames = \"{frames}\"",
                                                                              'run_ahead_secondary_instance = "false"', 'run_ahead_hide_warnings = "true"'])
                order = self.assert_converges(text, expected)
                self.assertGreater(len(order), 60 * (frames + 1) - 1)                    # RetroArch ran the core frames + 1 times per frame
                self.assertTrue(any(b <= a for a, b in zip(order, order[1:])))            # and went back after the speculative runs: a load, not a run
                self.assertNotRegex(text, r"(?m)^\[libretro (ERROR|WARN)\]")
                self.assertNotIn("not supported", text.lower().replace("not supported by", ""))

    def test_asking_for_the_second_instance_of_runahead_changes_nothing_that_converges(self):
        # The profile has one native instance per loaded image and this adapter does nothing to be loaded twice. RetroArch 1.18 ran this core with a
        # single instance when the second was asked for (the same units in the same order, no message), so nothing is claimed about a second instance:
        # the run must only converge with the plain one.
        expected = self.expected_ticks(200)
        text = self.retroarch("ahead-secondary", frames=60, settings=['run_ahead_enabled = "true"', 'run_ahead_frames = "1"', 'run_ahead_secondary_instance = "true"',
                                                                       'run_ahead_hide_warnings = "true"'])
        self.assert_converges(text, expected)
        self.assertNotRegex(text, r"(?m)^\[libretro (ERROR|WARN)\]")

    def test_a_state_saved_and_loaded_through_the_frontend_continues_where_it_was_saved(self):
        expected = self.expected_ticks(400)
        text = self.retroarch("state", drive="state")
        self.assert_converges(text, expected)
        lines = text.splitlines()
        saving = next(i for i, line in enumerate(lines) if re.search(r'\[State\]: Saving state ".*\.state", \d+ bytes', line))
        loading = next(i for i, line in enumerate(lines) if re.search(r'\[State\]: Loading state ".*\.state", \d+ bytes', line))
        before = [int(m[1]) for m in UNIT.finditer("\n".join(lines[:saving]))]
        after = [int(m[1]) for m in UNIT.finditer("\n".join(lines[loading:]))]
        self.assertGreater(max(before), before[-1] - 1)
        self.assertGreater(len([u for u in UNIT.finditer("\n".join(lines[saving:loading]))]), 40)       # it ran on for a while after the save
        self.assertEqual(after[0], before[-1] + 1)                                                     # and the load put it back right after the saved unit
        size = int(re.search(r'Saving state ".*\.state", (\d+) bytes', text)[1])
        states = list((Path(self.tmp.name) / "state" / "states" / "PUAE6").glob("*.state"))
        self.assertEqual([s.stat().st_size for s in states], [size])                                   # the file is exactly the announced size
        self.assertGreater(size, 1_000_000)
        self.assertNotRegex(text, r"(?m)^\[libretro (ERROR|WARN)\]")

    def test_rewind_in_the_real_frontend_loads_states_and_the_units_it_runs_again_are_the_plain_ones(self):
        expected = self.expected_ticks(400)
        text = self.retroarch("rewind", drive="rewind", settings=['rewind_enable = "true"', 'rewind_buffer_size = "40"', 'rewind_granularity = "1"'])
        order = self.assert_converges(text, expected)                       # every unit, whenever it was made, has the ticks of the plain run
        # RetroArch 1.18 with the network command REWIND (a press per command, sent faster than it rewinds) steps back by loading the previous state and
        # running the unit again: a unit made twice in a row. Each of those loads converged, which is what the ticks above say.
        again = sum(1 for a, b in zip(order, order[1:]) if b <= a)
        self.assertGreaterEqual(again, 10, "the rewind never loaded a state")
        self.assertNotRegex(text, r"(?m)^\[libretro (ERROR|WARN)\]")


if __name__ == "__main__":
    unittest.main()
