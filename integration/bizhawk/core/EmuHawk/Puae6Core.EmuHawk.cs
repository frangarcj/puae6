#nullable enable

using System.IO;
using System.Linq;

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
		var settings = lp.SyncSettings?.Clone() ?? new Puae6SyncSettings();
		if (lp.Discs.Count > 1 || lp.Discs.Count != 0 && lp.Roms.Count != 0)
			throw new NotSupportedException("CD32 loads one CD without other media");
		Puae6DiscSource? cd = null;
		if (lp.Discs.Count == 1)
		{
			settings.Machine = Puae6Machine.CD32;
			cd = new Puae6DiscSource(lp.Discs[0].DiscData);
		}
		byte[] Firmware(Puae6Machine machine)
		{
			var main = lp.Comm.CoreFileProvider.GetFirmwareOrThrow(FirmwareOf(machine), $"A Kickstart for the Amiga {machine} is required");
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
		return Puae6WhdLoad.Resolve(new Puae6Parameters
		{
			GuestDirectory = PathUtils.DllDirectoryPath,
			SyncSettings = settings,
			CdInfo = cd?.Descriptor(),
			ReadCd = cd is null ? null : (at, data, count) => cd.ReadAt(at, data, 0, count),
			Settings = lp.Settings ?? new Puae6Settings(),
			Media = floppies.Select(rom => rom.FileData).ToList(),
			MediaNames = floppies.Select(NameOf).ToList(),
			HardFiles = hardFiles.Select(rom => rom.FileData).ToList(),
			HardFileNames = hardFiles.Select(NameOf).ToList(),
			Volumes = volumes.Select(rom => rom.FileData).ToList(),
			VolumeNames = volumes.Select(NameOf).ToList(),
		}, Firmware,
			() => Puae6WhdLoad.ReadProviderFile(lp.Comm.CoreFileProvider.GetUserPath(VSystemID.Raw.Amiga, false)));
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
