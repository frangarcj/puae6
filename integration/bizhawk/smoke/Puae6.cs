using System.Runtime.InteropServices;
using System.Security.Cryptography;
using System.Text.Json;
using System.Text.RegularExpressions;

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

/// puae6.wbx in a real Waterbox sandbox (T19b, T19c1, T23e, T24f): the machine (A500 by default, A600 or A1200 with --machine) is initialized from a firmware image, sealed and advanced unit by unit through
/// the exports of adapters/waterbox/puae6_guest.cpp. Each unit is printed in the format of p6_headless so the two can be compared.
/// This is not the BizHawk wrapper; it is the smallest host that exercises the guest ABI (adapters/waterbox/p6w.h).
internal sealed unsafe class Puae6Machine : WaterboxSandbox
{
    [UnmanagedFunctionPointer(CallingConvention.Cdecl)] internal delegate uint AbiVersion();
    [UnmanagedFunctionPointer(CallingConvention.Cdecl)] internal delegate nuint Values(nint output, nuint capacity);
    [UnmanagedFunctionPointer(CallingConvention.Cdecl)] internal delegate int Init(nint args);
    [UnmanagedFunctionPointer(CallingConvention.Cdecl)] internal delegate int Advance(nint input, uint renderFlags, nint output);
    [UnmanagedFunctionPointer(CallingConvention.Cdecl)] internal delegate int ProbeException(int depth, nint destroyed);
    [UnmanagedFunctionPointer(CallingConvention.Cdecl)] internal delegate void MemoryAccess(uint type, nint buffer, long address, long count, uint write);
    [UnmanagedFunctionPointer(CallingConvention.Cdecl)] internal delegate ulong Counter();

    // adapters/waterbox/p6w.h and bridge/include/p6.h, checked against what the guest was compiled with (p6w_layout).
    internal static readonly ulong[] ExpectedLayout = [
        1264, 1000, 1008, 104, 56,          // p6w_init: size, result, error, media, sizeof(p6w_media)
        400, 8, 136, 144,                   // p6w_frame: size, frame, result, error
        128, 72, 72, 64, 80,                // p6_frame size, p6_input size, p6_config size, p6_frame.audio, p6_frame.master_ticks
        32, 8, 16, 24,                      // p6w_memory_area: size, name, size, flags (the layout of LibWaterboxCore.MemoryArea)
        320, 8, 56, 64,                     // p6w_media_event: size, event, result, error
        336, 8, 72, 80,                     // p6w_media_write_status: size, status, result, error (T28)
        304, 16, 32, 40, 48];               // p6w_media_export: size, buffer, written, result, error
    const int InitSize = 1264, InitFirmware = 80, InitMediaCount = 96, InitMedia = 104, MediaSize = 56, InitResult = 1000, InitError = 1008;
    const int FrameSize = 400, FrameInner = 8, FrameResult = 136, FrameError = 144, InputSize = 72;
    const uint FrameNoVideo = 2;

