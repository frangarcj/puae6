// T33 (tools/bench.py): the benchmark of the PUAE6 core as BizHawk drives it, in the same host the FFI tests use. Where integration/bizhawk/smoke/Puae6Bench.cs times the pieces of one unit at the guest ABI,
// this times what a frontend sees: Puae6Core.FrameAdvance (input packet, Activate, p6w_frame_advance, the copy of the picture to BizHawk's int[] and of the PCM, Deactivate) and the whole state
// (SaveStateBinary / LoadStateBinary: the envelope and its checksum around the sandbox's snapshot). The clock is read here, around the calls; the core never sees it.
//   Ffi.dll bench --runtime libwaterboxhost.dylib --guest puae6.wbx --sha HASH --firmware ROM --units N --out FILE [--warmup W] [--machine a500] [--cpu auto|68000|68010|68020] [--cpu-compatibility exact|compatible|normal] [--ntsc 1] [--adf DISK] [--state-rounds R] [--render 1|0]
// Managed allocation per unit comes from GC.GetAllocatedBytesForCurrentThread (the allocator of the .NET side; the native allocations of the engine are not visible from here).
using System.Diagnostics;
using System.Runtime.InteropServices;
using System.Text;
using System.Text.Json;

using Puae6;

internal static unsafe partial class Program
{
	[DllImport("libSystem.B.dylib")] static extern ulong clock_gettime_nsec_np(int clockId);

