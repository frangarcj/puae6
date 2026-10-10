"""T19b: puae6.wbx in a real Waterbox sandbox. It initializes the A500 from a firmware image, seals, and advances units through the same
p6_run_frame the headless adapter uses; every unit must equal what p6_headless prints for the same firmware.

The static tests need nothing but a C++ compiler. The runtime tests need the Waterbox runtime and the guest:
    python3 tools/build_waterbox.py --puae6-run build/waterbox/boot.rom --units 60
and skip when they are missing (the receipts of that command are how they find out). P6_TEST_WATERBOX=0 skips them too.
"""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT))
import build_waterbox  # noqa: E402
from tests.host.test_bridge_lifecycle import TOOLS, build_probe, builder  # noqa: E402
from tests.scenarios.disk import disk as adf  # noqa: E402

MANIFEST = ROOT / "manifests/waterbox-memory.json"
RUNTIME_RECEIPT = ROOT / "build/waterbox/receipt.json"
GUEST_RECEIPT = ROOT / "build/waterbox/puae6/puae6-receipt.json"
UNIT = re.compile(r"^unit (\d+) ticks=(\d+) video=(\d) active=(\d+)x(\d+) .* hash=(\w+) audio=(\d+)$", re.M)
ADF_BYTES = 901120
KICKSTART_BYTES = 512 * 1024
MEASURED = (("A500", "measured"), ("A600", "measured_a600"), ("A1200", "measured_a1200"))     # one set of budgets for the three machines (T23e, T24f)
# T49: machines with other memory: (label, manifest key, machine, memory flags of p6_headless and of build_waterbox.run_puae6). Their mmap budget is the base one plus twice the extra memory.
MEMORY_CONFIGS = (
    ("A1200 + 8 MiB Fast", "measured_a1200_fast8192", "A1200", {"fast_kib": 8192}),
    ("A600, 2 MiB Chip + 8 MiB Fast (PUAE5)", "measured_a600_chip2048_fast8192", "A600", {"chip_kib": 2048, "fast_kib": 8192}),
    ("A1200 + 1.5 MiB Slow + 8 MiB Fast", "measured_a1200_slow1536_fast8192", "A1200", {"slow_kib": 1536, "fast_kib": 8192}),
    ("A500 + 2 MiB Fast", "measured_a500_fast2048", "A500", {"fast_kib": 2048}),
    ("A1200 with a 68882 (T54)", "measured_a1200_fpu68882", "A1200", {"fpu_model": 68882}),     # SoftFloat adds no memory: the base budget
)
OWN_MEMORY_KIB = {"A500": (512, 512), "A600": (1024, 0), "A1200": (2048, 0)}      # Chip, Slow


def extra_memory_kib(machine, chip_kib=0, slow_kib=0, fast_kib=0, fpu_model=0):
    """The memory a configuration has above what the machine comes with, in KiB: all the Fast RAM and the Chip and Slow RAM above their own sizes."""
    own_chip, own_slow = OWN_MEMORY_KIB[machine]
    extra = fast_kib
    if chip_kib > own_chip:
        extra += chip_kib - own_chip
    if slow_kib not in (0, "none") and slow_kib > own_slow:
        extra += slow_kib - own_slow
    return extra


def memory_flags(memory):
    flags = []
    for flag, key in (("--chip", "chip_kib"), ("--slow", "slow_kib"), ("--fast", "fast_kib"), ("--fpu", "fpu_model")):
        if memory.get(key):
            flags += [flag, str(memory[key])]
    return flags


def runtime_available():
    return (os.environ.get("P6_TEST_WATERBOX") != "0" and RUNTIME_RECEIPT.is_file() and GUEST_RECEIPT.is_file()
            and shutil.which("dotnet") is not None and TOOLS)


def microrom(name, defines=None):
    source = (ROOT / "tests/fixtures/microrom" / name).read_text(encoding="utf-8")
    return builder.build(source, defines) if defines else builder.build(source)


class MemoryManifestTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.manifest = json.loads(MANIFEST.read_text())

    def test_every_heap_is_a_whole_number_of_16_kib_host_pages_and_the_unused_ones_are_the_smallest_the_runtime_accepts(self):
        layout = self.manifest["layout"]
        self.assertEqual({"sbrk", "sealed", "invisible", "plain", "mmap"}, set(layout))
        for name, size in layout.items():
            self.assertEqual(0, size % build_waterbox.PAGE_SIZE, name)
            self.assertGreater(size, 0, name)
        self.assertEqual(build_waterbox.PAGE_SIZE, layout["invisible"])
        self.assertEqual(build_waterbox.PAGE_SIZE, layout["plain"])

    def test_the_sealed_heap_holds_a_kickstart_and_every_medium_the_contract_allows(self):
        header = (ROOT / "bridge/include/p6.h").read_text()
        maximum = int(re.search(r"#define P6_MAX_MEDIA (\d+)", header).group(1))
        self.assertGreaterEqual(self.manifest["layout"]["sealed"], KICKSTART_BYTES + maximum * ADF_BYTES)

    def test_the_classification_names_every_region_with_a_category_of_the_docs_table(self):
        categories = {"Inmutable", "Estado emulado", "Estado del algoritmo", "Output de una llamada", "Cache derivada", "Estado C#"}
        regions = self.manifest["classification"]
        self.assertTrue(all(entry["category"] in categories and entry["treatment"] for entry in regions))
        text = " ".join(entry["region"] for entry in regions)
        for region in ("sbrk", "mmap", "sealed", "invisible", "plain", "stacks", "host buffers", "video and audio", "host (C#)", "writable data"):
            self.assertIn(region, text)
        docs = (ROOT / "docs/03-waterbox.md").read_text()
        for category in categories:
            self.assertIn(category, docs, "the categories are the ones of the table in docs/03-waterbox.md")

    def test_no_snapshot_content_is_excluded_until_a_poison_test_exists(self):
        treatments = json.dumps(self.manifest["classification"])
        self.assertNotIn("not in the snapshot", treatments.replace("does not put them in the snapshot", ""))
        for entry in self.manifest["candidates_for_exclusion"]:
            self.assertIn("T36", entry["needed"])

    def test_what_was_measured_fits_in_half_of_its_budget_on_the_three_machines(self):
        layout = self.manifest["layout"]
        for machine, key in MEASURED:
            measured = self.manifest[key]
            self.assertTrue(measured, f"run tools/build_waterbox.py --puae6-run ... --record-memory{'' if machine == 'A500' else ' --machine ' + machine}")
            self.assertEqual(machine, measured["fixture"]["machine"])
            for phase in ("after_seal", "after_first_unit", "after_last_unit"):
                for heap in ("sbrk", "mmap"):
                    self.assertLessEqual(2 * measured[phase][heap]["committed_bytes"], layout[heap], f"{machine} {heap} {phase}")
                self.assertLessEqual(measured[phase]["sealed"]["committed_bytes"], layout["sealed"])
                # nothing is allocated from the heaps whose content would be left out of a snapshot
                self.assertEqual(0, measured[phase]["plain"]["committed_bytes"])
                self.assertEqual(0, measured[phase]["invisible"]["committed_bytes"])

    def test_what_was_measured_with_other_memory_fits_in_half_of_the_budget_the_rule_gives_and_the_board_leaves_the_snapshot_clean(self):
        # T49: the mmap heap of a configuration is the base one plus twice its extra memory (memory_budget); the half rule holds for each measured configuration.
        rule = self.manifest["memory_budget"]
        self.assertEqual(2, rule["mmap_extra_factor"])
        layout = self.manifest["layout"]
        for label, key, machine, memory in MEMORY_CONFIGS:
            with self.subTest(label):
                measured = self.manifest[key]
                self.assertEqual(machine, measured["fixture"]["machine"])
                for name, value in memory.items():
                    self.assertEqual(value, measured["fixture"][name])
                budget = layout["mmap"] + rule["mmap_extra_factor"] * extra_memory_kib(machine, **memory) * 1024
                self.assertEqual(0, budget % build_waterbox.PAGE_SIZE)
                for phase in ("after_seal", "after_first_unit", "after_last_unit"):
                    self.assertLessEqual(2 * measured[phase]["mmap"]["committed_bytes"], budget, f"{label} mmap {phase}")
                    self.assertLessEqual(2 * measured[phase]["sbrk"]["committed_bytes"], layout["sbrk"], f"{label} sbrk {phase}")
                    self.assertEqual(0, measured[phase]["plain"]["committed_bytes"])
                    self.assertEqual(0, measured[phase]["invisible"]["committed_bytes"])
                self.assertEqual(0, measured["after_last_unit"]["sealed"]["dirty_bytes"])
        # The board is cleared without being written: a pristine 8 MiB board does not make the snapshot 8 MiB larger (before patch 0114 it did: 18.7 MB against 10.2 MB).
        plain, fast = self.manifest["measured_a1200"], self.manifest["measured_a1200_fast8192"]
        self.assertLess(fast["snapshot_bytes"]["after_last_unit"] - plain["snapshot_bytes"]["after_last_unit"], 1 << 20)
        self.assertEqual(plain["after_last_unit"]["mmap"]["dirty_bytes"], fast["after_last_unit"]["mmap"]["dirty_bytes"])
        self.assertGreater(fast["after_seal"]["mmap"]["committed_bytes"] - plain["after_seal"]["mmap"]["committed_bytes"], 8 << 20)

    def test_the_sealed_heap_holds_the_firmware_and_was_not_written_after_the_seal(self):
        for machine, key in MEASURED:
            measured = self.manifest[key]
            firmware = measured["fixture"]["firmware_bytes"]
            self.assertGreaterEqual(measured["after_seal"]["sealed"]["committed_bytes"], firmware, machine)
            self.assertEqual(0, measured["after_last_unit"]["sealed"]["dirty_bytes"], machine)


class GuestAbiTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("c++"), "no C++ compiler")
    def test_the_layout_the_managed_harness_was_written_for_is_the_one_the_header_compiles_to(self):
        program = """
        #include "p6w.h"
        #include <stddef.h>
        #include <stdio.h>
        int main() {
            const unsigned long long v[] = {sizeof(p6w_init), offsetof(p6w_init, result), offsetof(p6w_init, error), offsetof(p6w_init, media),
                sizeof(p6w_media), sizeof(p6w_frame), offsetof(p6w_frame, frame), offsetof(p6w_frame, result), offsetof(p6w_frame, error),
                sizeof(p6_frame), sizeof(p6_input), sizeof(p6_config), offsetof(p6_frame, audio), offsetof(p6_frame, master_ticks),
                offsetof(p6_frame, video), offsetof(p6_frame, video_stride_bytes), offsetof(p6_frame, active), offsetof(p6_frame, aspect_num),
                offsetof(p6_frame, audio_frames), offsetof(p6_frame, unit_index), offsetof(p6_frame, cadence_num), offsetof(p6_frame, field_parity),
                offsetof(p6w_init, firmware), offsetof(p6w_init, media_count), offsetof(p6_config, random_seed), offsetof(p6_config, rtc_epoch_seconds),
                sizeof(p6w_memory_area), offsetof(p6w_memory_area, name), offsetof(p6w_memory_area, size), offsetof(p6w_memory_area, flags),
                sizeof(p6w_media_event), offsetof(p6w_media_event, event), offsetof(p6w_media_event, result), offsetof(p6w_media_event, error),
                sizeof(p6w_media_write_status_args), offsetof(p6w_media_write_status_args, status), offsetof(p6w_media_write_status_args, result), offsetof(p6w_media_write_status_args, error),
                sizeof(p6w_media_export_args), offsetof(p6w_media_export_args, buffer), offsetof(p6w_media_export_args, written), offsetof(p6w_media_export_args, result), offsetof(p6w_media_export_args, error)};
            for (unsigned long long x : v) printf("%llu ", x);
        }"""
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "layout.cpp"
            source.write_text(program)
            subprocess.run(["c++", "-std=c++17", f"-I{ROOT / 'bridge/include'}", f"-I{ROOT / 'adapters/waterbox'}", str(source), "-o", f"{directory}/layout"],
                           check=True, capture_output=True)
            values = [int(x) for x in subprocess.run([f"{directory}/layout"], check=True, capture_output=True, text=True).stdout.split()]
        text = (ROOT / "integration/bizhawk/smoke/Puae6.cs").read_text()
        body = re.search(r"ExpectedLayout = \[(.*?)\];", text, re.S).group(1)
        self.assertEqual([int(x) for x in re.findall(r"\d+", re.sub(r"//[^\n]*", "", body))], values[:14] + values[26:43])
        names = {"InitSize": values[0], "InitFirmware": values[22], "InitMediaCount": values[23], "InitResult": values[1], "InitError": values[2],
                 "FrameSize": values[5], "FrameInner": values[6], "FrameResult": values[7], "FrameError": values[8], "InputSize": values[10]}
        for name, offset in names.items():
            self.assertEqual(offset, int(re.search(rf"\b{name} = (\d+)", text).group(1)), name)
        # the offsets inside p6_frame that Unit() reads by number ("f" is the start of the p6_frame), and inside p6w_init that it writes
        frame = {"(byte**)(f + 8)": values[14], "(uint*)(f + 36)": values[15], "(uint*)(f + 40)": values[16], "(uint*)(f + 44)": values[16] + 4,
                 "(uint*)(f + 48)": values[16] + 8, "(uint*)(f + 52)": values[16] + 12, "(uint*)(f + 56)": values[17], "(uint*)(f + 60)": values[17] + 4,
                 "(ulong*)(f + 72)": values[18], "(ulong*)(f + 80)": values[13], "(ulong*)(f + 88)": values[19], "(uint*)(f + 96)": values[20],
                 "(uint*)(f + 100)": values[20] + 4, "(uint*)(f + 104)": values[21]}
        for accessor, offset in frame.items():
            self.assertEqual(offset, int(re.search(r"f \+ (\d+)", accessor).group(1)), accessor)
            self.assertIn(accessor, text)
        config = 8                                                   # p6_config sits at offset 8 of p6w_init
        self.assertIn(f"(ulong*)(p + {config + values[24]})", text)    # random_seed
        self.assertIn(f"(long*)(p + {config + values[25]})", text)     # rtc_epoch_seconds


