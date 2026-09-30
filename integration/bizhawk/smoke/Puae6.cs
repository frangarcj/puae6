using System.Runtime.InteropServices;
using System.Security.Cryptography;
using System.Text.Json;

/// Host memory with a guard band of 0xCD on both sides, for what the guest is handed a pointer to.
internal sealed unsafe class Guarded : IDisposable
{
    const int Margin = 64;
    readonly nint block;
    internal readonly nint Address;
    internal readonly int Size;
    internal Guarded(int size)
    {
        Size = size;
        block = Marshal.AllocHGlobal(size + 2 * Margin);
        new Span<byte>((void*)block, size + 2 * Margin).Fill(0xCD);
        Address = block + Margin;
    }
    internal Span<byte> Payload => new((void*)Address, Size);
    internal void Check(string what)
    {
        var bytes = new ReadOnlySpan<byte>((void*)block, Size + 2 * Margin);
        for (int i = 0; i < Margin; ++i)
            Program.Require(bytes[i] == 0xCD && bytes[Margin + Size + i] == 0xCD, $"the guard bands around {what} are intact");
    }
    public void Dispose() => Marshal.FreeHGlobal(block);
}

/// puae6.wbx in a real Waterbox sandbox (T19b, T19c1): the A500 is initialized from a firmware image, sealed and advanced unit by unit through
/// the exports of adapters/waterbox/puae6_guest.cpp. Each unit is printed in the format of p6_headless so the two can be compared.
/// This is not the BizHawk wrapper; it is the smallest host that exercises the guest ABI (adapters/waterbox/p6w.h).
internal sealed unsafe class Puae6Machine : WaterboxSandbox
{
    [UnmanagedFunctionPointer(CallingConvention.Cdecl)] internal delegate uint AbiVersion();
    [UnmanagedFunctionPointer(CallingConvention.Cdecl)] internal delegate nuint Values(nint output, nuint capacity);
    [UnmanagedFunctionPointer(CallingConvention.Cdecl)] internal delegate int Init(nint args);
    [UnmanagedFunctionPointer(CallingConvention.Cdecl)] internal delegate int Advance(nint input, uint renderFlags, nint output);
    [UnmanagedFunctionPointer(CallingConvention.Cdecl)] internal delegate int ProbeException(int depth, nint destroyed);

    // adapters/waterbox/p6w.h and bridge/include/p6.h, checked against what the guest was compiled with (p6w_layout).
    internal static readonly ulong[] ExpectedLayout = [
        1264, 1000, 1008, 104, 56,          // p6w_init: size, result, error, media, sizeof(p6w_media)
        400, 8, 136, 144,                   // p6w_frame: size, frame, result, error
        128, 72, 72, 64, 80];               // p6_frame size, p6_input size, p6_config size, p6_frame.audio, p6_frame.master_ticks
    const int InitSize = 1264, InitFirmware = 80, InitMediaCount = 96, InitMedia = 104, MediaSize = 56, InitResult = 1000, InitError = 1008;
    const int FrameSize = 400, FrameInner = 8, FrameResult = 136, FrameError = 144, InputSize = 72;
    const uint FrameNoVideo = 2;

    internal readonly string[] HeapNames = ["elf", "main_thread", "alt_thread", "sbrk", "sealed", "invisible", "plain", "mmap"];
    internal readonly ulong[] Ranges = new ulong[16];
    /// The two controller ports of the packet every unit carries: type, buttons, dx, dy. Nothing pressed by default.
    internal readonly (uint Type, uint Buttons, short Dx, short Dy)[] Ports = [(1, 0, 0, 0), (1, 0, 0, 0)];
    internal ulong AudioFrames, AudioHash = 1469598103934665603UL, Canaries;
    /// FNV-1a of the PCM the last unit delivered (null when it delivered none), to compare units one by one.
    internal string? LastPcm;
    readonly Advance advance;
    readonly Guarded input = new(InputSize), output = new(FrameSize);