	static void Bench(Dictionary<string, string> options)
	{
		string cpuName = options.GetValueOrDefault("cpu", "auto"), accuracy = options.GetValueOrDefault("cpu-compatibility", "exact");
		var cpuModel = cpuName switch { "auto" => Puae6Cpu.Auto, "68000" => Puae6Cpu.M68000, "68010" => Puae6Cpu.M68010, "68020" => Puae6Cpu.M68020,
			_ => throw new ArgumentException($"invalid --cpu {cpuName}") };
		var cpuAccuracy = accuracy switch { "exact" => Puae6CpuCompatibility.Exact, "compatible" => Puae6CpuCompatibility.Compatible, "normal" => Puae6CpuCompatibility.Normal,
			_ => throw new ArgumentException($"invalid --cpu-compatibility {accuracy}") };
		LoadRuntime(options["runtime"]);        // eviction off unless P6_EVICT=1 (Runtime.cs)
		ulong units = ulong.Parse(options["units"]), warmup = ulong.Parse(options.GetValueOrDefault("warmup", "300")), rounds = ulong.Parse(options.GetValueOrDefault("state-rounds", "20"));
		bool render = options.GetValueOrDefault("render", "1") != "0";
		var settings = new Puae6SyncSettings
		{
			CpuModel = cpuModel, CpuCompatibility = cpuAccuracy,
			Machine = MachineOf(options.GetValueOrDefault("machine")),
			Region = options.ContainsKey("ntsc") ? Puae6Region.NTSC : Puae6Region.PAL,
		};
		var adfs = options.TryGetValue("adf", out var adf) ? new List<byte[]> { File.ReadAllBytes(adf) } : new List<byte[]>();
		long setup0 = Stopwatch.GetTimestamp();
		using var core = MakeCore(options, settings, adfs);
		double setupMs = (Stopwatch.GetTimestamp() - setup0) * 1000.0 / Stopwatch.Frequency;
		core.ControllerDefinition.BuildMnemonicsCache("Amiga");
		var controller = new ScriptedController(core.ControllerDefinition);
		controller.Clear();

		var warm = new List<long>();
		for (ulong u = 0; u < warmup; ++u)
		{
			long w = Stopwatch.GetTimestamp();
			core.FrameAdvance(controller, render, render);
			core.GetVideoBuffer();
			core.GetSamplesSync(out _, out _);
			warm.Add(Stopwatch.GetTimestamp() - w);
		}
		var wall = new List<long>(); var cpu = new List<long>();
		int gc0 = GC.CollectionCount(0), gc1 = GC.CollectionCount(1), gc2 = GC.CollectionCount(2);
		long allocated0 = GC.GetAllocatedBytesForCurrentThread();
		for (ulong u = 0; u < units; ++u)
		{
			ulong c0 = clock_gettime_nsec_np(16);                 // CLOCK_THREAD_CPUTIME_ID
			long w0 = Stopwatch.GetTimestamp();
			core.FrameAdvance(controller, render, render);
			core.GetVideoBuffer();
			core.GetSamplesSync(out _, out _);
			long w1 = Stopwatch.GetTimestamp();
			ulong c1 = clock_gettime_nsec_np(16);
			wall.Add((long)((w1 - w0) * 1e9 / Stopwatch.Frequency)); cpu.Add((long)(c1 - c0));
		}
		long allocated = GC.GetAllocatedBytesForCurrentThread() - allocated0;
		int frameAtEnd = core.Frame;
		long ticksAtEnd = core.CycleCount;
		int collections0 = GC.CollectionCount(0) - gc0, collections1 = GC.CollectionCount(1) - gc1, collections2 = GC.CollectionCount(2) - gc2;

		var save = new List<long>(); var load = new List<long>(); var firstAfter = new List<long>();
		long stateBytes = 0;
		for (ulong r = 0; r < rounds; ++r)
		{
			long s0 = Stopwatch.GetTimestamp();
			byte[] state;
			using (var stream = new MemoryStream())
			{
				using (var writer = new BinaryWriter(stream, Encoding.UTF8, true)) core.SaveStateBinary(writer);
				state = stream.ToArray();
			}
			save.Add((long)((Stopwatch.GetTimestamp() - s0) * 1e9 / Stopwatch.Frequency));
			stateBytes = state.Length;
			core.FrameAdvance(controller, render, render);
			long l0 = Stopwatch.GetTimestamp();
			using (var stream = new MemoryStream(state, false))
			using (var reader = new BinaryReader(stream)) core.LoadStateBinary(reader);
			load.Add((long)((Stopwatch.GetTimestamp() - l0) * 1e9 / Stopwatch.Frequency));
			long a0 = Stopwatch.GetTimestamp();
			core.FrameAdvance(controller, render, render);
			firstAfter.Add((long)((Stopwatch.GetTimestamp() - a0) * 1e9 / Stopwatch.Frequency));
		}
		var report = new Dictionary<string, object?>
		{
			["tool"] = "Ffi.dll bench", ["cpu_model"] = cpuName, ["cpu_compatibility"] = accuracy, ["machine"] = settings.Machine.ToString(), ["region"] = settings.Region.ToString(), ["render"] = render, ["evict_blocks_forced_off"] = Environment.GetEnvironmentVariable("P6_EVICT") != "1",
			["managed_build"] = typeof(Program).Assembly.GetCustomAttributes(typeof(DebuggableAttribute), false).Cast<DebuggableAttribute>().Any(a => a.IsJITOptimizerDisabled) ? "Debug (JIT optimizer disabled)" : "Release",
			["warmup_units"] = warmup, ["units"] = units, ["unit_index_at_window_end"] = frameAtEnd, ["master_ticks_at_window_end"] = ticksAtEnd, ["core_create_ms"] = setupMs, ["state_bytes"] = stateBytes,
			["warmup_median_ns"] = warm.Count == 0 ? 0.0 : warm.OrderBy(v => v).ElementAt(warm.Count / 2) * 1e9 / Stopwatch.Frequency, ["warmup_first_ns"] = warm.Count == 0 ? 0.0 : warm[0] * 1e9 / Stopwatch.Frequency,
			["managed_allocated_bytes_in_window"] = allocated, ["gc_collections_in_window"] = new[] { collections0, collections1, collections2 },
			["process_working_set"] = Process.GetCurrentProcess().WorkingSet64,
			["unit_wall_ns"] = wall, ["unit_cpu_ns"] = cpu, ["state_save_ns"] = save, ["state_load_ns"] = load, ["first_unit_after_load_ns"] = firstAfter,
		};
		File.WriteAllText(options["out"], JsonSerializer.Serialize(report) + "\n");
		Console.WriteLine($"ok bench units={units}");
	}
}
