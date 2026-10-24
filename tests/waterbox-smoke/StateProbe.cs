using System.Runtime.InteropServices;
using System.Text.Json;

// Linked with the original Native.cs, never a replacement EmuHawk or guest.
internal static unsafe class Program
{
    internal static void Require(bool ok, string message) { if (!ok) throw new InvalidOperationException(message); }
    sealed class Probe : WaterboxSandbox
    {
        internal Probe(Native api, byte[] image) : base(api, image, Layout.Smoke, "p6-smoke.wbx")
        {
            In(() => { Require(Guest<Sandbox.Value>("smoke_constructor")() == 43, "Constructor");
                Require(Guest<Sandbox.Status>("smoke_init")() == 0, "Init"); api.Seal(host, out var result); result.Checked(); return 0; });
        }
        internal object Capture() => In(() => {
            using var stream = new MemoryStream(); var chunks = new List<long[]>(); Exception? failure = null;
            Native.Write write = (_, data, size) => {
                try { chunks.Add([stream.Position, checked((long)size)]); stream.Write(new ReadOnlySpan<byte>((void*)data, checked((int)size))); return 0; }
                catch (Exception error) { failure = error; return -1; }
            };
            api.SaveState(host, write, 0, out var result); result.Checked(); GC.KeepAlive(write);
            if (failure != null) throw new InvalidOperationException("Snapshot transport", failure);
            var pages = new byte[PageCount]; for (int i = 0; i < pages.Length; i++) pages[i] = PageInfo(i);
            return new { data = stream.ToArray(), chunks, pages };
        });
        internal ulong[][] Advance(nint buffer)
        {
            var rows = new List<ulong[]>(); ulong counter = 0;
            for (int frame = 0; frame < 3; frame++) {
                ulong input = (ulong)((frame * 7 + 3) % 17); counter += input + 1;
                var row = In(() => { Require(Guest<Sandbox.Frame>("smoke_frame")(input, buffer + 8, 6) == 0, "Frame"); return new ReadOnlySpan<ulong>((void*)(buffer + 8), 6).ToArray(); });
                ulong packed = 0; for (int n = 0; n < 4; n++) packed |= ((counter + (ulong)n) & 255) << (n * 8);
                Require(row.SequenceEqual(new ulong[] { counter + (ulong)frame + 40, counter, (ulong)frame + 8, packed, 2088960, 19 }), "Original smoke oracle"); rows.Add(row);
            }
            return rows.ToArray();
        }
    }
    public static int Main(string[] args)
    {
        try {
            Require(args.Length == 2 && Environment.GetEnvironmentVariable("P6_EVICT") == "1", "Explicit original eviction policy required");
            using var native = new Native(args[0]); native.SetAlwaysEvict(true);
            var image = File.ReadAllBytes(args[1]); using var a = new Probe(native, image); using var b = new Probe(native, image);
            nint first = Marshal.AllocHGlobal(64), second = Marshal.AllocHGlobal(64);
            try {
                Require(first != second, "Buffers must differ and remain alive");
                new Span<byte>((void*)first, 64).Fill(0xA5); new Span<byte>((void*)second, 64).Fill(0xA5);
                var captures = new Dictionary<string, object>();
                void Take(string name, Probe sandbox) { captures.Add(name + "0", sandbox.Capture()); captures.Add(name + "1", sandbox.Capture()); }
                Take("initialA", a); Take("initialB", b); var initial = a.Save();
                var left = a.Advance(first); var right = b.Advance(second);
                Require(left.Zip(right, (x, y) => x.SequenceEqual(y)).All(equal => equal), "Twins output differs");
                Take("afterA", a); Take("afterB", b);
                a.Load(initial); var intervention = a.Advance(second);
                Require(left.Zip(intervention, (x, y) => x.SequenceEqual(y)).All(equal => equal), "Buffer-only intervention changed outputs");
                Take("interventionA", a);
                Console.WriteLine("P6_STATE_PROBE " + JsonSerializer.Serialize(new { eviction = true, runtime = Environment.Version.ToString(), frames = 3, buffers = new long[] { (long)first, (long)second }, captures }));
            } finally { Marshal.FreeHGlobal(first); Marshal.FreeHGlobal(second); }
            return 0;
        } catch (Exception error) { Console.Error.WriteLine(error); return 2; }
    }
}
