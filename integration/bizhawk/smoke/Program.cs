using System.Runtime.InteropServices;
using System.Security.Cryptography;
using System.Text.Json;

internal static unsafe class Program
{
    internal static void Require(bool ok, string assertion)
    { if (!ok) throw new InvalidOperationException("FAIL: " + assertion); }
    static string Hash(string path) => Convert.ToHexString(SHA256.HashData(File.ReadAllBytes(path))).ToLowerInvariant();
    static ulong Input(int frame) => (ulong)((frame * 7 + 3) % 17);

    sealed class Buffer : IDisposable
    {
        internal readonly nint Address = Marshal.AllocHGlobal(64);
        internal Span<ulong> Words => new((void*)Address, 8);
        public void Dispose() => Marshal.FreeHGlobal(Address);
    }
    static ulong[] Frame(Sandbox sandbox, Buffer buffer, int frame)
    {
        buffer.Words.Fill(0xabcdef1234567890);
        return sandbox.In(() => {
            var run = sandbox.Guest<Sandbox.Frame>("smoke_frame");
            Require(run(Input(frame), buffer.Address + 8, 5) == -1, "short output rejected");
            Require(buffer.Words.ToArray().All(x => x == 0xabcdef1234567890), "short output untouched");
            Require(run(Input(frame), 0, 6) == -1, "null output rejected");
            Require(run(Input(frame), buffer.Address + 8, 6) == 0, "frame success");
            Require(buffer.Words[0] == 0xabcdef1234567890 && buffer.Words[7] == 0xabcdef1234567890, "output canaries");
            var actual = buffer.Words.Slice(1, 6).ToArray();
            ulong counter = 0;
            for (int n = 0; n <= frame; ++n) counter += Input(n) + 1;
            ulong tls = (ulong)frame + 8, packed = 0;
            for (int n = 0; n < 4; ++n) packed |= ((counter + (ulong)n) & 255) << (n * 8);
            ulong[] expected = [counter + tls + 32, counter, tls, packed, 2088960, 19];
            Require(actual.SequenceEqual(expected), $"oracle frame {frame}: [{string.Join(',', actual)}]");
            return actual;
        });
    }
    static ulong[] Memory(Sandbox sandbox) => sandbox.In(() => {
        ulong* ranges = stackalloc ulong[10];
        Require(sandbox.Guest<Sandbox.MemoryInfo>("smoke_memory_info")((nint)ranges, 10) == 10, "heap layout");
        ulong[] sizes = [1048576, 16384, 16384, 32768, 1048576];
        for (int i = 0; i < 5; ++i) {
            Require(ranges[2*i] % 16384 == 0 && ranges[2*i+1] == sizes[i], $"heap {i} alignment/budget");
            if (i > 0) Require(ranges[2*i] == ranges[2*i-2] + sizes[i-1], "contiguous heaps");
        }
        Require(sandbox.Guest<Sandbox.Status>("smoke_capacity")() == 1, "heap exhaustion controlled");
        Require(sandbox.Guest<Sandbox.Status>("smoke_capacity")() == 1, "failed allocation repeatable");
        return new ReadOnlySpan<ulong>(ranges, 10).ToArray();
    });
    static int Main(string[] args)
    {
        try {
            Require(args.Length == 2, "usage: Smoke.dll build-receipt.json result.json");
            Require(OperatingSystem.IsMacOS() && RuntimeInformation.ProcessArchitecture == Architecture.Arm64, "Darwin ARM64 runner");
            Require(Marshal.SizeOf<Layout>() == 40 && Marshal.SizeOf<Result>() == 1032 &&
                Marshal.OffsetOf<Result>(nameof(Result.Data)).ToInt64() == 1024, "native ABI layout");
            using var receipt = JsonDocument.Parse(File.ReadAllText(args[0]));
            var root = receipt.RootElement;
            var guestPath = root.GetProperty("guest").GetProperty("path").GetString()!;
            var dylibPath = root.GetProperty("runtime_dylib").GetProperty("path").GetString()!;
            Require(Hash(guestPath) == root.GetProperty("guest").GetProperty("sha256").GetString(), "guest artifact identity");
            Require(Hash(dylibPath) == root.GetProperty("runtime_dylib").GetProperty("sha256").GetString(), "runtime artifact identity");
            byte[] image = File.ReadAllBytes(guestPath);
            using var api = new Native(dylibPath);
            using var a = new Sandbox(api, image);
            using var first = new Buffer();
            using var second = new Buffer();
            Require(first.Address != second.Address, "different host buffers");
            var heaps = Memory(a);
            const int checkpoint = 5, count = 32;
            for (int n = 0; n < checkpoint; ++n) Frame(a, first, n);
            var state = a.Save(); Require(state.Length > 0, "nonempty native snapshot");
            var expected = Enumerable.Range(checkpoint, count).Select(n => Frame(a, first, n)).ToArray();
            a.Load(state);
            a.In(() => a.Guest<Sandbox.Poison>("smoke_poison")(0xa5));
            for (int n = 0; n < count; ++n) Require(Frame(a, second, checkpoint+n).SequenceEqual(expected[n]), "same sandbox restore");
            using var b = new Sandbox(api, image); // Independently initialized and sealed.
            Require(Memory(b).SequenceEqual(heaps), "equivalent new sandbox layout");
            b.Load(state); a.Load(state);
            GC.Collect(); GC.WaitForPendingFinalizers();
            for (int n = 0; n < count; ++n) {
                a.In(() => a.Guest<Sandbox.Poison>("smoke_poison")(0x55));
                b.In(() => b.Guest<Sandbox.Poison>("smoke_poison")(0xaa));
                Require(Frame(a, first, checkpoint+n).SequenceEqual(expected[n]), "alternating A");
                var old = first.Words.ToArray();
                Require(Frame(b, second, checkpoint+n).SequenceEqual(expected[n]), "new sandbox restore B");
                Require(first.Words.SequenceEqual(old), "old host buffer unchanged");
            }
            string corelib = typeof(object).Assembly.Location;
            File.WriteAllText(args[1], JsonSerializer.Serialize(new {
                status = "PASS", evidence = "SMOKE_RUNTIME", task = "T09b", amiga_emulation = false,
                assertion_set = new[] { "constructor_cpp", "heap_limits", "frame_oracle", "buffer_bounds", "restore_same", "restore_new", "alternate_sandboxes", "invisible_poison" },
                guest_sha256 = Hash(guestPath), runtime_sha256 = Hash(dylibPath),
                managed_runtime = RuntimeInformation.FrameworkDescription, corelib, corelib_sha256 = Hash(corelib),
                harness_sha256 = Hash(typeof(Program).Assembly.Location), snapshot_bytes = state.Length,
                checkpoint, continuation_frames = count, heap_ranges = heaps,
                native_tests_do_not_prove_amiga = true
            }, new JsonSerializerOptions { WriteIndented = true }) + "\n");
            Console.WriteLine("T09b SMOKE_RUNTIME PASS: snapshot restored into a new Waterbox sandbox");
            return 0;
        }
        catch (Exception error) { Console.Error.WriteLine(error); return 1; }
    }
}