@unittest.skipUnless(runtime_available(), "Waterbox runtime or puae6.wbx not built (tools/build_waterbox.py --puae6-run build/waterbox/boot.rom)")
class WaterboxRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="p6-waterbox-")
        cls.headless = build_probe(ROOT / "build/link-audit", False, "p6_headless")
        cls.manifest = json.loads(MANIFEST.read_text())

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def firmware(self, name, defines=None):
        path = Path(self.tmp.name) / (name + ".rom")
        path.write_bytes(microrom(name + ".s", defines))
        return path

    def native(self, rom, units, machine="A500", memory=None):
        result = subprocess.run([str(self.headless), "--run", str(rom), "--units", str(units), *(["--machine", machine.lower()] if machine != "A500" else []),
                                 *memory_flags(memory or {})], capture_output=True, text=True, timeout=600)
        self.assertEqual(0, result.returncode, result.stderr)
        return UNIT.findall(result.stdout)

    def waterbox(self, rom, units, media=(), machine="A500", memory=None):
        lines, result = build_waterbox.run_puae6(rom, units, tuple(media), machine=machine, **(memory or {}))
        return UNIT.findall("\n".join(lines)), result

    def test_the_first_unit_inside_waterbox_equals_headless_ticks_picture_and_audio(self):
        rom = self.firmware("boot")
        inside, result = self.waterbox(rom, 1)
        self.assertEqual(self.native(rom, 1), inside)
        self.assertEqual(1, len(inside))
        self.assertEqual("PASS", result["status"])

    def test_sixty_units_of_each_fixture_program_equal_headless(self):
        for name, defines in (("boot", None), ("vsync", None), ("copper_irq", {"HP": "2"}), ("long_ops", {"PAD": "3"}), ("halt", None)):
            with self.subTest(name):
                rom = self.firmware(name, defines)
                inside, _ = self.waterbox(rom, 60)
                self.assertEqual(60, len(inside))
                self.assertEqual(self.native(rom, 60), inside)

    def test_the_a600_inside_waterbox_equals_headless_on_the_a600_and_is_not_the_a500(self):
        # T23e: the guest takes the machine the host puts in p6_config (the A600 is ECS with 1 MiB of Chip RAM and no Slow RAM): 60 units of the fixture programs
        # are those of p6_headless --machine a600, and the guest reports the machine it was given.
        for name, defines in (("boot", None), ("vsync", None), ("copper_irq", {"HP": "2"})):
            with self.subTest(name):
                rom = self.firmware(name, defines)
                inside, result = self.waterbox(rom, 60, machine="A600")
                self.assertEqual("A600", result["machine"])
                self.assertEqual(60, len(inside))
                self.assertEqual(self.native(rom, 60, "A600"), inside)

    def test_the_a1200_inside_waterbox_equals_headless_on_the_a1200_and_is_neither_of_the_others(self):
        # T24f: the A1200 (68EC020, AGA, 2 MiB of Chip RAM, no Slow RAM) in the sandbox: the same fixture programs are p6_headless --machine a1200 unit by unit, and the guest
        # reports the machine it was given. The AGA code made the guest bigger (the ELF region is not a budgeted heap); the heaps are the ones of the other machines.
        runs = {}
        for name, defines in (("boot", None), ("vsync", None), ("copper_irq", {"HP": "2"})):
            with self.subTest(name):
                rom = self.firmware(name, defines)
                inside, result = self.waterbox(rom, 60, machine="A1200")
                self.assertEqual("A1200", result["machine"])
                self.assertEqual(60, len(inside))
                self.assertEqual(self.native(rom, 60, "A1200"), inside)
                runs[name] = inside
        self.assertNotEqual(runs["boot"], self.native(self.firmware("boot"), 60, "A600"))
        self.assertNotEqual(runs["boot"], self.native(self.firmware("boot"), 60, "A500"))

    def test_a_machine_with_other_memory_inside_waterbox_equals_headless_with_the_same_memory(self):
        # T49: Chip, Slow and Zorro II Fast RAM in the sandbox: the board is allocated from the mmap heap the budget rule gives, and 60 units of the fixture programs are those of
        # p6_headless with the same --chip, --slow and --fast, tick by tick, picture by picture and sample by sample.
        for label, _, machine, memory in MEMORY_CONFIGS:
            for name, defines in (("boot", None), ("copper_irq", {"HP": "2"})):
                with self.subTest(label=label, program=name):
                    rom = self.firmware(name, defines)
                    inside, result = self.waterbox(rom, 60, machine=machine, memory=memory)
                    self.assertEqual(machine, result["machine"])
                    self.assertEqual({"chip_kib": 0, "slow_kib": 0, "fast_kib": 0, **{k: v for k, v in memory.items() if k != "fpu_model"}}, result["configured_memory"])
                    self.assertEqual(memory.get("fpu_model", 0), result["configured_fpu"])
                    self.assertEqual(self.manifest["layout"]["mmap"] + self.manifest["memory_budget"]["mmap_extra_factor"] * extra_memory_kib(machine, **memory) * 1024,
                                     result["layout"]["mmap"])
                    self.assertEqual(60, len(inside))
                    self.assertEqual(self.native(rom, 60, machine, memory), inside)

    def test_the_machine_does_not_notice_that_the_host_buffers_were_overwritten_after_init_and_after_every_call(self):
        # The harness fills the firmware copy, p6w_init and p6_input with 0xA5 right after each call. Equality with headless above is the proof;
        # here the harness reports that it did it, and the firmware in the sealed heap is the only copy the guest could still read.
        _, result = self.waterbox(self.firmware("boot"), 2)
        self.assertTrue(result["host_buffers_poisoned_after_init"])
        self.assertGreaterEqual(result["after_seal"]["sealed"]["committed_bytes"], result["firmware_bytes"])

    def test_disk_images_are_copied_into_the_sealed_heap_at_init_and_do_not_change_a_run_that_never_mounts_them(self):
        # p6_load registers each image by pointer; the pointers the engine keeps are the ones into the sealed copy, since the harness
        # overwrites the host image right after init. Nothing mounts them (that is a media event), so the units are the ones without media.
        disks = []
        for label in (1, 2):
            disk = Path(self.tmp.name) / f"{label}.adf"
            disk.write_bytes(adf.make_adf(label))
            disks.append(disk)
        rom = self.firmware("boot")
        inside, result = self.waterbox(rom, 5, disks)
        self.assertEqual(self.native(rom, 5), inside)
        self.assertEqual(2 * ADF_BYTES, result["media_bytes"])
        self.assertGreaterEqual(result["after_seal"]["sealed"]["committed_bytes"], rom.stat().st_size + 2 * ADF_BYTES)
        self.assertEqual(0, result["after_last_unit"]["sealed"]["dirty_bytes"])

    def test_a_guest_or_runtime_that_is_not_the_one_of_the_receipts_is_refused_before_anything_runs(self):
        # WB15: identity by hash, no fallback. The receipt names puae6.wbx by path and sha256; a receipt that disagrees with the file is an error.
        receipt = json.loads(GUEST_RECEIPT.read_text())
        wrong = Path(self.tmp.name) / "wrong-guest-hash.json"
        receipt["guest"]["sha256"] = "0" * 64
        wrong.write_text(json.dumps(receipt))
        with self.assertRaisesRegex(build_waterbox.BuildError, "guest artifact identity"):
            build_waterbox.run_puae6(self.firmware("boot"), 1, guest_receipt=wrong)
        other = Path(self.tmp.name) / "other-guest.wbx"
        other.write_bytes(build_waterbox.BUILD.joinpath("guest/release/p6-smoke.wbx").read_bytes())
        receipt = json.loads(GUEST_RECEIPT.read_text())
        receipt["guest"]["path"] = str(other)
        wrong_file = Path(self.tmp.name) / "wrong-guest-file.json"
        wrong_file.write_text(json.dumps(receipt))
        with self.assertRaisesRegex(build_waterbox.BuildError, "guest artifact identity"):
            build_waterbox.run_puae6(self.firmware("boot"), 1, guest_receipt=wrong_file)

    def test_c_plus_plus_exceptions_unwind_inside_the_sandbox(self):
        _, result = self.waterbox(self.firmware("boot"), 1)
        self.assertEqual([0, 1, 7, 40], [entry["depth"] for entry in result["exceptions"]])
        for entry in result["exceptions"]:
            self.assertEqual(42, entry["caught"])
            self.assertEqual(entry["depth"] + 1, entry["destructors_run"])

    def test_the_run_used_the_artifacts_of_the_receipts_and_the_budgets_of_the_manifest(self):
        _, result = self.waterbox(self.firmware("boot"), 1)
        self.assertEqual(json.loads(GUEST_RECEIPT.read_text())["guest"]["sha256"], result["guest_sha256"])
        self.assertEqual(json.loads(RUNTIME_RECEIPT.read_text())["runtime_dylib"]["sha256"], result["runtime_sha256"])
        self.assertEqual(self.manifest["layout"], result["layout"])
        for heap, size in self.manifest["layout"].items():
            self.assertEqual(size, result["ranges"][heap]["size"], heap)
        starts = [result["ranges"][name] for name in ("sbrk", "sealed", "invisible", "plain", "mmap")]
        self.assertTrue(all(a["start"] + a["size"] == b["start"] for a, b in zip(starts, starts[1:])), "the heaps are contiguous, as the runtime lays them out")

    def test_the_measurements_in_the_manifest_are_the_ones_a_run_produces_now_on_every_machine_and_configuration(self):
        configs = [(machine, key, machine, {}) for machine, key in MEASURED] + [(label, key, machine, memory) for label, key, machine, memory in MEMORY_CONFIGS]
        for label, key, machine, memory in configs:
            with self.subTest(machine=label):
                measured = self.manifest[key]
                fixture = measured["fixture"]
                rom = self.firmware("boot")
                self.assertEqual(fixture["firmware_sha256"], build_waterbox.sha256(rom), "the recorded fixture is the boot micro-ROM")
                _, result = self.waterbox(rom, fixture["units"], machine=machine, memory=memory)
                for field in ("after_seal", "after_first_unit", "after_last_unit", "snapshot_bytes"):
                    self.assertEqual(measured[field], result[field], f"{label} {field} changed: rerun tools/build_waterbox.py --puae6-run build/waterbox/boot.rom --units "
                                     f"{fixture['units']} --record-memory --machine {machine} {' '.join(memory_flags(memory))} and review manifests/waterbox-memory.json")
                for phase in ("after_seal", "after_first_unit", "after_last_unit"):
                    for heap, size in result["layout"].items():
                        self.assertLessEqual(result[phase][heap]["committed_bytes"], size, f"{label} {heap} exceeds its budget")


if __name__ == "__main__":
    unittest.main()
