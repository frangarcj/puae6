// T20b: the C# side of the PUAE6 ABI, run with BizHawk's own sources (references/machawk, pinned) and its real WaterboxHost.
//   Ffi.dll layout
//   Ffi.dll pin
//   Ffi.dll identity --guest puae6.wbx --sha HASH
//   Ffi.dll play --runtime libwaterboxhost.dylib --guest puae6.wbx --sha HASH --firmware input_all.rom --script script.txt --units N [--port1 joystick] [--ntsc] [--power-at U,U]
//   Ffi.dll volume --archive FILE --out FILE     the hard file that Puae6Volumes builds from an LHA or a ZIP of files (T64b); "volume bytes=N sha=HEX", or the reason it is not a volume
//   Ffi.dll bench --runtime libwaterboxhost.dylib --guest puae6.wbx --sha HASH --firmware ROM --units N --out FILE [--warmup W] [--machine NAME] [--ntsc 1] [--adf DISK] [--state-rounds R]   (T33, Bench.cs)
//   Ffi.dll iobench --out FILE [--iterations N]   equivalence and cost of the controller questions and of the picture copy next to the forms they replaced (T34, IoBench.cs)
//   Ffi.dll open --runtime libwaterboxhost.dylib --guest puae6.wbx --sha HASH --manifest waterbox-memory.json --firmware boot.rom --units N
// Every check prints a line "ok ..." and any failure ends the process with a nonzero exit code and the reason on stderr.
using System.Runtime.InteropServices;
using System.Runtime.CompilerServices;
using System.Security.Cryptography;
using System.Text.Json;

using BizHawk.Common;
using BizHawk.Emulation.Common;
using BizHawk.Emulation.Cores.Waterbox;

using Puae6;

internal static unsafe partial class Program
{
	static void Require(bool ok, string what)
	{
		if (!ok) throw new InvalidOperationException($"FAIL: {what}");
		Console.WriteLine($"ok {what}");
	}