    internal Puae6Machine(Native api, byte[] image, Layout layout) : base(api, image, layout, "puae6.wbx")
    {
        try
        {
            advance = Guest<Advance>("p6w_frame_advance");
            In(() => {
                Program.Require(Guest<AbiVersion>("p6w_abi_version")() == 1, "guest ABI version");
                var values = new ulong[ExpectedLayout.Length];
                fixed (ulong* v = values)
                    Program.Require(Guest<Values>("p6w_layout")((nint)v, (nuint)values.Length) == (nuint)values.Length, "guest layout report");
                Program.Require(values.SequenceEqual(ExpectedLayout), $"the guest layout [{string.Join(',', values)}] is the one the host was written for");
                fixed (ulong* r = Ranges) Program.Require(Guest<Values>("p6w_memory_info")((nint)r, 16) == 16, "heap ranges");
                return 0;
            });
        }
        catch { Dispose(); throw; }
    }

    /// p6w_init_machine with what the host has: returns its result code and message. The struct is guarded; nothing in it but result and error
    /// may change, and the host's copies of the firmware and the disks are overwritten afterwards (the guest keeps its own in the sealed heap).
    internal (int Result, string Error) TryInit(byte[] firmware, byte[][] media, uint standard, ulong seed, long epoch, uint? mediaCount = null)
    {
        using var args = new Guarded(InitSize);
        nint rom = Marshal.AllocHGlobal(Math.Max(firmware.Length, 1));
        var disks = media.Select(image => Marshal.AllocHGlobal(Math.Max(image.Length, 1))).ToArray();
        try
        {
            args.Payload.Clear();
            Marshal.Copy(firmware, 0, rom, firmware.Length);
            var p = (byte*)args.Address;
            *(uint*)p = 1; *(uint*)(p + 4) = InitSize;                      // abi_version, struct_size
            *(uint*)(p + 8) = 1; *(uint*)(p + 12) = 72;                     // p6_config: abi_version, struct_size
            *(uint*)(p + 16) = 1; *(uint*)(p + 20) = standard;              //   machine = A500, video standard
            *(uint*)(p + 24) = 48000; *(ulong*)(p + 32) = seed;             //   audio rate, random seed
            *(long*)(p + 40) = epoch;                                       //   rtc epoch
            *(nint*)(p + InitFirmware) = rom; *(ulong*)(p + InitFirmware + 8) = (ulong)firmware.Length;
            *(uint*)(p + InitMediaCount) = mediaCount ?? (uint)media.Length;
            for (int i = 0; i < media.Length && i < 16; ++i)                // p6w_media: type, flags, content_id (SHA-256), data, bytes
            {
                Marshal.Copy(media[i], 0, disks[i], media[i].Length);
                byte* entry = p + InitMedia + i * MediaSize;
                *(uint*)entry = 1;                                          // P6_MEDIA_ADF
                SHA256.HashData(media[i]).CopyTo(new Span<byte>(entry + 8, 32));
                *(nint*)(entry + 40) = disks[i]; *(ulong*)(entry + 48) = (ulong)media[i].Length;
            }
            var before = args.Payload.ToArray();
            int result = In(() => Guest<Init>("p6w_init_machine")(args.Address));
            var after = args.Payload;
            for (int i = 0; i < InitSize; ++i)
                if (i < InitResult || i >= InitError + 256)
                    Program.Require(after[i] == before[i], $"p6w_init_machine left byte {i} of its argument as it found it");
            args.Check("p6w_init"); Canaries++;
            string error = Marshal.PtrToStringUTF8((nint)(p + InitError)) ?? "";
            Program.Require(*(int*)(p + InitResult) == result, "the result field is the returned value");
            new Span<byte>((void*)rom, firmware.Length).Fill(0xA5);
            for (int i = 0; i < media.Length; ++i) new Span<byte>((void*)disks[i], media[i].Length).Fill(0xA5);
            after.Fill(0xA5);
            return (result, error);
        }
        finally
        {
            Marshal.FreeHGlobal(rom);
            foreach (var disk in disks) Marshal.FreeHGlobal(disk);
        }
    }

