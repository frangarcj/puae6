using System.Diagnostics;
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

    const ulong ArgumentSum = 0x11 + 0x22 + 0x33 + 0x44 + 0x55 + 0x66;
    const int Checkpoint = 5, Count = 32;

    // WB13: one guest -> host callback with canaries in every callee-saved register.
    internal static (ulong[] Out, ulong[] Nested) AbiCall(WaterboxSandbox sandbox, bool reenter, string probeName = "smoke_abi_probe", string identityName = "smoke_identity")
    {
        var output = new ulong[30];
        var nested = new ulong[3];
        Exception? failure = null;
        int calls = 0;
        var identity = default(Sandbox.Identity);
        Native.HostCallback callback = (a1, a2, a3, a4, a5, a6) => {
            try {
                ++calls;
                Require(a1 == 0x11 && a2 == 0x22 && a3 == 0x33 && a4 == 0x44 && a5 == 0x55 && a6 == 0x66, "callback receives six integer arguments");
                if (reenter) fixed (ulong* n = nested) Require(identity!((nint)n, 3) == 0, "guest entered from inside a callback");
                return (nuint)(0xC0FFEE00 + ArgumentSum);
            } catch (Exception error) { failure = error; return 0; }
        };
        sandbox.In(() => {
            var probe = sandbox.Guest<Sandbox.AbiProbe>(probeName);
            identity = sandbox.Guest<Sandbox.Identity>(identityName);
            nint thunk = sandbox.Callback(callback, reenter ? 1 : 0);
            fixed (ulong* o = output) Require(probe(thunk, (nint)o, 30) == 0, "abi probe returned");
            return 0;
        });
        GC.KeepAlive(callback);
        if (failure != null) throw new InvalidOperationException("FAIL: host callback", failure);
        Require(calls == 1, "callback called exactly once");
        return (output, nested);
    }
    internal static void CheckAbi((ulong[] Out, ulong[] Nested) call, bool reenter, string label)
    {
        var o = call.Out;
        Require(o[1] != 0 && o[1] == o[3], $"{label}: x28 (sandbox context) preserved through the callback");
        Require(o[0] == o[2], $"{label}: x18 (Darwin platform register) preserved");
        for (int i = 0; i < 9; ++i) Require(o[4 + i] == 0xA113UL + (ulong)i, $"{label}: x{19 + i} preserved");
        for (int i = 0; i < 8; ++i) Require(o[18 + i] == 0xD808UL + (ulong)i, $"{label}: d{8 + i} preserved");
        Require(o[14] == o[15] && o[16] == o[17], $"{label}: x29 and sp preserved");
        Require(o[13] == 0xC0FFEE00UL + ArgumentSum, $"{label}: callback result returned to the guest");
        Require(o[26] == 0x7157 && o[27] == 0x7157, $"{label}: musl thread-specific data preserved");
        Require(o[28] == 0x1234 && o[29] == 0x1234, $"{label}: errno (musl thread state) preserved");
        if (reenter)
            Require(call.Nested[0] == o[1] && call.Nested[1] == 0x7157 && call.Nested[2] == 0x1234,
                $"{label}: guest re-entered from a callback sees the same context and thread state");
    }

    // WB14: RAM is one 16 KiB host group made of four 4 KiB logical pages.
    static byte[] ReadRam(ulong address) => new ReadOnlySpan<byte>((void*)address, 16384).ToArray();
    static bool[] DirtyMap(Sandbox sandbox)
    {
        var map = new bool[sandbox.PageCount];
        for (int i = 0; i < map.Length; ++i) map[i] = (sandbox.PageInfo(i) & 0x80) != 0;
        return map;
    }
    static object PageGroups(Native api, byte[] image)
    {
        var groupDirty = new List<object>();
        var restoreClean = new List<object>();
        // A. First write to a group init never touched, one fresh sandbox per logical page.
        // B. Restore of a snapshot taken while that group was clean, twice (dirty tracking re-arms).
        for (int page = 0; page < 4; ++page)
        {
            using var g = new Sandbox(api, image);
            var heaps = Memory(g);
            ulong baseAddress = g.In(() => g.Guest<Sandbox.Value>("smoke_block_base")());
            ulong scratch = g.In(() => g.Guest<Sandbox.Value>("smoke_scratch")());
            Require(baseAddress % 16384 == 0 && scratch % 16384 == 0 && scratch != 0, "scratch starts a 16 KiB host group");
            Require((ulong)g.PageCount == (heaps[8] + heaps[9] - baseAddress) / 4096, "page table covers the whole block");
            int first = (int)((scratch - baseAddress) / 4096);
            for (int i = 0; i < 4; ++i)
                Require((g.PageInfo(first + i) & 0x43) == 3, $"page {page}: scratch logical page {i} is visible, readable and writable");
            var group = () => DirtyMap(g).Skip(first).Take(4).ToArray();
            Func<bool[], bool[]> outside = map => map.Where((_, i) => i < first || i >= first + 4).ToArray();
            var clean = DirtyMap(g);
            Require(!group().Any(x => x), $"page {page}: an untouched group starts clean");
            var snapshot = g.Save();
            byte value = (byte)(0xE0 + page);
            foreach (ulong offset in new ulong[] { 0, 4095 })
                Require(g.In(() => g.Guest<Sandbox.Touch>("smoke_touch")(1, (ulong)page, offset, value)) == 0, $"touch page {page}+{offset}");
            var written = DirtyMap(g);
            Require(written.Skip(first).Take(4).All(x => x), $"page {page}: one write marks the whole 16 KiB group dirty");
            Require(outside(clean).SequenceEqual(outside(written)), $"page {page}: no page outside the group changed state");
            var mutated = g.In(() => ReadRam(scratch));
            for (int i = 0; i < 16384; ++i)
            {
                bool touched = i / 4096 == page && (i % 4096 == 0 || i % 4096 == 4095);
                Require(mutated[i] == (touched ? value : (byte)0), $"page {page}: only the two written bytes changed (offset {i})");
            }
            groupDirty.Add(new { page, dirty_before = clean.Skip(first).Take(4).ToArray(), dirty_after_write = written.Skip(first).Take(4).ToArray() });

            g.Load(snapshot);
            Require(g.In(() => ReadRam(scratch)).All(x => x == 0), $"page {page}: all four logical pages restored to their snapshot content");
            var restored = group();
            Require(restored.All(x => x == restored[0]), $"page {page}: the four logical pages agree on their dirty state after a restore");
            int next = (page + 1) % 4;
            Require(g.In(() => g.Guest<Sandbox.Touch>("smoke_touch")(1, (ulong)next, 17, 0x5A)) == 0, "second write after the restore");
            Require(g.In(() => ReadRam(scratch))[next * 4096 + 17] == 0x5A, "second write visible");
            g.Load(snapshot);
            Require(g.In(() => ReadRam(scratch)).All(x => x == 0), $"page {page}: a second restore also restores all four logical pages");
            restoreClean.Add(new { page, dirty_after_restore = restored });

            Require(g.In(() => g.Guest<Sandbox.Touch>("smoke_touch")(1, 4, 0, 1)) == -2, "guest rejects a fifth logical page");
            Require(g.In(() => g.Guest<Sandbox.Touch>("smoke_touch")(1, 0, 4096, 1)) == -2, "guest rejects an offset past the page");
            Require(g.In(() => g.Guest<Sandbox.Touch>("smoke_touch")(2, 0, 0, 1)) == -1, "guest rejects an unknown buffer");
            Require(g.In(() => ReadRam(scratch)).All(x => x == 0), "rejected writes changed nothing");
        }
        // C. RAM holds a pattern written after the seal: mutate each logical page and restore the group.
        using var r = new Sandbox(api, image);
        ulong ram = r.In(() => r.Guest<Sandbox.Value>("smoke_ram")());
        ulong start = r.In(() => r.Guest<Sandbox.Value>("smoke_block_base")());
        Require(ram % 16384 == 0, "RAM starts a 16 KiB host group");
        int ramFirst = (int)((ram - start) / 4096);
        Require(!DirtyMap(r).Skip(ramFirst).Take(4).Any(x => x), "what init wrote before the seal is the clean baseline");
        Require(r.In(() => r.Guest<Sandbox.Fill>("smoke_fill")(0, 1)) == 0, "fill RAM");
        Require(DirtyMap(r).Skip(ramFirst).Take(4).All(x => x), "filling RAM dirties its whole group");
        var pattern = r.In(() => ReadRam(ram));
        var state = r.Save();
        for (int page = 0; page < 4; ++page)
        {
            Require(r.In(() => r.Guest<Sandbox.Touch>("smoke_touch")(0, (ulong)page, 4095, 0xC0)) == 0, "mutate RAM page");
            Require(!r.In(() => ReadRam(ram)).SequenceEqual(pattern), $"RAM page {page} mutated");
            r.Load(state);
            Require(r.In(() => ReadRam(ram)).SequenceEqual(pattern), $"RAM page {page}: all four logical pages restored");
        }
        return new { snapshot_bytes = state.Length, first_write = groupDirty, restore_of_clean_snapshot = restoreClean };
    }

    // Runs this same binary in a child process. Used for a restore in a fresh process and for
    // loads a runtime might abort on: an abort must only ever kill the child.
    static (int Exit, string Output) Child(params string[] arguments)
    {
        var start = new ProcessStartInfo(Environment.ProcessPath!) { RedirectStandardOutput = true, RedirectStandardError = true };
        start.ArgumentList.Add(typeof(Program).Assembly.Location);
        foreach (var argument in arguments) start.ArgumentList.Add(argument);
        using var process = Process.Start(start)!;
        var stdout = process.StandardOutput.ReadToEndAsync();
        var stderr = process.StandardError.ReadToEndAsync();
        if (!process.WaitForExit(180000)) { process.Kill(true); throw new InvalidOperationException("FAIL: child process timed out: " + string.Join(' ', arguments)); }
        return (process.ExitCode, stdout.Result + stderr.Result);
    }
    static (string Guest, string Dylib, byte[] Image) Artifacts(string receiptPath)
    {
        using var receipt = JsonDocument.Parse(File.ReadAllText(receiptPath));
        var root = receipt.RootElement;
        var guestPath = root.GetProperty("guest").GetProperty("path").GetString()!;
        var dylibPath = root.GetProperty("runtime_dylib").GetProperty("path").GetString()!;
        Require(Hash(guestPath) == root.GetProperty("guest").GetProperty("sha256").GetString(), "guest artifact identity");
        Require(Hash(dylibPath) == root.GetProperty("runtime_dylib").GetProperty("sha256").GetString(), "runtime artifact identity");
        return (guestPath, dylibPath, File.ReadAllBytes(guestPath));
    }

    static ushort U16(byte[] b, int at) => BitConverter.ToUInt16(b, at);
    static ulong U64(byte[] b, int at) => BitConverter.ToUInt64(b, at);
    static void Put(byte[] b, int at, ulong value) => BitConverter.TryWriteBytes(b.AsSpan(at, 8), value);

    // Damaged variants of the real guest. Which of them the runtime refuses, accepts or aborts on
    // is what this harness measures; it never executes a mutated guest.
    static byte[] Mutate(byte[] image, string kind, ref Layout layout)
    {
        var bytes = (byte[])image.Clone();
        switch (kind)
        {
            case "wrong-machine": BitConverter.TryWriteBytes(bytes.AsSpan(18, 2), (ushort)62); break; // EM_X86_64
            case "bad-magic": bytes[0] = 0; break;
            case "truncated": return bytes[..100];
            case "unaligned-layout": layout.Sealed = 4096; break;
            case "mixed-rw-rx-group":
            {
                int phoff = (int)U64(bytes, 32), entry = U16(bytes, 54), count = U16(bytes, 56);
                int Header(uint flags) => Enumerable.Range(0, count).Select(n => phoff + n * entry)
                    .First(at => BitConverter.ToUInt32(bytes, at) == 1 && BitConverter.ToUInt32(bytes, at + 4) == flags);
                int rx = Header(5), rw = Header(6);
                ulong tail = U64(bytes, rx + 16) + U64(bytes, rx + 40) - 0x1000;
                Put(bytes, rx + 32, U64(bytes, rx + 32) - 0x1000);
                Put(bytes, rx + 40, U64(bytes, rx + 40) - 0x1000);
                Put(bytes, rw + 16, tail); Put(bytes, rw + 24, tail); // one RW page inside the last RX group
                Put(bytes, rw + 32, 0x1000); Put(bytes, rw + 40, 0x1000);
                break;
            }
            default: throw new ArgumentException("unknown mutation " + kind);
        }
        return bytes;
    }
    static int ProbeLoad(string kind, string receiptPath)
    {
        var (_, dylib, image) = Artifacts(receiptPath);
        using var api = new Native(dylib);
        var layout = Layout.Smoke;
        var damaged = Mutate(image, kind, ref layout);
        using var stream = new MemoryStream(damaged, writable: false);
        Native.Read read = (_, data, size) => {
            try { return stream.Read(new Span<byte>((void*)data, checked((int)size))); } catch { return -1; }
        };
        api.CreateHost(ref layout, "p6-damaged.wbx", read, 0, out var result);
        GC.KeepAlive(read);
        try
        {
            api.Destroy(result.Checked(), out var destroyed); destroyed.Checked();
            Console.WriteLine($"LOAD_ACCEPTED {kind}");
        }
        catch (InvalidOperationException error) { Console.WriteLine($"LOAD_REJECTED {kind}: {error.Message}"); }
        return 0;
    }
    static int RestoreInFreshProcess(string receiptPath, string statePath)
    {
        var (guest, dylib, image) = Artifacts(receiptPath);
        using var api = new Native(dylib);
        using var sandbox = new Sandbox(api, image);
        using var buffer = new Buffer();
        sandbox.Load(File.ReadAllBytes(statePath));
        for (int n = 0; n < Count; ++n) Frame(sandbox, buffer, Checkpoint + n);
        Console.WriteLine($"RESTORE_OK pid={Environment.ProcessId} guest={Hash(guest)} dylib={Hash(dylib)}");
        return 0;
    }
    static int Main(string[] args)
    {
        try {
            Require(OperatingSystem.IsMacOS() && RuntimeInformation.ProcessArchitecture == Architecture.Arm64, "Darwin ARM64 runner");
            Require(Marshal.SizeOf<Layout>() == 40 && Marshal.SizeOf<Result>() == 1032 &&
                Marshal.OffsetOf<Result>(nameof(Result.Data)).ToInt64() == 1024, "native ABI layout");
            if (args.Length == 3 && args[0] == "--restore") return RestoreInFreshProcess(args[1], args[2]);
            if (args.Length == 3 && args[0] == "--probe-load") return ProbeLoad(args[1], args[2]);
            if (args.Length >= 7 && args[0] == "--puae6") return Puae6.Run(args);
            Require(args.Length == 2, "usage: Smoke.dll build-receipt.json result.json");
            var (guestPath, dylibPath, image) = Artifacts(args[0]);
            using var api = new Native(dylibPath);
            using var a = new Sandbox(api, image);
            using var first = new Buffer();
            using var second = new Buffer();
            Require(first.Address != second.Address, "different host buffers");
            var heaps = Memory(a);
            const int checkpoint = Checkpoint, count = Count;
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

            // WB13: callbacks preserve the guest ABI, also after a restore and in a new sandbox.
            var abiBefore = AbiCall(a, reenter: false); CheckAbi(abiBefore, false, "sandbox A");
            var abiNested = AbiCall(a, reenter: true); CheckAbi(abiNested, true, "sandbox A re-entered");
            a.Load(state);
            var abiRestored = AbiCall(a, reenter: true); CheckAbi(abiRestored, true, "sandbox A after restore");
            Require(abiRestored.Out[1] == abiBefore.Out[1], "same sandbox keeps the same x28 context across a restore");
            var abiNew = AbiCall(b, reenter: true); CheckAbi(abiNew, true, "sandbox B");
            Require(abiNew.Out[1] != abiBefore.Out[1], "a second sandbox has its own x28 context");

            // WB14: dirty tracking and restore over the four logical pages of a 16 KiB host group.
            var groups = PageGroups(api, image);

            // WB15: a fresh process restores the snapshot with the exact artifacts and matches the oracle.
            var stateFile = Path.Combine(Path.GetDirectoryName(Path.GetFullPath(args[1]))!, "smoke-state.bin");
            File.WriteAllBytes(stateFile, state);
            var restored = Child("--restore", args[0], stateFile);
            var restoredPid = System.Text.RegularExpressions.Regex.Match(restored.Output, @"RESTORE_OK pid=(\d+) guest=([0-9a-f]{64}) dylib=([0-9a-f]{64})");
            Require(restored.Exit == 0 && restoredPid.Success, "fresh process restored the snapshot: " + restored.Output);
            Require(int.Parse(restoredPid.Groups[1].Value) != Environment.ProcessId, "restore ran in a different process");
            Require(restoredPid.Groups[2].Value == Hash(guestPath) && restoredPid.Groups[3].Value == Hash(dylibPath), "fresh process loaded the same guest and runtime");

            // WB15: an accidental other guest (wrong file, or a receipt that does not match) fails, no fallback.
            var resultDirectory = Path.GetDirectoryName(Path.GetFullPath(args[1]))!;
            var damagedDirectory = Path.Combine(resultDirectory, "damaged");
            Directory.CreateDirectory(damagedDirectory);
            foreach (var kind in new[] { "wrong-machine", "bad-magic", "truncated", "mixed-rw-rx-group" })
            {
                var unchanged = Layout.Smoke;
                File.WriteAllBytes(Path.Combine(damagedDirectory, kind + ".wbx"), Mutate(image, kind, ref unchanged));
            }
            var original = File.ReadAllText(args[0]);
            var wrongHash = Path.Combine(resultDirectory, "receipt-wrong-hash.json");
            File.WriteAllText(wrongHash, original.Replace(Hash(guestPath), new string('0', 64)));
            var wrongFile = Path.Combine(resultDirectory, "receipt-wrong-guest.json");
            File.WriteAllText(wrongFile, original.Replace(JsonSerializer.Serialize(guestPath)[1..^1],
                JsonSerializer.Serialize(Path.Combine(damagedDirectory, "wrong-machine.wbx"))[1..^1]));
            foreach (var receiptPath in new[] { wrongHash, wrongFile })
            {
                var refused = Child("--restore", receiptPath, stateFile);
                Require(refused.Exit != 0 && refused.Output.Contains("guest artifact identity"), $"{Path.GetFileName(receiptPath)} must be refused by the identity check: {refused.Output}");
            }

            var loads = new List<object>();
            foreach (var kind in new[] { "wrong-machine", "bad-magic", "truncated", "unaligned-layout", "mixed-rw-rx-group" })
            {
                var attempt = Child("--probe-load", kind, args[0]);
                var verdict = attempt.Output.Split('\n').FirstOrDefault(line => line.StartsWith("LOAD_"))?.Trim();
                string outcome = attempt.Exit != 0 ? "aborted" : verdict?.StartsWith("LOAD_REJECTED") == true ? "rejected"
                    : verdict?.StartsWith("LOAD_ACCEPTED") == true ? "accepted" : "unknown";
                loads.Add(new { kind, outcome, exit = attempt.Exit, detail = verdict });
            }

            string corelib = typeof(object).Assembly.Location;
            File.WriteAllText(args[1], JsonSerializer.Serialize(new {
                status = "PASS", evidence = "SMOKE_RUNTIME", task = "T09c", amiga_emulation = false,
                checks = new Dictionary<string, string[]> {
                    ["WB01"] = ["constructor_cpp", "musl_tls_key", "heap_limits", "frame_oracle", "buffer_bounds", "restore_same", "restore_new", "alternate_sandboxes", "invisible_poison"],
                    ["WB13"] = ["abi_callback_registers", "abi_callback_reentrant", "abi_across_restore", "abi_new_sandbox"],
                    ["WB14"] = ["page_first_write_group", "page_restore_clean_snapshot", "page_restore_rearm", "page_write_bounds", "ram_group_restore", "damaged_loads_classified"],
                    ["WB15"] = ["artifact_identity", "restore_fresh_process", "identity_mismatch_rejected"],
                },
                guest_sha256 = Hash(guestPath), runtime_sha256 = Hash(dylibPath),
                runner = new { os = RuntimeInformation.OSDescription, architecture = RuntimeInformation.OSArchitecture.ToString(),
                    process_architecture = RuntimeInformation.ProcessArchitecture.ToString(), machine = Environment.MachineName },
                managed_runtime = RuntimeInformation.FrameworkDescription, corelib, corelib_sha256 = Hash(corelib),
                harness_sha256 = Hash(typeof(Program).Assembly.Location), snapshot_bytes = state.Length,
                checkpoint, continuation_frames = count, heap_ranges = heaps,
                abi = new { x18 = abiBefore.Out[0], x28_a = abiBefore.Out[1], x28_b = abiNew.Out[1] },
                page_groups = groups, load_probes = loads,
                native_tests_do_not_prove_amiga = true
            }, new JsonSerializerOptions { WriteIndented = true }) + "\n");
            Console.WriteLine("T09c SMOKE_RUNTIME PASS: snapshot restored into a new Waterbox sandbox");
            return 0;
        }
        catch (Exception error) { Console.Error.WriteLine(error); return 1; }
    }
}