	static int Main(string[] args)
	{
		try
		{
			var options = args.Skip(1).Chunk(2).ToDictionary(pair => pair[0].TrimStart('-'), pair => pair.Length > 1 ? pair[1] : "");
			switch (args.FirstOrDefault())
			{
				case "layout": Layout(); break;
				case "settings": Settings(); break;
                case "cdlayout": Require(sizeof(P6CdInfo) == 2432 && Marshal.OffsetOf<P6CdInfo>(nameof(P6CdInfo.Bytes)).ToInt64() == 40
                    && Marshal.OffsetOf<P6CdInfo>(nameof(P6CdInfo.Tracks)).ToInt64() == 56, "private CD layout");
                    Require(sizeof(P6CdInfoV2) == 4808 && sizeof(P6CdTrackV2) == 48
                     && Marshal.OffsetOf<P6CdInfoV2>(nameof(P6CdInfoV2.Bytes)).ToInt64() == 40
                     && Marshal.OffsetOf<P6CdInfoV2>(nameof(P6CdInfoV2.Tracks)).ToInt64() == 56, "regional CD layout");
                    var fields = new[] { nameof(P6CdTrackV2.Index0), nameof(P6CdTrackV2.Index1), nameof(P6CdTrackV2.End),
                     nameof(P6CdTrackV2.Mode), nameof(P6CdTrackV2.Control), nameof(P6CdTrackV2.Reserved),
                     nameof(P6CdTrackV2.StoredPregap), nameof(P6CdTrackV2.SyntheticPregap), nameof(P6CdTrackV2.Postgap),
                     nameof(P6CdTrackV2.Flags), nameof(P6CdTrackV2.PregapMode), nameof(P6CdTrackV2.PregapControl) };
                    for (int i = 0; i < fields.Length; ++i)
                     Require(Marshal.OffsetOf<P6CdTrackV2>(fields[i]).ToInt64() == i * 4, "regional CD " + fields[i]);
                    break;
				case "cd32log": Cd32Log(); break; // Controller definition / real BK2 log, no sandbox.
				case "mouse2log": Mouse2Log(); break;
				case "pointerlog": PointerLog(); break;
                case "parallellog": ParallelLog(); break;
				case "pin": Console.WriteLine("pin " + Puae6Guest.BuiltWithSha256); break;
				case "identity": Identity(options["guest"], options["sha"]); break;
				case "open": Open(options, int.Parse(options["units"])); break;
				case "cartridges": Cartridges(options); break;
				case "fmvstate": FmvState(options); break;
				case "heaps":
				{
					// heaps [--machine A500|A600|A1200] [--chip Auto|KB512|MB1|MB2] [--slow ...] [--fast ...] [--writes]: the heaps of the core for those settings (T49: the mmap heap follows the memory).
					var settings = new Puae6SyncSettings();
					if (options.TryGetValue("machine", out var machine)) settings.Machine = Enum.Parse<Puae6Machine>(machine);
					if (options.TryGetValue("chip", out var chip)) settings.ChipMemory = Enum.Parse<Puae6Chip>(chip);
					if (options.TryGetValue("slow", out var slow)) settings.SlowMemory = Enum.Parse<Puae6Slow>(slow);
					if (options.TryGetValue("fast", out var fast)) settings.FastMemory = Enum.Parse<Puae6Fast>(fast);
					if (options.TryGetValue("z3", out var z3)) settings.Z3Memory = Enum.Parse<Puae6Z3Memory>(z3);
					settings.FloppyWrites = options.ContainsKey("writes");
					// --hdf-bytes N[,N...] (T63b): the sizes of the hard files the core would be given.
					var hard = options.TryGetValue("hdf-bytes", out var sizes) ? sizes.Split(',', StringSplitOptions.RemoveEmptyEntries).Select(long.Parse).ToArray() : [];
					var h = Puae6Core.Heaps(settings, hard); Console.WriteLine($"heaps {h.SbrkHeapSizeKB} {h.SealedHeapSizeKB} {h.InvisibleHeapSizeKB} {h.PlainHeapSizeKB} {h.MmapHeapSizeKB}"); break;
				}
				case "volume":
				{
					var archive = File.ReadAllBytes(options["archive"]);
					byte[] image;
					try { image = Puae6Volumes.FromArchive(archive); }
					catch (VolumeException e) { Console.WriteLine($"volume refused {e.Message}"); return 2; }
					if (options.TryGetValue("out", out var outPath)) File.WriteAllBytes(outPath, image);
					Console.WriteLine($"volume bytes={image.Length} sha={Convert.ToHexString(SHA256.HashData(image)).ToLowerInvariant()} zip={Puae6Volumes.IsVolumeZip(archive)}");
					break;
				}
				case "whdbundle":
				{
					var game = File.ReadAllBytes(options["archive"]);
					var kickstarts = options.TryGetValue("kickstarts", out var assets) ? Puae6Volumes.ReadArchive(File.ReadAllBytes(assets)).Where(f => !f.IsDirectory).ToDictionary(f => f.Path, f => f.Data) : null;
					bool wait = options.GetValueOrDefault("buttonwait", "false") == "true";
					var images = options.TryGetValue("provider", out var provider)
						? Puae6WhdLoad.Prepare(game, File.ReadAllBytes(provider), wait, kickstarts)
						: Puae6WhdLoad.Build(Puae6Volumes.ReadArchive(game), File.ReadAllBytes(options["program"]), File.ReadAllBytes(options["vfs"]), wait, kickstarts);
					if (options.TryGetValue("out", out var directory))
					{
						Directory.CreateDirectory(directory);
						foreach (var image in images) File.WriteAllBytes(Path.Combine(directory, image.Key), image.Value);
					}
					foreach (var image in images) Console.WriteLine($"bundle {image.Key} bytes={image.Value.Length} sha={GuestIdentity.Sha256Hex(image.Value)}");
					break;
				}
				case "whdresolve":
				{
					var settings = new Puae6SyncSettings { Machine = MachineOf(options.GetValueOrDefault("machine")), WhdLoad = Enum.Parse<Puae6WhdLoadMode>(options.GetValueOrDefault("whdload", "Auto"), true), WhdLoadButtonWait = options.GetValueOrDefault("buttonwait") == "true" };
					var before = settings.Machine;
					var source = new Puae6Parameters { SyncSettings = settings, Volumes = [ File.ReadAllBytes(options["archive"]) ], Media = options.ContainsKey("mixed") ? [ new byte[1] ] : [],
						WhdLoadProvider = options.TryGetValue("provider", out var provider) ? File.ReadAllBytes(provider) : null };
					var resolved = Puae6WhdLoad.Resolve(source, machine => { Console.WriteLine($"firmware {machine}"); return new byte[] { 0x11, 0x14, 0x4e, 0xf9 }; },
						options.TryGetValue("providerpath", out var path) ? () => Puae6WhdLoad.ReadProviderFile(path) : null);
					resolved = Puae6Volumes.Resolve(resolved);
					Require(settings.Machine == before && source.HardFiles.Count == 0 && source.Volumes.Count == 1, "loader preserves caller parameters");
					Require(Puae6SyncSettings.NeedsReboot(settings, new Puae6SyncSettings { Machine = before, WhdLoad = settings.WhdLoad == Puae6WhdLoadMode.Auto ? Puae6WhdLoadMode.Disabled : Puae6WhdLoadMode.Auto }), "WHDLoad setting requires restart");
					Console.WriteLine($"resolved machine={resolved.SyncSettings.Machine} hard={resolved.HardFiles.Count} volumes={resolved.Volumes.Count}");
					if (options.TryGetValue("out", out var directory))
					{
						Directory.CreateDirectory(directory);
						for (int i = 0; i < resolved.HardFiles.Count; ++i) File.WriteAllBytes(Path.Combine(directory, $"hard-{i}.hdf"), resolved.HardFiles[i]);
					}
					break;
				}
				case "whdclassify":
					var zip = File.ReadAllBytes(options["archive"]);
					Console.WriteLine($"auto={Puae6Volumes.IsVolumeZip(zip, true)} disabled={Puae6Volumes.IsVolumeZip(zip)}"); break;
				case "play": Play(options); break;
				case "session": RunSession(options); break;
				case "bench": Bench(options); break;                 // T33: tools/bench.py
				case "iobench": IoBench(options); break;             // T34: tests/bench/test_core_io.py
				case "inventory": Inventory(); break;
				case "media": Media(options); break;
				case "saveramformat": SaveRamFormat(); break;
				case "av": Av(options); break;
				case "domains": Domains(options); break;
				case "two": Two(options); break;
				default: throw new ArgumentException("usage: layout | identity | open");
			}
			return 0;
		}
		catch (Exception e)
		{
			Console.Error.WriteLine(e is GuestRejectedException or InvalidOperationException ? e.Message : e.ToString());
			return 1;
		}
	}

