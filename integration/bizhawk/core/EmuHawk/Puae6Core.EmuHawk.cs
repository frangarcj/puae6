#nullable enable

using System.IO;
using System.Linq;
using System.Collections.Generic;

using BizHawk.Common.PathExtensions;
using BizHawk.Emulation.Common;
using BizHawk.Emulation.Cores;

namespace Puae6;

// The half of the core that only exists inside BizHawk: the constructor that CoreInventory finds by its attribute and calls with what the loader
// has. integration/bizhawk/patches/build_tree.py copies it, with the rest of Puae6/, into BizHawk.Emulation.Cores; the standalone build of the core
// (Puae6.Core.csproj) does not compile it, because CoreInventory and CoreLoadParameters are not part of the pinned capture.
public sealed partial class Puae6Core
{
	/// <summary>
	/// Registered with a low priority: UAE stays the core an Amiga game opens with, and PUAE6 is chosen explicitly (the core picker or
	/// Config.PreferredCores["Amiga"]), so nothing that exists is replaced and the movies that name UAE keep naming it.
	/// </summary>
	[CoreConstructor(VSystemID.Raw.Amiga, Priority = CorePriority.Low)]
	public Puae6Core(CoreLoadParameters<Puae6Settings, Puae6SyncSettings> lp)
		: this(lp.Comm, ParametersFrom(lp))
	{
	}

