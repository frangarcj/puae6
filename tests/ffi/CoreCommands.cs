using System.Globalization;
using System.Runtime.InteropServices;

using BizHawk.Common;
using BizHawk.Emulation.Common;

using Puae6;

/// <summary>T20c: the core itself, driven the way EmuHawk drives a core (an IController per frame), with the scripts and fixtures of the native tests.</summary>
internal static unsafe partial class Program
{
	/// <summary>One frame of controller state, built from a packet of the native input scripts (tests/input).</summary>
	sealed class ScriptedController : IController
	{
		readonly HashSet<string> pressed = [];
		readonly Dictionary<string, int> axes = [];
		public ControllerDefinition Definition { get; }
		public ScriptedController(ControllerDefinition definition) => Definition = definition;
		public IReadOnlyCollection<(string Name, int Strength)> GetHapticsSnapshot() => [];
		public bool IsPressed(string button) => pressed.Contains(button);
		public int AxisValue(string name) => axes.TryGetValue(name, out int value) ? value : Definition.Axes.TryGetValue(name, out var spec) ? spec.Neutral : 0;
		public void SetHapticChannelStrength(string name, int strength) { }
		internal void Press(string name) => pressed.Add(name);
		internal void Axis(string name, int value) => axes[name] = value;
		internal void Clear() => pressed.Clear();
	}

	/// <summary>The player of a script: "k0 k1 k2 k3 p0type p0buttons p0dx p0dy p1type p1buttons p1dx p1dy" per unit (P6_INPUT_SCRIPT), as BizHawk controller states. What a
	/// unit presses depends on the unit's number only (the mouse position is the sum of the movements before it, a Caps Lock press the change of the latch), so a core that
	/// goes back to an earlier unit by loading a state is played the same input again.</summary>
	sealed class Script
	{
		readonly List<uint[]> keys = [];
		readonly List<(uint Type, uint Buttons, int Dx, int Dy)[]> ports = [];
		readonly List<int> mouseX = [], mouseY = [];

		internal Script(string path)
		{
			int x = Controllers.MouseFieldWidth / 2, y = Controllers.MouseFieldHeight / 2;
			foreach (var line in File.ReadAllLines(path).Where(l => l.Trim().Length > 0))
			{
				var f = line.Split(' ', StringSplitOptions.RemoveEmptyEntries);
				keys.Add(f[..4].Select(v => uint.Parse(v, NumberStyles.HexNumber)).ToArray());
				var p0 = (uint.Parse(f[4]), uint.Parse(f[5]), int.Parse(f[6]), int.Parse(f[7]));
				ports.Add([p0, (uint.Parse(f[8]), uint.Parse(f[9]), int.Parse(f[10]), int.Parse(f[11]))]);
				if (p0.Item1 == P6.PortMouse) { x += p0.Item3; y += p0.Item4; }
				mouseX.Add(x); mouseY.Add(y);
			}
		}

		internal int Units => keys.Count;

		bool Caps(int unit) => (keys[Math.Min(unit, keys.Count - 1)][Controllers.CapsLockCode / 32] & (1u << (Controllers.CapsLockCode % 32))) != 0;

		/// <summary>Controller state of 0-based unit <paramref name="unit"/>. After the script ends the last line goes on with no movement, as the native probe does.</summary>
		internal void Fill(ScriptedController controller, int unit, bool mousePort0)
		{
			int at = Math.Min(unit, keys.Count - 1);
			for (int code = 0; code < 0x68; ++code)
			{
				if (Controllers.KeyNames[code] is null || (keys[at][code / 32] & (1u << (code % 32))) == 0) continue;
				if (code == Controllers.CapsLockCode) continue;          // a latch in the script, a press that toggles it in the controller (below)
				controller.Press(Controllers.KeyButton(code));
			}
			if (Caps(unit) != (unit > 0 && Caps(unit - 1))) controller.Press(Controllers.KeyButton(Controllers.CapsLockCode));

			var p0 = ports[at][0];
			if (mousePort0)
			{
				if ((p0.Buttons & P6.Button1) != 0) controller.Press($"P1 {Controllers.MouseLeft}");
				if ((p0.Buttons & P6.Button2) != 0) controller.Press($"P1 {Controllers.MouseRight}");
				if ((p0.Buttons & P6.Button3) != 0) controller.Press($"P1 {Controllers.MouseMiddle}");
				controller.Axis($"P1 {Controllers.MouseX}", Math.Clamp(mouseX[at], 0, Controllers.MouseFieldWidth));
				controller.Axis($"P1 {Controllers.MouseY}", Math.Clamp(mouseY[at], 0, Controllers.MouseFieldHeight));
			}
			else PressJoystick(controller, 1, p0.Buttons);
			PressJoystick(controller, 2, ports[at][1].Buttons);
		}