	static void Settings()
	{
		var active = new Puae6SyncSettings();
		var core = (Puae6Core)RuntimeHelpers.GetUninitializedObject(typeof(Puae6Core));
		var fields = typeof(Puae6Core).GetFields(System.Reflection.BindingFlags.Instance | System.Reflection.BindingFlags.NonPublic);
		fields.Single(f => f.Name == "_syncSettings").SetValue(core, active);
		fields.Single(f => f.Name == "_parameters").SetValue(core, new Puae6Parameters { SyncSettings = active.Clone() });

		var requested = core.GetSyncSettings();
		requested.ControllerPort2 = Puae6Port2.Cd32;
		requested.FloppyDrives = 0;
		Require(core.PutSyncSettings(requested) == PutSettingsDirtyBits.RebootCore, "changed sync settings request a core reboot");
		var pending = core.GetSyncSettings();
		Require(pending.ControllerPort2 == Puae6Port2.Cd32 && pending.FloppyDrives == 0, "controller and zero-drive settings remain pending for EmuHawk");
		requested.ControllerPort2 = Puae6Port2.Joystick;
		requested.FloppyDrives = 4;
		Require(core.GetSyncSettings().ControllerPort2 == Puae6Port2.Cd32 && core.GetSyncSettings().FloppyDrives == 0, "pending sync settings are a defensive copy");
		var stillActive = (Puae6SyncSettings)fields.Single(f => f.Name == "_syncSettings").GetValue(core)!;
		Require(stillActive.ControllerPort2 == Puae6Port2.Joystick && stillActive.FloppyDrives == 1, "the live machine does not change before reboot");
		Require(core.PutSyncSettings(active.Clone()) == PutSettingsDirtyBits.None, "restoring the live settings cancels the pending reboot difference");

		var presentation = System.ComponentModel.TypeDescriptor.GetProperties(typeof(Puae6Settings));
		Require(presentation[nameof(Puae6Settings.DriveSoundVolume)]!.DisplayName == "Floppy drive sound volume", "the live volume says it is mechanical floppy sound");
		var sync = System.ComponentModel.TypeDescriptor.GetProperties(typeof(Puae6SyncSettings));
		var floppyDrives = sync[nameof(Puae6SyncSettings.FloppyDrives)]!;
		var range = (System.ComponentModel.DataAnnotations.RangeAttribute)floppyDrives.Attributes[typeof(System.ComponentModel.DataAnnotations.RangeAttribute)]!;
		Require(floppyDrives.DisplayName == "Floppy disk drives" && (int)range.Minimum == 0 && (int)range.Maximum == 4
			&& floppyDrives.Converter is ConstrainedIntConverter, "the old UAE-compatible drive count is labelled and bounded from zero to four");
		var physicalDrives = sync[nameof(Puae6SyncSettings.PhysicalFloppyDrives)]!;
		Require(!physicalDrives.IsBrowsable, "the obsolete split physical-drive setting is hidden from the PropertyGrid");
		Require(P6.FloppyDrivesNone == byte.MaxValue, "the explicit no-drive ABI value does not change historical zero-as-two callers");
		Require(Puae6Core.AudioRateHz == 44100, "the core produces BizHawk's synchronous 44.1 kHz rate without a transient frontend inference");
		Require(Puae6Core.CartridgeModel(Puae6Cartridge.Fmv) == 32 && Puae6Core.ConfigFlags(Puae6Cartridge.Fmv, false, Puae6Machine.CD32) == 0,
			"FMV is transported as native cartridge model 32 without consuming a config flag");
		Require(!Puae6Core.IsFreezerCartridge(Puae6Cartridge.Fmv) && Puae6Core.IsFreezerCartridge(Puae6Cartridge.Ar3),
			"FMV is not a freezer while Action Replay remains one");
		var fmvSettings = new Puae6SyncSettings { Machine = Puae6Machine.CD32, Cartridge = Puae6Cartridge.Fmv };
		var fmvController = Controllers.Define(fmvSettings, 1);
		Require(!fmvController.BoolButtons.Contains(Controllers.Freeze), "the FMV controller definition does not publish Freeze");
	}