    internal void Start(byte[] firmware, byte[][] media, uint standard, ulong seed, long epoch)
    {
        var (result, error) = TryInit(firmware, media, standard, seed, epoch);
        if (result != 0) throw new InvalidOperationException($"p6w_init_machine failed ({result}): {error}");
        Seal();
    }

    internal void Seal() => In(() => { api.Seal(host, out var sealedResult); sealedResult.Checked(); return 0; });

    bool InGuest(ulong address, ulong bytes) =>
        address >= Ranges[0] && address + bytes >= address && address + bytes <= Ranges[14] + Ranges[15];

    void WriteInput()
    {
        input.Payload.Clear();
        byte* p = (byte*)input.Address;
        *(uint*)p = 1; *(uint*)(p + 4) = InputSize;
        for (int i = 0; i < 2; ++i)
        {
            byte* port = p + 24 + 16 * i;
            *(uint*)port = Ports[i].Type; *(uint*)(port + 4) = Ports[i].Buttons;
            *(short*)(port + 8) = Ports[i].Dx; *(short*)(port + 10) = Ports[i].Dy;
        }
    }
    void PrepareOutput()
    {
        output.Payload.Clear();
        *(uint*)output.Address = 1; *(uint*)(output.Address + 4) = FrameSize;
    }

    /// One unit: the line p6_headless prints for it, with the picture hashed from guest memory while the sandbox is active.
    internal string Unit(ulong unit, uint flags)
    {
        WriteInput();
        var sent = input.Payload.ToArray();
        PrepareOutput();
        string line = In(() => {
            int result = advance(input.Address, flags, output.Address);
            if (result != 0 || *(int*)(output.Address + FrameResult) != 0)
                throw new InvalidOperationException($"p6w_frame_advance failed ({result}): {Marshal.PtrToStringUTF8(output.Address + FrameError)}");
            byte* f = (byte*)output.Address + FrameInner;
            var video = *(byte**)(f + 8);
            ulong videoBytes = *(ulong*)(f + 16);
            uint width = *(uint*)(f + 48), height = *(uint*)(f + 52), stride = *(uint*)(f + 36);
            uint x = *(uint*)(f + 40), y = *(uint*)(f + 44);
            ulong hash = 1469598103934665603UL;
            if ((flags & 1) == 0)
                Program.Require(video == null && (*(uint*)(f + 108) & FrameNoVideo) != 0, $"unit {unit}: no picture was asked for and none was delivered");
            if (video != null)
            {
                Program.Require((flags & 1) != 0, $"unit {unit}: a picture was delivered although none was asked for");
                Program.Require(InGuest((ulong)video, videoBytes), $"unit {unit}: the picture is inside the sandbox");
                Program.Require(height == 0 || ((ulong)(y + height - 1) * stride + (ulong)(x + width) * 4) <= videoBytes, $"unit {unit}: the active rows are inside the picture");
                for (uint row = y; row < y + height; ++row)
                {
                    byte* at = video + (ulong)row * stride + (ulong)x * 4;
                    for (ulong i = 0; i < (ulong)width * 4; ++i) { hash ^= at[i]; hash *= 1099511628211UL; }
                }
            }
            ulong frames = *(ulong*)(f + 72);
            var audio = *(byte**)(f + 64);
            LastPcm = null;
            if ((flags & 2) == 0) Program.Require(frames == 0 && audio == null, $"unit {unit}: no audio was asked for and none was delivered");
            else if (frames != 0)
            {
                Program.Require(InGuest((ulong)audio, frames * 4), $"unit {unit}: the audio is inside the sandbox");
                ulong pcm = 1469598103934665603UL;
                for (ulong i = 0; i < frames * 4; ++i)
                {
                    AudioHash ^= audio[i]; AudioHash *= 1099511628211UL;
                    pcm ^= audio[i]; pcm *= 1099511628211UL;
                }
                LastPcm = pcm.ToString("x16");
                AudioFrames += frames;
            }
            Program.Require(*(ulong*)(f + 88) == unit, $"unit index {unit}");
            return $"unit {*(ulong*)(f + 88)} ticks={*(ulong*)(f + 80)} video={(video != null ? 1 : 0)} active={width}x{height} " +
                   $"parity={*(uint*)(f + 104)} aspect={*(uint*)(f + 56)}/{*(uint*)(f + 60)} cadence={*(uint*)(f + 96)}/{*(uint*)(f + 100)} " +
                   $"hash={hash:x16} audio={frames}";
        });
        Program.Require(input.Payload.SequenceEqual(sent), $"unit {unit}: the guest left p6_input as it found it");
        input.Check("p6_input"); output.Check("p6w_frame"); Canaries += 2;
        input.Payload.Fill(0xA5);                                           // nothing of the host's buffers stays in use after the call
        return line;
    }

