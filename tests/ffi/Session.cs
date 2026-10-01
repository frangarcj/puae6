using System.Runtime.InteropServices;
using System.Security.Cryptography;
using System.Text;

using BizHawk.Common;
using BizHawk.Emulation.Common;

using Puae6;

/// <summary>
/// T21b: a small program language to drive several cores in one process, one command per line, so that a test can say "save here, go on, load there, compare":
///   core NAME [firmware=PATH] [adf=A,B] [seed=N] [ntsc] [drives=N] [port1=mouse|joystick]
///   run NAME UNITS [script=FILE] [press=SPEC] [silent=A-B]      one "u NAME N ..." line per unit (SPEC is "79:Next Slot;80-85:Insert Disk", core frames, 1-based)
///   save NAME TAG | load NAME TAG | savefile NAME PATH | loadfile NAME PATH   (a refused load prints "rejected NAME TAG reason=... message=...")
///   mutate TAG NEW truncate:N | flip:OFFSET | magic | version:N | length:N | grow:N      garbage NEW BYTES      (states made up from other states or from nothing)
///   repack TAG NEW offset=N bytes=HEX [length=N]     the payload of a state with some bytes replaced (and cut to `length`), under an envelope whose length and checksum are right
///   digest NAME [LABEL] | peek NAME DOMAIN ADDR COUNT | poke NAME DOMAIN ADDR HEX | dispose NAME | echo TEXT
/// </summary>
internal static unsafe partial class Program
{
	sealed class Machine
	{
		internal Puae6Core Core = null!;
		internal Dictionary<string, string> Options = [];
		internal ulong Pcm = 1469598103934665603UL;
		internal ScriptedController Controller = null!;
		internal Script? Script;
		internal bool Mouse = true;
	}

	static void RunSession(Dictionary<string, string> options)
	{
		NativeLibrary.Load(Path.GetFullPath(options["runtime"]));
		var machines = new Dictionary<string, Machine>();
		var states = new Dictionary<string, byte[]>();
		var fixedOptions = new Dictionary<string, string>(options);
		foreach (var raw in File.ReadAllLines(options["program"]))
		{
			var line = raw.Trim();
			if (line.Length == 0 || line.StartsWith('#')) continue;
			var words = Tokens(line);
			var arguments = words.Skip(2).Where(w => w.Contains('=')).ToDictionary(w => w[..w.IndexOf('=')], w => w[(w.IndexOf('=') + 1)..]);
			foreach (var flag in words.Skip(2).Where(w => !w.Contains('='))) arguments[flag] = "";
			switch (words[0])
			{
				case "echo": Console.WriteLine(string.Join(' ', words.Skip(1))); break;
				case "core":
				{
					var settings = new Puae6SyncSettings
					{
						Region = arguments.ContainsKey("ntsc") ? Puae6Region.NTSC : Puae6Region.PAL,
						FloppyDrives = int.Parse(arguments.GetValueOrDefault("drives", "1")),
						ControllerPort1 = arguments.GetValueOrDefault("port1", "mouse") == "joystick" ? Puae6Port1.Joystick : Puae6Port1.Mouse,
					};
					var make = new Dictionary<string, string>(fixedOptions);
					if (arguments.TryGetValue("firmware", out var firmware)) make["firmware"] = firmware;
					if (arguments.TryGetValue("seed", out var seed)) make["seed"] = seed;
					var adfs = arguments.TryGetValue("adf", out var list) ? list.Split(',', StringSplitOptions.RemoveEmptyEntries).Select(File.ReadAllBytes).ToList() : [];
					var machine = new Machine { Mouse = settings.ControllerPort1 == Puae6Port1.Mouse, Options = arguments };
					machine.Core = MakeCore(make, settings, adfs);
					machine.Controller = new ScriptedController(machine.Core.ControllerDefinition);
					machines[words[1]] = machine;
					Console.WriteLine($"core {words[1]} ready");
					break;
				}
				case "run": Run(words[1], machines[words[1]], int.Parse(words[2]), arguments); break;
				case "save":
				{
					var bytes = Save(machines[words[1]].Core);
					states[words[2]] = bytes;
					Console.WriteLine($"saved {words[2]} bytes={bytes.Length} sha={Hex(SHA256.HashData(bytes))}");
					break;
				}
				case "savefile": File.WriteAllBytes(words[2], Save(machines[words[1]].Core)); Console.WriteLine($"savedfile {words[1]}"); break;
				case "load": Load(machines[words[1]], states[words[2]], words[1], words[2]); break;
				case "loadfile": Load(machines[words[1]], File.ReadAllBytes(words[2]), words[1], words[2]); break;
				case "mutate": states[words[2]] = Mutate(states[words[1]], words[3]); Console.WriteLine($"mutated {words[2]} bytes={states[words[2]].Length}"); break;
				case "repack": states[words[2]] = Repack(states[words[1]], arguments); Console.WriteLine($"repacked {words[2]} bytes={states[words[2]].Length}"); break;
				case "garbage": states[words[1]] = new Random(7).GetBytes(int.Parse(words[2])); Console.WriteLine($"garbage {words[1]} bytes={states[words[1]].Length}"); break;
				case "digest":
				{
					var m = machines[words[1]];
					var bytes = Save(m.Core);
					Console.WriteLine($"digest {words[1]} {(words.Length > 2 ? words[2] : "")} sha={Hex(SHA256.HashData(bytes))} frame={m.Core.Frame} lag={m.Core.LagCount} " +
						$"events={m.Core.MediaEventsSent} pcm={m.Pcm:x16}");
					break;
				}
				case "peek":
				{
					var domain = machines[words[1]].Core.ServiceProvider.GetService<IMemoryDomains>()![words[2].Replace('_', ' ')]!;
					long at = Convert.ToInt64(words[3], 16);
					int count = int.Parse(words[4]);
					var bytes = new byte[count];
					domain.BulkPeekByte(at.RangeToExclusive(at + count), bytes);
					Console.WriteLine($"peek {words[1]} {words[2]} {words[3]} {Hex(bytes)}");
					break;
				}
				case "poke":
				{
					var domain = machines[words[1]].Core.ServiceProvider.GetService<IMemoryDomains>()![words[2].Replace('_', ' ')]!;
					long at = Convert.ToInt64(words[3], 16);
					var bytes = Convert.FromHexString(words[4]);
					for (int i = 0; i < bytes.Length; ++i) domain.PokeByte(at + i, bytes[i]);
					Console.WriteLine($"poked {words[1]} {words[2]} {words[3]}");
					break;
				}
				case "dispose": machines[words[1]].Core.Dispose(); machines.Remove(words[1]); Console.WriteLine($"disposed {words[1]}"); break;
				default: throw new ArgumentException($"unknown command {words[0]}");
			}
		}
		foreach (var machine in machines.Values) machine.Core.Dispose();
	}