    /// p6_config.machine the next TryInit gives the guest: P6_MACHINE_A500 (1), A600 (2), A1200 (3), A500OG (4), A500PLUS (5), A1000 (6), A2000OG (7) or A2000 (8).
    internal uint MachineId = 1;
    /// p6_config.chip_kib, slow_kib and fast_kib the next TryInit gives the guest (T49): 0 is the machine's own memory, uint.MaxValue (Slow) is none.
    internal uint ChipKib, SlowKib, FastKib;
    /// p6_config.fpu_model the next TryInit gives the guest (T54): 0 (none), 68881 or 68882 (A1200 only).
    internal uint FpuModel;
    /// p6_config.flags the next TryInit gives the guest: P6_CONFIG_FLOPPY_WRITES (1) makes the floppies writable (T28, --floppy-writes).
    internal uint ConfigFlags;
    /// p6_media.type of each medium the next TryInit gives (T29d): P6_MEDIA_ADF (1) unless it says P6_MEDIA_HDF (2). Media past its end are floppies.
    internal uint[] MediaTypes = [];
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
            *(uint*)(p + 16) = MachineId; *(uint*)(p + 20) = standard;      //   machine (1 = A500, 2 = A600, 3 = A1200, 4 = A500OG, 5 = A500PLUS, 6 = A1000, 7 = A2000OG, 8 = A2000), video standard
            *(uint*)(p + 24) = 48000; *(uint*)(p + 28) = ConfigFlags; *(ulong*)(p + 32) = seed;     //   audio rate, flags, random seed
            *(long*)(p + 40) = epoch;                                       //   rtc epoch
            *(uint*)(p + 48) = ChipKib; *(uint*)(p + 52) = SlowKib; *(uint*)(p + 56) = FastKib;     //   chip_kib, slow_kib, fast_kib (reserved[2] stays zero)
            *(uint*)(p + 60) = FpuModel;
            *(nint*)(p + InitFirmware) = rom; *(ulong*)(p + InitFirmware + 8) = (ulong)firmware.Length;
            *(uint*)(p + InitMediaCount) = mediaCount ?? (uint)media.Length;
            for (int i = 0; i < media.Length && i < 16; ++i)                // p6w_media: type, flags, content_id (SHA-256), data, bytes
            {
                Marshal.Copy(media[i], 0, disks[i], media[i].Length);
                byte* entry = p + InitMedia + i * MediaSize;
                *(uint*)entry = i < MediaTypes.Length ? MediaTypes[i] : 1u;  // P6_MEDIA_ADF, or P6_MEDIA_HDF for a hard file
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

    /// Queues the mount of media[index] into a drive (p6w_media_event_enqueue, sequence numbers from 1): what the wrapper does for the boot disks (T28 measures with writable disks in the drives).
    internal void MountBoot(uint drive, uint index, ulong sequence)
    {
        using var call = new Guarded(320);
        call.Payload.Clear();
        byte* p = (byte*)call.Address;
        *(uint*)p = 1; *(uint*)(p + 4) = 320;                       // p6w_media_event: abi_version, struct_size
        *(uint*)(p + 8) = 1; *(uint*)(p + 12) = 48;                 // p6_media_event: abi_version, struct_size
        *(uint*)(p + 16) = 1; *(uint*)(p + 20) = drive; *(uint*)(p + 24) = index;      // P6_MEDIA_EVENT_MOUNT, drive, media index
        *(ulong*)(p + 32) = sequence;
        int result = In(() => Guest<Init>("p6w_media_event_enqueue")(call.Address));
        Program.Require(result == 0 && *(int*)(p + 56) == 0, $"p6w_media_event_enqueue failed ({result}): {Marshal.PtrToStringUTF8(call.Address + 64)}");
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

    internal (ulong[] Out, ulong[] Nested) Abi(bool reenter) => Program.AbiCall(this, reenter, "p6w_probe_abi", "p6w_identity");

    /// Where Chip RAM is in this sandbox: the host finds it by the marker a fixture program stores at a known Chip RAM address (there is no
    /// memory-region export yet). The marker must be preceded by four equal bytes, zeros (A500, A600) or the 0xFF of the A1200's power-on pattern of Chip RAM (alternating
    /// runs of eight zero bytes and eight 0xFF bytes), which excludes the copies of the firmware that hold its immediate (the opcode of MOVE.L #imm precedes it).
    internal ulong LocateChipRam(ulong markerAt, byte[] marker)
    {
        var candidates = new List<ulong>();
        In(() => {
            ulong pages = (ulong)PageCount;
            for (int range = 0; range < 8; ++range)
            {
                if (HeapNames[range] != "sbrk" && HeapNames[range] != "mmap") continue;
                ulong first = (Ranges[2 * range] - Ranges[0]) / 4096, count = Ranges[2 * range + 1] / 4096;
                for (ulong page = first; page < first + count && page < pages;)
                {
                    if ((PageInfo((int)page) & 1) == 0) { ++page; continue; }
                    ulong run = page;
                    while (run < first + count && run < pages && (PageInfo((int)run) & 1) != 0) ++run;
                    ulong start = Ranges[0] + page * 4096, bytes = (run - page) * 4096;
                    var span = new ReadOnlySpan<byte>((void*)start, (int)bytes);
                    for (int from = 4; from <= span.Length - marker.Length;)
                    {
                        int found = span[from..].IndexOf(marker);
                        if (found < 0) break;
                        int position = from + found;
                        if (span.Slice(position - 4, 4).IndexOfAnyExcept(span[position - 4]) < 0 && (span[position - 4] is 0 or 0xFF) && start + (ulong)position >= markerAt)
                            candidates.Add(start + (ulong)position - markerAt);
                        from = position + 1;
                    }
                    page = run;
                }
            }
            return 0;
        });
        Program.Require(candidates.Count == 1, $"Chip RAM is found by its marker exactly once (found {candidates.Count})");
        return candidates[0];
    }

    internal bool[] DirtyMap()
    {
        var map = new bool[PageCount];
        for (int i = 0; i < map.Length; ++i) map[i] = (PageInfo(i) & 0x80) != 0;
        return map;
    }
    internal byte[] ReadGuest(ulong address, int bytes) => In(() => new ReadOnlySpan<byte>((void*)address, bytes).ToArray());
    [DllImport("libSystem.B.dylib")] static extern nint memcpy(nint destination, nint source, nuint bytes);
    /// A host write into the sandbox. It goes through the C library: a store made by managed code into a page that dirty tracking still has
    /// write-protected is turned into a fatal AccessViolationException by the .NET runtime before the Waterbox handler sees it, while a store
    /// made by native code is tracked like the guest's own.
    internal void PokeGuest(ulong address, byte value)
    {
        nint source = Marshal.AllocHGlobal(1);
        try { *(byte*)source = value; In(() => { memcpy((nint)address, source, 1); return 0; }); }
        finally { Marshal.FreeHGlobal(source); }
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

    internal const uint ChipRam = 1, SlowRam = 2, Rom = 3, FastRam = 4;      // P6_REGION_*

    /// The areas the guest reports (p6w_memory_areas): data (a guest function), name, size and flags of each, in the order it gives them.
    internal (ulong Data, string Name, long Size, long Flags)[] Areas()
    {
        nint buffer = Marshal.AllocHGlobal(4 * 32);
        try
        {
            new Span<byte>((void*)buffer, 4 * 32).Fill(0xCD);
            nuint count = In(() => Guest<Values>("p6w_memory_areas")(buffer, 4));
            Program.Require(count <= 4, "the guest reports at most four areas");
            var areas = new (ulong, string, long, long)[(int)count];
            for (int i = 0; i < areas.Length; ++i)
            {
                byte* a = (byte*)buffer + 32 * i;
                nint name = *(nint*)(a + 8);                                  // a pointer into the guest: read it while the sandbox is active
                areas[i] = (*(ulong*)a, In(() => Marshal.PtrToStringUTF8(name)) ?? "", *(long*)(a + 16), *(long*)(a + 24));
            }
            Program.Require(new ReadOnlySpan<byte>((void*)(buffer + 32 * (int)count), (4 - (int)count) * 32).IndexOfAnyExcept((byte)0xCD) < 0,
                "the guest wrote nothing past the areas it reported");
            return areas;
        }
        finally { Marshal.FreeHGlobal(buffer); }
    }

    /// One access to the machine's memory by area type, through the guest (the code a BizHawk memory domain runs). A read returns the bytes the
    /// guest gave; a write sends `data`. The host buffer has guard bands, which the guest must leave alone.
    internal byte[] Access(uint type, long address, long count, byte[]? write = null, bool nullBuffer = false)
    {
        using var buffer = new Guarded((int)Math.Max(count, 1));
        buffer.Payload.Fill(0x77);
        if (write != null) write.CopyTo(buffer.Payload);
        In(() => { Guest<MemoryAccess>("p6w_memory_access")(type, nullBuffer ? 0 : buffer.Address, address, count, write != null ? 1u : 0u); return 0; });
        buffer.Check("a memory access buffer"); Canaries++;
        return buffer.Payload.ToArray();
    }
    internal ulong Refused() => In(() => Guest<Counter>("p6w_memory_refused")());

    /// p6w_media_export (T28, T29d): the medium as it is now, the base with the overlay of the guest's writes over it, into a host buffer of `capacity` bytes. Returns the result code and the bytes.
    internal (int Result, byte[] Image) ExportMedium(uint index, int capacity)
    {
        using var call = new Guarded(304);
        using var buffer = new Guarded(Math.Max(capacity, 1));
        call.Payload.Clear();
        buffer.Payload.Fill(0x77);
        byte* p = (byte*)call.Address;
        *(uint*)p = 1; *(uint*)(p + 4) = 304;                       // p6w_media_export: abi_version, struct_size
        *(uint*)(p + 8) = index;
        *(nint*)(p + 16) = buffer.Address; *(ulong*)(p + 24) = (ulong)capacity;
        int result = In(() => Guest<Init>("p6w_media_export")(call.Address));
        ulong written = *(ulong*)(p + 32);
        buffer.Check("the export buffer"); call.Check("p6w_media_export"); Canaries += 2;
        return (result, result == 0 ? buffer.Payload[..(int)written].ToArray() : []);
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

    /// The mmap heap of the sandbox for a memory configuration (T49): the budget of manifests/waterbox-memory.json plus twice the memory asked for above what the machine comes with
    /// (Chip and Slow above their own sizes, and all the Fast RAM). The same rule as Puae6Core.Heaps in the wrapper (tests/waterbox-state compares them), so a budget is at least
    /// twice what was measured.
    internal static nuint MmapBudget(nuint baseBytes, uint machineId, uint chipKib, uint slowKib, uint fastKib, bool floppyWrites = false, ulong hardFileKib = 0)
    {
        uint ownChip = machineId == 3 ? 2048u : machineId is 2 or 5 or 8 ? 1024u : 512u, ownSlow = machineId is 1 or 7 ? 512u : 0u;
        ulong extra = fastKib;
        if (chipKib > ownChip) extra += chipKib - ownChip;
        if (slowKib != uint.MaxValue && slowKib > ownSlow) extra += slowKib - ownSlow;
        if (floppyWrites) extra += 4096;        // T28: the write overlay (Puae6Core.FloppyOverlayKiB), measured in manifests/waterbox-memory.json
        extra += hardFileKib;                   // T29d: HardFileHeapKiB of every hard file, see below
        return baseBytes + (nuint)(2 * extra * 1024);
    }

    /// The mmap heap a hard file asks for, in KiB (T29d), the counterpart of MacHawk's HardDiskHeapKB: the blob store keeps the base (the size of the image) and, reserved beside it at load, the room for
    /// the overlay of the guest's writes (the size of the image again, 4 KiB chunks made on first write) and its dirty bitmap, so twice the image plus the bitmap and 64 KiB of the allocator's
    /// own; the budget then counts it twice like any extra memory (a budget is at least twice what was measured).
    internal static ulong HardFileHeapKiB(long bytes) => (ulong)(2 * ((bytes + 1023) / 1024) + (bytes / 4096 + 7) / 8 / 1024 + 64);

    // Smoke.dll --puae6 --runtime receipt.json --guest puae6-receipt.json --manifest waterbox-memory.json --firmware rom --units N --result out.json
    //   [--rewind-to V --rewind-from U] (T29d: after unit U the snapshot of unit V is loaded back in the same sandbox and units V+1.. run again)
    //   [--hdf image.hdf]... (T29d: hard files on the UAE controller, after the --media disks; the mmap heap grows with HardFileHeapKiB of each)  [--export-at U[,U...]] (after those units: the
    //   hard files as the guest has written them, "export U INDEX result=N bytes=B sha=...")
    //   [--media disk.adf]... [--port N:type:buttons:dx:dy]... [--render 101-200:0,...] [--digest-every K] [--bad-calls-at U] [--limits] [--ntsc]
    //   [--abi-at U,U...] (WB13 before those units)  [--pages-at U] (WB14 after unit U, needs the chipcell fixture)
    //   [--memory-at U] (T20a: the memory areas and the accesses through the guest; U = 0 right after the seal, U > 0 after that unit and
    //   needs the chipcell fixture)
    //   [--machine a500|a600|a1200|a500og|a500plus|a1000|a2000og|a2000] (the machine the guest is configured as; A500 by default)
    //   [--drives N] (T28: the first N media are mounted into DF0..DF{N-1} before the first unit, as the wrapper does)
    //   [--floppy-writes] (T28: p6_config.flags = P6_CONFIG_FLOPPY_WRITES; the disks are writable and the overlay is part of every snapshot)
    //   [--fpu 68881|68882] (T54: the FPU the guest is configured with, A1200 only)
    //   [--chip KIB] [--slow KIB|none] [--fast KIB] (T49: the memory the guest is configured with, 0 is the machine's own; the mmap heap grows with the extra memory, see MmapBudget)
    //   [--save-at U --state file] (WB15: snapshot after unit U)  [--restore file --from U] (a new process: init, seal, load, continue from U+1)
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
        uint chipKib = uint.Parse(options.Optional("chip") ?? "0"), fastKib = uint.Parse(options.Optional("fast") ?? "0");
        uint fpuModel = uint.Parse(options.Optional("fpu") ?? "0");
        string slowText = options.Optional("slow") ?? "0";
        uint slowKib = slowText == "none" ? uint.MaxValue : uint.Parse(slowText);
        var firmware = File.ReadAllBytes(options.Get("firmware"));
        var disks = options.All("media").Select(File.ReadAllBytes).ToArray();
        var hardFiles = options.All("hdf").Select(File.ReadAllBytes).ToArray();
        var media = disks.Concat(hardFiles).ToArray();
        var mediaTypes = disks.Select(_ => 1u).Concat(hardFiles.Select(_ => 2u)).ToArray();
        var exportAt = (options.Optional("export-at") ?? "").Split(',', StringSplitOptions.RemoveEmptyEntries).Select(ulong.Parse).ToHashSet();
        ulong units = ulong.Parse(options.Get("units"));
        uint standard = options.Flag("ntsc") ? 2u : 1u;
        string machineName = (options.Optional("machine") ?? "a500").ToLowerInvariant();
        Program.Require(machineName is "a500" or "a600" or "a1200" or "a500og" or "a500plus" or "a1000" or "a2000og" or "a2000",
            $"--machine is a500, a600, a1200, a500og, a500plus, a1000, a2000og or a2000 (got {machineName})");
        uint machineId = machineName switch { "a600" => 2u, "a1200" => 3u, "a500og" => 4u, "a500plus" => 5u, "a1000" => 6u, "a2000og" => 7u, "a2000" => 8u, _ => 1u };
        layout.Mmap = MmapBudget(layout.Mmap, machineId, chipKib, slowKib, fastKib, options.Flag("floppy-writes"), (ulong)hardFiles.Sum(file => (long)HardFileHeapKiB(file.Length)));
        var image = File.ReadAllBytes(guestPath);
        using var api = new Native(dylibPath);
        var report = new Dictionary<string, object?> {
            ["status"] = "PASS", ["evidence"] = "PUAE6_WBX_RUNTIME", ["units"] = units, ["machine"] = machineName.ToUpperInvariant(),
            ["guest_sha256"] = guest.GetProperty("sha256").GetString(), ["runtime_sha256"] = runtime.GetProperty("sha256").GetString(),
            ["layout"] = new Dictionary<string, ulong> { ["sbrk"] = layout.Sbrk, ["sealed"] = layout.Sealed, ["invisible"] = layout.Invisible,
                ["plain"] = layout.Plain, ["mmap"] = layout.Mmap },
            ["configured_memory"] = new Dictionary<string, uint> { ["chip_kib"] = chipKib, ["slow_kib"] = slowKib, ["fast_kib"] = fastKib },
            ["configured_fpu"] = fpuModel,
            ["firmware_bytes"] = firmware.Length, ["media_bytes"] = disks.Sum(image => (long)image.Length), ["hard_file_bytes"] = hardFiles.Sum(image => (long)image.Length),
            ["host_buffers_poisoned_after_init"] = true };

        if (options.Flag("limits")) report["limits"] = Limits(api, image, layout, firmware, standard, machineId);
        else report["exceptions"] = ExceptionProbe(api, image, layout);
        if (options.Flag("limits")) { File.WriteAllText(options.Get("result"), JsonSerializer.Serialize(report) + "\n"); return 0; }

        var schedule = options.All("render").SelectMany(list => list.Split(',', StringSplitOptions.RemoveEmptyEntries)).Select(item => {
            var parts = item.Split(':'); var span = parts[0].Split('-');
            return (ulong.Parse(span[0]), ulong.Parse(span[^1]), uint.Parse(parts[1]));
        }).ToList();
        using var machine = new Puae6Machine(api, image, layout) { MachineId = machineId, ChipKib = chipKib, SlowKib = slowKib, FastKib = fastKib, FpuModel = fpuModel,
            ConfigFlags = options.Flag("floppy-writes") ? 1u : 0u, MediaTypes = mediaTypes };
        foreach (var port in options.All("port"))
        {
            var p = port.Split(':');
            machine.Ports[int.Parse(p[0])] = (uint.Parse(p[1]), uint.Parse(p[2]), short.Parse(p[3]), short.Parse(p[4]));
        }
        var (initResult, initError) = machine.TryInit(firmware, media, standard, seed: 1, epoch: 946684800);
        if (initResult != 0) throw new InvalidOperationException($"p6w_init_machine failed ({initResult}): {initError}");
        for (uint drive = 0; drive < uint.Parse(options.Optional("drives") ?? "0"); ++drive) machine.MountBoot(drive, drive, drive + 1);
        var exports = new List<object>();
        void ExportHardFiles(string label)
        {
            for (int k = 0; k < hardFiles.Length; ++k)
            {
                uint index = (uint)(disks.Length + k);
                var (exportResult, exported) = machine.ExportMedium(index, hardFiles[k].Length);
                string sha = exportResult == 0 ? Convert.ToHexString(SHA256.HashData(exported)).ToLowerInvariant() : "-";
                Console.WriteLine($"export {label} {index} result={exportResult} bytes={exported.Length} sha={sha}");
                exports.Add(new { unit = label, index, result = exportResult, bytes = exported.Length, sha256 = sha });
                if (exportResult == 0 && options.Optional("export-dir") is string directory)
                    File.WriteAllBytes(Path.Combine(directory, $"hdf{k}-{label}.bin"), exported);
            }
        }
        // WB10 in the same sandbox (T29d): --rewind-to V --rewind-from U keeps the snapshot of unit V and, after unit U, exports, loads it, exports again and runs units V+1.. once more.
        ulong rewindTo = ulong.Parse(options.Optional("rewind-to") ?? "0"), rewindFrom = ulong.Parse(options.Optional("rewind-from") ?? "0");
        byte[]? rewindState = null;
        bool rewound = false;
        machine.Seal();
        var afterSeal = Usage(machine);
        var sealedSnapshot = machine.Save().Length;
        ulong digestEvery = ulong.Parse(options.Optional("digest-every") ?? "0");
        ulong badCallsAt = ulong.Parse(options.Optional("bad-calls-at") ?? "0");
        ulong pagesAt = ulong.Parse(options.Optional("pages-at") ?? "0"), saveAt = ulong.Parse(options.Optional("save-at") ?? "0");
        ulong? memoryAt = options.Optional("memory-at") is string memoryUnit ? ulong.Parse(memoryUnit) : null;
        var abiAt = (options.Optional("abi-at") ?? "").Split(',', StringSplitOptions.RemoveEmptyEntries).Select(ulong.Parse).ToHashSet();
        ulong from = 0;
        object? areas = null;
        if (memoryAt == 0) areas = MemoryTests(machine, 0, machineId, chipKib, slowKib, fastKib);               // sealed and not yet advanced: every page of the machine is still clean
        if (options.Optional("restore") is string restore)
        {
            from = ulong.Parse(options.Get("from"));
            machine.Load(File.ReadAllBytes(restore));          // a machine that was initialized and sealed like the one that saved, then given its state
            if (exportAt.Contains(from)) ExportHardFiles($"{from}");
        }
        var digests = new Dictionary<string, string>();
        var abi = new List<object>();
        ulong machineContext = 0;
        object? afterFirst = null, refusals = null, pages = null;
        for (ulong unit = from + 1; unit <= units; ++unit)
        {
            if (unit == badCallsAt) refusals = machine.Refusals();
            if (abiAt.Contains(unit))
            {
                var plain = machine.Abi(false); Program.CheckAbi(plain, false, $"puae6 before unit {unit}");
                var nested = machine.Abi(true); Program.CheckAbi(nested, true, $"puae6 re-entered before unit {unit}");
                Program.Require(machineContext == 0 || machineContext == plain.Out[1], "the sandbox keeps its x28 context for as long as it lives");
                machineContext = plain.Out[1];
                abi.Add(new { unit, x18 = plain.Out[0], x28 = plain.Out[1], nested_x28 = nested.Nested[0] });
            }
            Console.WriteLine(machine.Unit(unit, RenderFlags(schedule, unit)));
            if (machine.LastPcm != null) Console.WriteLine($"pcm {unit} {machine.LastPcm}");
            if (unit == from + 1) afterFirst = Usage(machine);
            if (digestEvery != 0 && unit % digestEvery == 0) digests[unit.ToString()] = machine.Digest();
            if (exportAt.Contains(unit)) ExportHardFiles(unit.ToString());
            if (rewindTo != 0 && unit == rewindTo) rewindState = machine.Save();
            if (unit == saveAt) File.WriteAllBytes(options.Get("state"), machine.Save());
            if (unit == pagesAt) pages = PageTests(machine, unit);
            if (unit == memoryAt) areas = MemoryTests(machine, unit, machineId, chipKib, slowKib, fastKib);
            if (rewindFrom != 0 && unit == rewindFrom && !rewound && rewindState != null)
            {
                rewound = true;
                ExportHardFiles($"{unit}-before-rewind");
                machine.Load(rewindState);
                ExportHardFiles($"{rewindTo}-after-rewind");
                unit = rewindTo;                            // the next unit is rewindTo + 1 again, from the machine the snapshot held
            }
        }
        if (abiAt.Count > 0)
        {
            using var other = new WaterboxSandbox(api, image, layout, "puae6-other.wbx");
            var call = Program.AbiCall(other, true, "p6w_probe_abi", "p6w_identity");
            Program.CheckAbi(call, true, "a second sandbox of the same guest");
            Program.Require(call.Out[1] != machineContext, "a second sandbox has its own x28 context");
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
        report["abi"] = abi;
        report["pages"] = pages;
        report["memory"] = areas;
        report["restored_from"] = from;
        report["exports"] = exports;
        report["ports"] = machine.Ports.Select(p => $"{p.Type}:{p.Buttons}:{p.Dx}:{p.Dy}").ToArray();
        File.WriteAllText(options.Get("result"), JsonSerializer.Serialize(report, new JsonSerializerOptions { WriteIndented = true }) + "\n");
        return 0;
    }

    /// WB14 on the real machine, alive: a group of 16 KiB host pages inside Chip RAM, which the fixture program does not use, takes writes at the
    /// first and last byte of each of its four logical pages; a snapshot taken before brings the content back whole, the four logical pages agree
    /// on their dirty state afterwards, and a second write after the restore is visible and restorable again. Then the word the fixture program
    /// copies into COLOR00 is overwritten: the next unit must show a different picture (the writes reach the machine) and, after the restore, the
    /// machine continues as if nothing had happened (the caller compares every unit with headless).
    static object PageTests(Puae6Machine machine, ulong lastUnit)
    {
        const ulong MarkerAt = 0x10000, CellAt = 0x30000, GroupAt = 0x50000;
        byte[] marker = [0x50, 0x36, 0x4D, 0x57];                      // "P6MW", stored by the fixture at $10000 as the 68000 has it: big endian
        ulong chip = machine.LocateChipRam(MarkerAt, marker), chipBytes = (ulong)machine.Areas()[0].Size;      // 512 KiB on the A500, 1 MiB on the A600, 2 MiB on the A1200
        var state = machine.Save();
        AudioMark mark = new(machine);

        // 1. A group of the machine that nothing has written since the seal (the engine allocated it and never touched it): the first write
        //    dirties the whole group, and a snapshot taken while it was clean restores it and lets the next write be seen again.
        ulong clean = FindCleanGroup(machine, chip, chipBytes);
        var cleanGroup = GroupTest(machine, clean, state, requireClean: true);
        // 2. A group inside Chip RAM, which a hard reset has written (so it is dirty from the first unit): same writes, same restores.
        ulong group = (chip + GroupAt + 16383) & ~16383UL;
        Program.Require(group % 16384 == 0 && group + 16384 <= chip + 0x80000, "the group is 16 KiB aligned and inside Chip RAM");
        var chipGroup = GroupTest(machine, group, state, requireClean: false);

        // 3. The writes reach the machine: COLOR00 takes the word at $30000 from then on.
        ulong cell = chip + CellAt;
        string quiet = machine.Unit(lastUnit + 1, 3);
        machine.Load(state);
        machine.PokeGuest(cell, 0x0F); machine.PokeGuest(cell + 1, 0x00);
        string poked = machine.Unit(lastUnit + 1, 3);
        Program.Require(Regex.Match(quiet, "hash=(\\w+)").Value != Regex.Match(poked, "hash=(\\w+)").Value, "a write into Chip RAM changes the next picture");
        machine.Load(state);
        mark.Restore();
        return new { chip_ram = chip, marker_at = MarkerAt, cell_at = CellAt, clean_group = clean, chip_group = group,
                     clean_group_pages = cleanGroup, chip_group_pages = chipGroup };
    }

    /// T20a: what a BizHawk memory domain needs from the guest. The areas are the machine's (Chip RAM primary, Slow RAM, the ROM: big endian,
    /// 16-bit words, function hooks that point into the sandbox); reads give what the 68000 stored; writes go through the guest, also into
    /// pages that nothing has written since the seal (a managed store there would be fatal); the ROM and everything out of range is refused
    /// and counted; a restore undoes the writes; and the word the fixture copies into COLOR00 changes the next picture.
    static object MemoryTests(Puae6Machine machine, ulong lastUnit, uint machineId, uint chipKib, uint slowKib, uint fastKib)
    {
        // What the machine has: its own memory unless the configuration says otherwise (0 = the machine's own, uint.MaxValue = no Slow RAM).
        long chipBytes = (chipKib != 0 ? chipKib : machineId == 3 ? 2048u : machineId == 2 ? 1024u : 512u) * 1024L;
        long slowBytes = slowKib == uint.MaxValue ? 0 : (slowKib != 0 ? slowKib : machineId == 1 ? 512u : 0u) * 1024L;
        long fastBytes = fastKib * 1024L;
        bool noSlow = slowBytes == 0;
        string machineName = machineId == 3 ? "A1200" : machineId == 2 ? "A600" : "A500";
        const long MarkerAt = 0x10000, CellAt = 0x30000;
        AudioMark mark = new(machine);
        var areas = machine.Areas();
        // Chip RAM first and primary, then Slow RAM and Fast RAM when the machine has them, then the ROM. The A500 comes with 512 KiB of Slow RAM, the A600 and the A1200 with none.
        var names = new List<string> { "Chip RAM" };
        var sizes = new List<long> { chipBytes };
        if (!noSlow) { names.Add("Slow RAM"); sizes.Add(slowBytes); }
        if (fastBytes != 0) { names.Add("Fast RAM"); sizes.Add(fastBytes); }
        names.Add("ROM");
        Program.Require(areas.Length == names.Count, $"the {machineName} reports {string.Join(", ", names)} (got {string.Join(", ", areas.Select(a => a.Name))})");
        for (int i = 0; i < names.Count; ++i)
        {
            var area = areas[i];
            Program.Require(area.Name == names[i], $"area {i} is {names[i]} (got {area.Name})");
            if (names[i] != "ROM") Program.Require(area.Size == sizes[i], $"{area.Name}: {sizes[i]} bytes (got {area.Size})");
            else Program.Require(area.Size >= 262144 && area.Size <= 524288, $"{area.Name}: a real size ({area.Size})");
            long expected = 16L | 64 | 1024 | (names[i] == "ROM" ? 0L : 1L) | (i == 0 ? 8L : 0L);            // big endian, 16-bit words, function hook, writable, primary
            Program.Require(area.Flags == expected, $"{area.Name}: flags {expected} (got {area.Flags})");
            Program.Require(area.Data >= machine.Ranges[0] && area.Data < machine.Ranges[14] + machine.Ranges[15], $"{area.Name}: the hook is a function of the sandbox");
        }
        Program.Require(areas.Count(a => (a.Flags & 8) != 0) == 1, "exactly one primary area");
        // The area the writes go to: the Fast RAM board when there is one (nothing writes it: its pages are clean since the seal), else Slow RAM, and on the A600 and the A1200, which have
        // none, the part of Chip RAM above 512 KiB (the fixtures use the first 320 KiB).
        uint scratch = fastBytes != 0 ? Puae6Machine.FastRam : noSlow ? Puae6Machine.ChipRam : Puae6Machine.SlowRam;
        int scratchName = fastBytes != 0 ? names.IndexOf("Fast RAM") : noSlow ? 0 : 1;
        long scratchBase = scratch == Puae6Machine.ChipRam ? 0x80000 : 0, scratchSize = areas[scratchName].Size - scratchBase;

        // Reads are the 68000's bytes: the fixture stored the long $50364D57 at $10000 (once it has run).
        if (lastUnit > 0)
            Program.Require(machine.Access(Puae6Machine.ChipRam, MarkerAt, 4).SequenceEqual(new byte[] { 0x50, 0x36, 0x4D, 0x57 }), "the marker reads big endian");
        var state = machine.Save();
        ulong refused = machine.Refused();
        var report = new List<object>();

        // Writes into every area: Slow RAM is untouched by the fixture (its pages are clean since the seal); the ROM refuses. What was there
        // before is kept for the restore below.
        long[] offsets = [scratchBase, scratchBase + 4095, scratchBase + 4096, scratchBase + scratchSize - 1];
        var wide = Enumerable.Range(0, 8192).Select(i => (byte)(i * 7)).ToArray();
        var wideBefore = machine.Access(scratch, scratchBase + 0x10000, wide.Length);
        int dirtyBefore = machine.DirtyMap().Count(dirty => dirty);
        var bytesBefore = offsets.Select(offset => machine.Access(scratch, offset, 1)[0]).ToArray();
        foreach (long offset in offsets)
        {
            machine.Access(scratch, offset, 1, [(byte)(0xE0 + offset % 16)]);
            Program.Require(machine.Access(scratch, offset, 1)[0] == (byte)(0xE0 + offset % 16), $"a write to {areas[scratchName].Name} at {offset} reads back");
        }
        machine.Access(scratch, scratchBase + 0x10000, wide.Length, wide);
        Program.Require(machine.Access(scratch, scratchBase + 0x10000, wide.Length).SequenceEqual(wide), "a bulk write to the scratch area reads back");
        Program.Require(!wide.SequenceEqual(wideBefore), "and it changed what was there");
        int dirtyAfter = machine.DirtyMap().Count(dirty => dirty);
        // Right after the seal nothing has written Slow RAM: the stores land in pages the runtime still protects, and a store from managed code
        // there would kill the process; these went through the guest and were recorded as its own (three 16 KiB groups at least).
        if (lastUnit == 0)
            Program.Require(dirtyAfter >= dirtyBefore + 12, $"the writes reached pages that were clean and the runtime tracked them ({dirtyBefore} -> {dirtyAfter} dirty pages)");
        Program.Require(machine.Refused() == refused, "valid accesses are not counted as refused");
        var rom = machine.Access(Puae6Machine.Rom, 0, 16);
        machine.Access(Puae6Machine.Rom, 0, 16, new byte[16]);
        Program.Require(machine.Access(Puae6Machine.Rom, 0, 16).SequenceEqual(rom), "a write to the ROM changes nothing");
        Program.Require(machine.Refused() == refused + 1, "and is counted as refused");
        refused = machine.Refused();

        // Everything out of range or malformed is refused, never partial: a read gives zeros, a write changes nothing.
        long slowSize = areas[scratchName].Size;           // the end of the area the writes went to
        var before = machine.Access(scratch, slowSize - 8, 8);
        machine.Access(scratch, slowSize - 4, 8, new byte[8]);                        // runs past the end
        Program.Require(machine.Access(scratch, slowSize - 8, 8).SequenceEqual(before), "a write past the end changed nothing, not even the part inside");
        Program.Require(machine.Access(scratch, slowSize - 4, 8).All(b => b == 0), "a read past the end gives zeros");
        Program.Require(machine.Access(scratch, slowSize, 1).All(b => b == 0), "a read at the end gives zeros");
        Program.Require(machine.Access(scratch, -1, 1).All(b => b == 0), "a negative address is refused");
        machine.Access(scratch, 0, -1);
        machine.Access(scratch, 0, 1, nullBuffer: true);
        machine.Access(99, 0, 1);
        Program.Require(machine.Refused() == refused + 7, $"seven refusals were counted (got {machine.Refused() - refused})");

        // A restore takes the writes back.
        machine.Load(state);
        Program.Require(machine.Access(scratch, scratchBase + 0x10000, wide.Length).SequenceEqual(wideBefore), "a restore brought the scratch area back");
        Program.Require(offsets.Select(offset => machine.Access(scratch, offset, 1)[0]).SequenceEqual(bytesBefore), "and the single bytes");

        // The write reaches the machine: COLOR00 takes the word at $30000 (big endian, $0F00 is red) and the next picture changes.
        if (lastUnit > 0)
        {
            string quiet = machine.Unit(lastUnit + 1, 3);
            machine.Load(state);
            machine.Access(Puae6Machine.ChipRam, CellAt, 2, [0x0F, 0x00]);
            string poked = machine.Unit(lastUnit + 1, 3);
            Program.Require(Regex.Match(quiet, "hash=(\\w+)").Value != Regex.Match(poked, "hash=(\\w+)").Value, "a write through the guest into Chip RAM changes the next picture");
            machine.Load(state);
        }
        mark.Restore();
        report.Add(new { areas = areas.Select(a => new { a.Name, a.Size, a.Flags }).ToArray(), dirty_pages_before_writes = dirtyBefore, dirty_pages_after_writes = dirtyAfter });
        return report;
    }

    /// A 16 KiB aligned group of four readable and writable logical pages in the mmap heap whose pages are all clean, away from Chip RAM.
    static ulong FindCleanGroup(Puae6Machine machine, ulong chip, ulong chipBytes)
    {
        int mmap = Array.IndexOf(machine.HeapNames, "mmap");
        ulong first = (machine.Ranges[2 * mmap] - machine.Ranges[0]) / 4096, count = machine.Ranges[2 * mmap + 1] / 4096;
        for (ulong page = first; page + 4 <= first + count; ++page)
        {
            ulong address = machine.Ranges[0] + page * 4096;
            if (address % 16384 != 0 || (address + 16384 > chip && address < chip + chipBytes)) continue;
            bool clean = true;
            for (int i = 0; i < 4 && clean; ++i) clean = (machine.PageInfo((int)page + i) & 0xC3) == 3;   // allocated RW, visible, not dirty
            if (clean) return address;
        }
        throw new InvalidOperationException("FAIL: no clean 16 KiB group in the mmap heap");
    }

    /// The smoke's WB14 sequence on any group of the real guest: write the first and last byte of each logical page, see the whole group dirty
    /// and nothing else change, restore the snapshot taken before, see all four logical pages agree, write again, restore again.
    static List<object> GroupTest(Puae6Machine machine, ulong group, byte[] state, bool requireClean)
    {
        int first = (int)((group - machine.Ranges[0]) / 4096);
        bool[] Group(bool[] map) => map.Skip(first).Take(4).ToArray();
        bool[] Outside(bool[] map) => map.Where((_, i) => i < first || i >= first + 4).ToArray();
        var results = new List<object>();
        var original = machine.ReadGuest(group, 16384);
        for (int page = 0; page < 4; ++page)
        {
            var before = machine.DirtyMap();
            if (requireClean) Program.Require(!Group(before).Any(x => x), $"page {page}: the group starts clean");
            byte value = (byte)(0xE0 + page);
            foreach (ulong offset in new ulong[] { 0, 4095 }) machine.PokeGuest(group + (ulong)page * 4096 + offset, value);
            var written = machine.DirtyMap();
            Program.Require(Group(written).All(x => x), $"page {page}: one write marks the whole 16 KiB group dirty");
            Program.Require(Outside(before).SequenceEqual(Outside(written)), $"page {page}: no page outside the group changed state");
            var mutated = machine.ReadGuest(group, 16384);
            for (int i = 0; i < 16384; ++i)
            {
                bool touched = i / 4096 == page && (i % 4096 == 0 || i % 4096 == 4095);
                Program.Require(mutated[i] == (touched ? value : original[i]), $"page {page}: only the two written bytes changed (offset {i})");
            }
            machine.Load(state);
            Program.Require(machine.ReadGuest(group, 16384).SequenceEqual(original), $"page {page}: all four logical pages came back");
            var restored = Group(machine.DirtyMap());
            Program.Require(restored.All(x => x == restored[0]), $"page {page}: the four logical pages agree on their dirty state after a restore");
            int next = (page + 1) % 4;
            machine.PokeGuest(group + (ulong)next * 4096 + 17, 0x5A);
            Program.Require(machine.ReadGuest(group, 16384)[next * 4096 + 17] == 0x5A, $"page {page}: a write after the restore is visible");
            machine.Load(state);
            Program.Require(machine.ReadGuest(group, 16384).SequenceEqual(original), $"page {page}: a second restore also brings all four back");
            results.Add(new { page, dirty_before = Group(before), dirty_after_write = Group(written), dirty_after_restore = restored });
        }
        return results;
    }

    /// The running totals of the audio counters, which the control units of a test must not leave changed.
    sealed class AudioMark
    {
        readonly Puae6Machine machine; readonly ulong frames, hash;
        internal AudioMark(Puae6Machine m) { machine = m; frames = m.AudioFrames; hash = m.AudioHash; }
        internal void Restore() { machine.AudioFrames = frames; machine.AudioHash = hash; }
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
    static object Limits(Native api, byte[] image, Layout layout, byte[] firmware, uint standard, uint machineId)
    {
        var outcomes = new List<object>();
        using var machine = new Puae6Machine(api, image, layout) { MachineId = machineId };
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
