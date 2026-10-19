#nullable enable
using System.IO;
using System.Linq;
using System.Text;
using System.Collections.Generic;
using System.Security.Cryptography;

namespace Puae6;

/// <summary>Deterministic three-HDF recipe and frontend resolution. No host mounts, runtime Python or shipped WHDLoad/ROM binary.</summary>
public static class Puae6WhdLoad
{
	public const string ProviderFileName = "WHDLoad_usr.lha";

	/// <summary>The one user-supplied provider location; no fallback, download or host mount.</summary>
	public static byte[] ReadProviderFile(string directory)
	{
		var path = Path.Combine(directory, ProviderFileName);
		if (!File.Exists(path)) throw new FileNotFoundException($"WHDLoad needs {ProviderFileName} in the Amiga user directory", path);
		if (new FileInfo(path).Length > Puae6Volumes.MaxArchiveBytes) throw new VolumeException("WHDLoad provider exceeds the archive limit");
		return File.ReadAllBytes(path);
	}

	/// <summary>Resolve before requesting firmware. Callbacks run only during frontend initialization, never in the guest or its MMIO path.</summary>
	public static Puae6Parameters Resolve(Puae6Parameters p, Func<Puae6Machine, byte[]>? firmware = null, Func<byte[]>? provider = null)
	{
		var settings = p.SyncSettings.Clone();
		if (!Enum.IsDefined(typeof(Puae6WhdLoadMode), settings.WhdLoad)) throw new VolumeException("unknown WHDLoad mode");
		var trees = settings.WhdLoad == Puae6WhdLoadMode.Disabled ? new List<List<VolumeFile>>() : p.Volumes.Select(Puae6Volumes.ReadArchive).ToList();
		int slaves = trees.Sum(t => t.Count(f => !f.IsDirectory && f.Path.EndsWith(".slave", StringComparison.OrdinalIgnoreCase)));
		if (slaves > 1) throw new VolumeException("WHDLoad Auto needs exactly one .slave");
		bool pack = slaves == 1;
		(byte[] Program, byte[] Vfs) tool = default;
		if (pack)
		{
			if (p.Volumes.Count != 1 || p.Media.Count != 0 || p.HardFiles.Count != 0) throw new VolumeException("WHDLoad Auto cannot be mixed with other media; use Disabled for generic volumes");
			settings.Machine = Puae6Machine.A1200;
			tool = ReadProvider(p.WhdLoadProvider ?? provider?.Invoke() ?? throw new VolumeException($"WHDLoad Auto needs the user's {ProviderFileName}"));
		}
		var rom = Puae6RomKey.Resolve(firmware?.Invoke(settings.Machine) ?? p.Firmware, p.RomKey);
		var result = new Puae6Parameters
		{
			GuestDirectory = p.GuestDirectory, GuestFileName = p.GuestFileName, PinnedGuestSha256 = p.PinnedGuestSha256,
			Firmware = rom, SyncSettings = settings, Settings = p.Settings, Media = p.Media, MediaNames = p.MediaNames,
			HardFiles = p.HardFiles, HardFileNames = p.HardFileNames, Volumes = p.Volumes, VolumeNames = p.VolumeNames,
		};
		if (!pack) return result;
		var kickstarts = p.WhdLoadKickstarts.ToDictionary(e => e.Key, e => e.Value);
		using (var sha = SHA1.Create())
			if (WhdLoadData.KickstartNames.TryGetValue(BitConverter.ToString(sha.ComputeHash(rom)).Replace("-", "").ToLowerInvariant(), out var name)) kickstarts[name] = rom;
		var images = Build(trees[0], tool.Program, tool.Vfs, settings.WhdLoadButtonWait, kickstarts);
		result.HardFiles = WhdLoadData.Names.Select(n => images[n]).ToList(); result.HardFileNames = WhdLoadData.Names.ToArray();
		result.Volumes = new List<byte[]>(); result.VolumeNames = new List<string>();
		return result;
	}

