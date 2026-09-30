using System.Runtime.InteropServices;
using System.Security.Cryptography;
using System.Text.Json;

/// puae6.wbx in a real Waterbox sandbox (T19b): the A500 is initialized from a firmware image, sealed and advanced unit by unit through
/// the exports of adapters/waterbox/puae6_guest.cpp. Each unit is printed in the format of p6_headless so the two can be compared.
/// This is not the BizHawk wrapper; it is the smallest host that exercises the guest ABI (adapters/waterbox/p6w.h).
internal sealed unsafe class Puae6Machine : WaterboxSandbox
{
    [UnmanagedFunctionPointer(CallingConvention.Cdecl)] internal delegate uint AbiVersion();
    [UnmanagedFunctionPointer(CallingConvention.Cdecl)] internal delegate nuint Values(nint output, nuint capacity);
    [UnmanagedFunctionPointer(CallingConvention.Cdecl)] internal delegate int Init(nint args);
    [UnmanagedFunctionPointer(CallingConvention.Cdecl)] internal delegate int Advance(nint input, uint renderFlags, nint output);
    [UnmanagedFunctionPointer(CallingConvention.Cdecl)] internal delegate int ProbeException(int depth, nint destroyed);
    [UnmanagedFunctionPointer(CallingConvention.Cdecl)] internal delegate int Status();

    // adapters/waterbox/p6w.h and bridge/include/p6.h, checked against what the guest was compiled with (p6w_layout).
    internal static readonly ulong[] ExpectedLayout = [
        1264, 1000, 1008, 104, 56,          // p6w_init: size, result, error, media, sizeof(p6w_media)
        400, 8, 136, 144,                   // p6w_frame: size, frame, result, error
        128, 72, 72, 64, 80];               // p6_frame size, p6_input size, p6_config size, p6_frame.audio, p6_frame.master_ticks
    const int InitSize = 1264, InitFirmware = 80, InitMediaCount = 96, InitMedia = 104, MediaSize = 56, InitResult = 1000, InitError = 1008;
    const int FrameSize = 400, FrameInner = 8, FrameResult = 136, FrameError = 144, InputSize = 72;

    internal readonly string[] HeapNames = ["elf", "main_thread", "alt_thread", "sbrk", "sealed", "invisible", "plain", "mmap"];
    internal readonly ulong[] Ranges = new ulong[16];
    readonly Advance advance;
    readonly nint input = Marshal.AllocHGlobal(InputSize), output = Marshal.AllocHGlobal(FrameSize);