		static void PressJoystick(ScriptedController controller, int port, uint buttons)
		{
			for (int i = 0; i < Controllers.JoystickNames.Length; ++i)
				if ((buttons & Controllers.JoystickBits[i]) != 0) controller.Press(Controllers.Joystick(port, Controllers.JoystickNames[i]));
		}
	}

	/// <summary>The machine a command asks for: "a500" (the default), "a600", "a1200" or, from T50, "a500og", "a500plus", "a1000", "a2000og" or "a2000".</summary>
	static Puae6Machine MachineOf(string? name) => (name ?? "a500").ToLowerInvariant() switch
	{
		"a500" => Puae6Machine.A500,
		"a600" => Puae6Machine.A600,
		"a1200" => Puae6Machine.A1200,
		"a500og" => Puae6Machine.A500OG,
		"a500plus" => Puae6Machine.A500PLUS,
		"a1000" => Puae6Machine.A1000,
		"a2000og" => Puae6Machine.A2000OG,
		"a2000" => Puae6Machine.A2000,
		var other => throw new ArgumentException($"machine is a500, a600, a1200, a500og, a500plus, a1000, a2000og or a2000 (got {other})"),
	};

	static Puae6Core MakeCore(Dictionary<string, string> options, Puae6SyncSettings? settings = null, IReadOnlyList<byte[]>? media = null, IReadOnlyList<byte[]>? hardFiles = null)
	{
		string guest = Path.GetFullPath(options["guest"]);
		var comm = new CoreComm(_ => { }, (message, _) => { if (message.StartsWith("SaveRAM")) Console.WriteLine($"notice {message}"); }, null!, CoreComm.CorePreferencesFlags.None, null!);
		settings ??= new Puae6SyncSettings();
		if (options.TryGetValue("seed", out var seed)) settings.RandomSeed = ulong.Parse(seed);
		return new Puae6Core(comm, new Puae6Parameters
		{
			GuestDirectory = Path.GetDirectoryName(guest)!, GuestFileName = Path.GetFileName(guest), PinnedGuestSha256 = options["sha"],
			Firmware = File.ReadAllBytes(options["firmware"]), SyncSettings = settings, Media = media ?? new List<byte[]>(), HardFiles = hardFiles ?? new List<byte[]>(),
		});
	}

	static string Words(Puae6Core core, int from, int count)
	{
		var chip = core.ServiceProvider.GetService<IMemoryDomains>()!["Chip RAM"]!;
		return string.Join(' ', Enumerable.Range(0, count).Select(i => chip.PeekUshort(from + 2 * i, true).ToString("x4")));
	}

	/// <summary>Plays a native input script through the core. Prints a line per unit and then the first 512 words of the log the input_all fixture writes at $1000.</summary>
	static void Play(Dictionary<string, string> options)
	{
		NativeLibrary.Load(Path.GetFullPath(options["runtime"]));
		var script = new Script(options["script"]);
		bool mouse = options.GetValueOrDefault("port1", "mouse") == "mouse";
		var settings = new Puae6SyncSettings
		{
			Region = options.ContainsKey("ntsc") ? Puae6Region.NTSC : Puae6Region.PAL,
			ControllerPort1 = mouse ? Puae6Port1.Mouse : Puae6Port1.Joystick,
		};
		using var core = MakeCore(options, settings);
		var controller = new ScriptedController(core.ControllerDefinition);
		int units = int.Parse(options["units"]);
		var powerAt = options.TryGetValue("power-at", out var at) ? at.Split(',').Select(int.Parse).ToHashSet() : [];
		for (int unit = 0; unit < units; ++unit)
		{
			controller.Clear();
			script.Fill(controller, unit, mouse);
			if (powerAt.Contains(unit)) controller.Press(Controllers.Power);          // the Power button, held for this unit only
			Require(core.FrameAdvance(controller, true, true), $"unit {unit + 1} advanced");
			Console.WriteLine($"unit {core.Frame} ticks={core.CycleCount} lag={(core.IsLagFrame ? 1 : 0)}");
		}
		Console.WriteLine("log " + Words(core, 0x1000, 512));
	}