	static Puae6Parameters ParametersFrom(CoreLoadParameters<Puae6Settings, Puae6SyncSettings> lp)
	{
		// Refuse unsupported hosts, the other ISA or a different build's guest
		// before firmware, media ownership or any sandbox mapping.
		string guestDirectory = PathUtils.DllDirectoryPath;
		Puae6Guest.VerifyForCurrentHost(guestDirectory, "puae6.wbx");
		var settings = lp.SyncSettings?.Clone() ?? new Puae6SyncSettings();
		if (lp.Discs.Count > 16)
			throw new NotSupportedException("PUAE6 loads at most 16 fixed CD slots");
		var sources = new List<Puae6DiscSource>();
		var owners = new List<IOwnedDiscAsset?>();
		var slots = new List<int>();
		var typed = new List<bool>();
		foreach (var asset in lp.Discs)
		{
			var source = new Puae6DiscSource(asset.DiscData);
			int ordinal = sources.FindIndex(s => s.ContentId.SequenceEqual(source.ContentId));
			if (ordinal < 0) { ordinal = sources.Count; sources.Add(source); owners.Add(asset as IOwnedDiscAsset); typed.Add(asset.DiscData.Regions.Count != 0); }
			slots.Add(ordinal);
		}
		bool catalog = lp.Discs.Count > 1, regional = lp.Discs.Exists(a => a.DiscData.Regions.Count != 0);
		bool sessioned = sources.Exists(s => s.SessionCount >= 2);
		Puae6DiscSource? cd = lp.Discs.Count == 1 ? sources[0] : null;
		byte[] Firmware(Puae6Machine machine)
		{
			var main = lp.Comm.CoreFileProvider.GetFirmwareOrThrow(FirmwareOf(machine), $"A Kickstart for the Amiga {machine} is required");
			if (machine == Puae6Machine.CDTV)
			{
				var extended = lp.Comm.CoreFileProvider.GetFirmwareOrThrow(new(VSystemID.Raw.Amiga, "CDTVext27"), "CDTV Extended ROM v2.7 is required");
				bool Matches(byte[] data, string hash) { using var sha = System.Security.Cryptography.SHA1.Create(); return data.Length == 262144 && BitConverter.ToString(sha.ComputeHash(data)).Replace("-", "") == hash; }
				if (!Matches(main, "891E9A547772FE0C6C19B610BAF8BC4EA7FCB785") || !Matches(extended, "5BC114BBA29F60A614A311745B3E2464BFA06846"))
					throw new InvalidDataException("CDTV requires raw KS1.3 ID6 and EXT v2.7 ID22, 256 KiB each");
				return extended.Concat(main).ToArray();
			}
			if (machine != Puae6Machine.CD32 || main.Length == 1024 * 1024) return main;
			var ext = lp.Comm.CoreFileProvider.GetFirmwareOrThrow(new(VSystemID.Raw.Amiga, "CD32ext"), "CD32 Extended ROM is required");
			if (main.Length != 512 * 1024 || ext.Length != 512 * 1024) throw new InvalidDataException("CD32 requires 512 KiB main and extended ROMs, or a combined 1 MiB ROM");
			return ext.Concat(main).ToArray();
		}
		bool IsHardFile(IRomAsset rom) => rom.Extension.Equals(".hdf", StringComparison.OrdinalIgnoreCase);
		bool IsVolume(IRomAsset rom) => rom.Extension.Equals(".lha", StringComparison.OrdinalIgnoreCase) || rom.Extension.Equals(".lzh", StringComparison.OrdinalIgnoreCase)
			|| rom.Extension.Equals(".zip", StringComparison.OrdinalIgnoreCase) && Puae6Volumes.IsVolumeZip(rom.FileData, settings.WhdLoad == Puae6WhdLoadMode.Auto);
		foreach (var rom in lp.Roms)
			if (!IsHardFile(rom) && !IsVolume(rom) && !rom.Extension.Equals(".adf", StringComparison.OrdinalIgnoreCase) && !rom.Extension.Equals(".fdi", StringComparison.OrdinalIgnoreCase) && !rom.Extension.Equals(".ipf", StringComparison.OrdinalIgnoreCase))
				throw new NotSupportedException($"PUAE6 loads ADF (plain or extended), FDI and IPF disks, HDF hard files and LHA or ZIP volumes only ({rom.Extension} is not one); open the game with the UAE core");
		var floppies = lp.Roms.Where(rom => !IsHardFile(rom) && !IsVolume(rom)).ToList();
		var hardFiles = lp.Roms.Where(IsHardFile).ToList();
		var volumes = lp.Roms.Where(IsVolume).ToList();
		string NameOf(IRomAsset rom) => Path.GetFileName(rom.RomPath.Split('|').Last());
		var result = Puae6WhdLoad.Resolve(new Puae6Parameters
		{
			GuestDirectory = guestDirectory,
			SyncSettings = settings,
			CdInfo = cd is not null && !sessioned && lp.Discs[0].DiscData.Regions.Count == 0 ? cd.Descriptor() : null,
			CdInfoV2 = cd is not null && !sessioned && lp.Discs[0].DiscData.Regions.Count != 0 ? cd.DescriptorV2() : null,
			CdInfoV3 = cd is not null && sessioned ? cd.DescriptorV3() : null,
			CdOwner = lp.Discs.Count == 1 ? lp.Discs[0] as IOwnedDiscAsset : lp.Discs.OfType<IOwnedDiscAsset>().Any() ? new CdOwners(lp.Discs.OfType<IOwnedDiscAsset>().Distinct().ToArray()) : null,
			CdCatalog = catalog && !sessioned && !regional ? sources.Select(s => s.Descriptor()).ToArray() : null,
			CdCatalogV2 = catalog && !sessioned && regional ? sources.Select((s, n) => RegionalDescriptor(s, typed[n])).ToArray() : null,
			CdCatalogV3 = catalog && sessioned ? sources.Select((s, n) => SessionDescriptor(s, typed[n])).ToArray() : null,
			CdSlots = catalog ? slots.ToArray() : [ ], CdNames = catalog ? lp.Discs.Select(a => a.DiscName).ToArray() : [ ],
			ReadCdCatalog = catalog ? (ordinal, at, data, count) => ordinal < sources.Count && owners[(int)ordinal]?.IsDisposed != true && sources[(int)ordinal].ReadAt(at, data, 0, count) : null,
			ReadCd = cd is null ? null : (at, data, count) => owners[0]?.IsDisposed != true && cd.ReadAt(at, data, 0, count),
			Settings = lp.Settings ?? new Puae6Settings(),
			Media = floppies.Select(rom => rom.FileData).ToList(),
			MediaNames = floppies.Select(NameOf).ToList(),
			HardFiles = hardFiles.Select(rom => rom.FileData).ToList(),
			HardFileNames = hardFiles.Select(NameOf).ToList(),
			Volumes = volumes.Select(rom => rom.FileData).ToList(),
			VolumeNames = volumes.Select(NameOf).ToList(),
		}, Firmware,
			() => Puae6WhdLoad.ReadProviderFile(lp.Comm.CoreFileProvider.GetUserPath(VSystemID.Raw.Amiga, false)));
		ConfigFlags(result.SyncSettings.Cartridge, result.SyncSettings.FloppyWrites, result.SyncSettings.Machine);
		uint cartridge = CartridgeModel(result.SyncSettings.Cartridge);
		if (cartridge != 0)
			result.CartridgeFirmware = cartridge == 32
				? lp.Comm.CoreFileProvider.GetFirmwareOrThrow(new(VSystemID.Raw.Amiga, "CD32FMV"), "CD32 Full Motion Video Cartridge ROM is required")
				: lp.Comm.CoreFileProvider.GetFirmwareOrThrow(new(VSystemID.Raw.Amiga, "AR" + cartridge), $"Action Replay {cartridge} firmware is required");
		return result;
	}