	static string[] Tokens(string line) => line.Split(' ', StringSplitOptions.RemoveEmptyEntries);
	static string Hex(byte[] bytes) => Convert.ToHexString(bytes).ToLowerInvariant();

	static byte[] Save(Puae6Core core)
	{
		using var stream = new MemoryStream();
		using (var writer = new BinaryWriter(stream, Encoding.UTF8, true)) core.SaveStateBinary(writer);
		return stream.ToArray();
	}

	static void Load(Machine machine, byte[] state, string name, string tag)
	{
		using var stream = new MemoryStream(state, false);
		using var reader = new BinaryReader(stream);
		try
		{
			machine.Core.LoadStateBinary(reader);
			Console.WriteLine($"loaded {name} {tag}");
		}
		catch (StateRejectedException e)
		{
			Console.WriteLine($"rejected {name} {tag} reason={e.Reason} message={e.Message}");
		}
	}

	/// <summary>A number in a mutation: a plain one, LEN (the declared payload length of this state), LAST (the index of the last byte), each with +n or -n, or a negative number
	/// for "that many from the end".</summary>
	static long Value(byte[] state, string text)
	{
		long length = (long)BitConverter.ToUInt64(state, LengthOffset), last = state.Length - 1;
		if (text.StartsWith("LEN")) return length + (text.Length > 3 ? long.Parse(text[3..]) : 0);
		if (text.StartsWith("LAST")) return last + (text.Length > 4 ? long.Parse(text[4..]) : 0);
		long value = long.Parse(text);
		return value < 0 ? state.Length + value : value;
	}

