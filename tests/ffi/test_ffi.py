"""T20b: the C# side of the PUAE6 ABI, built from BizHawk's own sources (references/machawk, pinned) and run with its real WaterboxHost.

ABI01   every size and offset the C# structs have is what a C compiler makes of bridge/include/p6.h and adapters/waterbox/p6w.h, and the guest, opened
        by BizHawk's WaterboxHost, reports the same numbers (Puae6Guest.Open refuses to go on otherwise). A guard-banded p6w_init that the guest refuses
        in four ways comes back with nothing written but result and error.
WB15    the identity gate: an ELF for another machine (with the right hash, so that only the machine can refuse it), 32-bit and big-endian ELFs, a
        guest with one byte changed, a different pin, a truncated header, an empty file, not an ELF, a missing file, and a .zst sibling are refused
        before wbx_create_host; the guest the wrapper was built with is accepted and is the only one that reaches the host. A decoy named uae.wbx
        sits in the same directory: there is no fallback.
        The machine then runs under BizHawk's host unit by unit with the ticks of headless.

Needs the Waterbox runtime and the guest (tools/build_waterbox.py --puae6-run build/waterbox/boot.rom), dotnet and the local NuGet cache; skips without them.
    python3 -m unittest discover -s tests/ffi -p "test_ffi.py"
"""
import json
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
sys.path.insert(0, str(ROOT / "tests/waterbox-smoke"))
import build_waterbox  # noqa: E402
from tests.host.test_bridge_lifecycle import build_probe, builder  # noqa: E402
from test_puae6 import runtime_available  # noqa: E402

GUEST = build_waterbox.BUILD / "puae6/release/puae6.wbx"
RECEIPT = build_waterbox.BUILD / "puae6/puae6-receipt.json"
# C# struct -> (C struct, {C# field: C field}); the C name is the C# name in snake case except where it says otherwise.
STRUCTS = {
    "P6Config": ("p6_config", ["AbiVersion", "StructSize", "Machine", "VideoStandard", "AudioRateHz", "Flags", "RandomSeed", "RtcEpochSeconds", "ChipKib", "SlowKib", "FastKib", "FpuModel", "SoundSeparation", "SoundInterpolation", "SoundFilter", "SoundFilterType", "FloppySpeed", "VideoResolution", "VideoLineMode", "CpuModel", "CpuMultiplier", "HdfController", "CpuCompatibility", "FloppyDrives", "CollisionLevel", "Reserved7", "Z3Memory", "Reserved8"]),
    "P6PortInput": ("p6_port_input", ["Type", "Buttons", "Dx", "Dy", "Reserved"]),
    "P6Input": ("p6_input", ["AbiVersion", "StructSize", "Keys", "Port0", "Port1", "Reserved"]),
    "P6Rect": ("p6_rect", ["X", "Y", "Width", "Height"]),
    "P6Frame": ("p6_frame", ["AbiVersion", "StructSize", "Video", "VideoBytes", "VideoFormat", "VideoWidth", "VideoHeight", "VideoStrideBytes", "Active",
                             "AspectNum", "AspectDen", "Audio", "AudioFrames", "MasterTicks", "UnitIndex", "CadenceNum", "CadenceDen", "FieldParity",
                             "Flags", "Reserved"]),
    "P6wMedia": ("p6w_media", ["Type", "Flags", "ContentId", "Data", "Bytes"]),
    "P6wInit": ("p6w_init", ["AbiVersion", "StructSize", "Config", "Firmware", "FirmwareBytes", "MediaCount", "Reserved0", "Media", "Result", "Reserved1", "Error"]),
    "P6wFrame": ("p6w_frame", ["AbiVersion", "StructSize", "Frame", "Result", "Reserved", "Error"]),
    "P6wMemoryArea": ("p6w_memory_area", ["Data", "Name", "Size", "Flags"]),
    "P6MediaEvent": ("p6_media_event", ["AbiVersion", "StructSize", "Type", "Drive", "MediaIndex", "Reserved0", "Sequence", "Reserved"]),
    "P6wMediaEvent": ("p6w_media_event", ["AbiVersion", "StructSize", "Event", "Result", "Reserved", "Error"]),
    "P6MediaWriteStatus": ("p6_media_write_status", ["AbiVersion", "StructSize", "MediaIndex", "Reserved0", "OverlayCapacity", "OverlayUsed", "MediumSectors", "RefusedWrites", "RawTracks", "Reserved"]),
    "P6wMediaWriteStatus": ("p6w_media_write_status_args", ["AbiVersion", "StructSize", "Status", "Result", "Reserved", "Error"]),
    "P6wMediaExport": ("p6w_media_export_args", ["AbiVersion", "StructSize", "MediaIndex", "Reserved0", "Buffer", "Capacity", "Written", "Result", "Reserved1", "Error"]),
}
# The tail of p6_config after fpu_model is, since T73, Paula's four sound_* bytes, the floppy speed (T62), the raster options (T72), the CPU and its clock (T52) and reserved7: the C# mirror names each one (T73a), so the snake-case rule finds them.
SPECIAL = {("P6Input", "Port0"): "offsetof(p6_input, ports) + 0 * sizeof(p6_port_input)", ("P6Input", "Port1"): "offsetof(p6_input, ports) + 1 * sizeof(p6_port_input)"}