    /// Calls the guest must refuse: each has to return the expected p6_result with a message, leave the guard bands alone and not advance the
    /// machine (the caller checks the next unit against the reference).
    internal List<object> Refusals()
    {
        var results = new List<object>();
        (string Name, Action Prepare, uint Flags, bool NullOutput, bool NullInput, int Expected, string Message)[] cases = [
            ("wrong frame abi_version", () => *(uint*)output.Address = 2, 3, false, false, 1, "ABI"),
            ("wrong frame struct_size", () => *(uint*)(output.Address + 4) = FrameSize - 1, 3, false, false, 1, "ABI"),
            ("wrong input struct_size", () => *(uint*)(input.Address + 4) = InputSize - 4, 3, false, false, 1, ""),
            ("unknown render flag", () => { }, 0x80, false, false, 1, "render"),
            ("missing input", () => { }, 3, false, true, 1, "input"),
            ("missing output", () => { }, 3, true, false, 1, ""),
        ];
        foreach (var c in cases)
        {
            WriteInput(); PrepareOutput();
            c.Prepare();
            int result = In(() => advance(c.NullInput ? 0 : input.Address, c.Flags, c.NullOutput ? 0 : output.Address));
            string message = c.NullOutput ? "" : Marshal.PtrToStringUTF8(output.Address + FrameError) ?? "";
            Program.Require(result == c.Expected, $"{c.Name}: refused with p6_result {c.Expected} (got {result})");
            if (!c.NullOutput)
            {
                Program.Require(*(int*)(output.Address + FrameResult) == c.Expected, $"{c.Name}: the result field carries the code");
                Program.Require(message.Length > 0 && message.Contains(c.Message, StringComparison.OrdinalIgnoreCase), $"{c.Name}: the message says why ({message})");
            }
            input.Check("p6_input"); output.Check("p6w_frame"); Canaries += 2;
            results.Add(new { c.Name, result, message });
        }
        input.Payload.Fill(0xA5);
        return results;
    }

    /// Calls the guest with a valid frame, as a host that forgot p6w_init would.
    internal int AdvanceWithoutPreparing()
    {
        WriteInput(); PrepareOutput();
        int result = In(() => advance(input.Address, 3, output.Address));
        output.Check("p6w_frame"); input.Check("p6_input");
        return result;
    }

    internal string Digest() => Convert.ToHexString(SHA256.HashData(Save())).ToLowerInvariant();

    /// Bytes of each range the page table reports as allocated, and as dirty, in the order of HeapNames.
    internal (ulong Committed, ulong Dirty, ulong Invisible)[] Usage()
    {
        ulong baseAddress = Ranges[0];
        var usage = new (ulong, ulong, ulong)[8];
        ulong pages = (ulong)PageCount;
        for (int i = 0; i < 8; ++i)
        {
            ulong first = (Ranges[2 * i] - baseAddress) / 4096, count = Ranges[2 * i + 1] / 4096;
            for (ulong page = first; page < first + count && page < pages; ++page)
            {
                byte info = PageInfo((int)page);
                if ((info & 0x3f) != 0) usage[i].Item1 += 4096;
                if ((info & 0x80) != 0) usage[i].Item2 += 4096;
                if ((info & 0x40) != 0) usage[i].Item3 += 4096;
            }
        }
        return usage;
    }