	const ulong FnvBasis = 1469598103934665603UL, FnvPrime = 1099511628211UL;

	static ulong Fnv(ulong hash, ReadOnlySpan<byte> bytes)
	{
		foreach (byte b in bytes) { hash ^= b; hash *= FnvPrime; }
		return hash;
	}

	/// <summary>Runs a ROM with no input and prints, per unit, what BizHawk would see (picture size, the picture's hash, the audio, lag and drive light),
	/// in the terms of the lines p6_headless prints, so that the two can be compared. --silent A-B asks for neither picture nor audio in those units.</summary>
	static void Av(Dictionary<string, string> options)
	{
		NativeLibrary.Load(Path.GetFullPath(options["runtime"]));
		var settings = new Puae6SyncSettings
		{
			Machine = MachineOf(options.GetValueOrDefault("machine")),
			Region = options.ContainsKey("ntsc") ? Puae6Region.NTSC : Puae6Region.PAL,
		};
		using var core = MakeCore(options, settings);
		var controller = new ScriptedController(core.ControllerDefinition);
		(int From, int To)? silent = options.TryGetValue("silent", out var range) ? (int.Parse(range.Split('-')[0]), int.Parse(range.Split('-')[1])) : null;
		Require(core.GetVideoBuffer().Length == Puae6Core.MaxWidth * Puae6Core.MaxHeight, "the picture buffer is the profile's");
		ulong pcm = FnvBasis, totalFrames = 0;
		var pixel = new byte[4];
		var two = new byte[2];
		int units = int.Parse(options["units"]);
		for (int unit = 1; unit <= units; ++unit)
		{
			bool off = silent is { } s && unit >= s.From && unit <= s.To;
			core.FrameAdvance(controller, !off, !off);
			var video = core.GetVideoBuffer();
			ulong hash = FnvBasis;
			bool opaque = true;
			for (int i = 0; i < core.BufferWidth * core.BufferHeight; ++i)
			{
				opaque &= (video[i] >>> 24) == 0xFF;                   // BizHawk's pixels are opaque ARGB
				BitConverter.TryWriteBytes(pixel, video[i] & 0x00FFFFFF);
				hash = Fnv(hash, pixel);
			}
			core.GetSamplesSync(out var samples, out int frames);
			Require(samples.Length == 2 * Puae6Core.MaxAudioFrames && frames * 2 <= samples.Length, $"unit {unit}: the audio fits the buffer");
			ulong unitPcm = FnvBasis;
			for (int i = 0; i < frames * 2; ++i)
			{
				BitConverter.TryWriteBytes(two, samples[i]);
				unitPcm = Fnv(unitPcm, two);
				pcm = Fnv(pcm, two);
			}
			totalFrames += (ulong)frames;
			Require(core.BufferWidth <= Puae6Core.MaxWidth && core.BufferHeight <= Puae6Core.MaxHeight, $"unit {unit}: the picture fits the profile's buffer");
			Console.WriteLine($"av {unit} ticks={core.CycleCount} size={core.BufferWidth}x{core.BufferHeight} virtual={core.VirtualWidth}x{core.VirtualHeight} " +
				$"vsync={core.VsyncNumerator}/{core.VsyncDenominator} hash={hash:x16} audio={frames} pcm={unitPcm:x16} lag={(core.IsLagFrame ? 1 : 0)} light={(core.DriveLightOn ? 1 : 0)} lagcount={core.LagCount} opaque={(opaque ? 1 : 0)}");
		}
		Console.WriteLine($"audio frames={totalFrames} hash={pcm:x16}");
		Console.WriteLine($"region={core.Region} system={core.SystemId} deterministic={core.DeterministicEmulation}");
	}