	/// <summary>Production entry: the user's install and official WHDLoad package, both bounded LHA/ZIP archives.</summary>
	public static IReadOnlyDictionary<string, byte[]> Prepare(byte[] game, byte[] provider, bool buttonWait = false, IReadOnlyDictionary<string, byte[]>? kickstarts = null)
	{
		var tool = ReadProvider(provider);
		return Build(Puae6Volumes.ReadArchive(game), tool.Program, tool.Vfs, buttonWait, kickstarts);
	}

	static (byte[] Program, byte[] Vfs) ReadProvider(byte[] provider)
	{
		var tool = Puae6Volumes.ReadArchive(provider);
		byte[] Member(string name, string hash)
		{
			var matches = tool.Where(f => !f.IsDirectory && f.Path == "WHDLoad/C/" + name).ToList();
			if (matches.Count != 1 || GuestIdentity.Sha256Hex(matches[0].Data) != hash)
				throw new VolumeException($"{name} is missing or is not the pinned WHDLoad 20.0 member");
			return matches[0].Data;
		}
		return (Member("WHDLoad", WhdLoadData.ProgramSha256), Member("WHDLoad.VFS", WhdLoadData.VfsSha256));
	}

	/// <summary>Pure recipe over explicit bytes; also usable with synthetic programs in tests. Prepare checks the executable/plugin pins.</summary>
	public static IReadOnlyDictionary<string, byte[]> Build(IReadOnlyList<VolumeFile> files, byte[] program, byte[] vfs, bool buttonWait = false, IReadOnlyDictionary<string, byte[]>? kickstarts = null)
	{
		Validate(files);
		if (program.Length == 0 || vfs.Length == 0) throw new VolumeException("WHDLoad and WHDLoad.VFS are required");
		var slaves = files.Where(f => !f.IsDirectory && f.Path.EndsWith(".slave", StringComparison.OrdinalIgnoreCase)).ToList();
		if (slaves.Count != 1) throw new VolumeException($"the bundle needs exactly one .slave, found {slaves.Count}");
		var slave = slaves[0];
		var prefix = slave.Path.Substring(0, slave.Path.LastIndexOf('/') + 1);
		var data = files.Where(f => !f.IsDirectory && f.Path.StartsWith(prefix, StringComparison.Ordinal)
			&& f.Path is not ("C/WHDLoad" or "S/Startup-Sequence" or "S/WHDLoad.prefs"))
			.Select(f => new VolumeFile(f.Path.Substring(prefix.Length), false, f.Data)).ToList();
		var boot = new List<VolumeFile> { new("C/WHDLoad", false, program), new("C/WHDLoad.VFS", false, vfs), new("C/P6Assign", false, WhdLoadData.AssignProgram),
			new("Game.slave", false, slave.Data), new("S/Startup-Sequence", false, Encoding.ASCII.GetBytes(WhdLoadData.StartupSequence)) };
		var system = new List<VolumeFile> { new("S/WHDLoad.prefs", false, Encoding.ASCII.GetBytes(WhdLoadData.Preferences + (buttonWait ? WhdLoadData.ButtonWaitLine : ""))),
			new("Devs/Kickstarts", true), new("Game.zip", false, StoredZip(data)) };
		system.AddRange(files.Where(f => !f.IsDirectory && f.Path.StartsWith("Devs/Kickstarts/", StringComparison.Ordinal)));
		foreach (var entry in kickstarts ?? new Dictionary<string, byte[]>())
		{
			if (!entry.Key.StartsWith("kick", StringComparison.Ordinal) || entry.Key.Length > 30 || entry.Key.Any(c => c is '/' or ':' || c < 32 || c > 255 || c == 127))
				throw new VolumeException("invalid Kickstart member name");
			system.RemoveAll(f => f.Path == "Devs/Kickstarts/" + entry.Key);
			system.Add(new("Devs/Kickstarts/" + entry.Key, false, entry.Value));
		}
		var trees = new[] { boot, system, new List<VolumeFile>() };
		var images = new Dictionary<string, byte[]>();
		for (int i = 0; i < trees.Length; ++i)
		{
			Validate(trees[i]);
			images.Add(WhdLoadData.Names[i], Puae6Volumes.Build(trees[i], WhdLoadData.Labels[i], WhdLoadData.FreeBlocks[i]));
		}
		return images;  // no image is published before every role succeeded
	}

