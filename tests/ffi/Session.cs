using System.Runtime.InteropServices;
using System.Security.Cryptography;
using System.Text;

using BizHawk.Common;
using BizHawk.Emulation.Common;

using Puae6;
using BizHawk.Client.Common;

/// <summary>
/// T21b, T28: a small program language to drive several cores in one process, one command per line, so that a test can say "save here, go on, load there, compare":
///   core NAME [firmware=PATH] [adf=A,B] [hdf=A,B] [volume=A,B] [seed=N] [ntsc] [drives=N] [port1=mouse|joystick] [machine=a500|a600|a1200] [chip=Auto|KB512|MB1|MB2] [slow=Auto|None|KB512|MB1|MB1_5|MB1_75] [fast=None|MB1|MB2|MB4|MB8] [fpu=None|M68881|M68882] [sep=P0..P100] [interp=None|Anti|RH|Crux] [filter=Emulated|Off|On] [ftype=Auto|A500|A1200] [fspeed=P100|P200|P400|P800|Turbo] [vres=Lores|Hires|SuperHires] [vlines=Single|Double] [writes]
///   (writes: the floppy disks can be written, T28; without it every disk is protected. hdf: hard files, T63b; always writable, their media index follows the floppies. volume: LHA archives or ZIP files of files, T64b,
///    built into hard files in memory after the hdf ones)
///   run NAME UNITS [script=FILE] [press=SPEC] [silent=A-B] [record]      one "u NAME N ..." line per unit (SPEC is "79:Next Slot;80-85:Insert Disk", core frames, 1-based);
///       "record" keeps each unit's input as BizHawk's own BK2 log entry
///   run NAME UNITS movie=FILE [silent=A-B]                       the input of each unit is read from a .bk2 by the core's frame number (after a load, from the frame of the state)
///   moviefinish NAME FILE                                         the play ended where the movie does and its CycleCount and lag count are the header's, or the program fails ("desync: ...")
///   buffer NAME                                                   BufferWidth x BufferHeight of the core now (right after the load: the buffer announced for the settings)
///   digest NAME [LABEL [machine]]                                 SHA-256 of the whole state; with "machine", of the sandbox's snapshot alone
///   endmovie NAME FILE                                            writes what "record" kept, with the header of this core, as a .bk2
///   movieverify NAME FILE UNITS [script=FILE] [press=SPEC]      decodes the .bk2 with BizHawk's Bk2Controller and compares every button and axis of every frame with the scripted input that was recorded
///   moviecheck NAME FILE                                          validates the header of a .bk2 against this core ("movie accepted" or "movie rejected reason=...")
///   save NAME TAG | load NAME TAG | savefile NAME PATH | loadfile NAME PATH   (a refused load prints "rejected NAME TAG reason=... message=...")
///   mutate TAG NEW truncate:N | flip:OFFSET | magic | version:N | length:N | grow:N      garbage NEW BYTES      (states made up from other states or from nothing)
///   repack TAG NEW offset=N bytes=HEX [length=N]     the payload of a state with some bytes replaced (and cut to `length`), under an envelope whose length and checksum are right
///   export NAME INDEX [file=PATH]                                the disk as it is now (base with the overlay over it): "export NAME INDEX result=CODE bytes=N sha=..." (nothing is written to disk unless file=)
///   status NAME INDEX                                             the write overlay: "status NAME INDEX result=CODE capacity=N used=N medium=N refused=N raw=N"
///   saveram NAME TAG [keep]                                       CloneSaveRam (clearDirty unless "keep"): "saveram NAME TAG bytes=N sha=... modified=BEFORE" or "saveram NAME TAG null"
///   services NAME                                                 "services NAME saveram=0|1 memorydomains=0|1": ISaveRam is a service only when the core can write its disks
///   modified NAME                                                 SaveRamModified: "modified NAME 0|1"
///   storesaveram NAME TAG                                         StoreSaveRam (before the first unit): "storedsaveram NAME TAG" or "saveram-rejected NAME TAG reason=... message=..."
///   writesaveram TAG PATH | readsaveram TAG PATH                  a SaveRam to a file / from a file (both are the caller's files, the core never touches them)
///   mutatesaveram TAG NEW truncate:N|flip:OFFSET|pad:N|junk:N     a SaveRam made up from another one
///   replay NAME on|off                                            ReplayInProgress; "run ... movie=" turns it on, as a frontend playing a movie does
///   mainflush NAME PATH | mainload NAME PATH [stock|patched]       EmuHawk's FlushSaveRAM and LoadSaveRam as MainForm.cs runs them (MainFormSaveRam.cs); "notice TEXT" for what the core notified about SaveRAM
///   savedisk NAME TAG | loaddisk NAME INDEX TAG                   keep an exported disk in memory / compare: loaddisk prints "diskcmp NAME INDEX TAG equal=0|1 differs=N"
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
		internal List<string> Recorded = [];
		internal Dictionary<string, MovieFile.Movie> Movies = [];
		internal Dictionary<string, Bk2Controller> MovieControllers = [];
	}

	static void RunSession(Dictionary<string, string> options)
	{
		var runtime = NativeLibrary.Load(Path.GetFullPath(options["runtime"]));
		// P6_EVICT=0: the setting a Release build of BizHawk gives the host (WaterboxHost's static constructor calls it under #if !DEBUG): a sandbox stays mapped between calls instead of
		// being unmapped and mapped again at every EnterExit. This host is a Debug build, so by default it runs the eviction path (T28a measures both).
		if (Environment.GetEnvironmentVariable("P6_EVICT") == "0")
			((delegate* unmanaged[Cdecl]<byte, void>)NativeLibrary.GetExport(runtime, "wbx_set_always_evict_blocks"))(0);
		var machines = new Dictionary<string, Machine>();
		var states = new Dictionary<string, byte[]>();
		var saverams = new Dictionary<string, byte[]>();
		var disks = new Dictionary<string, byte[]>();
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
						Machine = MachineOf(arguments.GetValueOrDefault("machine")),
						Region = arguments.ContainsKey("ntsc") ? Puae6Region.NTSC : Puae6Region.PAL,
						FloppyDrives = int.Parse(arguments.GetValueOrDefault("drives", "1")),
						ControllerPort1 = arguments.GetValueOrDefault("port1", "mouse") == "joystick" ? Puae6Port1.Joystick : Puae6Port1.Mouse,
						ChipMemory = Enum.Parse<Puae6Chip>(arguments.GetValueOrDefault("chip", "Auto")),
						SlowMemory = Enum.Parse<Puae6Slow>(arguments.GetValueOrDefault("slow", "Auto")),
						FastMemory = Enum.Parse<Puae6Fast>(arguments.GetValueOrDefault("fast", "None")),
						FpuModel = Enum.Parse<Puae6Fpu>(arguments.GetValueOrDefault("fpu", "None")),
						SoundSeparation = Enum.Parse<Puae6SoundSeparation>(arguments.GetValueOrDefault("sep", "P70")),
						SoundInterpolation = Enum.Parse<Puae6SoundInterpolation>(arguments.GetValueOrDefault("interp", "Anti")),
						SoundFilter = Enum.Parse<Puae6SoundFilter>(arguments.GetValueOrDefault("filter", "Emulated")),
						SoundFilterType = Enum.Parse<Puae6SoundFilterType>(arguments.GetValueOrDefault("ftype", "Auto")),
						FloppySpeed = Enum.Parse<Puae6FloppySpeed>(arguments.GetValueOrDefault("fspeed", "P100")),
						VideoResolution = Enum.Parse<Puae6VideoResolution>(arguments.GetValueOrDefault("vres", "Hires")),
						VideoLineMode = Enum.Parse<Puae6VideoLineMode>(arguments.GetValueOrDefault("vlines", "Double")),
						FloppyWrites = arguments.ContainsKey("writes"),
					};
					var make = new Dictionary<string, string>(fixedOptions);
					if (arguments.TryGetValue("firmware", out var firmware)) make["firmware"] = firmware;
					if (arguments.TryGetValue("seed", out var seed)) make["seed"] = seed;
					var adfs = arguments.TryGetValue("adf", out var list) ? list.Split(',', StringSplitOptions.RemoveEmptyEntries).Select(File.ReadAllBytes).ToList() : [];
					var machine = new Machine { Mouse = settings.ControllerPort1 == Puae6Port1.Mouse, Options = arguments };
					var hdfs = arguments.TryGetValue("hdf", out var hardList) ? hardList.Split(',', StringSplitOptions.RemoveEmptyEntries).Select(File.ReadAllBytes).ToList() : [];
					var volumes = arguments.TryGetValue("volume", out var volumeList) ? volumeList.Split(',', StringSplitOptions.RemoveEmptyEntries).Select(File.ReadAllBytes).ToList() : [];
					machine.Core = MakeCore(make, settings, adfs, hdfs, volumes);
					machine.Core.ControllerDefinition.BuildMnemonicsCache("Amiga");        // what EmuHawk does when it records
					machine.Controller = new ScriptedController(machine.Core.ControllerDefinition);
					machines[words[1]] = machine;
					Console.WriteLine($"core {words[1]} ready");
					break;
				}
				case "buffer": Console.WriteLine($"buffer {words[1]} {machines[words[1]].Core.BufferWidth}x{machines[words[1]].Core.BufferHeight}"); break;     // the buffer announced at the load, or the last picture's
				case "run": Run(words[1], machines[words[1]], int.Parse(words[2]), arguments); break;
				case "endmovie": EndMovie(machines[words[1]], words[2]); Console.WriteLine($"movie {words[2]} frames={machines[words[1]].Recorded.Count}"); break;
				case "movieverify": MovieVerify(machines[words[1]], words[2], int.Parse(words[3]), arguments); break;
				case "moviecheck":
				{
					var machine = machines[words[1]];
					try { Puae6Movie.Validate(MovieFile.Read(words[2]).Header, machine.Core); Console.WriteLine($"movie accepted {words[1]}"); }
					catch (MovieRejectedException e) { Console.WriteLine($"movie rejected {words[1]} reason={e.Reason} message={e.Message}"); }
					break;
				}
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
				case "export":
				{
					var core = machines[words[1]].Core;
					int result = core.ExportMedium(int.Parse(words[2]), out var image, out var error);
					if (image is not null && arguments.TryGetValue("file", out var file)) File.WriteAllBytes(file, image);
					Console.WriteLine($"export {words[1]} {words[2]} result={result} bytes={image?.Length ?? 0} sha={(image is null ? "-" : Hex(SHA256.HashData(image)))}{(result == 0 ? "" : " message=" + error.Replace(' ', '_'))}");
					break;
				}
				case "status":
				{
					var s = machines[words[1]].Core.GetMediaWriteStatus(int.Parse(words[2]));
					Console.WriteLine($"status {words[1]} {words[2]} result={s.Result} capacity={s.OverlayCapacity} used={s.OverlayUsed} medium={s.MediumSectors} refused={s.RefusedWrites} raw={s.RawTracks}");
					break;
				}
				case "saveram":
				{
					var core = machines[words[1]].Core;
					bool before = core.SaveRamModified;
					var blob = core.CloneSaveRam(clearDirty: !arguments.ContainsKey("keep"));
					if (blob is null) Console.WriteLine($"saveram {words[1]} {words[2]} null modified={(before ? 1 : 0)}");
					else { saverams[words[2]] = blob; Console.WriteLine($"saveram {words[1]} {words[2]} bytes={blob.Length} sha={Hex(SHA256.HashData(blob))} modified={(before ? 1 : 0)}"); }
					break;
				}
				case "services":
				{
					var provider = machines[words[1]].Core.ServiceProvider;
					Console.WriteLine($"services {words[1]} saveram={(provider.HasService<ISaveRam>() ? 1 : 0)} memorydomains={(provider.HasService<IMemoryDomains>() ? 1 : 0)}");
					break;
				}
				case "modified": Console.WriteLine($"modified {words[1]} {(machines[words[1]].Core.SaveRamModified ? 1 : 0)}"); break;
				case "storesaveram":
				{
					try { machines[words[1]].Core.StoreSaveRam(saverams[words[2]]); Console.WriteLine($"storedsaveram {words[1]} {words[2]}"); }
					catch (SaveRamRejectedException e) { Console.WriteLine($"saveram-rejected {words[1]} {words[2]} reason={e.Reason} message={e.Message}"); }
					break;
				}
				case "writesaveram": File.WriteAllBytes(words[2], saverams[words[1]]); Console.WriteLine($"wrotesaveram {words[1]}"); break;
				case "readsaveram": saverams[words[1]] = File.ReadAllBytes(words[2]); Console.WriteLine($"readsaveram {words[1]} bytes={saverams[words[1]].Length}"); break;
				case "mutatesaveram":
				{
					var copy = (byte[])saverams[words[1]].Clone();
					var parts = words[3].Split(':');
					switch (parts[0])
					{
						case "truncate": copy = copy[..int.Parse(parts[1])]; break;
						case "flip": copy[int.Parse(parts[1])] ^= 0x01; break;
						case "pad": copy = copy.Concat(new byte[int.Parse(parts[1])]).ToArray(); break;
						case "junk": copy = copy.Concat(Enumerable.Repeat((byte)0x5A, int.Parse(parts[1]))).ToArray(); break;
						default: throw new ArgumentException($"unknown mutation {words[3]}");
					}
					saverams[words[2]] = copy;
					Console.WriteLine($"mutatedsaveram {words[2]} bytes={copy.Length}");
					break;
				}
				case "mainflush": MainFormFlush(machines[words[1]].Core, words[1], words[2]); break;
				case "mainload": MainFormLoad(machines[words[1]].Core, words[1], words[2], words.Length > 3 && words[3] == "patched"); break;
				case "replay": machines[words[1]].Core.ReplayInProgress = words[2] == "on"; Console.WriteLine($"replay {words[1]} {words[2]}"); break;
				case "savedisk":
				{
					int result = machines[words[1]].Core.ExportMedium(int.Parse(arguments.GetValueOrDefault("index", "0")), out var image, out var error);
					if (image is null) throw new InvalidOperationException($"savedisk: {result} {error}");
					disks[words[2]] = image;
					Console.WriteLine($"saveddisk {words[2]} sha={Hex(SHA256.HashData(image))}");
					break;
				}
				case "loaddisk":
				{
					int result = machines[words[1]].Core.ExportMedium(int.Parse(words[2]), out var image, out var error);
					if (image is null) throw new InvalidOperationException($"loaddisk: {result} {error}");
					var reference = disks[words[3]];
					int differs = Enumerable.Range(0, reference.Length / 512).Count(sector => !image.AsSpan(sector * 512, 512).SequenceEqual(reference.AsSpan(sector * 512, 512)));
					Console.WriteLine($"diskcmp {words[1]} {words[2]} {words[3]} equal={(differs == 0 ? 1 : 0)} differs={differs}");
					break;
				}
				case "mutate": states[words[2]] = Mutate(states[words[1]], words[3]); Console.WriteLine($"mutated {words[2]} bytes={states[words[2]].Length}"); break;
				case "repack": states[words[2]] = Repack(states[words[1]], arguments); Console.WriteLine($"repacked {words[2]} bytes={states[words[2]].Length}"); break;
				case "garbage": states[words[1]] = new Random(7).GetBytes(int.Parse(words[2])); Console.WriteLine($"garbage {words[1]} bytes={states[words[1]].Length}"); break;
				case "digest":
				{
					var m = machines[words[1]];
					var bytes = Save(m.Core);
					// "machine": the sandbox's snapshot alone, without the wrapper's own fields (some of them, like the size of the last picture, only exist when a picture was asked for)
					if (words.Length > 3 && words[3] == "machine") bytes = bytes[(HeaderBytes + Puae6Core.WrapperStateBytes)..];
					Console.WriteLine($"digest {words[1]} {(words.Length > 2 ? words[2] : "")} sha={Hex(SHA256.HashData(bytes))} frame={m.Core.Frame} lag={m.Core.LagCount} " +
						$"events={m.Core.MediaEventsSent} pcm={m.Pcm:x16}");
					break;
				}
				case "moviefinish": MovieFinish(machines[words[1]], words[2]); break;
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

	static void MovieVerify(Machine machine, string path, int units, Dictionary<string, string> arguments)
	{
		var core = machine.Core;
		var movie = MovieFile.Read(path);
		var decoded = new Bk2Controller(core.ControllerDefinition, movie.LogKey);
		Script? script = arguments.TryGetValue("script", out var scriptPath) ? new Script(scriptPath) : null;
		var presses = ParsePresses(arguments.GetValueOrDefault("press", ""));
		var live = machine.Controller;
		if (movie.LogKey != Bk2LogEntryGenerator.GenerateLogKey(core.ControllerDefinition)) throw new InvalidOperationException("the log key of the movie is not the one of this core's buttons");
		int compared = 0, buttons = core.ControllerDefinition.BoolButtons.Count, axes = core.ControllerDefinition.Axes.Count;
		for (int frame = 0; frame < units; ++frame)
		{
			live.Clear();
			script?.Fill(live, frame, machine.Mouse);
			foreach (var (from, to, button) in presses) if (frame + 1 >= from && frame + 1 <= to) live.Press(button);
			decoded.SetFromMnemonic(movie.Entries[frame]);
			foreach (var button in core.ControllerDefinition.BoolButtons)
			{
				if (live.IsPressed(button) != decoded.IsPressed(button)) throw new InvalidOperationException($"frame {frame}: {button} is {live.IsPressed(button)} in the input and {decoded.IsPressed(button)} in the movie");
				++compared;
			}
			foreach (var axis in core.ControllerDefinition.Axes.Keys)
			{
				if (live.AxisValue(axis) != decoded.AxisValue(axis)) throw new InvalidOperationException($"frame {frame}: {axis} is {live.AxisValue(axis)} in the input and {decoded.AxisValue(axis)} in the movie");
				++compared;
			}
		}
		Console.WriteLine($"movie verified frames={units} buttons={buttons} axes={axes} compared={compared} entries={movie.Entries.Count}");
	}

	static List<(int From, int To, string Button)> ParsePresses(string spec)
	{
		var presses = new List<(int, int, string)>();
		foreach (var item in spec.Replace('_', ' ').Split(';', StringSplitOptions.RemoveEmptyEntries))
		{
			var parts = item.Split(':');
			var range = parts[0].Split('-');
			presses.Add((int.Parse(range[0]), int.Parse(range[^1]), parts[1]));
		}
		return presses;
	}

	/// <summary>What EmuHawk checks when a movie ends, and what a test of a movie must fail on: the frame, the cycle count and the lag count. Anything else is the unit lines.</summary>
	static void MovieFinish(Machine machine, string path)
	{
		var core = machine.Core;
		var movie = MovieFile.Read(path);
		if (core.Frame != movie.Entries.Count) throw new InvalidOperationException($"desync: the play is at frame {core.Frame} and the movie has {movie.Entries.Count}");
		long cycles = long.Parse(movie.Header["CycleCount"], System.Globalization.CultureInfo.InvariantCulture);
		int lag = int.Parse(movie.Header["PUAE6LagCount"], System.Globalization.CultureInfo.InvariantCulture);
		if (core.CycleCount != cycles) throw new InvalidOperationException($"desync: CycleCount is {core.CycleCount} and the movie says {cycles}");
		if (core.LagCount != lag) throw new InvalidOperationException($"desync: the lag count is {core.LagCount} and the movie says {lag}");
		Console.WriteLine($"movie finished ok frames={core.Frame} cycles={core.CycleCount} lag={core.LagCount}");
	}

	static void EndMovie(Machine machine, string path)
	{
		var core = machine.Core;
		var header = Puae6Movie.Header(core);
		var head = new Dictionary<string, string>
		{
			["MovieVersion"] = "BizHawk v2.0.0", ["emuVersion"] = "PUAE6 test host", ["Platform"] = header["Platform"], ["GameName"] = "PUAE6 fixture", ["Author"] = "tests/bizhawk-movies",
			["rerecordCount"] = "0", ["Core"] = header["Core"], [Puae6Movie.GuestKey] = header[Puae6Movie.GuestKey], [Puae6Movie.ConfigKey] = header[Puae6Movie.ConfigKey],
			[Puae6Movie.FirmwareKey] = header[Puae6Movie.FirmwareKey], [Puae6Movie.MediaKey] = header[Puae6Movie.MediaKey],
			["SyncSettings"] = System.Text.Json.JsonSerializer.Serialize(core.GetSyncSettings()),
			["PUAE6LagCount"] = core.LagCount.ToString(System.Globalization.CultureInfo.InvariantCulture),
			["CycleCount"] = core.CycleCount.ToString(System.Globalization.CultureInfo.InvariantCulture), ["ClockRate"] = core.ClockRate.ToString("R", System.Globalization.CultureInfo.InvariantCulture),
		};
		MovieFile.Write(path, head, Bk2LogEntryGenerator.GenerateLogKey(core.ControllerDefinition), machine.Recorded);
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
		if (arguments.ContainsKey("movie")) core.ReplayInProgress = true;           // a frontend that plays a movie says so; the durable copy is not read or replaced while it does
		var presses = new List<(int From, int To, string Button)>();
		foreach (var item in arguments.GetValueOrDefault("press", "").Replace('_', ' ').Split(';', StringSplitOptions.RemoveEmptyEntries))
		{
			var parts = item.Split(':');
			var range = parts[0].Split('-');
			presses.Add((int.Parse(range[0]), int.Parse(range[^1]), parts[1]));
		}
		MovieFile.Movie? movie = null;
		Bk2Controller? movieController = null;
		if (arguments.TryGetValue("movie", out var moviePath))
		{
			if (!machine.Movies.TryGetValue(moviePath, out movie))
			{
				movie = MovieFile.Read(moviePath);
				Puae6Movie.Validate(movie.Header, core);                  // a movie that is not this core's is refused before anything runs
				machine.Movies[moviePath] = movie;
				machine.MovieControllers[moviePath] = new Bk2Controller(core.ControllerDefinition, movie.LogKey);
			}
			movieController = machine.MovieControllers[moviePath];
		}
		(int From, int To)? silent = arguments.TryGetValue("silent", out var range2) ? (int.Parse(range2.Split('-')[0]), int.Parse(range2.Split('-')[1])) : null;
		var pixel = new byte[4];
		var two = new byte[2];
		for (int i = 0; i < units; ++i)
		{
			int frame = core.Frame;                                   // 0-based number of the unit about to run
			IController input = machine.Controller;
			if (movie is not null)
			{
				if (frame >= movie.Entries.Count) throw new InvalidOperationException($"the movie ends at frame {movie.Entries.Count} and the core is at {frame}");
				movieController!.SetFromMnemonic(movie.Entries[frame]);
				input = movieController;
			}
			else
			{
				machine.Controller.Clear();
				if (script is not null) { machine.Script ??= script; script.Fill(machine.Controller, frame, machine.Mouse); }
				foreach (var (from, to, button) in presses)
					if (frame + 1 >= from && frame + 1 <= to) machine.Controller.Press(button);
				if (arguments.ContainsKey("record"))
				{
					while (machine.Recorded.Count > frame) machine.Recorded.RemoveAt(machine.Recorded.Count - 1);      // recording over a branch: the log is the one that was played
					machine.Recorded.Add(Bk2LogEntryGenerator.GenerateLogEntry(machine.Controller));
				}
			}
			bool off = silent is { } s && frame + 1 >= s.From && frame + 1 <= s.To;
			core.FrameAdvance(input, !off, !off);
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