def snake(name):
    return re.sub(r"(?<=[a-z0-9])([A-Z])", r"_\1", name).lower().replace("p6w_", "").replace("p6_", "")


def c_expression(struct, field):
    return SPECIAL.get((struct, field)) or f"offsetof({STRUCTS[struct][0]}, {snake(field)})"


class ConfigLayoutTests(unittest.TestCase):
    def test_csharp_config_size_and_reserved_tail_match_the_native_contract(self):
        dotnet = shutil.which('dotnet')
        self.assertIsNotNone(dotnet, 'C# compiler required for ABI01')
        (ROOT / 'build').mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='p6-managed-layout-', dir=ROOT / 'build') as tmp:
            work = Path(tmp)
            (work / 'Layout.csproj').write_text(f'''<Project Sdk="Microsoft.NET.Sdk"><PropertyGroup>
<OutputType>Exe</OutputType><TargetFramework>net8.0</TargetFramework><AllowUnsafeBlocks>true</AllowUnsafeBlocks>
<UseAppHost>false</UseAppHost>
<RollForward>Major</RollForward>
<ImplicitUsings>enable</ImplicitUsings></PropertyGroup><ItemGroup>
<Compile Include="{ROOT / 'integration/bizhawk/core/Puae6/Abi.cs'}" /></ItemGroup></Project>''')
            (work / 'Program.cs').write_text('''using Puae6; using System.Runtime.InteropServices;
unsafe { Console.WriteLine($"{sizeof(P6Config)} {Marshal.OffsetOf<P6Config>(nameof(P6Config.Reserved7))} {Marshal.OffsetOf<P6Config>(nameof(P6Config.Z3Memory))} {Marshal.OffsetOf<P6Config>(nameof(P6Config.Reserved8))}"); }''')
            result = subprocess.run([dotnet, 'run', '--project', str(work / 'Layout.csproj'), '--verbosity', 'quiet',
                                     f'-p:RestoreSources={Path.home() / ".nuget/packages"}'], capture_output=True, text=True, timeout=120)
            self.assertEqual(0, result.returncode, result.stdout + result.stderr)
            self.assertEqual('72 69 70 71', result.stdout.strip())


class SyncSettingsPersistenceTests(unittest.TestCase):
    def test_emuhawk_keeps_pending_sync_settings_without_changing_the_live_machine(self):
        dotnet = shutil.which('dotnet')
        self.assertIsNotNone(dotnet, 'C# compiler required for the EmuHawk settings regression')
        env = {**build_waterbox.managed_env(), 'NUGET_PACKAGES': str(ROOT / 'build/bizhawk-core/nuget')}
        sources = build_waterbox.managed_restore_sources(Path.home() / '.nuget/packages')
        build = subprocess.run([
            dotnet, 'build', str(ROOT / 'tests/ffi/Ffi.csproj'), '--nologo',
            '-p:Puae6GuestSha256=' + '0' * 64, *sources,
        ], cwd=ROOT, env=env, capture_output=True, text=True, timeout=180)
        self.assertEqual(0, build.returncode, (build.stdout + build.stderr)[-7000:])
        result = subprocess.run([
            dotnet, str(ROOT / 'build/ffi/bin/Debug/net8.0/Ffi.dll'), 'settings',
        ], cwd=ROOT, env=env, capture_output=True, text=True, timeout=30)
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertNotIn('FAIL', result.stdout)
        self.assertEqual(13, result.stdout.count('ok '), result.stdout)