	/// <summary>Every instance field of the core and every field of its saved state, by reflection: tests/waterbox-state compares them with manifests/state-inventory.json.</summary>
	static void Inventory()
	{
		var flags = System.Reflection.BindingFlags.Instance | System.Reflection.BindingFlags.Public | System.Reflection.BindingFlags.NonPublic | System.Reflection.BindingFlags.DeclaredOnly;
		foreach (var field in typeof(Puae6Core).GetFields(flags))
			Console.WriteLine($"field {(field.Name.StartsWith('<') ? field.Name[1..field.Name.IndexOf('>')] : field.Name)}");
		var state = typeof(Puae6Core).GetNestedType("WrapperState", System.Reflection.BindingFlags.NonPublic) ?? throw new InvalidOperationException("WrapperState is gone");
		foreach (var field in state.GetFields(System.Reflection.BindingFlags.Instance | System.Reflection.BindingFlags.Public)) Console.WriteLine($"saved {field.Name}");
	}

	static void Layout()
	{
		Console.WriteLine("layout " + string.Join(' ', AbiLayout.Compute()));
		// Every field of every struct, by the offset the C# layout gives it ("field Struct.name offset"): tests/ffi/test_ffi.py compares them with what a C compiler makes of the headers.
		void Dump<T>(string name, params string[] fields) where T : unmanaged
		{
			Console.WriteLine($"size {name} {sizeof(T)}");
			foreach (string field in fields) Console.WriteLine($"field {name}.{field} {Marshal.OffsetOf<T>(field)}");
		}
		Dump<P6Config>("P6Config", "AbiVersion", "StructSize", "Machine", "VideoStandard", "AudioRateHz", "Flags", "RandomSeed", "RtcEpochSeconds", "ChipKib", "SlowKib", "FastKib", "FpuModel", "SoundSeparation", "SoundInterpolation", "SoundFilter", "SoundFilterType", "FloppySpeed", "VideoResolution", "VideoLineMode", "CpuModel", "CpuMultiplier", "HdfController", "CpuCompatibility", "FloppyDrives", "CollisionLevel", "Reserved7", "Z3Memory", "Reserved8");
		Dump<P6PortInput>("P6PortInput", "Type", "Buttons", "Dx", "Dy", "Reserved");
		Dump<P6Input>("P6Input", "AbiVersion", "StructSize", "Keys", "Port0", "Port1", "Reserved");
		Dump<P6Rect>("P6Rect", "X", "Y", "Width", "Height");
		Dump<P6Frame>("P6Frame", "AbiVersion", "StructSize", "Video", "VideoBytes", "VideoFormat", "VideoWidth", "VideoHeight", "VideoStrideBytes", "Active",
			"AspectNum", "AspectDen", "Audio", "AudioFrames", "MasterTicks", "UnitIndex", "CadenceNum", "CadenceDen", "FieldParity", "Flags", "Reserved");
		Dump<P6wMedia>("P6wMedia", "Type", "Flags", "ContentId", "Data", "Bytes");
		Dump<P6wInit>("P6wInit", "AbiVersion", "StructSize", "Config", "Firmware", "FirmwareBytes", "MediaCount", "Reserved0", "Media", "Result", "Reserved1", "Error");
		Dump<P6wFrame>("P6wFrame", "AbiVersion", "StructSize", "Frame", "Result", "Reserved", "Error");
		Dump<P6wMemoryArea>("P6wMemoryArea", "Data", "Name", "Size", "Flags");
		Dump<P6MediaEvent>("P6MediaEvent", "AbiVersion", "StructSize", "Type", "Drive", "MediaIndex", "Reserved0", "Sequence", "Reserved");
		Dump<P6wMediaEvent>("P6wMediaEvent", "AbiVersion", "StructSize", "Event", "Result", "Reserved", "Error");
		Dump<P6MediaWriteStatus>("P6MediaWriteStatus", "AbiVersion", "StructSize", "MediaIndex", "Reserved0", "OverlayCapacity", "OverlayUsed", "MediumSectors", "RefusedWrites", "RawTracks", "Reserved");
		Dump<P6wMediaWriteStatus>("P6wMediaWriteStatus", "AbiVersion", "StructSize", "Status", "Result", "Reserved", "Error");
		Dump<P6wMediaExport>("P6wMediaExport", "AbiVersion", "StructSize", "MediaIndex", "Reserved0", "Buffer", "Capacity", "Written", "Result", "Reserved1", "Error");
	}

