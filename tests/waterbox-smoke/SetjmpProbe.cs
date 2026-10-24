using System.Runtime.InteropServices;
using System.Security.Cryptography;
using System.Text.Json;
internal static unsafe class Program
{
    internal static void Require(bool ok, string detail) { if (!ok) throw new InvalidOperationException(detail); }
    static string Hash(byte[] data) => Convert.ToHexString(SHA256.HashData(data)).ToLowerInvariant();
    [UnmanagedFunctionPointer(CallingConvention.Cdecl)] delegate int Jump(int value, nint output, nuint capacity);
    sealed class Probe : WaterboxSandbox
    {
        internal Probe(Native api, byte[] image) : base(api, image, Layout.Smoke, "p6-setjmp.wbx")
        { In(() => { Require(Guest<Sandbox.Value>("smoke_constructor")() == 43 && Guest<Sandbox.Status>("smoke_init")() == 0 && Guest<Sandbox.Status>("sj_init")() == 0, "Init"); api.Seal(host, out var result); result.Checked(); return 0; }); }
        internal bool Check(int value, bool original = false) => In(() => {
            ulong* o = stackalloc ulong[30]; new Span<ulong>(o, 30).Fill(0xFEED);
            Require(Guest<Jump>("sj_run")(value, (nint)o, 30) == 0, "ABI export");
            if (original) { Require(Guest<Sandbox.Value>("sj_status")() == 28 && Guest<Sandbox.Value>("sj_nonzero_slot")() == 1 && Guest<Sandbox.Value>("sj_return")() == 0 && o[13] == 0xBAD28, "Original must reject before longjmp"); return true; }
            Require(Guest<Sandbox.Value>("sj_status")() == 0 && Guest<Sandbox.Value>("sj_nonzero_slot")() == 0 && Guest<Sandbox.Value>("sj_return")() == (ulong)(value == 0 ? 1 : value), "Writer, reader, TLS, errno, return");
            Require(o[0] == o[2] && o[1] != 0 && o[1] == o[3] && o[14] == o[15] && o[16] == o[17], "x18/x28/x29/SP");
            for (int i = 0; i < 9; i++) Require(o[4+i] == 0xA113UL+(ulong)i, "x19..x27");
            for (int i = 0; i < 8; i++) Require(o[18+i] == 0xD808UL+(ulong)i, "d8..d15");
            Require(o[13] == 0xC0FFEF65 && o[26] == 0x7157 && o[27] == 0x7157 && o[28] == 0x1234 && o[29] == 0x1234, "Callback/TLS/errno canaries");
            return true;
        });
        internal void Cases() { Check(0); Check(7); }
    }
    public static int Main(string[] args)
    {
        try {
            Require(args.Length == 4 && Environment.Version.ToString() == "10.0.13" && Environment.GetEnvironmentVariable("P6_EVICT") == "1", "Pinned runtime/context");
            string mode = args[0]; Require(mode is "original" or "fixed" or "restore", "Unknown mode");
            using var native = new Native(args[1]); native.SetAlwaysEvict(true); var image = File.ReadAllBytes(args[2]);
            using var a = new Probe(native, image);
            if (mode == "original") { a.Check(0, true); Console.WriteLine("P6_SETJMP " + JsonSerializer.Serialize(new { status = "EXPECTED_ORIGINAL_REJECTION", jumps = 0 })); return 28; }
            if (mode == "restore") {
                var saved = File.ReadAllBytes(args[3]); a.Load(saved); Require(a.Save().SequenceEqual(saved), "Fresh restore full state");
                a.Cases(); Console.WriteLine("P6_SETJMP " + JsonSerializer.Serialize(new { status = "RESTORED", jumps = 2, baseline = Hash(saved), future = Hash(a.Save()) })); return 0;
            }
            using var b = new Probe(native, image); a.Cases(); b.Cases(); var baseline = a.Save();
            Require(b.Save().SequenceEqual(baseline), "Twins full snapshots"); File.WriteAllBytes(args[3], baseline);
            a.Cases(); var future = a.Save(); b.Load(baseline); Require(b.Save().SequenceEqual(baseline), "Second sandbox immediate full restore"); b.Cases();
            Require(b.Save().SequenceEqual(future), "Second sandbox full future");
            Console.WriteLine("P6_SETJMP " + JsonSerializer.Serialize(new { status = "FIXED", jumps = 8, baseline = Hash(baseline), future = Hash(future) })); return 0;
        } catch (Exception error) { Console.Error.WriteLine(error); return 2; }
    }
}
