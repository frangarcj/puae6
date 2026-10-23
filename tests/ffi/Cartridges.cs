using System.Buffers.Binary;
using System.Runtime.InteropServices;
using System.Security.Cryptography;
using Puae6;

internal static unsafe partial class Program
{
	sealed class CartridgeSandbox : IDisposable
	{
		internal readonly Puae6Guest Guest;
		readonly byte[] firmware, cartridge, legacyFirmware;
		readonly uint model;
		internal CartridgeSandbox(Dictionary<string, string> options)
		{
			model = uint.Parse(options["model"]); firmware = File.ReadAllBytes(options["firmware"]); cartridge = File.ReadAllBytes(options["cartridge"]);
			legacyFirmware = File.ReadAllBytes(options["legacy"]);
			var path = Path.GetFullPath(options["guest"]);
			Guest = Puae6Guest.Open(Path.GetDirectoryName(path)!, Path.GetFileName(path), options["sha"], Puae6Core.Heaps());
		}
		internal int Init(int expected = 0, uint? selected = null, bool missing = false, int trim = 0, uint abi = 1, uint? machine = null, bool badHeader = false, bool legacy = false, bool shortArgs = false)
		{
			using var args = new Guarded(sizeof(P6wInit));
			var c = new P6Config { AbiVersion = 1, StructSize = (uint)sizeof(P6Config), Machine = machine ?? (model == 1200 ? 3u : 1u),
				VideoStandard = 1, AudioRateHz = 48000, RandomSeed = 1, RtcEpochSeconds = 946684800 };
			var copy = (byte[])cartridge.Clone(); if (badHeader) Array.Clear(copy);
			var boot = legacy ? legacyFirmware : firmware;
			fixed (byte* fw = boot, raw = copy)
			using (Guest.Host.EnterExit())
			{
				*(P6wInit*)args.Address = new P6wInit { AbiVersion = abi, StructSize = (uint)sizeof(P6wInit) - (shortArgs ? 4u : 0u), Config = c, Firmware = fw, FirmwareBytes = (ulong)boot.Length };
				var before = args.Payload.ToArray();
				int result = legacy ? Guest.Lib.p6w_init_machine((IntPtr)args.Address) : Guest.Lib.p6w_init_machine_cartridge((IntPtr)args.Address, selected ?? model, missing ? IntPtr.Zero : (IntPtr)raw, (ulong)(copy.Length + trim));
				var got = (P6wInit*)args.Address; int end = (int)Marshal.OffsetOf<P6wInit>(nameof(P6wInit.Result));
				Require(result == expected && got->Result == expected, $"init {selected ?? model}: expected {expected}, got {result}: {Marshal.PtrToStringUTF8((IntPtr)got->Error)}");
				Require(args.BandsIntact() && args.Payload[..end].SequenceEqual(before.AsSpan(0, end)) && got->Reserved1 == 0, "init writes only result/error; guards intact");
				if (expected != 0) Require(got->Error[0] != 0, "refusal explains the error");
				Require(copy.AsSpan().SequenceEqual(badHeader ? new byte[copy.Length] : cartridge), "caller ROM remains read-only");
				Array.Fill(copy, (byte)0xA5); // No caller ROM survives the call; subsequent guest execution must use the binder's copy.
				return result;
			}
		}
		internal void NoMachine()
		{
			using var areas = new Guarded(4 * sizeof(P6wMemoryArea)); using var active = Guest.Host.EnterExit();
			Require(Guest.Lib.p6w_memory_areas((IntPtr)areas.Address, 4) == 0 && areas.BandsIntact(), "cleanup leaves no memory areas");
		}
		internal void Memory(int offset, Span<byte> bytes, bool write = false)
		{
			fixed (byte* p = bytes) using (Guest.Host.EnterExit()) Guest.Lib.p6w_memory_access(1, (IntPtr)p, offset, bytes.Length, write ? 1u : 0u);
		}
		internal void Signal(int offset, byte value) { Span<byte> bytes = stackalloc byte[] { value }; Memory(offset, bytes, true); }
		internal byte[] Markers() { var bytes = new byte[48]; Memory(0x11000, bytes); return bytes; }
		internal uint Counter() => BinaryPrimitives.ReadUInt32BigEndian(Markers().AsSpan(32, 4));
		internal string Step(uint command = 0, bool v1 = false)
		{
			using var input = new Guarded(sizeof(P6InputV2)); using var output = new Guarded(sizeof(P6wFrame));
			var packet = new P6InputV2 { Base = new P6Input { AbiVersion = v1 ? 1u : 2u, StructSize = (uint)(v1 ? sizeof(P6Input) : sizeof(P6InputV2)) }, Commands = command };
			*(P6InputV2*)input.Address = packet; var before = input.Payload.ToArray();
			*(P6wFrame*)output.Address = new P6wFrame { AbiVersion = 1, StructSize = (uint)sizeof(P6wFrame) };
			using (Guest.Host.EnterExit())
			{
				Require(Guest.Lib.p6w_frame_advance((IntPtr)input.Address, 3, (IntPtr)output.Address) == 0, "cartridge frame");
				var f = ((P6wFrame*)output.Address)->Frame;
				string Hash(byte* p, int n) => Convert.ToHexString(SHA256.HashData(new ReadOnlySpan<byte>(p, n)));
				Require(input.Payload.SequenceEqual(before) && input.BandsIntact() && output.BandsIntact(), "frame guards and input unchanged");
				return $"{f.UnitIndex} {f.MasterTicks} {f.VideoWidth} {f.VideoHeight} {f.Active.X} {f.Active.Y} {f.Active.Width} {f.Active.Height} {f.Flags} {f.CadenceNum}/{f.CadenceDen} {f.FieldParity} {Hash((byte*)f.Video, checked((int)f.VideoBytes))} {f.AudioFrames} {Hash((byte*)f.Audio, checked((int)f.AudioFrames * 4))} {Convert.ToHexString(Markers())}";
			}
		}
		internal byte[] Save()
		{
			using var stream = new MemoryStream(); using var writer = new BinaryWriter(stream);
			using var active = Guest.Host.EnterExit(); Guest.Host.SaveStateBinary(writer); return stream.ToArray();
		}
		internal void Load(byte[] state) { using var reader = new BinaryReader(new MemoryStream(state, false)); using var active = Guest.Host.EnterExit(); Guest.Host.LoadStateBinary(reader); }
		internal void Usage()
		{
			var ranges = new ulong[16]; using var held = Guest.Host.EnterExit();
			fixed (ulong* p = ranges) Require(Guest.Lib.p6w_memory_info((IntPtr)p, 16) == 16, "heap ranges");
			var pages = Guest.Host.GetPagesDomain();
			foreach (int n in new[] { 3, 4, 5, 6, 7 })
			{
				ulong used = 0; long first = (long)((ranges[n * 2] - ranges[0]) / 4096);
				for (long i = 0; i < (long)(ranges[n * 2 + 1] / 4096); ++i) if ((pages.PeekByte(first + i) & 0x3f) != 0) used += 4096;
				Require(n is 5 or 6 ? used == 0 : used * 2 <= ranges[n * 2 + 1], $"heap {n}: committed={used} budget={ranges[n * 2 + 1]}, at least half free");
			}
		}
		public void Dispose() => Guest.Dispose();
	}
	static void Cartridges(Dictionary<string, string> options)
	{
		LoadRuntime(options["runtime"]); bool writing = options["mode"] == "write"; uint model = uint.Parse(options["model"]);
		if (writing)
		{
			using var rejected = new CartridgeSandbox(options);
			using (rejected.Guest.Host.EnterExit()) Require(rejected.Guest.Lib.p6w_init_machine_cartridge(IntPtr.Zero, model, IntPtr.Zero, 0) == P6.BadArgument, "null init");
			rejected.Init(P6.BadArgument, abi: 2); rejected.Init(P6.BadArgument, shortArgs: true); rejected.Init(P6.BadArgument, selected: 0, machine: 1); rejected.Init(P6.BadArgument, selected: 1201, machine: 1);
			rejected.Init(P6.WrongMedia, missing: true); rejected.Init(P6.WrongMedia, trim: -1); rejected.Init(P6.WrongMedia, trim: 1);
			rejected.Init(P6.Unsupported, machine: model == 1200 ? 1u : 3u);
			rejected.Init(P6.WrongMedia, badHeader: true);
			rejected.NoMachine();
			rejected.Init(legacy: true); rejected.Step();
			using (rejected.Guest.Host.EnterExit()) Require(rejected.Guest.Lib.p6w_shutdown() == 0, "legacy init/frame/cleanup after failed bind");
		}
		using var owner = new CartridgeSandbox(options); owner.Init(); owner.Guest.Seal(); if (writing) owner.Init(P6.BadState);
		if (writing)
		{
			owner.Step();
			if (model == 1) { owner.Signal(0x11010, 1); owner.Step(); }
			else { owner.Signal(0x1100c, 1); owner.Step(); }
			owner.Signal(0x1100c, 0);
			if (model is 2 or 3) Require(owner.Counter() == 1, "reset chipwrite entry completed before the Freeze cut");
			owner.Usage();
		}
		for (int cut = 0; cut < 2; ++cut)
		{
			string path = Path.Combine(options["states"], $"{model}-{cut}.state");
			if (writing && cut == 1) { owner.Step(1); Require(owner.Counter() == (model is 2 or 3 ? 2u : 1u) && owner.Markers()[37] == 1, "ACTIVE waits in cartridge before RTE"); }
			var state = writing ? owner.Save() : File.ReadAllBytes(path); if (writing) File.WriteAllBytes(path, state); else owner.Load(state);
			string Future()
			{
				var lines = new List<string>(); uint initial = owner.Counter(), pausedMain = 0;
				for (int i = 0; i < 3; ++i)
				{
					lines.Add(owner.Step(1)); if (i == 0) pausedMain = BinaryPrimitives.ReadUInt32BigEndian(owner.Markers());
				}
				Require(BinaryPrimitives.ReadUInt32BigEndian(owner.Markers()) == pausedMain, "cartridge wait holds the interrupted main program");
				Require(owner.Counter() == (cut == 0 ? (model is 2 or 3 ? 2u : 1u) : initial), "Freeze edge enters once; held cannot retrigger");
				Require(owner.Markers()[36] == (model is 2 or 3 ? 0xA8 : 0xA7), "guest observes final cartridge RAM byte");
				if (model == 1200) Require(BinaryPrimitives.ReadUInt16BigEndian(owner.Markers().AsSpan(38, 2)) == 4, "Exception2 fixture observes freeze cause4");
				owner.Signal(0x1100c, 1); for (int i = 3; i < 6; ++i) lines.Add(owner.Step(1));
				uint returned = owner.Counter();
				Require(BinaryPrimitives.ReadUInt32BigEndian(owner.Markers()) != pausedMain, "strobe/RTE returns to the main counter");
				lines.Add(owner.Step(0, true)); // V1 has no commands: releases the edge.
				owner.Signal(0x1100c, 0); for (int i = 7; i < 15; ++i) lines.Add(owner.Step(1));
				Require(owner.Counter() == returned + 1 && owner.Markers()[37] == 1 && owner.Markers()[36] == 0xA6 + returned + 1, "V1 release rearms Freeze; final cartRAM byte survives and increments");
				return string.Join("\n", lines) + "\nstate " + Convert.ToHexString(SHA256.HashData(owner.Save()));
			}
			string expected = Future(); owner.Load(state); Require(expected == Future(), "local snapshot repeats full future");
			using (var fresh = new CartridgeSandbox(options))
			{
				fresh.Init(); fresh.Guest.Seal(); fresh.Load(state);
				// The reader process below checks this same init+Seal-before-first-run path for every unit and the entire snapshot.
				Require(fresh.Save().AsSpan().SequenceEqual(state), "new sandbox restores complete state before its first frame");
			}
			Console.WriteLine($"cartridge continuation {model}/{cut}\n{expected}\nend cartridge");
			owner.Load(state);
		}
		owner.Usage();
		using (owner.Guest.Host.EnterExit()) Require(owner.Guest.Lib.p6w_shutdown() == 0, "loaded cartridge cleanup removes the machine");
		owner.NoMachine();
		Console.WriteLine("cartridge transport PASS");
	}
}