	/// <summary>Host memory with a guard band on both sides, which the guest must leave as it finds it.</summary>
	sealed class Guarded : IDisposable
	{
		const int Margin = 64;
		readonly nint block;
		internal readonly byte* Address;
		internal readonly int Size;
		internal Guarded(int size)
		{
			Size = size;
			block = Marshal.AllocHGlobal(size + 2 * Margin);
			new Span<byte>((void*)block, size + 2 * Margin).Fill(0xCD);
			Address = (byte*)block + Margin;
		}
		internal Span<byte> Payload => new(Address, Size);
		internal bool BandsIntact()
		{
			var all = new ReadOnlySpan<byte>((void*)block, Size + 2 * Margin);
			return all[..Margin].IndexOfAnyExcept((byte)0xCD) < 0 && all[(Margin + Size)..].IndexOfAnyExcept((byte)0xCD) < 0;
		}
		public void Dispose() => Marshal.FreeHGlobal(block);
	}

	/// <summary>The identity gate: what it accepts and every way it refuses, none of which gets as far as creating the host.</summary>
	static void Identity(string guestPath, string sha)
	{
		var image = File.ReadAllBytes(guestPath);
		Require(Convert.ToHexString(SHA256.HashData(image)).ToLowerInvariant() == sha.ToLowerInvariant(), "the guest on disk is the one that was pinned");
		string directory = Path.Combine(Path.GetTempPath(), "p6-ffi-identity-" + Environment.ProcessId);
		Directory.CreateDirectory(directory);
		int hosts = 0;
		var original = Puae6Guest.CreateHost;
		Puae6Guest.CreateHost = options => { ++hosts; throw new InvalidOperationException("the host was created"); };
		File.WriteAllBytes(Path.Combine(directory, "uae.wbx"), image);        // a decoy: the old core's file name must never be a way out
		try
		{
			var heaps = new WaterboxOptions { SbrkHeapSizeKB = 4096, SealedHeapSizeKB = 16384, InvisibleHeapSizeKB = 16, PlainHeapSizeKB = 16, MmapHeapSizeKB = 16384 };
			void Refused(string what, byte[]? content, GuestRejection reason, string? pin = null, Action? setup = null)
			{
				string name = $"case{reason}-{what.GetHashCode():x}.wbx";
				string path = Path.Combine(directory, name);
				if (content != null) File.WriteAllBytes(path, content);
				setup?.Invoke();
				var thrown = Assert.Throws<GuestRejectedException>(() => Puae6Guest.Open(directory, name, pin ?? Convert.ToHexString(SHA256.HashData(content ?? [])), heaps));
				Require(thrown.Reason == reason && thrown.Message.Length > 0, $"{what}: refused as {reason} ({thrown.Message})");
				Require(hosts == 0, $"{what}: wbx_create_host was not reached");
			}

			byte[] Patched(Action<byte[]> edit) { var copy = (byte[])image.Clone(); edit(copy); return copy; }
			// x86-64: the hash is the one of the patched file, so that only the machine can refuse it.
			Refused("an x86-64 ELF whose hash is pinned", Patched(b => { b[18] = 0x3E; b[19] = 0; }), GuestRejection.WrongMachine);
			Refused("an ELF for another machine (RISC-V)", Patched(b => { b[18] = 0xF3; b[19] = 0; }), GuestRejection.WrongMachine);
			Refused("a 32-bit ELF", Patched(b => b[4] = 1), GuestRejection.WrongClassOrByteOrder);
			Refused("a big-endian ELF", Patched(b => b[5] = 2), GuestRejection.WrongClassOrByteOrder);
			Refused("the AArch64 guest with one byte changed", Patched(b => b[b.Length / 2] ^= 1), GuestRejection.WrongHash, pin: sha);
			Refused("a different hash for the right file", image, GuestRejection.WrongHash, pin: new string('0', 64));
			Refused("a truncated header", image[..40], GuestRejection.NotElf);
			Refused("an empty file", [], GuestRejection.NotElf);
			Refused("something that is not an ELF", System.Text.Encoding.ASCII.GetBytes(new string('x', 4096)), GuestRejection.NotElf);
			Refused("a file that does not exist", null, GuestRejection.Missing, pin: sha);
			// A compressed sibling would be what the host loads: the verified file would not be the one that runs.
			File.WriteAllBytes(Path.Combine(directory, "good.wbx"), image);
			File.WriteAllBytes(Path.Combine(directory, "good.wbx.zst"), [1, 2, 3]);
			var sibling = Assert.Throws<GuestRejectedException>(() => Puae6Guest.Open(directory, "good.wbx", sha, heaps));
			Require(sibling.Reason == GuestRejection.AmbiguousCompressedSibling && hosts == 0, "a .zst sibling is refused: the host would load that one");
			Assert.Throws<ArgumentException>(() => Puae6Guest.Open(directory, "good.wbx", "not a hash", heaps));
			Require(hosts == 0, "a malformed pin is refused before anything is read");

			// The good guest is accepted and gets as far as the host.
			File.Delete(Path.Combine(directory, "good.wbx.zst"));
			var verified = GuestIdentity.Verify(Path.Combine(directory, "good.wbx"), sha);
			Require(verified.Sha256 == sha.ToLowerInvariant() && verified.Bytes == image.Length, "the pinned AArch64 guest passes the gate");
			var reached = Assert.Throws<InvalidOperationException>(() => Puae6Guest.Open(directory, "good.wbx", sha, heaps));
			Require(hosts == 1 && reached.Message == "the host was created", "and only it reaches wbx_create_host");
		}
		finally
		{
			Puae6Guest.CreateHost = original;
			Directory.Delete(directory, true);
		}
	}

