using System.Buffers.Binary;
using System.IO.Compression;
using System.Runtime.InteropServices;
using System.Text;

using BizHawk.Emulation.Common;
using BizHawk.Emulation.Cores;
using BizHawk.Emulation.Cores.Computers.Amiga;
using BizHawk.Emulation.DiscSystem;

namespace Waterbox.UaeCd32Headless;

internal sealed class FirmwareProvider : ICoreFileProvider
{
	private readonly Dictionary<string, (string Name, byte[] Data)> _files = new();

	public FirmwareProvider(string root)
	{
		void Add(string id, string pattern)
		{
			var path = Directory.EnumerateFiles(root, pattern).OrderBy(p => p).FirstOrDefault();
			if (path is not null) _files[id] = (Path.GetFileName(path), File.ReadAllBytes(path));
		}
		Add("CD32", "kick40060.CD32");
		Add("CD32ext", "kick40060.CD32.ext");
		Add("CD32FMV", "*cd32*fmv*");
		Add("CDTVext", "*CDTV*Ext*");
		Add("A1200", "*A1200*.rom");
		Add("A500", "*A500*.rom");
		Add("A600", "*A600*.rom");
		Add("A4000", "*A4000*.rom");
	}

	public string GetRetroSaveRAMDirectory(string corePath) => throw new NotSupportedException();
	public string GetRetroSystemPath(string corePath) => throw new NotSupportedException();
	public string GetUserPath(string sysID, bool temp) => throw new NotSupportedException();
	public string GetPalettesPath(string sysID) => throw new NotSupportedException();
	public byte[]? GetFirmware(FirmwareID id, string? msg = null) => TryGet(id, out var d) ? d : null;
	public byte[] GetFirmwareOrThrow(FirmwareID id, string? msg = null) => TryGet(id, out var d) ? d : throw new MissingFirmwareException(id.ToString());
	public (byte[] FW, GameInfo Game) GetFirmwareWithGameInfoOrThrow(FirmwareID id, string? msg = null)
	{
		if (!TryGet(id, out var data)) throw new MissingFirmwareException(id.ToString());
		return (data, new GameInfo { Name = _files[id.Firmware].Name, System = VSystemID.Raw.Amiga });
	}

	private bool TryGet(FirmwareID id, out byte[] data)
	{
		data = Array.Empty<byte>();
		if (id.System != VSystemID.Raw.Amiga || !_files.TryGetValue(id.Firmware, out var f)) return false;
		data = f.Data;
		return true;
	}
}

internal sealed class RomAsset : IRomAsset
{
	public required byte[] RomData { get; init; }
	public required byte[] FileData { get; init; }
	public required string Extension { get; init; }
	public required string RomPath { get; init; }
	public GameInfo? Game => null;
}

internal sealed class DiscAsset : IDiscAsset
{
	public required Disc DiscData { get; init; }
	public DiscType DiscType => DiscType.Amiga;
	public string DiscName { get; set; } = string.Empty;
}

internal sealed class TestController : IController
{
	private readonly HashSet<string> _pressed = new();
	public TestController(ControllerDefinition definition) => Definition = definition;
	public ControllerDefinition Definition { get; }
	public bool IsPressed(string button) => _pressed.Contains(button);
	public int AxisValue(string name) => Definition.Axes[name].Neutral;
	public IReadOnlyCollection<(string Name, int Strength)> GetHapticsSnapshot() => Array.Empty<(string Name, int Strength)>();
	public void SetHapticChannelStrength(string name, int strength) { }
	public void Set(string button, bool pressed) { if (pressed) _pressed.Add(button); else _pressed.Remove(button); }
}

internal sealed class OpenGlProvider : IOpenGLProvider
{
	public bool SupportsGLVersion(int major, int minor) => false;
	public object RequestGLContext(int major, int minor, bool coreProfile) => throw new NotSupportedException();
	public void ReleaseGLContext(object context) => throw new NotSupportedException();
	public void ActivateGLContext(object context) => throw new NotSupportedException();
	public void DeactivateGLContext() => throw new NotSupportedException();
	public IntPtr GetGLProcAddress(string? proc) => IntPtr.Zero;
}