    /// Addresses of the 4 KiB pages the runtime considers dirty since the seal (what a snapshot has to carry), for measuring what a unit writes.
    internal List<ulong> DirtyAddresses()
    {
        var dirty = new List<ulong>();
        for (int page = 0; page < PageCount; ++page)
            if ((PageInfo(page) & 0x80) != 0) dirty.Add(Ranges[0] + (ulong)page * 4096);
        return dirty;
    }

    public override void Dispose()
    {
        base.Dispose();
        input.Dispose();
        output.Dispose();
    }
}

internal static unsafe class Puae6
{
    static string Hash(string path) => Convert.ToHexString(SHA256.HashData(File.ReadAllBytes(path))).ToLowerInvariant();
    static object Usage(Puae6Machine machine)
    {
        var usage = machine.Usage();
        return machine.HeapNames.Select((name, i) => (name, i)).ToDictionary(x => x.name,
            x => new { committed_bytes = usage[x.i].Committed, dirty_bytes = usage[x.i].Dirty, invisible_bytes = usage[x.i].Invisible });
    }

    /// Options of the puae6 mode: `--key value`, repeatable keys collected in order, bare flags as "true".
    sealed class Options
    {
        readonly Dictionary<string, List<string>> values = new();
        internal Options(string[] args)
        {
            for (int i = 1; i < args.Length; ++i)
            {
                Program.Require(args[i].StartsWith("--"), $"option expected, found {args[i]}");
                string key = args[i][2..];
                string value = i + 1 < args.Length && !args[i + 1].StartsWith("--") ? args[++i] : "true";
                if (!values.TryGetValue(key, out var list)) values[key] = list = new();
                list.Add(value);
            }
        }
        internal string Get(string key) => values.TryGetValue(key, out var list) ? list[^1] : throw new ArgumentException($"missing --{key}");
        internal string? Optional(string key) => values.TryGetValue(key, out var list) ? list[^1] : null;
        internal IReadOnlyList<string> All(string key) => values.TryGetValue(key, out var list) ? list : [];
        internal bool Flag(string key) => values.ContainsKey(key);
    }

    /// "101-200:0,201-300:2": the render flags (1 video, 2 audio) of a range of units; every other unit asks for both.
    static uint RenderFlags(List<(ulong From, ulong To, uint Flags)> schedule, ulong unit)
    {
        foreach (var range in schedule) if (unit >= range.From && unit <= range.To) return range.Flags;
        return 3;
    }

