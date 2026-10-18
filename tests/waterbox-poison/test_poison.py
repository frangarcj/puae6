"""T36: poison the real guest's pixel history after restore, without changing the guest.

Positive controls restore every byte, future A/V/ticks and the full sandbox digest in
the same sandbox, a new sandbox and a new process. The negative control discards the
candidate after restore, as invisible storage would; both interlaced fields must
detect it. No historical observations or commercial fixtures are needed.
"""
import hashlib
import json
from pathlib import Path
import re
import shutil
import statistics
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / "tools")]
import build_waterbox as wb
from tests.host.test_bridge_lifecycle import builder
from tests.scenarios.a500.display import Display, rom_source

PROJECT = ROOT / "tests/waterbox-poison/Poison.csproj"
HOST = ROOT / "build/waterbox-poison/bin/Debug/net8.0/Poison.dll"
SYMBOLS = {"pixels": "_ZL6pixels", "cpufunctbl": "cpufunctbl", "cpufunctbl_noret": "cpufunctbl_noret",
           "loop_mode_table": "loop_mode_table", "cpudatatbl": "_ZL10cpudatatbl", "mem_banks": "mem_banks",
           "currprefs": "currprefs", "changed_prefs": "changed_prefs", "audio_channel": "_ZL13audio_channel",
           "audio_stream": "_ZL12audio_stream", "floppy": "_ZL6floppy", "dfxhistory": "_ZL10dfxhistory",
           "xcolors": "xcolors", "xredcolors": "xredcolors", "xgreencolors": "xgreencolors", "xbluecolors": "xbluecolors"}
DISPLAYS = {
    "lace": Display("poison-lace", mode="hires", lace=True, bg=0x008, cycle_fg=True),
    "mode": Display("poison-mode", mode="lores", flip="mode", bg=0x008, cycle_fg=True),
    "flip-lace": Display("poison-flip-lace", mode="hires", flip="lace", bg=0x008, cycle_fg=True),
}
AVAILABLE = shutil.which("dotnet") and (wb.BUILD / "puae6/puae6-receipt.json").is_file() and wb.runtime_receipt_path().is_file()


