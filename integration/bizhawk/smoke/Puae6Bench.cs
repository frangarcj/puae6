using System.Diagnostics;
using System.Runtime.InteropServices;
using System.Security.Cryptography;
using System.Text.Json;

/// T33 (tools/bench.py): the benchmark of puae6.wbx in a real Waterbox sandbox, at the level of the guest ABI. The clock is read here, in the host, around the pieces; it never reaches the guest (the
/// machine does not know how long anything took). Every unit is timed in four parts: entering the sandbox (Activate), the p6w_frame_advance call (the engine and the guest's ABI layer), what the wrapper
/// does with the result (the copy of the picture into a managed int[] with the alpha set, the PCM into a short[]) and leaving it (Deactivate). Then snapshots (Save/Load of the runtime, the first unit after
/// a load) and the page table (committed and dirty bytes of each heap; on Darwin ARM64 the dirty unit is a 16 KiB group of four 4 KiB logical pages, see DirtyGroups).
///   Smoke.dll --puae6-bench --runtime receipt.json --guest puae6-receipt.json --manifest waterbox-memory.json --firmware rom --units N --result out.json
///             [--warmup W] [--machine a500|a600|a1200|...] [--ntsc] [--media disk.adf] [--evict 0|1] [--render 0..3] [--state-rounds R]
/// --evict 1 is the runtime's own default (the sandbox is unmapped when it is deactivated); --evict 0 is what a Release BizHawk sets (WaterboxHost's static constructor), and the one a user gets.
internal static unsafe class Puae6Bench
{
    [DllImport("libSystem.B.dylib")] static extern ulong clock_gettime_nsec_np(int clockId);
    const int ClockThreadCpuTime = 16;

    static double Ns(long ticks) => ticks * 1e9 / Stopwatch.Frequency;

    static (ulong Committed, ulong Dirty, ulong Groups, ulong GroupsWhole) Pages(Puae6Machine machine)
    {
        var usage = machine.Usage();
        ulong committed = 0, dirty = 0;
        foreach (var heap in usage) { committed += heap.Committed; dirty += heap.Dirty; }
        var map = machine.DirtyMap();
        ulong groups = 0, whole = 0;
        for (int first = 0; first < map.Length; first += 4)
        {
            int count = 0;
            for (int i = first; i < Math.Min(first + 4, map.Length); ++i) if (map[i]) ++count;
            if (count > 0) ++groups;
            if (count == 4) ++whole;
        }
        return (committed, dirty, groups, whole);
    }

    static object HeapUsage(Puae6Machine machine)
    {
        var usage = machine.Usage();
        return machine.HeapNames.Select((name, i) => (name, i)).ToDictionary(x => x.name,
            x => new { committed_bytes = usage[x.i].Committed, dirty_bytes = usage[x.i].Dirty });
    }