    internal Puae6Machine(Native api, byte[] image, Layout layout, byte[] firmware, byte[][] media, uint standard, ulong seed, long epoch)
        : base(api, image, layout, "puae6.wbx")
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
            InitializeAndSeal(firmware, media, standard, seed, epoch);
        }
        catch { Dispose(); throw; }
    }

    void InitializeAndSeal(byte[] firmware, byte[][] media, uint standard, ulong seed, long epoch)
    {
        nint args = Marshal.AllocHGlobal(InitSize), rom = Marshal.AllocHGlobal(firmware.Length);
        var disks = media.Select(image => Marshal.AllocHGlobal(image.Length)).ToArray();
        try
        {
            new Span<byte>((void*)args, InitSize).Clear();
            Marshal.Copy(firmware, 0, rom, firmware.Length);
            var p = (byte*)args;
            *(uint*)p = 1; *(uint*)(p + 4) = InitSize;                      // abi_version, struct_size
            *(uint*)(p + 8) = 1; *(uint*)(p + 12) = 72;                     // p6_config: abi_version, struct_size
            *(uint*)(p + 16) = 1; *(uint*)(p + 20) = standard;              //   machine = A500, video standard
            *(uint*)(p + 24) = 48000; *(ulong*)(p + 32) = seed;             //   audio rate, random seed
            *(long*)(p + 40) = epoch;                                       //   rtc epoch
            *(nint*)(p + InitFirmware) = rom; *(ulong*)(p + InitFirmware + 8) = (ulong)firmware.Length;
            *(uint*)(p + InitMediaCount) = (uint)media.Length;
            for (int i = 0; i < media.Length; ++i)                          // p6w_media: type, flags, content_id (SHA-256), data, bytes
            {
                Marshal.Copy(media[i], 0, disks[i], media[i].Length);
                byte* entry = p + InitMedia + i * MediaSize;
                *(uint*)entry = 1;                                          // P6_MEDIA_ADF
                SHA256.HashData(media[i]).CopyTo(new Span<byte>(entry + 8, 32));
                *(nint*)(entry + 40) = disks[i]; *(ulong*)(entry + 48) = (ulong)media[i].Length;
            }
            int result = In(() => Guest<Init>("p6w_init_machine")(args));
            if (result != 0 || *(int*)(p + InitResult) != 0)
                throw new InvalidOperationException($"p6w_init_machine failed ({result}): {Marshal.PtrToStringUTF8(args + InitError)}");
            // The guest kept no pointer into these: from here on they hold garbage, and the machine must not notice.
            new Span<byte>((void*)rom, firmware.Length).Fill(0xA5);
            for (int i = 0; i < media.Length; ++i) new Span<byte>((void*)disks[i], media[i].Length).Fill(0xA5);
            new Span<byte>((void*)args, InitSize).Fill(0xA5);
            In(() => { api.Seal(host, out var sealedResult); sealedResult.Checked(); return 0; });
        }
        finally
        {
            Marshal.FreeHGlobal(args); Marshal.FreeHGlobal(rom);
            foreach (var disk in disks) Marshal.FreeHGlobal(disk);
        }
    }

    /// One unit: the line p6_headless prints for it, with the picture hashed from guest memory while the sandbox is active.
    internal string Unit(ulong unit)
    {
        new Span<byte>((void*)input, InputSize).Clear();
        *(uint*)input = 1; *(uint*)(input + 4) = InputSize;
        new Span<byte>((void*)output, FrameSize).Clear();
        *(uint*)output = 1; *(uint*)(output + 4) = FrameSize;
        string line = In(() => {
            int result = advance(input, 3, output);
            if (result != 0 || *(int*)(output + FrameResult) != 0)
                throw new InvalidOperationException($"p6w_frame_advance failed ({result}): {Marshal.PtrToStringUTF8(output + FrameError)}");
            byte* f = (byte*)output + FrameInner;
            var video = *(byte**)(f + 8);
            uint width = *(uint*)(f + 48), height = *(uint*)(f + 52), stride = *(uint*)(f + 36);
            uint x = *(uint*)(f + 40), y = *(uint*)(f + 44);
            ulong hash = 1469598103934665603UL;
            if (video != null)
                for (uint row = y; row < y + height; ++row)
                {
                    byte* at = video + (ulong)row * stride + (ulong)x * 4;
                    for (ulong i = 0; i < (ulong)width * 4; ++i) { hash ^= at[i]; hash *= 1099511628211UL; }
                }
            Program.Require(*(ulong*)(f + 88) == unit, $"unit index {unit}");
            return $"unit {*(ulong*)(f + 88)} ticks={*(ulong*)(f + 80)} video={(video != null ? 1 : 0)} active={width}x{height} " +
                   $"parity={*(uint*)(f + 104)} aspect={*(uint*)(f + 56)}/{*(uint*)(f + 60)} cadence={*(uint*)(f + 96)}/{*(uint*)(f + 100)} " +
                   $"hash={hash:x16} audio={*(ulong*)(f + 72)}";
        });
        new Span<byte>((void*)input, InputSize).Fill(0xA5);                // nothing of the host's buffers stays in use after the call
        return line;
    }

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
        Marshal.FreeHGlobal(input);
        Marshal.FreeHGlobal(output);
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

    // Smoke.dll --puae6 runtime-receipt.json puae6-receipt.json waterbox-memory.json firmware units result.json [disk.adf ...]
    internal static int Run(string[] args)
    {
        var runtime = JsonDocument.Parse(File.ReadAllText(args[1])).RootElement.GetProperty("runtime_dylib");
        var guest = JsonDocument.Parse(File.ReadAllText(args[2])).RootElement.GetProperty("guest");
        string dylibPath = runtime.GetProperty("path").GetString()!, guestPath = guest.GetProperty("path").GetString()!;
        Program.Require(Hash(dylibPath) == runtime.GetProperty("sha256").GetString(), "runtime artifact identity");
        Program.Require(Hash(guestPath) == guest.GetProperty("sha256").GetString(), "guest artifact identity");
        var memory = JsonDocument.Parse(File.ReadAllText(args[3])).RootElement.GetProperty("layout");
        Layout layout = new() { Sbrk = memory.GetProperty("sbrk").GetUInt32(), Sealed = memory.GetProperty("sealed").GetUInt32(),
            Invisible = memory.GetProperty("invisible").GetUInt32(), Plain = memory.GetProperty("plain").GetUInt32(),
            Mmap = memory.GetProperty("mmap").GetUInt32() };
        var firmware = File.ReadAllBytes(args[4]);
        var media = args.Skip(7).Select(File.ReadAllBytes).ToArray();
        ulong units = ulong.Parse(args[5]);
        var image = File.ReadAllBytes(guestPath);

        using var api = new Native(dylibPath);

        // C++ exceptions unwind inside the sandbox (the 68000 core throws m68k_exception for bus errors); a sandbox of its own, nothing initialized.
        object exceptions;
        using (var probe = new WaterboxSandbox(api, image, layout, "puae6-exceptions.wbx"))
        {
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
            exceptions = results;
        }

        using var machine = new Puae6Machine(api, image, layout, firmware, media, standard: 1, seed: 1, epoch: 946684800);
        var afterSeal = Usage(machine);
        var sealedSnapshot = machine.Save().Length;
        var lines = new List<string>();
        object? afterFirst = null;
        for (ulong unit = 1; unit <= units; ++unit)
        {
            string line = machine.Unit(unit);
            lines.Add(line);
            Console.WriteLine(line);
            if (unit == 1) afterFirst = Usage(machine);
        }
        var afterLast = Usage(machine);
        var dirtyDump = Environment.GetEnvironmentVariable("P6W_DIRTY_DUMP");      // diagnostics: which pages a run writes
        if (!string.IsNullOrEmpty(dirtyDump)) File.WriteAllLines(dirtyDump, machine.DirtyAddresses().Select(a => a.ToString("x")));
        var finalSnapshot = machine.Save().Length;
        Console.WriteLine($"done units={units}");
        File.WriteAllText(args[6], JsonSerializer.Serialize(new {
            status = "PASS", evidence = "PUAE6_WBX_RUNTIME", task = "T19b", units,
            guest_sha256 = guest.GetProperty("sha256").GetString(), runtime_sha256 = runtime.GetProperty("sha256").GetString(),
            layout = new Dictionary<string, ulong> { ["sbrk"] = layout.Sbrk, ["sealed"] = layout.Sealed, ["invisible"] = layout.Invisible,
                ["plain"] = layout.Plain, ["mmap"] = layout.Mmap },
            ranges = machine.HeapNames.Select((name, i) => (name, i)).ToDictionary(x => x.name,
                x => new { start = machine.Ranges[2 * x.i], size = machine.Ranges[2 * x.i + 1] }),
            after_seal = afterSeal, after_first_unit = afterFirst, after_last_unit = afterLast,
            snapshot_bytes = new { after_seal = sealedSnapshot, after_last_unit = finalSnapshot },
            exceptions,
            firmware_bytes = firmware.Length, media_bytes = media.Sum(image => (long)image.Length), host_buffers_poisoned_after_init = true,
        }, new JsonSerializerOptions { WriteIndented = true }) + "\n");
        return 0;
    }
}