internal static class Program
{
	/// <summary>Single-word buttons that are not CD32 pad buttons, so a script can name them directly.</summary>
	private static readonly HashSet<string> CoreButtons = new() { "Reset", "Power" };

	private static int[]? _prevRows;
	private static string Ranges(List<int> v)
	{
		if (v.Count == 0) return "-";
		var sb = new StringBuilder(); var start = v[0]; var prev = v[0]; var step = 0;
		for (var i = 1; i <= v.Count; i++)
		{
			var cur = i < v.Count ? v[i] : int.MinValue;
			if (i < v.Count && (step == 0 || cur - prev == step)) { step = cur - prev; prev = cur; continue; }
			sb.Append(start == prev ? $"{start}" : $"{start}..{prev}" + (step == 2 ? "/2" : "")).Append(' ');
			start = prev = cur; step = 0;
		}
		return sb.ToString().TrimEnd();
	}

	private static uint Signature(IVideoProvider video)
	{
		var px = video.GetVideoBuffer();
		var len = video.BufferWidth * video.BufferHeight;
		var sig = 2166136261u;
		for (var i = 0; i < len; i += 16) sig = (sig ^ (uint)px[i]) * 16777619u;
		return sig;
	}

	private static int VisiblePercent(IVideoProvider video)
	{
		var px = video.GetVideoBuffer();
		var len = video.BufferWidth * video.BufferHeight;
		var n = 0;
		for (var i = 0; i < len; i += 4) if ((px[i] & 0xffffff) != 0) n++;
		return n * 400 / Math.Max(1, len);
	}

	private static void WritePng(IVideoProvider video, string path)
	{
		var w = video.BufferWidth; var h = video.BufferHeight; var px = video.GetVideoBuffer();
		using var raw = new MemoryStream();
		for (var y = 0; y < h; y++)
		{
			raw.WriteByte(0);
			for (var x = 0; x < w; x++) { var p = px[y * w + x]; raw.WriteByte((byte)(p >> 16)); raw.WriteByte((byte)(p >> 8)); raw.WriteByte((byte)p); }
		}
		using var comp = new MemoryStream();
		using (var z = new ZLibStream(comp, CompressionLevel.Fastest, leaveOpen: true)) { raw.Position = 0; raw.CopyTo(z); }
		using var o = File.Create(path);
		o.Write([137, 80, 78, 71, 13, 10, 26, 10]);
		Span<byte> hdr = stackalloc byte[13];
		BinaryPrimitives.WriteUInt32BigEndian(hdr, (uint)w); BinaryPrimitives.WriteUInt32BigEndian(hdr[4..], (uint)h); hdr[8] = 8; hdr[9] = 2;
		Chunk(o, "IHDR", hdr); Chunk(o, "IDAT", comp.ToArray()); Chunk(o, "IEND", []);
	}

	private static void Chunk(Stream o, string type, ReadOnlySpan<byte> data)
	{
		var t = Encoding.ASCII.GetBytes(type);
		Span<byte> len = stackalloc byte[4];
		BinaryPrimitives.WriteUInt32BigEndian(len, (uint)data.Length);
		o.Write(len); o.Write(t); o.Write(data);
		var crc = 0xffffffffu;
		foreach (var v in t) crc = Crc(crc, v);
		foreach (var v in data) crc = Crc(crc, v);
		BinaryPrimitives.WriteUInt32BigEndian(len, ~crc); o.Write(len);
	}

	private static uint Crc(uint crc, byte v)
	{
		crc ^= v;
		for (var b = 0; b < 8; b++) crc = (crc & 1) != 0 ? (crc >> 1) ^ 0xedb88320u : crc >> 1;
		return crc;
	}