	/// <summary>The memory domains the way a BizHawk tool uses them, before and after a frame.</summary>
	static void Domains(Dictionary<string, string> options)
	{
		NativeLibrary.Load(Path.GetFullPath(options["runtime"]));
		var machine = MachineOf(options.GetValueOrDefault("machine"));
		// T49: --chip, --slow and --fast (the names of the enums: Auto, KB512, MB1, MB2, None, MB1_5, MB1_75, MB4, MB8) give the machine other memory.
		var settings = new Puae6SyncSettings
		{
			Machine = machine,
			ChipMemory = Enum.Parse<Puae6Chip>(options.GetValueOrDefault("chip", "Auto")),
			SlowMemory = Enum.Parse<Puae6Slow>(options.GetValueOrDefault("slow", "Auto")),
			FastMemory = Enum.Parse<Puae6Fast>(options.GetValueOrDefault("fast", "None")),
		};
		using var core = MakeCore(options, settings);
		var domains = core.ServiceProvider.GetService<IMemoryDomains>()!;
		var names = domains.Select(d => d.Name).ToList();
		Console.WriteLine("domains " + string.Join(',', names));
		Require(domains.MainMemory.Name == "Chip RAM", "the main memory is Chip RAM");
		foreach (var d in domains.Where(d => d.Name != "Waterbox PageData"))
		{
			Require(d.Size > 0, $"{d.Name}: not empty ({d.Size} bytes)");
			Require(d.EndianType == MemoryDomain.Endian.Big && d.WordSize == 2, $"{d.Name}: big endian, 16-bit words");
			Require(d.Writable == (d.Name != "ROM"), $"{d.Name}: writable only if it is RAM");
			Console.WriteLine($"domain {d.Name} size={d.Size} writable={d.Writable}");
		}
		var chip = domains["Chip RAM"]!;
		// The Fast RAM board, when there is one, is what the pokes go to (nothing writes it). Else the A500 has Slow RAM, which nothing writes; the A600 and the A1200 have none and the part of
		// Chip RAM above 512 KiB, which the fixtures leave alone, takes its place.
		var board = domains["Fast RAM"];
		Require((board != null) == (settings.FastMemory != Puae6Fast.None), "the Fast RAM domain exists when a board is configured and only then");
		bool hasSlow = settings.SlowMemory switch { Puae6Slow.None => false, Puae6Slow.Auto => machine is Puae6Machine.A500 or Puae6Machine.A2000OG, _ => true };
		Require((domains["Slow RAM"] != null) == hasSlow, "Slow RAM exists when the machine has it");
		var slow = board ?? domains["Slow RAM"] ?? chip;
		// With nothing but Chip RAM (the 512 KiB of the A500 OG and the A1000 included) the pokes go to the part the fixtures leave alone: above 512 KiB when there is any, else $40000.
		long scratch = ReferenceEquals(slow, chip) ? (chip.Size > 0x80000 ? 0x80000 : 0x40000) : 0;
		Console.WriteLine($"scratch {slow.Name} base={scratch}");
		var rom = domains["ROM"]!;
		// Before the first unit, right after the seal: nothing has written the machine, so a poke from managed code would be fatal; these go through the guest.
		foreach (long at in new long[] { scratch, scratch + 4095, scratch + 4096, slow.Size - 2 })
		{
			slow.PokeUshort(at, 0xBEEF, true);
			Require(slow.PeekUshort(at, true) == 0xBEEF && slow.PeekByte(at) == 0xBE && slow.PeekByte(at + 1) == 0xEF, $"{slow.Name} word at {at}: poked before the first unit, read back big endian");
		}
		slow.PokeUint(scratch + 8, 0x01020304, true);
		Require(slow.PeekUint(scratch + 8, true) == 0x01020304 && slow.PeekUshort(scratch + 8, false) == 0x0201, "a 32-bit poke reads back and the byte order follows the flag");
		var before = rom.PeekByte(0);
		rom.PokeByte(0, (byte)~before);
		Require(rom.PeekByte(0) == before, "the ROM does not take a poke");
		var bulk = new byte[16];
		rom.BulkPeekByte(0L.RangeToExclusive(16L), bulk);
		for (int i = 0; i < 16; ++i) Require(bulk[i] == rom.PeekByte(i), $"ROM bulk read byte {i}");
		foreach (var bad in new Action[] { () => chip.PeekByte(chip.Size), () => chip.PeekByte(-1), () => chip.PokeByte(chip.Size, 0), () => slow.PeekUshort(slow.Size - 1, true) })
		{
			try { bad(); Require(false, "an out-of-range access throws"); }
			catch (ArgumentOutOfRangeException) { Console.WriteLine("ok an out-of-range access throws"); }
		}
		// After a unit the fixture has written its marker; a poke into the colour word changes the picture.
		var controller = new ScriptedController(core.ControllerDefinition);
		core.FrameAdvance(controller, true, true);
		Require(chip.PeekUint(0x10000, true) == 0x50364D57, "after a unit Chip RAM holds the marker the guest stored, big endian");
		Console.WriteLine("chip-marker " + chip.PeekUint(0x10000, true).ToString("x8"));
	}