	static class Assert
	{
		internal static T Throws<T>(Action action) where T : Exception
		{
			try { action(); }
			catch (T e) { return e; }
			catch (Exception e) { throw new InvalidOperationException($"FAIL: expected {typeof(T).Name}, got {e.GetType().Name}: {e.Message}"); }
			throw new InvalidOperationException($"FAIL: expected {typeof(T).Name}, nothing was thrown");
		}
	}

	/// <summary>The guest opened by the real WaterboxHost: layout agreed, canaries around what the guest is handed, refusals that touch nothing, and a machine that runs.</summary>
	static void Open(Dictionary<string, string> options, int units)
	{
		// BizHawk asks dlopen for the bare name libwaterboxhost.dylib; on macOS that only finds it when it is already loaded, which loading it by its
		// path first arranges (EmuHawk ships it next to the executable and sets the search path itself). LoadRuntime also sets the eviction (Runtime.cs).
		LoadRuntime(options["runtime"]);
		string guestPath = Path.GetFullPath(options["guest"]);
		var layout = JsonDocument.Parse(File.ReadAllText(options["manifest"])).RootElement.GetProperty("layout");
		uint Kb(string name) => layout.GetProperty(name).GetUInt32() / 1024;
		var heaps = new WaterboxOptions { SbrkHeapSizeKB = Kb("sbrk"), SealedHeapSizeKB = Kb("sealed"), InvisibleHeapSizeKB = Kb("invisible"),
			PlainHeapSizeKB = Kb("plain"), MmapHeapSizeKB = Kb("mmap") };
		using var guest = Puae6Guest.Open(Path.GetDirectoryName(guestPath)!, Path.GetFileName(guestPath), options["sha"], heaps);
		Require(guest.Identity.Sha256 == options["sha"].ToLowerInvariant(), "the guest was opened by BizHawk's WaterboxHost and its layout is the wrapper's");
		var firmware = File.ReadAllBytes(options["firmware"]);

		using (guest.Host.EnterExit())
		{
			// Refusals: the guest answers with a code and a message and changes nothing of what it was handed but Result and Error.
			var cases = new (string Name, Action<P6wInit>? Unused, Func<int> Run)[0];
			void Refusal(string name, int expected, Func<P6wInit, P6wInit> edit)
			{
				using var args = new Guarded(sizeof(P6wInit));
				var init = default(P6wInit);
				init.AbiVersion = 1; init.StructSize = (uint)sizeof(P6wInit);
				init.Config.AbiVersion = 1; init.Config.StructSize = (uint)sizeof(P6Config); init.Config.Machine = P6.MachineA500;
				init.Config.VideoStandard = P6.VideoPal; init.Config.AudioRateHz = 48000; init.Config.RandomSeed = 1; init.Config.RtcEpochSeconds = 946684800;
				fixed (byte* rom = firmware)
				{
					init.Firmware = rom; init.FirmwareBytes = (ulong)firmware.Length;
					init = edit(init);
					*(P6wInit*)args.Address = init;
					var before = args.Payload.ToArray();
					int result = guest.Lib.p6w_init_machine((IntPtr)args.Address);
					var after = args.Payload;
					int resultAt = (int)AbiLayout.Compute()[1], errorAt = (int)AbiLayout.Compute()[2];
					Require(result == expected && *(int*)(args.Address + resultAt) == expected, $"{name}: refused with {P6.ResultName(expected)}");
					Require(Marshal.PtrToStringUTF8((IntPtr)(args.Address + errorAt))!.Length > 0, $"{name}: the guest says why");
					Require(after[..resultAt].SequenceEqual(before.AsSpan(..resultAt)) && after[(errorAt + P6.ErrorTextCapacity)..].SequenceEqual(before.AsSpan((errorAt + P6.ErrorTextCapacity)..)),
						$"{name}: nothing but result and error was written");
					Require(args.BandsIntact(), $"{name}: the guard bands around p6w_init are intact");
				}
			}
			Refusal("a wrong ABI version", P6.BadArgument, i => { i.AbiVersion = 2; return i; });
			Refusal("a wrong struct size", P6.BadArgument, i => { i.StructSize -= 4; return i; });
			Refusal("more media than the contract allows", P6.BadArgument, i => { i.MediaCount = 17; return i; });
			Refusal("no firmware", P6.BadArgument, i => { i.Firmware = null; i.FirmwareBytes = 0; return i; });
			Require(guest.Lib.p6w_memory_areas(0, 4) == 0, "no memory area is reported before a machine is loaded");

			// A good init, the seal, and units that advance: the first thing that proves the machine runs under BizHawk's host.
			using (var args = new Guarded(sizeof(P6wInit)))
			{
				var init = default(P6wInit);
				init.AbiVersion = 1; init.StructSize = (uint)sizeof(P6wInit);
				init.Config.AbiVersion = 1; init.Config.StructSize = (uint)sizeof(P6Config); init.Config.Machine = P6.MachineA500;
				init.Config.VideoStandard = P6.VideoPal; init.Config.AudioRateHz = 48000; init.Config.RandomSeed = 1; init.Config.RtcEpochSeconds = 946684800;
				fixed (byte* rom = firmware)
				{
					init.Firmware = rom; init.FirmwareBytes = (ulong)firmware.Length;
					*(P6wInit*)args.Address = init;
					Require(guest.Lib.p6w_init_machine((IntPtr)args.Address) == 0 && args.BandsIntact(), "a good init succeeds");
				}
			}
			guest.Host.Seal();
		}

		using var input = new Guarded(sizeof(P6Input));
		using var output = new Guarded(sizeof(P6wFrame));
		for (int unit = 1; unit <= units; ++unit)
		{
			var packet = default(P6Input);
			packet.AbiVersion = 1; packet.StructSize = (uint)sizeof(P6Input);
			packet.Port0.Type = P6.PortJoystick; packet.Port1.Type = P6.PortJoystick;
			*(P6Input*)input.Address = packet;
			var frame = default(P6wFrame);
			frame.AbiVersion = 1; frame.StructSize = (uint)sizeof(P6wFrame);
			*(P6wFrame*)output.Address = frame;
			var sent = input.Payload.ToArray();
			using (guest.Host.EnterExit())
			{
				int result = guest.Lib.p6w_frame_advance((IntPtr)input.Address, P6.RenderVideo | P6.RenderAudio, (IntPtr)output.Address);
				var got = *(P6wFrame*)output.Address;
				if (result != 0) throw new InvalidOperationException($"FAIL: unit {unit} returned {P6.ResultName(result)}: {Marshal.PtrToStringUTF8((IntPtr)got.Error)}");
				Require(got.Frame.UnitIndex == (ulong)unit && got.Frame.VideoFormat == P6.PixelXrgb8888 && got.Frame.Video != null, $"unit {unit} delivered picture number {got.Frame.UnitIndex}");
				Console.WriteLine($"unit {got.Frame.UnitIndex} ticks={got.Frame.MasterTicks}");
			}
			Require(input.Payload.SequenceEqual(sent) && input.BandsIntact() && output.BandsIntact(), $"unit {unit}: the guest left p6_input as it found it and the guard bands are intact");
		}
	}
}
