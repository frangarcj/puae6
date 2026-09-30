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

    def test_what_was_measured_fits_in_half_of_its_budget(self):
        measured = self.manifest["measured"]
        self.assertTrue(measured, "run tools/build_waterbox.py --puae6-run ... --record-memory")
        layout = self.manifest["layout"]
        for phase in ("after_seal", "after_first_unit", "after_last_unit"):
            for heap in ("sbrk", "mmap"):
                self.assertLessEqual(2 * measured[phase][heap]["committed_bytes"], layout[heap], f"{heap} {phase}")
            self.assertLessEqual(measured[phase]["sealed"]["committed_bytes"], layout["sealed"])
            # nothing is allocated from the heaps whose content would be left out of a snapshot
            self.assertEqual(0, measured[phase]["plain"]["committed_bytes"])
            self.assertEqual(0, measured[phase]["invisible"]["committed_bytes"])

    def test_the_sealed_heap_holds_the_firmware_and_was_not_written_after_the_seal(self):
        measured = self.manifest["measured"]
        firmware = measured["fixture"]["firmware_bytes"]
        self.assertGreaterEqual(measured["after_seal"]["sealed"]["committed_bytes"], firmware)
        self.assertEqual(0, measured["after_last_unit"]["sealed"]["dirty_bytes"])


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
                offsetof(p6w_init, firmware), offsetof(p6w_init, media_count), offsetof(p6_config, random_seed), offsetof(p6_config, rtc_epoch_seconds)};
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
        self.assertEqual([int(x) for x in re.findall(r"\d+", re.sub(r"//[^\n]*", "", body))], values[:14])
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

    def native(self, rom, units):
        result = subprocess.run([str(self.headless), "--run", str(rom), "--units", str(units)], capture_output=True, text=True, timeout=600)
        self.assertEqual(0, result.returncode, result.stderr)
        return UNIT.findall(result.stdout)

    def waterbox(self, rom, units, media=()):
        lines, result = build_waterbox.run_puae6(rom, units, tuple(media))
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

    def test_the_measurements_in_the_manifest_are_the_ones_a_run_produces_now(self):
        measured = self.manifest["measured"]
        fixture = measured["fixture"]
        rom = self.firmware("boot")
        self.assertEqual(fixture["firmware_sha256"], build_waterbox.sha256(rom), "the recorded fixture is the boot micro-ROM")
        _, result = self.waterbox(rom, fixture["units"])
        for key in ("after_seal", "after_first_unit", "after_last_unit", "snapshot_bytes"):
            self.assertEqual(measured[key], result[key], f"{key} changed: rerun tools/build_waterbox.py --puae6-run build/waterbox/boot.rom --units "
                             f"{fixture['units']} --record-memory and review manifests/waterbox-memory.json")
        for phase in ("after_seal", "after_first_unit", "after_last_unit"):
            for heap, size in self.manifest["layout"].items():
                self.assertLessEqual(result[phase][heap]["committed_bytes"], size, f"{heap} exceeds its budget")


if __name__ == "__main__":
    unittest.main()