    internal static int Run(string[] args)
    {
        var options = new Dictionary<string, List<string>>();
        for (int i = 1; i < args.Length; ++i)
        {
            Program.Require(args[i].StartsWith("--"), $"option expected, found {args[i]}");
            string key = args[i][2..];
            string value = i + 1 < args.Length && !args[i + 1].StartsWith("--") ? args[++i] : "true";
            if (!options.TryGetValue(key, out var list)) options[key] = list = new();
            list.Add(value);
        }
        string Get(string key) => options.TryGetValue(key, out var l) ? l[^1] : throw new ArgumentException($"missing --{key}");
        string? Optional(string key) => options.TryGetValue(key, out var l) ? l[^1] : null;

        var runtime = JsonDocument.Parse(File.ReadAllText(Get("runtime"))).RootElement.GetProperty("runtime_dylib");
        var guest = JsonDocument.Parse(File.ReadAllText(Get("guest"))).RootElement.GetProperty("guest");
        string dylibPath = runtime.GetProperty("path").GetString()!, guestPath = guest.GetProperty("path").GetString()!;
        Program.Require(Convert.ToHexString(SHA256.HashData(File.ReadAllBytes(dylibPath))).ToLowerInvariant() == runtime.GetProperty("sha256").GetString(), "runtime artifact identity");
        Program.Require(Convert.ToHexString(SHA256.HashData(File.ReadAllBytes(guestPath))).ToLowerInvariant() == guest.GetProperty("sha256").GetString(), "guest artifact identity");
        var memory = JsonDocument.Parse(File.ReadAllText(Get("manifest"))).RootElement.GetProperty("layout");
        Layout layout = new() { Sbrk = memory.GetProperty("sbrk").GetUInt32(), Sealed = memory.GetProperty("sealed").GetUInt32(),
            Invisible = memory.GetProperty("invisible").GetUInt32(), Plain = memory.GetProperty("plain").GetUInt32(), Mmap = memory.GetProperty("mmap").GetUInt32() };
        string machineName = (Optional("machine") ?? "a500").ToLowerInvariant();
        uint machineId = machineName switch { "a600" => 2u, "a1200" => 3u, "a500og" => 4u, "a500plus" => 5u, "a1000" => 6u, "a2000og" => 7u, "a2000" => 8u, "a500" => 1u,
            _ => throw new ArgumentException($"unknown --machine {machineName}") };
        layout.Mmap = Puae6.MmapBudget(layout.Mmap, machineId, 0, 0, 0);
        var firmware = File.ReadAllBytes(Get("firmware"));
        var disks = (options.TryGetValue("media", out var media) ? media : []).Select(File.ReadAllBytes).ToArray();
        ulong units = ulong.Parse(Get("units")), warmup = ulong.Parse(Optional("warmup") ?? "300"), rounds = ulong.Parse(Optional("state-rounds") ?? "20");
        uint render = uint.Parse(Optional("render") ?? "3"), standard = options.ContainsKey("ntsc") ? 2u : 1u;
        bool evict = (Optional("evict") ?? "1") != "0";
        var image = File.ReadAllBytes(guestPath);
        using var api = new Native(dylibPath);
        api.SetAlwaysEvict(evict);
        using var machine = new Puae6Machine(api, image, layout) { MachineId = machineId, MediaTypes = disks.Select(_ => 1u).ToArray() };
        var (initResult, initError) = machine.TryInit(firmware, disks, standard, seed: 1, epoch: 946684800);
        if (initResult != 0) throw new InvalidOperationException($"p6w_init_machine failed ({initResult}): {initError}");
        if (disks.Length > 0) machine.MountBoot(0, 0, 1);
        machine.Seal();
        var afterSeal = Pages(machine);

        var videoBuffer = new int[768 * 580];
        var soundBuffer = new short[4096];
        uint width = 0, height = 0;
        ulong audio = 0;
        var warm = new List<double>();
        for (ulong u = 0; u < warmup; ++u)
        {
            var t = machine.BenchUnit(render, videoBuffer, soundBuffer);
            warm.Add(Ns(t.Enter + t.Guest + t.Copy + t.Exit));
        }
        var afterWarmup = Pages(machine);
        var enter = new List<long>(); var guestCall = new List<long>(); var copy = new List<long>(); var exit = new List<long>(); var total = new List<long>(); var cpu = new List<long>();
        long ticksLast = 0;
        for (ulong u = 0; u < units; ++u)
        {
            ulong c0 = clock_gettime_nsec_np(ClockThreadCpuTime);
            long w0 = Stopwatch.GetTimestamp();
            var t = machine.BenchUnit(render, videoBuffer, soundBuffer);
            long w1 = Stopwatch.GetTimestamp();
            ulong c1 = clock_gettime_nsec_np(ClockThreadCpuTime);
            total.Add((long)Ns(w1 - w0)); cpu.Add((long)(c1 - c0));
            enter.Add((long)Ns(t.Enter)); guestCall.Add((long)Ns(t.Guest)); copy.Add((long)Ns(t.Copy)); exit.Add((long)Ns(t.Exit));
            width = t.Width; height = t.Height; audio += t.AudioFrames; ticksLast = (long)t.Ticks;
        }
        var afterUnits = Pages(machine);

        // Snapshots of the runtime, then the first unit after a load.
        var save = new List<long>(); var load = new List<long>(); var firstAfter = new List<long>();
        long snapshotBytes = 0;
        for (ulong r = 0; r < rounds; ++r)
        {
            long s0 = Stopwatch.GetTimestamp();
            var state = machine.Save();
            save.Add((long)Ns(Stopwatch.GetTimestamp() - s0));
            snapshotBytes = state.Length;
            machine.BenchUnit(render, videoBuffer, soundBuffer);
            long l0 = Stopwatch.GetTimestamp();
            machine.Load(state);
            load.Add((long)Ns(Stopwatch.GetTimestamp() - l0));
            var t = machine.BenchUnit(render, videoBuffer, soundBuffer);
            firstAfter.Add((long)Ns(t.Enter + t.Guest + t.Copy + t.Exit));
        }
        var afterState = Pages(machine);
        var rusage = Process.GetCurrentProcess();
        object PagesJson((ulong Committed, ulong Dirty, ulong Groups, ulong GroupsWhole) p) =>
            new { committed_bytes = p.Committed, dirty_bytes_4k_pages = p.Dirty, dirty_groups_16k = p.Groups, dirty_groups_16k_all_four_logical_pages = p.GroupsWhole };
        var report = new Dictionary<string, object?> {
            ["tool"] = "Smoke.dll --puae6-bench", ["machine"] = machineName.ToUpperInvariant(), ["video_standard"] = standard, ["render"] = render, ["evict_blocks"] = evict,
            ["warmup_units"] = warmup, ["units"] = units, ["warmup_median_ns"] = warm.Count == 0 ? 0 : warm.OrderBy(v => v).ElementAt(warm.Count / 2), ["warmup_first_ns"] = warm.Count == 0 ? 0 : warm[0],
            ["guest_sha256"] = guest.GetProperty("sha256").GetString(), ["runtime_sha256"] = runtime.GetProperty("sha256").GetString(),
            ["firmware_bytes"] = firmware.Length, ["media_bytes"] = disks.Sum(d => (long)d.Length), ["layout"] = new Dictionary<string, ulong> { ["sbrk"] = layout.Sbrk, ["sealed"] = layout.Sealed, ["invisible"] = layout.Invisible, ["plain"] = layout.Plain, ["mmap"] = layout.Mmap },
            ["active"] = new[] { width, height }, ["audio_frames"] = audio, ["master_ticks_at_window_end"] = ticksLast, ["unit_index_at_window_end"] = warmup + units, ["host_page_bytes"] = 16384, ["logical_page_bytes"] = 4096,
            ["pages_after_seal"] = PagesJson(afterSeal), ["pages_after_warmup"] = PagesJson(afterWarmup), ["pages_after_units"] = PagesJson(afterUnits), ["pages_after_state_rounds"] = PagesJson(afterState),
            ["heaps_after_units"] = HeapUsage(machine),
            ["snapshot_bytes"] = snapshotBytes, ["process_peak_working_set"] = rusage.PeakWorkingSet64, ["process_working_set"] = rusage.WorkingSet64,
            ["unit_total_ns"] = total, ["unit_cpu_ns"] = cpu, ["unit_enter_ns"] = enter, ["unit_guest_ns"] = guestCall, ["unit_copy_ns"] = copy, ["unit_exit_ns"] = exit,
            ["state_save_ns"] = save, ["state_load_ns"] = load, ["first_unit_after_load_ns"] = firstAfter,
        };
        File.WriteAllText(Get("result"), JsonSerializer.Serialize(report) + "\n");
        Console.WriteLine($"bench done units={units} evict={(evict ? 1 : 0)}");
        return 0;
    }
}
