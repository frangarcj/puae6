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
	public Puae6Core(CoreLoadParameters<object, Puae6SyncSettings> lp)
		: this(lp.Comm, ParametersFrom(lp))
	{
	}

	static Puae6Parameters ParametersFrom(CoreLoadParameters<object, Puae6SyncSettings> lp)
	{
		// ADF disks and HDF hard files (T63b); WHDLoad archives and optical discs are not supported by the guest, and booting without them would hide that they were asked for.
		if (lp.Discs.Count != 0)
			throw new NotSupportedException("PUAE6 does not load optical discs; open the game with the UAE core");
		bool IsHardFile(IRomAsset rom) => rom.Extension.Equals(".hdf", StringComparison.OrdinalIgnoreCase);
		foreach (var rom in lp.Roms)
			if (!IsHardFile(rom) && !rom.Extension.Equals(".adf", StringComparison.OrdinalIgnoreCase))
				throw new NotSupportedException($"PUAE6 loads ADF disks and HDF hard files only ({rom.Extension} is not one); open the game with the UAE core");
		var floppies = lp.Roms.Where(rom => !IsHardFile(rom)).ToList();
		var hardFiles = lp.Roms.Where(IsHardFile).ToList();
		string NameOf(IRomAsset rom) => Path.GetFileName(rom.RomPath.Split('|').Last());
		var settings = lp.SyncSettings ?? new Puae6SyncSettings();
		return new Puae6Parameters
		{
			GuestDirectory = PathUtils.DllDirectoryPath,
			Firmware = lp.Comm.CoreFileProvider.GetFirmwareOrThrow(FirmwareOf(settings.Machine), $"A Kickstart for the Amiga {settings.Machine.ToString().Substring(1)} is required"),
			SyncSettings = settings,
			Media = floppies.Select(rom => rom.FileData).ToList(),
			MediaNames = floppies.Select(NameOf).ToList(),
			HardFiles = hardFiles.Select(rom => rom.FileData).ToList(),
			HardFileNames = hardFiles.Select(NameOf).ToList(),
		};
	}

	/// <summary>The entry of BizHawk's firmware database for each machine (FirmwareDatabase.cs, Amiga): "A500" is Kickstart 1.3, "A600" Kickstart 2.05 and "A1200" Kickstart 3.1 r40.68, the ideal option of each.</summary>
	public static FirmwareID FirmwareOf(Puae6Machine machine) => new(VSystemID.Raw.Amiga, machine.ToString());
}