@unittest.skipUnless(AVAILABLE, "requires the actual Darwin ARM64 Waterbox guest/runtime and dotnet")
class SnapshotPoisonTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="p6-waterbox-poison-")
        cls.addClassCleanup(cls.tmp.cleanup)
        cls.directory = Path(cls.tmp.name)
        receipt = wb.BUILD / "puae6/puae6-receipt.json"
        wb.check_guest_matches_trees(receipt)
        guest = json.loads(receipt.read_text())["guest"]
        runtime = json.loads(wb.runtime_receipt_path().read_text())["runtime_dylib"]
        for item in (guest, runtime):
            if hashlib.sha256(Path(item["path"]).read_bytes()).hexdigest() != item["sha256"]:
                raise AssertionError("the loaded artifact must match its receipt")
        cls.env = wb.managed_env()
        command = [shutil.which("dotnet"), "build", str(PROJECT), "--nologo", f"-p:RestoreSources={Path.home() / '.nuget/packages'}"]
        built = subprocess.run(command, cwd=wb.MANAGED, env=cls.env, capture_output=True, text=True, timeout=180)
        if built.returncode:
            raise AssertionError(built.stdout + built.stderr)
        clang = json.loads((ROOT / "plan/toolchains.json").read_text())["tools"]["clang"]["path"]
        nm = subprocess.run([str(Path(clang).with_name("llvm-nm")), "--print-size", guest["path"]], capture_output=True, text=True, check=True)
        symbols = {p[3]: (int(p[0], 16), int(p[1], 16)) for line in nm.stdout.splitlines() if len(p := line.split()) == 4}
        cls.regions = [{"symbol": name, "address": symbols[sym][0], "size": symbols[sym][1]} for name, sym in SYMBOLS.items()]
        video = (ROOT / "ports/portable/video_present.h").read_text()
        width = int(re.search(r"P6_VIDEO_MAX_WIDTH = (\d+)", video)[1])
        # Foreground cells inside both fields, also used by test_display.py's independent picture model.
        offsets = [(row * width + 76) * 4 for row in (200, 201)]
        cls.config = dict(guest=guest["path"], runtime=runtime["path"], manifest=str(wb.MEMORY_MANIFEST), regions=cls.regions, probe_offsets=offsets)
        cls.index = 0

    def run_host(self, config):
        type(self).index += 1
        path = self.directory / f"config-{self.index}.json"
        path.write_text(json.dumps(config))
        result = subprocess.run([shutil.which("dotnet"), str(HOST), str(path)], cwd=ROOT, env=self.env,
                                capture_output=True, text=True, timeout=180)
        self.assertEqual(result.returncode, 0, result.stdout[-1000:] + result.stderr[-4000:])
        return json.loads(next(line[7:] for line in result.stdout.splitlines() if line.startswith("RESULT ")))

    def probe(self, display, machine, standard):
        directory = self.directory / f"{display}-{machine}-{standard}"
        directory.mkdir()
        rom = directory / "fixture.rom"
        source = rom_source(DISPLAYS[display])
        if display == "flip-lace":
            # Leave four VERTBs per mode so that both fields finish before the next
            # change; toggling LACE every VERTB can keep selecting the same field.
            # MOVE.W D1,D0; ANDI.W #3,D0; BNE.S +8 skips EXG (2) + MOVE abs.L (6).
            source = source.replace("    EXG D2,D3", "    .word $3001,$0240,$0003,$6608\n    EXG D2,D3")
        rom.write_bytes(builder.build(source))
        config = dict(self.config, firmware=str(rom), machine=machine, standard=standard, directory=str(directory))
        result = self.run_host(config)
        parities, first_parities = set(), set()
        for point in result["checkpoints"]:
            parity = int(re.search(r"parity=(\d+)", point["trace"][0]["Line"])[1])
            first_parities.add(parity)
            for trace in point["trace"]:
                parities.add(int(re.search(r"parity=(\d+)", trace["Line"])[1]))
            for poison in point["poisons"]:
                # Pixel storage cannot influence the hardware clock, PCM or frame geometry.
                for ref, got in zip(point["trace"], poison["trace"], strict=True):
                    self.assertEqual(ref["Pcm"], got["Pcm"])
                    self.assertEqual(re.sub(r"hash=\w+", "hash=X", ref["Line"]), re.sub(r"hash=\w+", "hash=X", got["Line"]))
                if display == "lace":
                    self.assertNotEqual(point["trace"][0], poison["trace"][0], "discarding the previous field must be detected immediately")
                    drawn, held = ("EvenRgb", "OddRgb") if parity == 1 else ("OddRgb", "EvenRgb")
                    self.assertEqual(point["trace"][0][drawn], poison["trace"][0][drawn])
                    self.assertNotEqual(point["trace"][0][held], poison["trace"][0][held])
                    self.assertEqual(poison["trace"][0][held], poison["value"] * 0x010101, "RGB inside the previous field survived from the poison, not just an X byte or a margin")
            restored = self.run_host(dict(config, restore=str(directory / f"state-{point['checkpoint']}.bin"), checkpoint=point["checkpoint"]))
            self.assertEqual(point["trace"], restored["trace"], "new process: all eight fields, including the first after load")
            self.assertEqual(point["digest"], restored["digest"], "new process: complete future machine state")
            self.assertGreater(point["snapshot_bytes"], 0)
            for operation in ("save_ns", "load_ns"):
                self.assertEqual(len(point[operation]), 5)
                self.assertGreater(min(point[operation]), 0)
        if display in ("lace", "flip-lace"):
            self.assertTrue({1, 2} <= parities, f"the probe covers both interlaced fields: {parities}")
        if display == "lace":
            self.assertEqual(first_parities, {1, 2}, "restore starts immediately before each of the two fields")
        if display == "flip-lace":
            self.assertIn(0, parities, "the same continuation also switches to a non-interlaced field")
        self.assertEqual({h["name"]: h["committed"] for h in result["seal"]["heaps"]}["invisible"], 0)
        first = {r["symbol"]: r for r in result["first"]["regions"]}
        self.assertGreater(first["pixels"]["dirty_pages"], 0)
        self.assertLessEqual(first["pixels"]["dirty_pages"], first["pixels"]["logical_pages"])
        self.assertEqual(first["cpufunctbl"]["dirty_pages"], first["cpufunctbl"]["logical_pages"])
        costs = {op: [v / 1e6 for p in result["checkpoints"] for v in p[op + "_ns"]] for op in ("save", "load")}
        print("T36", display, machine, standard, json.dumps({"snapshot_bytes": [p["snapshot_bytes"] for p in result["checkpoints"]],
            "pixel_dirty_bytes": first["pixels"]["dirty_pages"] * 4096,
            "latency_ms": {op: {"p50": statistics.median(v), "p95": sorted(v)[-1]} for op, v in costs.items()}}), flush=True)
        if (display, machine, standard) == ("lace", 1, 1):
            print("T36 classification", json.dumps({stage: result[stage] for stage in ("seal", "first")}), flush=True)

    def test_interlaced_history_is_causal_on_ocs_ecs_and_aga_pal_and_ntsc(self):
        for machine in (1, 2, 3):
            for standard in (1, 2):
                with self.subTest(machine=machine, standard=standard):
                    self.probe("lace", machine, standard)

    def test_mode_changes_keep_the_exact_captured_future_on_ocs_ecs_and_aga(self):
        for display in ("mode", "flip-lace"):
            for machine in (1, 2, 3):
                for standard in (1, 2):
                    with self.subTest(display=display, machine=machine, standard=standard):
                        self.probe(display, machine, standard)