	static unsafe P6CdInfoV2 RegionalDescriptor(Puae6DiscSource source, bool regional)
	{
		if (regional) return source.DescriptorV2();
		var legacy = source.Descriptor();
		var result = new P6CdInfoV2 { Version = 2, Size = (uint)sizeof(P6CdInfoV2), Bytes = legacy.Bytes, Count = legacy.Count, Leadout = legacy.Leadout };
		for (int i = 0; i < 32; ++i) result.Id[i] = legacy.Id[i];
		for (int n = 0; n < legacy.Count; ++n)
		{
			for (int i = 0; i < 5; ++i) result.Tracks[n * 12 + i] = legacy.Tracks[n * 6 + i];
			result.Tracks[n * 12 + 6] = 1; // Stored INDEX00; flags0 selects the complete V1 policy in cd_descriptor_v2.
		}
		return result;
	}

	static unsafe P6CdInfoV3 SessionDescriptor(Puae6DiscSource source, bool regional)
	{
		if (source.SessionCount >= 2) return source.DescriptorV3();
		var v2 = RegionalDescriptor(source, regional);
		var result = new P6CdInfoV3 { Version = 3, Size = (uint)sizeof(P6CdInfoV3), Bytes = v2.Bytes, Count = v2.Count, Leadout = v2.Leadout };
		for (int i = 0; i < 32; ++i) result.Id[i] = v2.Id[i];
		for (int i = 0; i < 99 * 12; ++i) result.Tracks[i] = v2.Tracks[i];
		return result; // SessionCount0 retains the complete single-session V1/V2 policy and identity.
	}

	sealed class CdOwners(IOwnedDiscAsset[] owners) : IDisposable
	{
		bool _disposed;
		public void Dispose() { if (_disposed) return; _disposed = true; foreach (var owner in owners) owner.Dispose(); }
	}

	/// <summary>The entry of BizHawk's firmware database for each machine (FirmwareDatabase.cs, Amiga): "A500" is Kickstart 1.3 (1.2 and the 3.1 r40.063 are its other options), "A600" Kickstart 2.05 (2.04 of the A500+ and the 3.1
	/// r40.063 are its other options) and "A1200" Kickstart 3.1 r40.68, the ideal option of each. T50: the A500 OG, the A1000 and the A2000 OG ask for "A500" (the Kickstart 1.3 of the A500), the A500+ and the A2000 for "A600"
	/// (the 2.04 of the A500+, or the 3.1 r40.063 that PUAE5 gives the A2000: the person picks the option in the firmware manager; the core accepts any image the engine accepts by its content).</summary>
	public static FirmwareID FirmwareOf(Puae6Machine machine) => new(VSystemID.Raw.Amiga, FirmwareName(machine));

	/// <summary>The name of the FirmwareID of the machine: the name of the member for the A500, the A600 and the A1200, and the family's for the T50 machines.</summary>
	public static string FirmwareName(Puae6Machine machine) => machine switch
	{
		Puae6Machine.A500OG or Puae6Machine.A1000 or Puae6Machine.A2000OG => "A500",
		Puae6Machine.A500PLUS or Puae6Machine.A2000 => "A600",
		Puae6Machine.A4030 or Puae6Machine.A4040 => "A4000",
		_ => machine.ToString(),
	};
}