	// usage: <firmware-dir> <cue|hdf> <frames> <out-dir> [script]
	// script: comma separated "start-end:Button" ranges, e.g. "300-330:Play,900-910:Red" (CD32 pad)
	//         or "start-end:P1 Joystick Button 1" / "start-end:Key Return" (any button name, used verbatim)
	// env STATE_TEST=<frame>  save a state at <frame>, run 60 more frames, reload it and check the replay matches
	// env STATE_SAVE=<path>   write a state to <path> after the last frame
	// env STATE_LOAD=<path>   load a state from <path> before the first frame; exits 1 if the core rejects it,
	//                         which is how a state made by an earlier process gets checked (see the WHDLoad regression script)
	// env SAVERAM=1           dump the hard disk SaveRAM diff to <out-dir>/harddisk.saveram at the end and restore it
	// env CD_VOLUME=<0..100>  CD-DA audio volume in percent (default 100, i.e. full volume)
	// env PNG_INTERVAL=<n>    dump a PNG every n frames instead of the default 500
	// env WAV_DUMP=<path>     write all delivered audio samples to a 16-bit stereo PCM WAV file at <path>
	// env AUDIO_LOG=<path>    write a per-frame CSV (frame,count,nz) of delivered sample counts to <path>
	private static int Main(string[] args)
	{
		if (args.Length < 4) { Console.Error.WriteLine("usage: <firmware-dir> <cue|hdf|adf|ipf|none> <frames> <out-dir> [script]"); return 2; }
		var fwDir = Path.GetFullPath(args[0]);
		// "none" boots with no media at all, e.g. to check the CDTV welcome screen with no disc inserted
		var isNone = string.Equals(args[1], "none", StringComparison.OrdinalIgnoreCase);
		var cue = isNone ? "" : Path.GetFullPath(args[1]);
		var isHardDisk = !isNone && (cue.EndsWith(".hdf", StringComparison.OrdinalIgnoreCase)
			|| cue.EndsWith(".lha", StringComparison.OrdinalIgnoreCase) || cue.EndsWith(".zip", StringComparison.OrdinalIgnoreCase));
		var isFloppy = !isNone && (cue.EndsWith(".adf", StringComparison.OrdinalIgnoreCase) || cue.EndsWith(".ipf", StringComparison.OrdinalIgnoreCase));
		var frames = int.Parse(args[2]);
		var outDir = Path.GetFullPath(args[3]);
		Directory.CreateDirectory(outDir);
		var script = new List<(int start, int end, string button)>();
		if (args.Length > 4)
			foreach (var part in args[4].Split(',', StringSplitOptions.RemoveEmptyEntries))
			{
				var s = part.Split(':'); var r = s[0].Split('-');
				var name = s[^1];
				// a name the controller already knows (or any multi-word one) is used as it stands;
				// bare words are CD32 pad buttons, which is what most of these scripts are
				if (name.Contains(' ') || CoreButtons.Contains(name)) { script.Add((int.Parse(r[0]), int.Parse(r[1]), name)); continue; }
				var ports = s.Length > 2 ? new[] { s[1] } : new[] { "P1", "P2" };
				foreach (var port in ports) script.Add((int.Parse(r[0]), int.Parse(r[1]), port + " CD32 pad " + name));
			}

		Console.WriteLine($"arch={RuntimeInformation.ProcessArchitecture} os={RuntimeInformation.OSDescription}");
		var errors = new List<string>();
		Disc? disc = null;
		if (!isHardDisk && !isFloppy && !isNone)
		{
			disc = DiscExtensions.CreateAnyType(cue, errors.Add);
			if (disc is null) throw new InvalidOperationException(string.Join(Environment.NewLine, errors));
			var session = disc.Session1;
			Console.WriteLine($"TOC: {session.InformationTrackCount} tracks, leadout {session.LeadoutLBA}");
		}

		var firmware = new FirmwareProvider(fwDir);
		var comm = new CoreComm(
			showMessage: m => throw new InvalidOperationException(m),
			notifyMessage: (m, _) => Console.WriteLine($"notify: {m}"),
			coreFileProvider: firmware,
			prefs: CoreComm.CorePreferencesFlags.None,
			oglProvider: new OpenGlProvider());
		var settings = isHardDisk
			? new UAE.UAESyncSettings
			{
				MachineConfig = UAE.MachineConfig.A1200_AGA_310_2M_8M,
				ControllerPort1 = LibUAE.ControllerType.Mouse,
				ControllerPort2 = LibUAE.ControllerType.DJoy,
				FloppyDrives = 0,
			}
			: isFloppy
			? new UAE.UAESyncSettings
			{
				// the firmware directory used for the CD32 and HDF runs only carries the A1200 Kickstart
				MachineConfig = UAE.MachineConfig.A1200_AGA_310_2M_8M,
				ControllerPort1 = LibUAE.ControllerType.Mouse,
				ControllerPort2 = LibUAE.ControllerType.DJoy,
				FloppyDrives = 1,
			}
			: new UAE.UAESyncSettings
			{
				MachineConfig = UAE.MachineConfig.CD32,
				ControllerPort1 = LibUAE.ControllerType.CD32Joy,
				ControllerPort2 = LibUAE.ControllerType.CD32Joy,
				FloppyDrives = 0,
			};
		// env SYNC_MACHINE=<MachineConfig name>       override the machine configuration picked above
		// env SYNC_CD_CONTROLLER=<CdController name>  override the CD controller setting (Auto/CD32/IDE/None)
		// env SYNC_PORT1/SYNC_PORT2=<ControllerType>  override the controller ports (e.g. Mouse/DJoy/CD32Joy)
		// env CD32_FMV=1                              enable the CD32 FMV Cartridge sync setting
		// env EXTRA_HDF=<path>                        also load this .hdf as a second Roms entry, alongside
		//                                              whatever the primary <cue|hdf> argument loaded - used to
		//                                              exercise the "CD + hard disk together" config path
		if (Environment.GetEnvironmentVariable("SYNC_MACHINE") is string sm)
			settings.MachineConfig = Enum.Parse<UAE.MachineConfig>(sm);
		if (Environment.GetEnvironmentVariable("SYNC_CD_CONTROLLER") is string scc)
			settings.CdController = Enum.Parse<UAE.CdController>(scc);
		if (Environment.GetEnvironmentVariable("SYNC_PORT1") is string sp1)
			settings.ControllerPort1 = Enum.Parse<LibUAE.ControllerType>(sp1);
		if (Environment.GetEnvironmentVariable("SYNC_PORT2") is string sp2)
			settings.ControllerPort2 = Enum.Parse<LibUAE.ControllerType>(sp2);
		if (Environment.GetEnvironmentVariable("CD32_FMV") == "1")
			settings.Cd32Fmv = true;
		var cdVolume = Environment.GetEnvironmentVariable("CD_VOLUME") is string cv ? int.Parse(cv) : 100;
		var parameters = new CoreLoadParameters<UAE.UAESettings, UAE.UAESyncSettings>
		{
			Comm = comm,
			Game = new GameInfo { Name = isNone ? "none" : Path.GetFileNameWithoutExtension(cue), System = VSystemID.Raw.Amiga },
			Settings = new UAE.UAESettings { CdVolume = cdVolume },
			SyncSettings = settings,
			DeterministicEmulationRequested = true,
		};
		if (isHardDisk || isFloppy)
		{
			var data = File.ReadAllBytes(cue);
			Console.WriteLine(isFloppy
				? $"floppy: {data.LongLength} bytes, {(data.Length >= 4 && data[0] == 'C' && data[1] == 'A' && data[2] == 'P' && data[3] == 'S' ? "IPF" : "ADF")}"
				: $"HDF: {data.LongLength} bytes, rdb={(data.Length >= 4 && data[0] == 'R' && data[1] == 'D' ? 1 : 0)}");
			parameters.Roms.Add(new RomAsset { RomData = data, FileData = data, Extension = Path.GetExtension(cue).ToLowerInvariant(), RomPath = cue });
		}
		else if (!isNone)
		{
			parameters.Discs.Add(new DiscAsset { DiscData = disc!, DiscName = Path.GetFileName(cue) });
		}
		if (Environment.GetEnvironmentVariable("EXTRA_HDF") is string extraHdf)
		{
			var data = File.ReadAllBytes(extraHdf);
			Console.WriteLine($"extra HDF: {data.LongLength} bytes, rdb={(data.Length >= 4 && data[0] == 'R' && data[1] == 'D' ? 1 : 0)}");
			parameters.Roms.Add(new RomAsset { RomData = data, FileData = data, Extension = ".hdf", RomPath = extraHdf });
		}

		var pngInterval = Environment.GetEnvironmentVariable("PNG_INTERVAL") is string pi ? int.Parse(pi) : 500;
		const int audioSampleRate = 44100; // UAE's libretro audio output rate (see cda_new/audio_cda_new_buffer)
		using var wavStream = Environment.GetEnvironmentVariable("WAV_DUMP") is string wavPath ? new FileStream(wavPath, FileMode.Create) : null;
		var wavSampleCount = 0L;
		if (wavStream is not null) wavStream.Write(new byte[44]); // placeholder header, patched at the end
		using var audioLogWriter = Environment.GetEnvironmentVariable("AUDIO_LOG") is string audioLogPath ? new StreamWriter(audioLogPath) : null;
		audioLogWriter?.WriteLine("frame,count,nz");

		using var emulator = new UAE(parameters);
		var controller = new TestController(emulator.ControllerDefinition);
		var video = (IVideoProvider)emulator;
		var sound = (ISoundProvider)emulator;
		var light = (IDriveLight)emulator;
		var sw = System.Diagnostics.Stopwatch.StartNew();
		uint lastSig = 0; var sameCount = 0; long nonzero = 0;
		var stateFrame = Environment.GetEnvironmentVariable("STATE_TEST") is string st ? int.Parse(st) : -1;
		byte[]? state = null; var stateSigs = new List<uint>(); var replaySigs = new List<uint>(); var replaying = false;
		var statable = (IStatable)emulator;
		if (Environment.GetEnvironmentVariable("STATE_LOAD") is string loadPath)
		{
			using var ms = new MemoryStream(File.ReadAllBytes(loadPath));
			using var br = new BinaryReader(ms);
			try
			{
				statable.LoadStateBinary(br);
			}
			catch (Exception ex)
			{
				Console.WriteLine($"state load FAILED: {ex.Message}");
				return 1;
			}
			Console.WriteLine($"state loaded from {loadPath}");
		}
		var frame = 0;
		while (frame < frames)
		{
			if (frame == stateFrame && !replaying)
			{
				using var ms = new MemoryStream(); using var bw = new BinaryWriter(ms);
				var t0 = sw.Elapsed; statable.SaveStateBinary(bw); state = ms.ToArray();
				Console.WriteLine($"state saved at frame {frame}: {state.Length} bytes in {(sw.Elapsed - t0).TotalMilliseconds:F0} ms");
			}
			if (state is not null && !replaying && frame == stateFrame + 60)
			{
				using var ms = new MemoryStream(state); using var br = new BinaryReader(ms);
				var t0 = sw.Elapsed; statable.LoadStateBinary(br);
				Console.WriteLine($"state loaded in {(sw.Elapsed - t0).TotalMilliseconds:F0} ms, replaying {stateFrame}..{frame}");
				replaying = true; frame = stateFrame; continue;
			}
			foreach (var button in script.Select(e => e.button).Distinct()) controller.Set(button, script.Any(e => e.button == button && frame >= e.start && frame < e.end));
			emulator.FrameAdvance(controller, render: true, rendersound: true);
			sound.GetSamplesSync(out var buf, out var count);
			var nz = 0; for (var i = 0; i < count * 2; i++) if (buf[i] != 0) nz++;
			nonzero += nz;
			audioLogWriter?.WriteLine($"{frame},{count},{nz}");
			if (wavStream is not null && count > 0)
			{
				var bytes = MemoryMarshal.AsBytes(buf.AsSpan(0, count * 2));
				wavStream.Write(bytes);
				wavSampleCount += count;
			}
			var sig = Signature(video);
			sameCount = sig == lastSig ? sameCount + 1 : 0; lastSig = sig;
			if (state is not null && frame >= stateFrame && frame < stateFrame + 60)
			{
				if (!replaying) stateSigs.Add(sig);
				else
				{
					replaySigs.Add(sig);
					if (replaySigs.Count == stateSigs.Count)
					{
						var ok = stateSigs.SequenceEqual(replaySigs);
						Console.WriteLine($"state replay {(ok ? "MATCH" : "MISMATCH")} over {stateSigs.Count} frames");
						if (!ok) for (var i = 0; i < stateSigs.Count; i++) if (stateSigs[i] != replaySigs[i]) { Console.WriteLine($"  first diff at +{i}: {stateSigs[i]:x8} vs {replaySigs[i]:x8}"); break; }
						replaying = false; state = null;
					}
				}
			}
			if (frame % 100 == 0 || (frame % 25 == 0 && sameCount >= 25 && sameCount < 100))
			{
				Console.WriteLine($"frame {frame,6} t={sw.Elapsed.TotalSeconds,7:F1}s {video.BufferWidth}x{video.BufferHeight} vis={VisiblePercent(video),3}% sig={sig:x8} same={sameCount} audio_nz={nz} cd={(light.DriveLightOn ? 1 : 0)}");
				Console.Out.Flush();
			}
			if (frame % pngInterval == 0) WritePng(video, Path.Combine(outDir, $"frame-{frame:D6}.png"));
			if (Environment.GetEnvironmentVariable("ROWDIFF") is string rd)
			{
				var parts = rd.Split('-'); var a = int.Parse(parts[0]); var b = int.Parse(parts[1]);
				var px = video.GetVideoBuffer(); var w = video.BufferWidth; var h = video.BufferHeight;
				if (frame >= a && frame <= b)
				{
					var changed = new List<int>(); var nonblack = new List<int>();
					for (var y = 0; y < h; y++)
					{
						var diff = false; var nb = false;
						for (var x = 0; x < w; x++) { var p = px[y * w + x]; if (_prevRows != null && _prevRows[y * w + x] != p) diff = true; if ((p & 0xffffff) != 0) nb = true; }
						if (diff) changed.Add(y); if (nb) nonblack.Add(y);
					}
					Console.WriteLine($"rowdiff frame {frame}: changed={Ranges(changed)} nonblack={Ranges(nonblack)}");
				}
				_prevRows = (int[])px.Clone();
			}
			frame++;
		}
		WritePng(video, Path.Combine(outDir, $"frame-{frames:D6}.png"));
		if (Environment.GetEnvironmentVariable("STATE_SAVE") is string savePath)
		{
			using var ms = new MemoryStream();
			using var bw = new BinaryWriter(ms);
			statable.SaveStateBinary(bw);
			File.WriteAllBytes(savePath, ms.ToArray());
			Console.WriteLine($"state saved to {savePath}: {ms.Length} bytes");
		}
		if (Environment.GetEnvironmentVariable("SAVERAM") == "1" && emulator is ISaveRam saveRam)
		{
			var t0 = sw.Elapsed;
			var modified = saveRam.SaveRamModified;
			var diff = saveRam.CloneSaveRam(clearDirty: true);
			Console.WriteLine($"saveram: modified={modified} bytes={diff.Length} in {(sw.Elapsed - t0).TotalMilliseconds:F0} ms, modified after clear={saveRam.SaveRamModified}");
			File.WriteAllBytes(Path.Combine(outDir, "harddisk.saveram"), diff);
			saveRam.StoreSaveRam(diff);
			var again = saveRam.CloneSaveRam(clearDirty: false);
			Console.WriteLine($"saveram: restore roundtrip {(again.AsSpan().SequenceEqual(diff) ? "MATCH" : "MISMATCH")}, modified after store={saveRam.SaveRamModified}");
		}
		if (wavStream is not null)
		{
			var dataBytes = wavSampleCount * 4; // stereo, 16-bit
			var header = new byte[44];
			void PutStr(int off, string s) => Encoding.ASCII.GetBytes(s).CopyTo(header, off);
			void PutU32(int off, uint v) => BinaryPrimitives.WriteUInt32LittleEndian(header.AsSpan(off), v);
			void PutU16(int off, ushort v) => BinaryPrimitives.WriteUInt16LittleEndian(header.AsSpan(off), v);
			PutStr(0, "RIFF"); PutU32(4, (uint)(36 + dataBytes)); PutStr(8, "WAVE");
			PutStr(12, "fmt "); PutU32(16, 16); PutU16(20, 1); PutU16(22, 2);
			PutU32(24, audioSampleRate); PutU32(28, (uint)(audioSampleRate * 4)); PutU16(32, 4); PutU16(34, 16);
			PutStr(36, "data"); PutU32(40, (uint)dataBytes);
			wavStream.Seek(0, SeekOrigin.Begin);
			wavStream.Write(header);
		}
		Console.WriteLine($"done frames={frames} nonzero_audio={nonzero} elapsed={sw.Elapsed.TotalSeconds:F1}s");
		return 0;
	}
}