	static void Validate(IReadOnlyList<VolumeFile> files)
	{
		if (files.Count > Puae6Volumes.MaxEntries) throw new VolumeException("too many bundle members");
		long total = 0;
		var seen = new HashSet<string>(StringComparer.Ordinal);
		var nodes = new Dictionary<string, (string Spelling, bool Directory)>();
		foreach (var file in files)
		{
			if (!seen.Add(file.Path)) throw new VolumeException("duplicate bundle member");
			total += file.Data.Length;
			if (total > Puae6Volumes.MaxBytes) throw new VolumeException("bundle files exceed the volume limit");
			var parts = file.Path.Split('/');
			if (parts.Length > 64) throw new VolumeException("bundle path is too deep");
			var path = "";
			for (int i = 0; i < parts.Length; ++i)
			{
				var part = parts[i];
				if (part.Length is < 1 or > 30 || part is "." or ".." || part.Any(c => c == ':' || c < 32 || c > 255 || c == 127)) throw new VolumeException("unsafe bundle path");
				path += (i == 0 ? "" : "/") + part;
				bool directory = i < parts.Length - 1 || file.IsDirectory;
				var key = Puae6Volumes.Folded(path);
				if (nodes.TryGetValue(key, out var old) && (old.Spelling != path || old.Directory != directory)) throw new VolumeException("conflicting bundle paths");
				nodes[key] = (path, directory);
			}
		}
	}

	// Narrow ZIP_STORED writer: Python ZipInfo's exact headers, with Amiga creator
	// and RWED permissions, independent of .NET's host-dependent ZIP creator field.
	static byte[] StoredZip(IReadOnlyList<VolumeFile> files)
	{
		using var body = new MemoryStream();
		using var central = new MemoryStream();
		using var local = new BinaryWriter(body, Encoding.UTF8, true);
		using var directory = new BinaryWriter(central, Encoding.UTF8, true);
		void Words(BinaryWriter writer, params ushort[] values) { foreach (var value in values) writer.Write(value); }
		foreach (var file in files.OrderBy(f => f.Path, StringComparer.Ordinal))
		{
			bool unicode = file.Path.Any(c => c > 127);
			var name = (unicode ? Encoding.UTF8 : Encoding.ASCII).GetBytes(file.Path);
			ushort flags = (ushort)(unicode ? 0x800 : 0);
			uint crc = Puae6Volumes.Crc32(file.Data), length = (uint)file.Data.Length, offset = (uint)body.Position;
			local.Write(0x04034b50u); Words(local, 20, flags, 0, 0, (ushort)WhdLoadData.ZipDosDate);
			local.Write(crc); local.Write(length); local.Write(length); Words(local, (ushort)name.Length, 0); local.Write(name); local.Write(file.Data);
			directory.Write(0x02014b50u); Words(directory, (ushort)WhdLoadData.ZipMadeBy, 20, flags, 0, 0, (ushort)WhdLoadData.ZipDosDate);
			directory.Write(crc); directory.Write(length); directory.Write(length); Words(directory, (ushort)name.Length, 0, 0, 0, 0);
			directory.Write(WhdLoadData.ZipExternalAttributes); directory.Write(offset); directory.Write(name);
		}
		uint start = (uint)body.Position;
		local.Write(central.ToArray()); local.Write(0x06054b50u); Words(local, 0, 0, (ushort)files.Count, (ushort)files.Count);
		local.Write((uint)central.Length); local.Write(start); Words(local, 0);
		return body.ToArray();
	}
}
