#nullable enable

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
		// The guest has no media events yet (T22/T27): a disk cannot be put in a drive, and booting without it would hide that it was asked for.
		if (lp.Roms.Count != 0 || lp.Discs.Count != 0)
			throw new NotSupportedException("PUAE6 does not load disks yet; open the game with the UAE core");
		return new Puae6Parameters
		{
			GuestDirectory = PathUtils.DllDirectoryPath,
			Firmware = lp.Comm.CoreFileProvider.GetFirmwareOrThrow(new FirmwareID(VSystemID.Raw.Amiga, "A500"), "A Kickstart for the Amiga 500 is required"),
			SyncSettings = lp.SyncSettings ?? new Puae6SyncSettings(),
		};
	}
}