    // Smoke.dll --puae6 --runtime receipt.json --guest puae6-receipt.json --manifest waterbox-memory.json --firmware rom --units N --result out.json
    //   [--media disk.adf]... [--port N:type:buttons:dx:dy]... [--render 101-200:0,...] [--digest-every K] [--bad-calls-at U] [--limits] [--ntsc]
    internal static int Run(string[] args)
    {
        var options = new Options(args);
        var runtime = JsonDocument.Parse(File.ReadAllText(options.Get("runtime"))).RootElement.GetProperty("runtime_dylib");
        var guest = JsonDocument.Parse(File.ReadAllText(options.Get("guest"))).RootElement.GetProperty("guest");
        string dylibPath = runtime.GetProperty("path").GetString()!, guestPath = guest.GetProperty("path").GetString()!;
        Program.Require(Hash(dylibPath) == runtime.GetProperty("sha256").GetString(), "runtime artifact identity");
        Program.Require(Hash(guestPath) == guest.GetProperty("sha256").GetString(), "guest artifact identity");
        var memory = JsonDocument.Parse(File.ReadAllText(options.Get("manifest"))).RootElement.GetProperty("layout");
        Layout layout = new() { Sbrk = memory.GetProperty("sbrk").GetUInt32(), Sealed = memory.GetProperty("sealed").GetUInt32(),
            Invisible = memory.GetProperty("invisible").GetUInt32(), Plain = memory.GetProperty("plain").GetUInt32(),
            Mmap = memory.GetProperty("mmap").GetUInt32() };
        var firmware = File.ReadAllBytes(options.Get("firmware"));
        var media = options.All("media").Select(File.ReadAllBytes).ToArray();
        ulong units = ulong.Parse(options.Get("units"));
        uint standard = options.Flag("ntsc") ? 2u : 1u;
        var image = File.ReadAllBytes(guestPath);
        using var api = new Native(dylibPath);
        var report = new Dictionary<string, object?> {
            ["status"] = "PASS", ["evidence"] = "PUAE6_WBX_RUNTIME", ["units"] = units,
            ["guest_sha256"] = guest.GetProperty("sha256").GetString(), ["runtime_sha256"] = runtime.GetProperty("sha256").GetString(),
            ["layout"] = new Dictionary<string, ulong> { ["sbrk"] = layout.Sbrk, ["sealed"] = layout.Sealed, ["invisible"] = layout.Invisible,
                ["plain"] = layout.Plain, ["mmap"] = layout.Mmap },
            ["firmware_bytes"] = firmware.Length, ["media_bytes"] = media.Sum(image => (long)image.Length),
            ["host_buffers_poisoned_after_init"] = true };

        if (options.Flag("limits")) report["limits"] = Limits(api, image, layout, firmware, standard);
        else report["exceptions"] = ExceptionProbe(api, image, layout);
        if (options.Flag("limits")) { File.WriteAllText(options.Get("result"), JsonSerializer.Serialize(report) + "\n"); return 0; }

        var schedule = options.All("render").SelectMany(list => list.Split(',', StringSplitOptions.RemoveEmptyEntries)).Select(item => {
            var parts = item.Split(':'); var span = parts[0].Split('-');
            return (ulong.Parse(span[0]), ulong.Parse(span[^1]), uint.Parse(parts[1]));
        }).ToList();
        using var machine = new Puae6Machine(api, image, layout);
        foreach (var port in options.All("port"))
        {
            var p = port.Split(':');
            machine.Ports[int.Parse(p[0])] = (uint.Parse(p[1]), uint.Parse(p[2]), short.Parse(p[3]), short.Parse(p[4]));
        }
        machine.Start(firmware, media, standard, seed: 1, epoch: 946684800);
        var afterSeal = Usage(machine);
        var sealedSnapshot = machine.Save().Length;
        ulong digestEvery = ulong.Parse(options.Optional("digest-every") ?? "0");
        ulong badCallsAt = ulong.Parse(options.Optional("bad-calls-at") ?? "0");
        var digests = new Dictionary<string, string>();
        object? afterFirst = null, refusals = null;
        for (ulong unit = 1; unit <= units; ++unit)
        {
            if (unit == badCallsAt) refusals = machine.Refusals();
            Console.WriteLine(machine.Unit(unit, RenderFlags(schedule, unit)));
            if (machine.LastPcm != null) Console.WriteLine($"pcm {unit} {machine.LastPcm}");
            if (unit == 1) afterFirst = Usage(machine);
            if (digestEvery != 0 && unit % digestEvery == 0) digests[unit.ToString()] = machine.Digest();
        }
        var afterLast = Usage(machine);
        var dirtyDump = Environment.GetEnvironmentVariable("P6W_DIRTY_DUMP");      // diagnostics: which pages a run writes
        if (!string.IsNullOrEmpty(dirtyDump)) File.WriteAllLines(dirtyDump, machine.DirtyAddresses().Select(a => a.ToString("x")));
        var finalState = machine.Save();
        digests["final"] = Convert.ToHexString(SHA256.HashData(finalState)).ToLowerInvariant();
        Console.WriteLine($"audio frames={machine.AudioFrames} hash={machine.AudioHash:x16}");
        Console.WriteLine($"state final {digests["final"]}");
        Console.WriteLine($"done units={units}");
        report["ranges"] = machine.HeapNames.Select((name, i) => (name, i)).ToDictionary(x => x.name,
            x => new { start = machine.Ranges[2 * x.i], size = machine.Ranges[2 * x.i + 1] });
        report["after_seal"] = afterSeal; report["after_first_unit"] = afterFirst; report["after_last_unit"] = afterLast;
        report["snapshot_bytes"] = new { after_seal = sealedSnapshot, after_last_unit = finalState.Length };
        report["audio"] = new { frames = machine.AudioFrames, fnv = machine.AudioHash.ToString("x16") };
        report["state_digests"] = digests;
        report["canary_checks"] = machine.Canaries;
        report["refusals"] = refusals;
        report["ports"] = machine.Ports.Select(p => $"{p.Type}:{p.Buttons}:{p.Dx}:{p.Dy}").ToArray();
        File.WriteAllText(options.Get("result"), JsonSerializer.Serialize(report, new JsonSerializerOptions { WriteIndented = true }) + "\n");
        return 0;
    }

