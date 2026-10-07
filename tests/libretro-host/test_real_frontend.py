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
UNIT = re.compile(r"^\[libretro DEBUG\] \[PUAE6\] unit=(\d+) ticks=(\d+) ports=(\S+)$", re.M)
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

    def retroarch(self, name, content="a.adf", drive=False, options=None):
        """One RetroArch process: loads the core with the micro-ROM as the Kickstart of its system directory and `content` (a file of the scratch
        directory) as the game, runs FRAMES frames and exits, or runs until tests/libretro-host/drive_retroarch.py has changed the disk and
        stops it. `options` are core option values (key -> value) that RetroArch's own options file holds, as a person's menu choices do.
        Returns the log text."""
        work = Path(self.tmp.name)
        for folder in ("saves", "states", "system", "playlists", "cache"):
            (work / name / folder).mkdir(parents=True)
        shutil.copyfile(self.rom, work / name / "system" / "kick34005.A500")
        (work / name / "retroarch.cfg").write_text("\n".join([
            'video_driver = "null"', 'audio_driver = "null"', 'input_driver = "null"', 'menu_driver = "null"', 'libretro_log_level = "0"',
            'frontend_log_level = "0"', 'config_save_on_exit = "false"', f'savefile_directory = "/work/{name}/saves"',
            f'savestate_directory = "/work/{name}/states"', f'system_directory = "/work/{name}/system"',
            f'playlist_directory = "/work/{name}/playlists"', f'cache_directory = "/work/{name}/cache"', 'history_list_enable = "false"',
            'video_vsync = "false"', 'audio_sync = "false"', 'pause_nonactive = "false"', 'savestate_auto_save = "false"',
            'savestate_auto_load = "false"', 'network_cmd_enable = "true"', 'network_cmd_port = "55355"',
            'quit_press_twice = "false"', 'global_core_options = "true"']) + "\n")
        # RetroArch keeps the values of the options in a file next to its configuration; the core only ever sees them through GET_VARIABLE.
        (work / name / "retroarch-core-options.cfg").write_text("".join(f'{key} = "{value}"\n' for key, value in (options or {}).items()))
        start = (f"retroarch -c /work/{name}/retroarch.cfg -L /src/{LINUX_BUILD}/puae6_libretro.so /work/{content} --verbose "
                 f"--log-file /work/{name}/log.txt")
        if drive:
            # The script stops RetroArch when it is done; the timeout keeps a script that failed from leaving the container running.
            command = (f"timeout 200 {start} --max-frames 200000 & player=$!; python3 /work/drive_retroarch.py /work/{name}/log.txt; "
                       "wait $player; echo retroarch-exit=$?")
        else:
            command = f"{start} --max-frames {FRAMES}; echo retroarch-exit=$?"
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
        self.assertEqual(set(m[3] for m in UNIT.finditer(text)), {"1:0:0:0,1:0:0:0"})              # its input polls reach the bridge: nothing pressed
        self.assertNotRegex(text, r"(?m)^\[libretro (ERROR|WARN)\]")                               # nothing the core itself complained about
        frontend_errors = [line for line in text.splitlines() if line.startswith("[ERROR]")]
        # What RetroArch reports about its own null audio driver, menu and MIDI in a container is not about the core.
        self.assertEqual([line for line in frontend_errors if not re.search(r"audio driver|menu driver|MIDI", line)], [])
        self.assertIn("Unloading game", text)
        self.assertIn("Unloading core", text)

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

    def test_retroarch_changes_the_disk_through_disk_control_and_the_playlist_is_read_by_the_frontend_vfs(self):
        text = self.retroarch("disks", content="list.m3u", drive=True)
        self.assertRegex(text, r"(?m)^\[libretro INFO\] \[PUAE6\] disk 1: medium 0, label \"Disk A\"$")
        self.assertRegex(text, r"(?m)^\[libretro INFO\] \[PUAE6\] disk 2: medium 1, label \"Disk B\"$")
        events = re.findall(r"(?m)^\[libretro INFO\] \[PUAE6\] (DF0: .*)$", text)
        self.assertEqual(events, ["DF0: medium 0 inserted", "DF0: ejected", "DF0: medium 1 inserted"])
        self.assertNotRegex(text, r"(?m)^\[libretro (ERROR|WARN)\]")


if __name__ == "__main__":
    unittest.main()