	/// <summary>Two machines of the same guest in one process, taking turns: each must do what it does alone.</summary>
	static void Two(Dictionary<string, string> options)
	{
		NativeLibrary.Load(Path.GetFullPath(options["runtime"]));
		var script = new Script(options["script"]);
		using var a = MakeCore(options);
		using var b = MakeCore(options, new Puae6SyncSettings { RandomSeed = 7 });
		var ca = new ScriptedController(a.ControllerDefinition);
		var cb = new ScriptedController(b.ControllerDefinition);
		int units = int.Parse(options["units"]);
		for (int unit = 0; unit < units; ++unit)
		{
			ca.Clear();
			script.Fill(ca, unit, true);                     // A plays the script and B plays nothing: B must not see A's input
			a.FrameAdvance(ca, true, true);
			b.FrameAdvance(cb, true, true);
			Console.WriteLine($"two {unit + 1} a={a.CycleCount} b={b.CycleCount}");
		}
		Console.WriteLine("log-a " + Words(a, 0x1000, 512));
		Console.WriteLine("log-b " + Words(b, 0x1000, 512));
		a.Dispose();                                         // B goes on after A is gone
		for (int unit = units; unit < units + 3; ++unit) { b.FrameAdvance(cb, true, true); Console.WriteLine($"two {unit + 1} b={b.CycleCount}"); }
		Console.WriteLine("log-b-after " + Words(b, 0x1000, 512));
	}

	/// <summary>Disks in a core: the ADF images are given on the command line, the disk buttons are pressed by a schedule ("79:Next Slot;80-85:Insert Disk", 1-based
	/// units) and Chip RAM is written to --dump after the last unit, to be compared with the native media probe's.</summary>
	static void Media(Dictionary<string, string> options)
	{
		NativeLibrary.Load(Path.GetFullPath(options["runtime"]));
		var adfs = options.TryGetValue("adf", out var list) ? list.Split(',').Select(File.ReadAllBytes).ToList() : [];
		var settings = new Puae6SyncSettings
		{
			Region = options.ContainsKey("ntsc") ? Puae6Region.NTSC : Puae6Region.PAL,
			FloppyDrives = int.Parse(options.GetValueOrDefault("drives", "1")),
		};
		var presses = new List<(int From, int To, string Button)>();
		foreach (var item in options.GetValueOrDefault("press", "").Split(';', StringSplitOptions.RemoveEmptyEntries))
		{
			var parts = item.Split(':');
			var range = parts[0].Split('-');
			presses.Add((int.Parse(range[0]), int.Parse(range[^1]), parts[1]));
		}
		using var core = MakeCore(options, settings, adfs);
		var controller = new ScriptedController(core.ControllerDefinition);
		int units = int.Parse(options["units"]);
		for (int unit = 1; unit <= units; ++unit)
		{
			controller.Clear();
			foreach (var (from, to, button) in presses)
				if (unit >= from && unit <= to) controller.Press(button);
			core.FrameAdvance(controller, true, true);
			Console.WriteLine($"unit {core.Frame} ticks={core.CycleCount} events={core.MediaEventsSent} light={(core.DriveLightOn ? 1 : 0)}");
		}
		if (options.TryGetValue("dump", out var dump))
		{
			var chip = core.ServiceProvider.GetService<IMemoryDomains>()!["Chip RAM"]!;
			var bytes = new byte[chip.Size];
			chip.BulkPeekByte(0L.RangeToExclusive(chip.Size), bytes);
			File.WriteAllBytes(dump, bytes);
		}
		Console.WriteLine($"events {core.MediaEventsSent}");
	}
}