    /// C++ exceptions unwind inside the sandbox (the 68000 core throws m68k_exception for bus errors); a sandbox of its own, nothing initialized.
    static object ExceptionProbe(Native api, byte[] image, Layout layout)
    {
        using var probe = new WaterboxSandbox(api, image, layout, "puae6-exceptions.wbx");
        var results = new List<object>();
        foreach (int depth in new[] { 0, 1, 7, 40 })
        {
            nint destroyed = Marshal.AllocHGlobal(4);
            try
            {
                int value = probe.In(() => probe.Guest<Puae6Machine.ProbeException>("p6w_probe_exception")(depth, destroyed));
                int count = *(int*)destroyed;
                Program.Require(value == 42 && count == depth + 1, $"an exception thrown {depth} frames down is caught and unwinds {depth + 1} destructors (got {value}, {count})");
                results.Add(new { depth, caught = value, destructors_run = count });
            }
            finally { Marshal.FreeHGlobal(destroyed); }
        }
        Program.Require(probe.In(() => probe.Guest<Puae6Machine.ProbeException>("p6w_probe_exception")(-1, 0)) == -1, "bad arguments refused");
        return results;
    }

    /// Memory and state limits that have to fail cleanly: a firmware bigger than the sealed heap, too many media, a call before init,
    /// a second init. After them the same sandbox still takes a good init and runs.
    static object Limits(Native api, byte[] image, Layout layout, byte[] firmware, uint standard)
    {
        var outcomes = new List<object>();
        using var machine = new Puae6Machine(api, image, layout);
        void Expect(string name, (int Result, string Error) got, int code, string message)
        {
            Program.Require(got.Result == code && got.Error.Contains(message, StringComparison.OrdinalIgnoreCase),
                $"{name}: p6_result {code} mentioning '{message}' (got {got.Result}: {got.Error})");
            outcomes.Add(new { name, result = got.Result, message = got.Error });
        }
        Expect("a firmware bigger than the sealed heap", machine.TryInit(new byte[(long)layout.Sealed + 16384], [], standard, 1, 946684800), 7, "sealed heap");
        Expect("more media than the contract allows", machine.TryInit(firmware, [], standard, 1, 946684800, mediaCount: 17), 1, "too many media");
        Expect("no firmware", machine.TryInit([], [], standard, 1, 946684800), 1, "firmware");
        int early = machine.AdvanceWithoutPreparing();
        Program.Require(early == 2, $"a unit before init is refused with BAD_STATE (got {early})");
        outcomes.Add(new { name = "a unit before init", result = early, message = "" });
        machine.Start(firmware, [], standard, seed: 1, epoch: 946684800);        // the failed attempts consumed nothing
        Expect("a second init", machine.TryInit(firmware, [], standard, 1, 946684800), 2, "already loaded");
        Console.WriteLine(machine.Unit(1, 3));
        return outcomes;
    }
}
