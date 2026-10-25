"""Compile the real identity gate, without core stubs, packages or a sandbox."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from xml.sax.saxutils import escape

ROOT = Path(__file__).resolve().parents[2]
CORE = ROOT / "integration/bizhawk/core"
PROGRAM = r'''
using System.Runtime.InteropServices;
using Puae6;
static class Program
{
    static void Require(bool ok, string detail) { if (!ok) throw new Exception(detail); }
    static void Reject(GuestRejection reason, Action action)
    {
        try { action(); } catch (GuestRejectedException e) { Require(e.Reason == reason, e.ToString()); return; }
        throw new Exception("Accepted invalid guest: " + reason);
    }
    static void Throws<T>(Action action) where T : Exception
    {
        try { action(); } catch (T) { return; }
        throw new Exception("Expected " + typeof(T));
    }
    static byte[] Elf(GuestIsa isa)
    {
        var bytes = new byte[80]; bytes[0] = 0x7f; bytes[1] = (byte)'E'; bytes[2] = (byte)'L'; bytes[3] = (byte)'F';
        bytes[4] = 2; bytes[5] = 1; bytes[18] = (byte)isa; bytes[19] = (byte)((ushort)isa >> 8);
        return bytes;
    }
    static void Headers()
    {
        Require((ushort)GuestIsa.X64 == 62 && (ushort)GuestIsa.AArch64 == 183
            && GuestIdentity.EM_X86_64 == 62 && GuestIdentity.EM_AARCH64 == 183, "ELF machine numbers");
        GuestIdentity.CheckElf(Elf(GuestIsa.AArch64), "default-arm");
        Reject(GuestRejection.WrongMachine, () => GuestIdentity.CheckElf(Elf(GuestIsa.X64), "default-arm"));
        foreach (var isa in new[] { GuestIsa.AArch64, GuestIsa.X64 })
        {
            GuestIdentity.CheckElf(Elf(isa), "explicit", isa);
            var other = isa == GuestIsa.X64 ? GuestIsa.AArch64 : GuestIsa.X64;
            Reject(GuestRejection.WrongMachine, () => GuestIdentity.CheckElf(Elf(other), "other", isa));
            foreach (int length in new[] { 0, 4, 63 })
                Reject(GuestRejection.NotElf, () => GuestIdentity.CheckElf(new byte[length], "short", isa));
            var data = Elf(isa); data[0] = 0;
            Reject(GuestRejection.NotElf, () => GuestIdentity.CheckElf(data, "magic", isa));
            foreach (var field in new[] { 4, 5 })
            {
                data = Elf(isa); data[field] = field == 4 ? (byte)1 : (byte)2;
                Reject(GuestRejection.WrongClassOrByteOrder, () => GuestIdentity.CheckElf(data, "format", isa));
            }
            data = Elf(isa); data[18] = 3;
            Reject(GuestRejection.WrongMachine, () => GuestIdentity.CheckElf(data, "machine", isa));
        }
        Throws<ArgumentOutOfRangeException>(() => GuestIdentity.CheckElf(Elf((GuestIsa)200), "unknown", (GuestIsa)200));
    }
    static void Files(string directory)
    {
        string path = Path.Combine(directory, "fixture.wbx");
        foreach (var isa in new[] { GuestIsa.AArch64, GuestIsa.X64 })
        {
            var bytes = Elf(isa); File.WriteAllBytes(path, bytes);
            string pin = GuestIdentity.Sha256Hex(bytes);
            var actual = GuestIdentity.Verify(path, pin.ToUpperInvariant(), isa);
            Require(actual.Path == path && actual.Bytes == bytes.Length && actual.Sha256 == pin, "Verified identity changed");
            if (isa == GuestIsa.AArch64) Require(GuestIdentity.Verify(path, pin) == actual, "Default ARM changed");
            else Reject(GuestRejection.WrongMachine, () => GuestIdentity.Verify(path, pin));
            var other = isa == GuestIsa.X64 ? GuestIsa.AArch64 : GuestIsa.X64;
            Reject(GuestRejection.WrongMachine, () => GuestIdentity.Verify(path, pin, other));
            File.WriteAllBytes(path + ".zst", new byte[1]);
            Reject(GuestRejection.AmbiguousCompressedSibling, () => GuestIdentity.Verify(path, pin, isa));
            File.Delete(path + ".zst");
            foreach (string bad in new[] { "", new string('z', 64), "abc" })
                Throws<ArgumentException>(() => GuestIdentity.Verify(path, bad, isa));
            Throws<ArgumentException>(() => GuestIdentity.Verify(path, null!, isa));
            bytes[^1] = 1; File.WriteAllBytes(path, bytes);
            Reject(GuestRejection.WrongHash, () => GuestIdentity.Verify(path, pin, isa));
            File.Delete(path);
            Reject(GuestRejection.Missing, () => GuestIdentity.Verify(path, pin, isa));
        }
        Throws<ArgumentOutOfRangeException>(() => GuestIdentity.Verify(path, new string('a', 64), (GuestIsa)200));
    }
    static void Hosts()
    {
        Require(GuestIdentity.ForHost(OSPlatform.OSX, Architecture.Arm64) == GuestIsa.AArch64, "Darwin ARM");
        Require(GuestIdentity.ForHost(OSPlatform.Windows, Architecture.X64) == GuestIsa.X64, "Windows x64");
        foreach (var os in new[] { OSPlatform.Windows, OSPlatform.OSX, OSPlatform.Linux, OSPlatform.FreeBSD })
            foreach (var arch in new[] { Architecture.X86, Architecture.X64, Architecture.Arm, Architecture.Arm64 })
                if (!(os == OSPlatform.OSX && arch == Architecture.Arm64) && !(os == OSPlatform.Windows && arch == Architecture.X64))
                    Throws<PlatformNotSupportedException>(() => GuestIdentity.ForHost(os, arch));
        if (RuntimeInformation.IsOSPlatform(OSPlatform.OSX) && RuntimeInformation.ProcessArchitecture == Architecture.Arm64)
            Require(GuestIdentity.CurrentHostIsa == GuestIsa.AArch64, "Actual Darwin process");
        else if (RuntimeInformation.IsOSPlatform(OSPlatform.Windows) && RuntimeInformation.ProcessArchitecture == Architecture.X64)
            Require(GuestIdentity.CurrentHostIsa == GuestIsa.X64, "Actual Windows process");
        else Throws<PlatformNotSupportedException>(() => _ = GuestIdentity.CurrentHostIsa);
    }
    static int Main(string[] args)
    {
        if (args[0] == "headers") Headers();
        else if (args[0] == "files") Files(args[1]);
        else if (args[0] == "hosts") Hosts();
        else throw new Exception("Unknown fixture");
        Console.WriteLine("P6_IDENTITY " + args[0] + " PASS"); return 0;
    }
}
'''


class GuestIdentityX64Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        dotnet = shutil.which("dotnet")
        if not dotnet:
            raise RuntimeError("BLOCKED: existing .NET SDK required")
        (ROOT / "build").mkdir(exist_ok=True)
        temporary = tempfile.TemporaryDirectory(prefix="identity-x64-", dir=ROOT / "build")
        cls.addClassCleanup(temporary.cleanup)
        cls.stage = Path(temporary.name)
        (cls.stage / "global.json").write_text((ROOT / "tests/ffi/global.json").read_text())
        (cls.stage / "empty-feed").mkdir()
        (cls.stage / "Program.cs").write_text(PROGRAM)
        gate = escape(str(CORE / "Puae6/GuestIdentity.cs"))
        project = cls.stage / "Identity.csproj"
        project.write_text(f'''<Project Sdk="Microsoft.NET.Sdk"><PropertyGroup>
<OutputType>Exe</OutputType><TargetFramework>net10.0</TargetFramework><UseAppHost>false</UseAppHost>
<ImplicitUsings>enable</ImplicitUsings><Nullable>enable</Nullable><EnableDefaultCompileItems>false</EnableDefaultCompileItems>
</PropertyGroup><ItemGroup><Compile Include="Program.cs"/><Compile Include="{gate}"/></ItemGroup></Project>''')
        cls.env = {**os.environ, "DOTNET_CLI_HOME": str(cls.stage / "dotnet-home"),
                   "DOTNET_CLI_TELEMETRY_OPTOUT": "1", "DOTNET_NOLOGO": "1"}
        command = [dotnet, "build", str(project), "-c", "Release", "--nologo", "-p:UseSharedCompilation=false",
                   f"-p:RestoreSources={cls.stage / 'empty-feed'}", "-p:NuGetAudit=false"]
        result = subprocess.run(command, cwd=cls.stage, env=cls.env, text=True, capture_output=True, timeout=60)
        if result.returncode:
            raise AssertionError(result.stdout + result.stderr)
        cls.command = [dotnet, str(cls.stage / "bin/Release/net10.0/Identity.dll")]

    def run_fixture(self, name):
        result = subprocess.run([*self.command, name, str(self.stage)], cwd=self.stage,
                                env=self.env, capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.stdout.strip(), f"P6_IDENTITY {name} PASS")

    def test_real_elf_headers_keep_arm_default_and_require_explicit_x64(self):
        self.run_fixture("headers")

    def test_both_pinned_files_reject_corruption_hash_isa_and_compressed_sibling(self):
        self.run_fixture("files")

    def test_only_enabled_host_pairs_and_actual_process_are_selected(self):
        self.run_fixture("hosts")

    def test_public_loader_gates_before_ownership_and_uses_compiled_pin(self):
        loader = (CORE / "EmuHawk/Puae6Core.EmuHawk.cs").read_text()
        body = loader.split("static Puae6Parameters ParametersFrom", 1)[1]
        gate = 'Puae6Guest.VerifyForCurrentHost(guestDirectory, "puae6.wbx");'
        self.assertLess(body.index(gate), body.index("lp.Discs"))
        self.assertLess(body.index(gate), body.index("GetFirmwareOrThrow"))
        self.assertIn("GuestDirectory = guestDirectory,", body)
        guest = (CORE / "Puae6/Puae6Guest.cs").read_text()
        early = guest.split("VerifiedGuest VerifyForCurrentHost", 1)[1].split("///", 1)[0]
        self.assertIn("GuestIdentity.CurrentHostIsa", early)
        self.assertIn("BuiltWithSha256.Length == 0", early)
        self.assertIn("GuestIdentity.Verify(Path.Combine(directory, fileName), BuiltWithSha256, isa)", early)
        opening = guest.split("Open(string directory, string fileName, string pinnedSha256", 1)[1]
        self.assertLess(opening.index("GuestIdentity.CurrentHostIsa"), opening.index("CreateHost(options)"))


if __name__ == "__main__":
    unittest.main()