	static byte[] Mutate(byte[] state, string mode)
	{
		var copy = (byte[])state.Clone();
		var parts = mode.Split(':');
		switch (parts[0])
		{
			case "truncate": return copy[..(int)Value(state, parts[1])];
			case "flip": copy[(int)Value(state, parts[1])] ^= 0x01; return copy;
			case "magic": copy[0] ^= 0xFF; return copy;
			case "version": BitConverter.GetBytes(uint.Parse(parts[1])).CopyTo(copy, 4); return copy;
			case "length": BitConverter.GetBytes((ulong)(parts[1].StartsWith("LEN") ? Value(state, parts[1]) : long.Parse(parts[1]))).CopyTo(copy, LengthOffset); return copy;
			case "grow": return copy.Concat(new byte[int.Parse(parts[1])]).ToArray();
			default: throw new ArgumentException($"unknown mutation {mode}");
		}
	}

	/// <summary>magic 4, version 4, guest 32, heaps 5*4, config 32, firmware 32, media 32.</summary>
	const int LengthOffset = 4 + 4 + 32 + 20 + 32 + 32 + 32;

	/// <summary>The envelope is magic 4, version 4, identity (guest 32, heaps 20, config 32, firmware 32, media 32), length 8, checksum 32, payload.</summary>
	const int HeaderBytes = LengthOffset + 8 + 32;

	static byte[] Repack(byte[] state, Dictionary<string, string> arguments)
	{
		var payload = state[HeaderBytes..];
		int at = int.Parse(arguments["offset"]);
		var bytes = Convert.FromHexString(arguments["bytes"]);
		bytes.CopyTo(payload, at);
		if (arguments.TryGetValue("length", out var length)) payload = payload[..int.Parse(length)];
		using var stream = new MemoryStream();
		stream.Write(state, 0, LengthOffset);
		stream.Write(BitConverter.GetBytes((ulong)payload.Length));
		stream.Write(StateEnvelope.Checksum(payload));
		stream.Write(payload);
		return stream.ToArray();
	}

	static byte[] GetBytes(this Random random, int count)
	{
		var bytes = new byte[count];
		random.NextBytes(bytes);
		return bytes;
	}

	static void Run(string name, Machine machine, int units, Dictionary<string, string> arguments)
	{
		var core = machine.Core;
		Script? script = arguments.TryGetValue("script", out var path) ? new Script(path) : null;
		var presses = new List<(int From, int To, string Button)>();
		foreach (var item in arguments.GetValueOrDefault("press", "").Replace('_', ' ').Split(';', StringSplitOptions.RemoveEmptyEntries))
		{
			var parts = item.Split(':');
			var range = parts[0].Split('-');
			presses.Add((int.Parse(range[0]), int.Parse(range[^1]), parts[1]));
		}
		(int From, int To)? silent = arguments.TryGetValue("silent", out var range2) ? (int.Parse(range2.Split('-')[0]), int.Parse(range2.Split('-')[1])) : null;
		var pixel = new byte[4];
		var two = new byte[2];
		for (int i = 0; i < units; ++i)
		{
			int frame = core.Frame;                                   // 0-based number of the unit about to run
			machine.Controller.Clear();
			if (script is not null) { machine.Script ??= script; script.Fill(machine.Controller, frame, machine.Mouse); }
			foreach (var (from, to, button) in presses)
				if (frame + 1 >= from && frame + 1 <= to) machine.Controller.Press(button);
			bool off = silent is { } s && frame + 1 >= s.From && frame + 1 <= s.To;
			core.FrameAdvance(machine.Controller, !off, !off);
			var video = core.GetVideoBuffer();
			ulong hash = FnvBasis;
			for (int p = 0; p < core.BufferWidth * core.BufferHeight; ++p) { BitConverter.TryWriteBytes(pixel, video[p] & 0x00FFFFFF); hash = Fnv(hash, pixel); }
			core.GetSamplesSync(out var samples, out int frames);
			ulong unitPcm = FnvBasis;
			for (int s2 = 0; s2 < frames * 2; ++s2) { BitConverter.TryWriteBytes(two, samples[s2]); unitPcm = Fnv(unitPcm, two); machine.Pcm = Fnv(machine.Pcm, two); }
			Console.WriteLine($"u {name} {core.Frame} ticks={core.CycleCount} hash={hash:x16} audio={frames} pcm={unitPcm:x16} lag={(core.IsLagFrame ? 1 : 0)} light={(core.DriveLightOn ? 1 : 0)} events={core.MediaEventsSent}");
		}
	}
}