@unittest.skipUnless(runtime_available(), "Waterbox runtime or puae6.wbx not built (tools/build_waterbox.py --puae6-run build/waterbox/boot.rom)")
class FfiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.guest_sha = json.loads(RECEIPT.read_text())["guest"]["sha256"]
        cls.headless = build_probe(ROOT / "build/link-audit", False, "p6_headless")
        cls.tmp = tempfile.TemporaryDirectory(prefix="p6-ffi-")
        cls.rom = Path(cls.tmp.name) / "boot.rom"
        cls.rom.write_bytes(builder.build((ROOT / "tests/fixtures/microrom/boot.s").read_text(encoding="utf-8")))

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def ffi(self, *args):
        result = build_waterbox.run_ffi(*args)
        self.assertEqual(0, result.returncode, f"{result.stdout[-3000:]}\n{result.stderr[-3000:]}")
        self.assertNotIn("FAIL", result.stdout)
        return result.stdout

    # --- ABI01 --------------------------------------------------------------------------------------------------------------------------

    def test_every_csharp_size_and_offset_is_what_a_c_compiler_makes_of_the_headers(self):
        lines = ["#include \"p6w.h\"", "#include <stddef.h>", "#include <stdio.h>", "int main() {"]
        lines.append("  const unsigned long long layout[] = {sizeof(p6w_init), offsetof(p6w_init, result), offsetof(p6w_init, error), offsetof(p6w_init, media),"
                     " sizeof(p6w_media), sizeof(p6w_frame), offsetof(p6w_frame, frame), offsetof(p6w_frame, result), offsetof(p6w_frame, error),"
                     " sizeof(p6_frame), sizeof(p6_input), sizeof(p6_config), offsetof(p6_frame, audio), offsetof(p6_frame, master_ticks),"
                     " sizeof(p6w_memory_area), offsetof(p6w_memory_area, name), offsetof(p6w_memory_area, size), offsetof(p6w_memory_area, flags),"
                     " sizeof(p6w_media_event), offsetof(p6w_media_event, event), offsetof(p6w_media_event, result), offsetof(p6w_media_event, error),"
                     " sizeof(p6w_media_write_status_args), offsetof(p6w_media_write_status_args, status), offsetof(p6w_media_write_status_args, result), offsetof(p6w_media_write_status_args, error),"
                     " sizeof(p6w_media_export_args), offsetof(p6w_media_export_args, buffer), offsetof(p6w_media_export_args, written), offsetof(p6w_media_export_args, result), offsetof(p6w_media_export_args, error)};")
        lines.append("  printf(\"layout\"); for (unsigned long long v : layout) printf(\" %llu\", v); printf(\"\\n\");")
        for struct, (c_name, fields) in STRUCTS.items():
            lines.append(f"  printf(\"size {struct} %zu\\n\", sizeof({c_name}));")
            for field in fields:
                lines.append(f"  printf(\"field {struct}.{field} %zu\\n\", (size_t)({c_expression(struct, field)}));")
        lines.append("  return 0; }")
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "layout.cpp"
            source.write_text("\n".join(lines))
            subprocess.run(["c++", "-std=c++17", f"-I{ROOT / 'bridge/include'}", f"-I{ROOT / 'adapters/waterbox'}", str(source), "-o", f"{directory}/layout"],
                           check=True, capture_output=True)
            expected = subprocess.run([f"{directory}/layout"], check=True, capture_output=True, text=True).stdout.splitlines()
        got = self.ffi("layout").splitlines()
        self.assertEqual(expected, got)
        self.assertGreater(len(got), 60)

    def test_the_guest_opened_by_bizhawks_host_reports_the_layout_of_the_wrapper_and_refuses_calls_that_touch_nothing(self):
        out = self.ffi("open", "--runtime", "libwaterboxhost.dylib", "--guest", str(GUEST), "--sha", self.guest_sha, "--manifest",
                       str(ROOT / "manifests/waterbox-memory.json"), "--firmware", str(self.rom), "--units", "1")
        self.assertIn("ok the guest was opened by BizHawk's WaterboxHost and its layout is the wrapper's", out)
        self.assertEqual(4, out.count("refused with BAD_ARGUMENT"))
        self.assertEqual(4, out.count("nothing but result and error was written"))
        self.assertEqual(4, out.count("the guard bands around p6w_init are intact"))
        self.assertIn("ok a good init succeeds", out)

    def test_the_build_records_the_guest_it_belongs_with(self):
        self.assertEqual(f"pin {self.guest_sha}", self.ffi("pin").strip())

    # --- WB15 ---------------------------------------------------------------------------------------------------------------------------

    def test_a_guest_that_is_not_the_pinned_aarch64_one_is_refused_before_the_host_exists_and_nothing_else_is_tried(self):
        out = self.ffi("identity", "--guest", str(GUEST), "--sha", self.guest_sha)
        for reason in ("WrongMachine", "WrongClassOrByteOrder", "WrongHash", "NotElf", "Missing"):
            self.assertIn(f"refused as {reason}", out)
        self.assertEqual(10, out.count("wbx_create_host was not reached"))
        self.assertIn("ok a .zst sibling is refused", out)
        self.assertIn("ok and only it reaches wbx_create_host", out)

    def test_the_machine_runs_under_bizhawks_host_with_the_ticks_of_headless(self):
        units = 5
        out = self.ffi("open", "--runtime", "libwaterboxhost.dylib", "--guest", str(GUEST), "--sha", self.guest_sha, "--manifest",
                       str(ROOT / "manifests/waterbox-memory.json"), "--firmware", str(self.rom), "--units", str(units))
        managed = re.findall(r"^unit (\d+) ticks=(\d+)$", out, re.M)
        native = subprocess.run([str(self.headless), "--run", str(self.rom), "--units", str(units)], capture_output=True, text=True, timeout=600)
        self.assertEqual(0, native.returncode, native.stderr)
        expected = re.findall(r"^unit (\d+) ticks=(\d+)", native.stdout, re.M)
        self.assertEqual(units, len(expected))
        self.assertEqual(expected, managed)


if __name__ == "__main__":
    unittest.main()
