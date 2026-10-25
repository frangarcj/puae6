#nullable enable

using System.IO;
using System.Linq;
using System.Collections.Generic;
using System.Runtime.InteropServices;
using System.Text;

using BizHawk.Common;
using BizHawk.Emulation.Common;
using BizHawk.Emulation.Cores.Waterbox;

namespace Puae6;

/// <summary>What the core needs to be created. In EmuHawk the constructor that BizHawk calls fills this from CoreLoadParameters and the firmware database.</summary>
public sealed class Puae6Parameters
{
	/// <summary>Where the guest is, and its file name. The guest is the one this build of the wrapper was built with.</summary>
	public string GuestDirectory { get; set; } = "";
	public string GuestFileName { get; set; } = "puae6.wbx";
	/// <summary>The SHA-256 the guest must have; null means the one recorded in the build (<see cref="Puae6Guest.BuiltWithSha256"/>).</summary>
	public string? PinnedGuestSha256 { get; set; }
	/// <summary>The Kickstart (or the micro-ROM of a test): copied by the guest into its sealed heap.</summary>
	public byte[] Firmware { get; set; } = [ ];
	/// <summary>Optional raw Action Replay firmware; copied by the core and by the guest binder.</summary>
	public byte[]? CartridgeFirmware { get; set; }
	/// <summary>The rom.key of Amiga Forever (T51), only needed when <see cref="Firmware"/> is an encrypted ROM (AMIROMTYPE1): the core decrypts it (<see cref="Puae6RomKey"/>) and the decrypted image is the firmware whose
	/// SHA-256 is the identity of states and movies. The key is not kept.</summary>
	public byte[]? RomKey { get; set; }
	/// <summary>Immutable CD descriptor and pure offset reader. The owner of the source must outlive this core.</summary>
	public P6CdInfo? CdInfo { get; set; }
	public P6CdInfoV2? CdInfoV2 { get; set; }
	public P6CdInfoV3? CdInfoV3 { get; set; }
	public Func<ulong, byte[], int, bool>? ReadCd { get; set; }
	/// <summary>Fixed unique descriptors, in first occurrence order; choose one of V1, V2 or V3, never several or a single descriptor.</summary>
	public P6CdInfo[]? CdCatalog { get; set; }
	public P6CdInfoV2[]? CdCatalogV2 { get; set; }
	public P6CdInfoV3[]? CdCatalogV3 { get; set; }
	public Func<uint, ulong, byte[], int, bool>? ReadCdCatalog { get; set; }
	public int[] CdSlots { get; set; } = [ ];
	public string[] CdNames { get; set; } = [ ];
	internal int CdCount => CdCatalog?.Length ?? CdCatalogV2?.Length ?? CdCatalogV3?.Length ?? (CdInfo.HasValue || CdInfoV2.HasValue || CdInfoV3.HasValue ? 1 : 0);
	internal int CdSlotCount => ReadCdCatalog is not null ? CdSlots.Length : CdCount;
	// Only the public loader supplies a lease; direct API sources remain borrowed.
	internal IDisposable? CdOwner { get; set; }
	internal P6CdHost? CreateCdHost() => CdCatalog is { } c1 ? new P6CdHost(c1, ReadCdCatalog!)
		: CdCatalogV2 is { } c2 ? new P6CdHost(c2, ReadCdCatalog!) : CdCatalogV3 is { } c3 ? new P6CdHost(c3, ReadCdCatalog!)
		: CdInfoV3 is { } v3 ? new P6CdHost(v3, ReadCd!) : CdInfoV2 is { } v2 ? new P6CdHost(v2, ReadCd!)
		: CdInfo is { } v1 ? new P6CdHost(v1, ReadCd!) : null;
	internal unsafe byte[]? CdIdentity()
	{
		if (ReadCdCatalog is not null)
		{
			using var stream = new MemoryStream();
			using (var writer = new BinaryWriter(stream, Encoding.ASCII, true))
			{
				writer.Write(Encoding.ASCII.GetBytes("PUAE6 CD catalog/v1")); writer.Write(CdCount);
				for (int n = 0; n < CdCount; ++n) writer.Write(CatalogId(n));
				writer.Write(CdSlots.Length); foreach (var slot in CdSlots) writer.Write(slot);
			}
			return StateEnvelope.Sha256(stream.ToArray());
		}
		if (!CdInfo.HasValue && !CdInfoV2.HasValue && !CdInfoV3.HasValue) return null;
		var id = new byte[32];
		if (CdInfoV3 is { } v3) for (int i = 0; i < 32; ++i) id[i] = v3.Id[i];
		else if (CdInfoV2 is { } v2) for (int i = 0; i < 32; ++i) id[i] = v2.Id[i];
		else if (CdInfo is { } v1) for (int i = 0; i < 32; ++i) id[i] = v1.Id[i];
		return id;
	}
	unsafe byte[] CatalogId(int ordinal)
	{
		var id = new byte[32];
		if (CdCatalog is { } c1) { var info = c1[ordinal]; for (int i = 0; i < 32; ++i) id[i] = info.Id[i]; }
		else if (CdCatalogV2 is { } c2) { var info = c2[ordinal]; for (int i = 0; i < 32; ++i) id[i] = info.Id[i]; }
		else { var info = CdCatalogV3![ordinal]; for (int i = 0; i < 32; ++i) id[i] = info.Id[i]; }
		return id;
	}

	internal void ValidateCd()
	{
		int single = (CdInfo.HasValue ? 1 : 0) + (CdInfoV2.HasValue ? 1 : 0) + (CdInfoV3.HasValue ? 1 : 0);
		int catalog = (CdCatalog is not null ? 1 : 0) + (CdCatalogV2 is not null ? 1 : 0) + (CdCatalogV3 is not null ? 1 : 0);
		bool cd = single + catalog != 0;
		if (single + catalog > 1 || (single == 1) != (ReadCd is not null) || (catalog == 1) != (ReadCdCatalog is not null)
			|| !cd && CdOwner is not null || CdSlots is null || CdNames is null || catalog == 0 && (CdSlots.Length != 0 || CdNames.Length != 0))
			throw new InvalidOperationException("Choose one single CD or one fixed catalog with its matching reader");
		if (catalog == 1)
		{
			if (CdCount is < 1 or > 16 || CdSlots.Length is < 1 or > 16 || CdNames.Length != CdSlots.Length)
				throw new InvalidOperationException("CD catalog requires 1..16 slots and names");
			int next = 0;
			foreach (int slot in CdSlots) { if (slot < 0 || slot > next || slot >= CdCount) throw new InvalidOperationException("Invalid CD slot map"); if (slot == next) ++next; }
			if (next != CdCount) throw new InvalidOperationException("CD catalog has an unused ordinal");
			if (Enumerable.Range(0, CdCount).Select(n => BitConverter.ToString(CatalogId(n))).Distinct().ToArray().Length != CdCount)
				throw new InvalidOperationException("CD catalog IDs must be unique");
		}
		if (SyncSettings.Machine == Puae6Machine.CDTV)
		{
			var s = SyncSettings;
			if (Media.Count != 0 || HardFiles.Count != 0 || Volumes.Count != 0 || s.PhysicalFloppyDrives != Puae6FloppyDrives.Auto
				|| s.FloppyWrites || s.Cartridge != Puae6Cartridge.Disabled || s.CpuCompatibility != Puae6CpuCompatibility.Exact
				|| s.CpuModel is not (Puae6Cpu.Auto or Puae6Cpu.M68000) || s.CpuMultiplier is not (Puae6CpuClock.Auto or Puae6CpuClock.X2)
				|| s.ChipMemory is not (Puae6Chip.Auto or Puae6Chip.MB1) || s.SlowMemory is not (Puae6Slow.Auto or Puae6Slow.None)
				|| s.FastMemory is not (Puae6Fast.Auto or Puae6Fast.None) || s.FpuModel != Puae6Fpu.None || s.Z3Memory != Puae6Z3Memory.None
				|| s.HdfController != Puae6HdfController.Uae)
				throw new InvalidOperationException("CDTV requires stock exact 68000, 1 MiB Chip, no expansions or floppy drives");
			return;
		}
		if ((cd || SyncSettings.Machine == Puae6Machine.CD32) && (SyncSettings.Machine != Puae6Machine.CD32
			|| Media.Count != 0 || HardFiles.Count != 0 || Volumes.Count != 0 || SyncSettings.PhysicalFloppyDrives != Puae6FloppyDrives.Auto))
			throw new InvalidOperationException("CD32 requires its own model with no floppy drives, hard files or volumes");
	}
	public Puae6SyncSettings SyncSettings { get; set; } = new();
	public Puae6Settings Settings { get; set; } = new();
	/// <summary>The floppy images the machine may use (ADF of 880 KiB, an extended ADF, an FDI or an IPF, all together at most what 16 ADF take): the first <see cref="Puae6SyncSettings.FloppyDrives"/> are in a drive at power-on.</summary>
	public IReadOnlyList<byte[]> Media { get; set; } = new List<byte[]>();
	/// <summary>Names to show when a disk is selected or inserted; "disk N" where missing.</summary>
	public IReadOnlyList<string> MediaNames { get; set; } = new List<string>();
	/// <summary>
	/// The hard files (T63b): at most <see cref="P6.MaxHardFiles"/>, each a multiple of 512 bytes (an RDB image, or a single partition). They are mounted at power-on on the UAE controller as DH0, DH1... in this order,
	/// always writable; what the guest writes is the overlay of the sandbox, never the array given here. They need no <see cref="Puae6SyncSettings.FloppyWrites"/>.
	/// </summary>
	public IReadOnlyList<byte[]> HardFiles { get; set; } = new List<byte[]>();
	/// <summary>Names of the hard files, for messages and for the export; "hard file N" where missing.</summary>
	public IReadOnlyList<string> HardFileNames { get; set; } = new List<string>();
	/// <summary>
	/// LHA archives and ZIP files of files (T64b, ADR 0011): each becomes ONE FFS hard file built in memory (<see cref="Puae6Volumes"/>: the same bytes as the libretro adapter's) and is mounted like an .hdf, after <see cref="HardFiles"/>, within
	/// the same limit of <see cref="P6.MaxHardFiles"/> and without two copies of one image. The host is never mounted or read again: the identity of the medium is the hash of the image, so the same content from an LHA and from a ZIP is one medium.
	/// An archive that is not a usable volume (damaged, unsafe, over a limit) fails the creation of the core with the reason.
	/// </summary>
	public IReadOnlyList<byte[]> Volumes { get; set; } = new List<byte[]>();
	/// <summary>Names of the volumes, for messages and for the export; "volume N" where missing.</summary>
	public IReadOnlyList<string> VolumeNames { get; set; } = new List<string>();
	/// <summary>The user's WHDLoad_usr.lha, read once before boot. Required only when Auto detects an installed pack.</summary>
	public byte[]? WhdLoadProvider { get; set; }
	/// <summary>Optional explicit Kickstart/RTB bytes, by WHDLoad name; never a live host directory.</summary>
	public IReadOnlyDictionary<string, byte[]> WhdLoadKickstarts { get; set; } = new Dictionary<string, byte[]>();
}

/// <summary>
/// PUAE6 as a BizHawk core: WinUAE 6 running as a Waterbox guest (puae6.wbx), advanced exactly one video unit per FrameAdvance through the same
/// p6_run_frame the headless and libretro adapters use. It is independent of the UAE core of BizHawk (its own core ID, its own guest, none of its
/// state) and speaks only the ABI of adapters/waterbox/p6w.h.
/// </summary>
[PortedCore(name: Puae6Names.Core, author: "WinUAE team and the PUAE6 project", portedVersion: "WinUAE 6.0.3", portedUrl: "https://github.com/tonioni/WinUAE", isReleased: false)]
public sealed unsafe partial class Puae6Core : IEmulator, IVideoProvider, ISoundProvider, IStatable, IInputPollable, IRegionable, IDriveLight, ICycleTiming, ISaveRam, ISettable<Puae6Settings, Puae6SyncSettings>
{
	// The profile's buffers (p6_caps of ABI v1): the delivered picture is at most 760x576 and one unit never delivers more than 8192 audio frames.
	/// <summary>The largest picture any raster option can deliver (super-hires, 1520 columns; 576 rows); the buffer of one machine is <see cref="RasterWidth"/> x <see cref="RasterHeight"/>.</summary>
	public const int MaxWidth = 1520, MaxHeight = 576, MaxAudioFrames = 8192;

	// The heaps are part of what a snapshot is compatible with; they are the budgets of manifests/waterbox-memory.json (a test compares them). The mmap heap holds
	// Chip, Slow and Fast RAM among other large blocks, so it grows with the memory the settings ask for beyond the machine's own (T49: twice the extra memory, which
	// keeps the rule of the manifest, a budget of at least twice what was measured; the measurements are recorded per configuration).
	/// <summary>Frontend selections are separate from flags and from the native cartridge model.</summary>
	public static uint CartridgeModel(Puae6Cartridge cartridge) => cartridge switch
	{
		Puae6Cartridge.Disabled or Puae6Cartridge.HrtMon => 0, Puae6Cartridge.Ar1 => 1, Puae6Cartridge.Ar2 => 2,
		Puae6Cartridge.Ar3 => 3, Puae6Cartridge.Ar1200 => 1200, _ => throw new ArgumentOutOfRangeException(nameof(cartridge)),
	};
	/// <summary>Only floppy writes and builtin HRTMon occupy config flags.</summary>
	public static uint ConfigFlags(Puae6Cartridge cartridge, bool floppyWrites, Puae6Machine machine)
	{
		uint model = CartridgeModel(cartridge);
		if ((cartridge == Puae6Cartridge.HrtMon || model == 1200) && machine != Puae6Machine.A1200 || model is 1 or 2 or 3 && machine != Puae6Machine.A500)
			throw new ArgumentException("HRTMon/AR1200 require A1200; AR1/2/3 require stock A500", nameof(cartridge));
		return (floppyWrites ? P6.ConfigFloppyWrites : 0) | (cartridge == Puae6Cartridge.HrtMon ? P6.ConfigHrtMon : 0);
	}
	static void ValidateCartridge(Puae6SyncSettings settings, byte[]? raw, bool cd)
	{
		uint model = CartridgeModel(settings.Cartridge); ConfigFlags(settings.Cartridge, settings.FloppyWrites, settings.Machine);
		if (model == 0) { if (raw is not null) throw new ArgumentException("Only Action Replay takes cartridge firmware", nameof(raw)); return; }
		bool aga = model == 1200; var own = OwnMemory(settings.Machine);
		if (cd || settings.FloppyWrites || settings.FpuModel != Puae6Fpu.None || settings.Z3Memory != Puae6Z3Memory.None || settings.HdfController != Puae6HdfController.Uae
			|| settings.CpuModel != Puae6Cpu.Auto && settings.CpuModel != (aga ? Puae6Cpu.M68020 : Puae6Cpu.M68000)
			|| settings.CpuMultiplier != Puae6CpuClock.Auto && settings.CpuMultiplier != (aga ? Puae6CpuClock.X4 : Puae6CpuClock.X2)
			|| !Enum.IsDefined(typeof(Puae6CpuCompatibility), settings.CpuCompatibility) || !aga && settings.CpuCompatibility == Puae6CpuCompatibility.Normal
			|| settings.ChipMemory != Puae6Chip.Auto && ChipKib(settings.ChipMemory) != own.Chip
			|| !Enum.IsDefined(typeof(Puae6Slow), settings.SlowMemory) || settings.SlowMemory != Puae6Slow.Auto && (settings.SlowMemory == Puae6Slow.None ? 0u : SlowKib(settings.SlowMemory)) != own.Slow
			|| settings.FastMemory != Puae6Fast.Auto && settings.FastMemory != Puae6Fast.None)
			throw new ArgumentException("Action Replay requires its stock CPU, clock and memory, without CD, FPU, Z3, IDE or floppy writes", nameof(settings));
		int bytes = model == 1 ? 65536 : model == 2 ? 131072 : 262144;
		if (raw is null || raw.Length != bytes || !raw.Any(b => b != 0) || (System.Text.Encoding.ASCII.GetString(raw, 0, 8) == "ATZ!HRT!") != aga)
			throw new ArgumentException($"Action Replay {model} requires {bytes} bytes of matching raw firmware", nameof(raw));
	}

	public static WaterboxOptions Heaps() => Heaps(new Puae6SyncSettings());

	public static WaterboxOptions Heaps(Puae6SyncSettings settings) => Heaps(settings, Array.Empty<long>());

	/// <summary>
	/// The heaps for these settings and these hard files (T63b; the sizes in bytes of the images, in order). A hard file is extra memory of the mmap heap like Fast RAM: the guest keeps the image once as the base and
	/// reserves beside it the room for the overlay of its writes and its dirty bitmap (<see cref="HardFileHeapKiB"/>, manifests/waterbox-memory.json, measured_a500_hdf*), and the budget counts it twice like all
	/// extra memory. The image is not in the sealed heap, so that one does not grow.
	/// </summary>
	public static WaterboxOptions Heaps(Puae6SyncSettings settings, IEnumerable<long> hardFileBytes)
	{
		long hard = hardFileBytes.Sum(bytes => (long)HardFileHeapKiB(bytes));
		return new()
		{
			SbrkHeapSizeKB = 4096, SealedHeapSizeKB = 16384, InvisibleHeapSizeKB = 16, PlainHeapSizeKB = 16, MmapHeapSizeKB = (uint)(16384 + 2 * (ExtraMemoryKiB(settings) + hard)),
		};
	}

	/// <summary>The mmap heap a hard file asks for, in KiB: the base (the size of the image), the room for the overlay of the guest's writes (the size of the image again, 4 KiB chunks made on the first write), its dirty bitmap and 64 KiB of the allocator's own. The counterpart of MacHawk's HardDiskHeapKB, which keeps the image once because it writes in place.</summary>
	public static ulong HardFileHeapKiB(long bytes) => (ulong)(2 * ((bytes + 1023) / 1024) + (bytes / P6.HardFileChunkBytes + 7) / 8 / 1024 + 64);

	/// <summary>The write overlay of the floppies counted as extra memory of the sandbox (T28): its pool is 3520 sectors, 1.8 MiB, and a machine whose guest wrote every track of both disks measured 3.9 MiB more committed in the mmap heap (manifests/waterbox-memory.json, measured_a500_floppy_writes_full).</summary>
	public const int FloppyOverlayKiB = 4096;

	// What each machine comes with, in KiB: Chip, Slow.
	static (uint Chip, uint Slow) OwnMemory(Puae6Machine machine) => machine switch
	{
		Puae6Machine.A1200 or Puae6Machine.A4030 or Puae6Machine.A4040 or Puae6Machine.CD32 => (2048u, 0u), Puae6Machine.A600 or Puae6Machine.A500PLUS or Puae6Machine.A2000 or Puae6Machine.CDTV => (1024u, 0u),
		Puae6Machine.A500OG or Puae6Machine.A1000 => (512u, 0u), _ => (512u, 512u),   // A500 and A2000 OG: 512 KiB Chip + 512 KiB Slow
	};

	/// <summary>Whether the machine has an OCS Agnus: it cannot leave its video standard (no BEAMCON0), so its picture in NTSC has the NTSC rows.</summary>
	public static bool IsOcs(Puae6Machine machine) => machine is Puae6Machine.A500 or Puae6Machine.A500OG or Puae6Machine.A1000 or Puae6Machine.A2000OG;

	/// <summary>The number of the machine in p6_config.machine (P6_MACHINE_*, config/schema/options.json).</summary>
	static uint MachineNumber(Puae6Machine machine) => machine switch
	{
		Puae6Machine.A600 => P6.MachineA600, Puae6Machine.A1200 => P6.MachineA1200, Puae6Machine.A500OG => P6.MachineA500OG, Puae6Machine.A500PLUS => P6.MachineA500Plus,
		Puae6Machine.A1000 => P6.MachineA1000, Puae6Machine.A2000OG => P6.MachineA2000OG, Puae6Machine.A2000 => P6.MachineA2000, Puae6Machine.A4030 => P6.MachineA4030, Puae6Machine.A4040 => P6.MachineA4040, Puae6Machine.CD32 => P6.MachineCd32, Puae6Machine.CDTV => P6.MachineCdtv, _ => P6.MachineA500,
	};

	static uint ChipKib(Puae6Chip chip) => chip switch { Puae6Chip.KB512 => 512u, Puae6Chip.MB1 => 1024u, Puae6Chip.MB2 => 2048u, _ => 0u };

	static uint SlowKib(Puae6Slow slow) => slow switch { Puae6Slow.None => P6.MemoryNone, Puae6Slow.KB512 => 512u, Puae6Slow.MB1 => 1024u, Puae6Slow.MB1_5 => 1536u, Puae6Slow.MB1_75 => 1792u, _ => 0u };

	// The CPU (T52): the numbers of config/schema/options.json (0 is the machine's own, 1 + (model - 68000) / 10 the others).
	static byte CpuModelNumber(Puae6Cpu cpu) => cpu switch { Puae6Cpu.M68000 => (byte)1, Puae6Cpu.M68010 => (byte)2, Puae6Cpu.M68020 => (byte)3, Puae6Cpu.M68030 => (byte)4, Puae6Cpu.M68040 => (byte)5, Puae6Cpu.M68060 => (byte)7, _ => (byte)0 };

	static byte CollisionLevelNumber(Puae6CollisionLevel level) => level switch
	{
		Puae6CollisionLevel.Auto => (byte)0, Puae6CollisionLevel.None => (byte)1, Puae6CollisionLevel.Sprites => (byte)2,
		Puae6CollisionLevel.Playfields => (byte)3, Puae6CollisionLevel.Full => (byte)4,
		_ => throw new ArgumentOutOfRangeException(nameof(level)),
	};

	static byte CpuCompatibilityNumber(Puae6CpuCompatibility level) => level switch
	{
		Puae6CpuCompatibility.Exact => (byte)0, Puae6CpuCompatibility.Compatible => (byte)1, Puae6CpuCompatibility.Normal => (byte)2,
		_ => throw new ArgumentOutOfRangeException(nameof(level), level, "CPU accuracy must be Exact, Compatible or Normal"),
	};

	// The clock of the CPU (T52): the numbers of config/schema/options.json (0 is the machine's own, the others the multiplier itself).
	static byte CpuMultiplierNumber(Puae6CpuClock clock) => clock switch { Puae6CpuClock.X1 => (byte)1, Puae6CpuClock.X2 => (byte)2, Puae6CpuClock.X4 => (byte)4, Puae6CpuClock.X8 => (byte)8, Puae6CpuClock.X10 => (byte)10, Puae6CpuClock.X12 => (byte)12, Puae6CpuClock.X16 => (byte)16, _ => (byte)0 };

	// The controller of the hard files (T56): the numbers of config/schema/options.json (0 the UAE controller, 1 the IDE port of Gayle).
	static byte HdfControllerNumber(Puae6HdfController controller) => controller switch { Puae6HdfController.Ide => (byte)1, _ => (byte)0 };

	static uint FpuNumber(Puae6Fpu fpu) => fpu switch { Puae6Fpu.M68881 => 68881u, Puae6Fpu.M68882 => 68882u, Puae6Fpu.M68040 => 68040u, Puae6Fpu.M68060 => 68060u, _ => 0u };

	// Paula's output (T73a): the numbers of config/schema/options.json (0 in each is the profile's own setting; 70 % is 0, not 8). The enums list the values of the schema in order.
	static byte SoundSeparationNumber(Puae6SoundSeparation separation) => separation == Puae6SoundSeparation.P70 ? (byte)0 : (byte)(1 + (int)separation);

	static byte SoundInterpolationNumber(Puae6SoundInterpolation interpolation) => interpolation switch { Puae6SoundInterpolation.None => (byte)1, Puae6SoundInterpolation.RH => (byte)4, Puae6SoundInterpolation.Crux => (byte)5, _ => (byte)0 };

	static byte SoundFilterNumber(Puae6SoundFilter filter) => filter switch { Puae6SoundFilter.Off => (byte)1, Puae6SoundFilter.On => (byte)3, _ => (byte)0 };

	static byte SoundFilterTypeNumber(Puae6SoundFilterType type) => type switch { Puae6SoundFilterType.A500 => (byte)1, Puae6SoundFilterType.A1200 => (byte)2, _ => (byte)0 };

	// The floppy speed (T62): the numbers of config/schema/options.json (0 is the profile's own, the real drive).
	static byte FloppySpeedNumber(Puae6FloppySpeed speed) => speed switch { Puae6FloppySpeed.P200 => (byte)2, Puae6FloppySpeed.P400 => (byte)3, Puae6FloppySpeed.P800 => (byte)4, Puae6FloppySpeed.Turbo => (byte)5, _ => (byte)0 };
	static byte FloppyDrivesNumber(Puae6FloppyDrives drives) => drives switch { Puae6FloppyDrives.One => (byte)1, Puae6FloppyDrives.Two => (byte)2, Puae6FloppyDrives.Three => (byte)3, Puae6FloppyDrives.Four => (byte)4, _ => (byte)0 };
	int ConnectedFloppyDrives => _syncSettings.Machine is Puae6Machine.CD32 or Puae6Machine.CDTV ? 0 : _syncSettings.PhysicalFloppyDrives == Puae6FloppyDrives.Auto ? Math.Max(2, _syncSettings.FloppyDrives) : FloppyDrivesNumber(_syncSettings.PhysicalFloppyDrives);

	// The raster (T72): the numbers of config/schema/options.json (0 is the profile's own, hires and double lines).
	static byte VideoResolutionNumber(Puae6VideoResolution resolution) => resolution switch { Puae6VideoResolution.Lores => (byte)1, Puae6VideoResolution.SuperHires => (byte)3, _ => (byte)0 };

	static byte VideoLineModeNumber(Puae6VideoLineMode mode) => mode switch { Puae6VideoLineMode.Single => (byte)1, _ => (byte)0 };

	/// <summary>Columns of the buffer a machine with these settings announces at the load: 380 at lores, 760 at hires (the profile's), 1520 at super-hires (ADR 0006).</summary>
	public static int RasterWidth(Puae6SyncSettings settings) => settings.VideoResolution switch { Puae6VideoResolution.Lores => 380, Puae6VideoResolution.SuperHires => 1520, _ => 760 };

	/// <summary>Rows of that buffer: 576, except an OCS machine in NTSC (486; the ECS and AGA machines keep the PAL height so that a programmed PAL beam fits), and half of them with single lines.</summary>
	public static int RasterHeight(Puae6SyncSettings settings) =>
		(settings.Region == Puae6Region.NTSC && IsOcs(settings.Machine) ? 486 : 576) / (settings.VideoLineMode == Puae6VideoLineMode.Single ? 2 : 1);

	static uint FastKib(Puae6Fast fast) => fast switch { Puae6Fast.None => P6.MemoryNone, Puae6Fast.MB1 => 1024u, Puae6Fast.MB2 => 2048u, Puae6Fast.MB4 => 4096u, Puae6Fast.MB8 => 8192u, _ => 0u };
	static byte Z3MemoryNumber(Puae6Z3Memory memory) => Enum.IsDefined(typeof(Puae6Z3Memory), memory)
		? (byte)memory : throw new ArgumentOutOfRangeException(nameof(memory), "Unknown Z3 capacity");

	static int Z3MemoryKiB(Puae6Z3Memory memory) => memory switch
	{
		Puae6Z3Memory.None => 0, Puae6Z3Memory.MB16 => 16384,
		Puae6Z3Memory.MB1 => 1024, Puae6Z3Memory.MB2 => 2048, Puae6Z3Memory.MB4 => 4096, Puae6Z3Memory.MB8 => 8192,
		Puae6Z3Memory.MB32 => 32768, Puae6Z3Memory.MB64 => 65536, Puae6Z3Memory.MB128 => 131072,
		Puae6Z3Memory.MB256 => 262144, Puae6Z3Memory.MB512 => 524288,
		_ => throw new ArgumentOutOfRangeException(nameof(memory), "Unknown Z3 capacity"),
	};

	/// <summary>The memory the settings ask for beyond what the machine comes with (Chip and Slow above their own sizes, and all the Fast RAM), in KiB.</summary>
	static int ExtraMemoryKiB(Puae6SyncSettings settings)
	{
		long overlay = settings.FloppyWrites ? FloppyOverlayKiB : 0;
		var (ownChip, ownSlow) = OwnMemory(settings.Machine);
		uint chip = ChipKib(settings.ChipMemory), slow = SlowKib(settings.SlowMemory);
		uint fast = FastKib(settings.FastMemory);
		long extra = (fast == P6.MemoryNone ? 0 : fast == 0 && (settings.Machine is Puae6Machine.A4030 or Puae6Machine.A4040) ? 8192 : fast) + overlay;
		extra += Z3MemoryKiB(settings.Z3Memory);
		if (chip > ownChip) extra += chip - ownChip;
		if (slow != P6.MemoryNone && slow > ownSlow) extra += slow - ownSlow;
		return (int)extra;
	}


	Puae6Guest _guest = null!;                  // replaced once, before the first unit, when a SaveRam is stored (the base disks change: StoreSaveRam)
	readonly Puae6SyncSettings _syncSettings;
	Puae6Settings _settings;
	readonly BasicServiceProvider _serviceProvider;
	readonly int[] _videoBuffer = new int[MaxWidth * MaxHeight];
	readonly short[] _soundBuffer = new short[2 * MaxAudioFrames];
	MemoryDomainList _domains = null!;
	int _soundFrames;
	bool _disposed;

	// What the wrapper knows that the machine does not: all of it is causal (it decides what the next p6_input holds) and is saved with the state.
	bool _mouseKnown;
	int _mouseReferenceX, _mouseReferenceY;     // the position the machine was last told about
	bool _mouse2Known;
	int _mouse2ReferenceX, _mouse2ReferenceY;
	bool _capsLatched, _capsHeld;               // the Caps Lock latch, and whether the button was down in the previous frame
	// Media: the disk in each drive (an index into the media, -1 empty), what Next Drive and Next Slot selected, whether each disk button was down in the
	// previous frame (a press is an edge: a held button does not repeat) and the last sequence number given to the machine, which only ever grows.
	const int Drives = 4;
	readonly List<byte[]> _media;
	readonly IReadOnlyList<string> _mediaNames;
	// The hard files (T63b), as the core was given them: the bases of the SaveRam's diff and what the media hash covers. They are mounted at power-on and are the p6 media after the floppies
	// (the index of hard file n in p6w_media_export and the write status is _media.Count + n); the guest keeps the writes in its overlay, so these arrays never change.
	readonly List<byte[]> _hardFiles;
	readonly IReadOnlyList<string> _hardFileNames;
	readonly int[] _driveDisk = [ -1, -1, -1, -1 ];
	int _currentDrive, _currentSlot;
	int _cdOrdinal;
	int _cdPending = -1; // Requested ordinal; CDTV must observe absence before insertion can apply.
	bool _cdPresent;
	bool _ejectHeld, _insertHeld, _slotHeld, _driveHeld;
	ulong _mediaSequence;
	StateIdentity _identity = null!;            // what a state is valid for: this guest build, these heaps, these settings, this firmware, these disks
	// Persistence of the floppy writes (T28, docs/03-waterbox.md): none of it is machine state; the writes themselves are in the sandbox and travel with its snapshot.
	readonly Puae6Parameters _parameters;       // what is needed to open the guest again when a SaveRam is stored before the first unit
	ImportedSaveRam? _imported;                 // the SaveRam that was stored, if any: the disks the guest has are the bases with it over them
	bool _started;                              // a unit ran or a state loaded: the base disks of the guest cannot change any more
	byte[]? _saveRamClean;                      // SHA-256 of the SaveRam as of the last CloneSaveRam(clearDirty) or StoreSaveRam; SaveRamModified compares with it

	sealed class ImportedSaveRam
	{
		internal byte[] Hash = [ ];
		internal byte[] Bytes = [ ]; // Canonical imported base, never the later write overlay.
		internal HashSet<int> Disks = new();
		internal HashSet<int> HardFiles = new();
	}
	bool _faulted;                              // a state load failed after the sandbox was touched: the machine is undefined until a state loads whole

	public Puae6Core(CoreComm comm, Puae6Parameters parameters)
	{
		CoreComm = comm;
		parameters = Puae6WhdLoad.Resolve(parameters);
		parameters = Puae6Volumes.Resolve(parameters);
		_syncSettings = parameters.SyncSettings.Clone();
		ValidateCartridge(_syncSettings, parameters.CartridgeFirmware, parameters.CdCount != 0);
		_settings = parameters.Settings.Clone();
		if (parameters.Firmware.Length == 0) throw new ArgumentException("the PUAE6 core needs a Kickstart image", nameof(parameters));
		if (_syncSettings.FloppyDrives is < 1 or > Drives) throw new ArgumentException($"the Amiga has {Drives} floppy drives at most", nameof(parameters));
		if (!Enum.IsDefined(typeof(Puae6FloppyDrives), _syncSettings.PhysicalFloppyDrives)) throw new ArgumentException("unknown physical floppy drive setting", nameof(parameters));
		if (ConnectedFloppyDrives != 0 && _syncSettings.FloppyDrives > ConnectedFloppyDrives) throw new ArgumentException("more boot disks than connected floppy drives", nameof(parameters));
		if (parameters.Media.Count > P6.MaxMedia) throw new ArgumentException($"at most {P6.MaxMedia} disks are supported", nameof(parameters));
		foreach (var disk in parameters.Media)
			// A floppy image is a plain ADF or (T57, T58) an extended ADF, an FDI or an IPF, read only; the guest's bridge says which and why not (WRONG_MEDIA with the reason), here only the cap that keeps the sealed heap's budget.
			if (disk.Length != P6.AdfBytes && (disk.Length < 12 || disk.Length > P6.MaxTrackDiskBytes))
				throw new ArgumentException($"a floppy image is an ADF of {P6.AdfBytes} bytes or an extended ADF, FDI or IPF of at most {P6.MaxTrackDiskBytes}, this one has {disk.Length}", nameof(parameters));
		_media = parameters.Media.Select(disk => (byte[])disk.Clone()).ToList();
		ValidateHardFiles(parameters, _media.Count);
		_hardFiles = parameters.HardFiles.Select(image => (byte[])image.Clone()).ToList();
		_hardFileNames = parameters.HardFileNames;
		_parameters = new Puae6Parameters
		{
			GuestDirectory = parameters.GuestDirectory, GuestFileName = parameters.GuestFileName, PinnedGuestSha256 = parameters.PinnedGuestSha256,
			Firmware = (byte[])parameters.Firmware.Clone(), CartridgeFirmware = parameters.CartridgeFirmware?.ToArray(), CdInfo = parameters.CdInfo, CdInfoV2 = parameters.CdInfoV2, CdInfoV3 = parameters.CdInfoV3,
			ReadCd = parameters.ReadCd, CdOwner = parameters.CdOwner,
			CdCatalog = parameters.CdCatalog?.ToArray(), CdCatalogV2 = parameters.CdCatalogV2?.ToArray(), CdCatalogV3 = parameters.CdCatalogV3?.ToArray(), ReadCdCatalog = parameters.ReadCdCatalog,
			CdSlots = (int[])parameters.CdSlots.Clone(), CdNames = (string[])parameters.CdNames.Clone(),
		};
		_mediaNames = parameters.MediaNames;
		ControllerDefinition = Controllers.Define(_syncSettings, Math.Max(_media.Count, _parameters.CdSlotCount));
		// The buffer the machine announces at the load (ADR 0006): 760 columns and 576 rows for the profile's raster, except an OCS machine in NTSC (the A500, the A500 OG, the A1000 and the A2000 OG cannot leave their
		// standard); the ECS and AGA machines keep the PAL height in NTSC too so that a programmed PAL beam fits. The raster options scale it (T72). The first picture replaces it with the size that was really drawn.
		BufferWidth = RasterWidth(_syncSettings);
		BufferHeight = RasterHeight(_syncSettings);
		(VsyncNumerator, VsyncDenominator) = _syncSettings.Region == Puae6Region.NTSC ? (60, 1) : (50, 1);
		_aspectNum = 1; _aspectDen = 1;

		BootGuest(_media, _hardFiles, null);
		_serviceProvider = new BasicServiceProvider(this);
		_serviceProvider.Register<IMemoryDomains>(_domains);
		// ISaveRam exists for writable floppies, hard files and CD32 EEPROM; a core with none of these has no durable memory.
		if (!SaveRamPossible) _serviceProvider.Unregister<ISaveRam>();
		else _saveRamClean = _syncSettings.Machine == Puae6Machine.CD32 ? _nvramInitialHash : SaveRamHash(null);
	}

	/// <summary>
	/// Opens the guest and brings the machine to the state it is in at the first unit (initialized with <paramref name="guestMedia"/>, boot disks queued, sealed), then makes it the
	/// core's. When the core already had a guest (a SaveRam changes the base disks before the first unit) the old one is disposed only once the new one is whole; a failure leaves the
	/// core as it was.
	/// </summary>
	void BootGuest(IReadOnlyList<byte[]> guestMedia, IReadOnlyList<byte[]> guestHardFiles, ImportedSaveRam? imported)
	{
		var heaps = Heaps(_syncSettings, _hardFiles.Select(image => image.LongLength));
		var cd = _parameters.CreateCdHost();
		var guest = _parameters.PinnedGuestSha256 is { } pin
			? Puae6Guest.Open(_parameters.GuestDirectory, _parameters.GuestFileName, pin, heaps, cd)
			: Puae6Guest.Open(_parameters.GuestDirectory, _parameters.GuestFileName, heaps, cd);
		var previous = (Guest: _guest, Sequence: _mediaSequence, Disks: (int[])_driveDisk.Clone(), Imported: _imported, CdOrdinal: _cdOrdinal, CdPresent: _cdPresent, CdPending: _cdPending);
		try
		{
			_guest = guest;
			_mediaSequence = 0;
			for (int i = 0; i < Drives; ++i) _driveDisk[i] = -1;
			_imported = imported;
			InitializeMachine(_parameters.Firmware, guestMedia, guestHardFiles);
			MountBootDisks();
			var identity = MakeIdentity(_parameters.Firmware);
			var domains = CreateMemoryDomains();
			guest.Seal();
			if (cd is not null && cd.Activate(guest) != P6.Ok) throw new InvalidOperationException("CD device activation refused");
			_cdOrdinal = 0; _cdPresent = cd is not null; _cdPending = -1;
			_identity = identity;
			_domains = domains;
		}
		catch
		{
			_guest = previous.Guest;
			_mediaSequence = previous.Sequence;
			previous.Disks.CopyTo(_driveDisk, 0);
			_imported = previous.Imported; _cdOrdinal = previous.CdOrdinal; _cdPresent = previous.CdPresent; _cdPending = previous.CdPending;
			guest.Dispose();
			throw;
		}
		previous.Guest?.Dispose();
	}

	CoreComm CoreComm { get; }

	// The floppies first and then the hard files: the index of a floppy in the p6 media is its slot, and hard file n is _media.Count + n. The content id of each is the SHA-256 of the image it is given
	// (the p6 bridge checks it): after a SaveRam the images are the bases with its sectors and chunks over them, and their ids are not the bases'.
	void InitializeMachine(byte[] firmware, IReadOnlyList<byte[]> media, IReadOnlyList<byte[]> hardFiles)
	{
		var init = new P6wInit { AbiVersion = P6.AbiVersion, StructSize = (uint)sizeof(P6wInit) };
		var pins = new List<GCHandle>();
		init.MediaCount = (uint)(media.Count + hardFiles.Count);
		for (int i = 0; i < media.Count + hardFiles.Count; ++i)
		{
			var image = i < media.Count ? media[i] : hardFiles[i - media.Count];
			pins.Add(GCHandle.Alloc(image, GCHandleType.Pinned));
			var entry = init.Media.At(i);
			entry->Type = i < media.Count ? P6.MediaAdf : P6.MediaHdf;
			byte[] id;
			using (var sha = System.Security.Cryptography.SHA256.Create()) id = sha.ComputeHash(image);
			for (int b = 0; b < P6.ContentIdSize; ++b) entry->ContentId[b] = id[b];
			entry->Data = (byte*)pins[i].AddrOfPinnedObject();
			entry->Bytes = (ulong)image.Length;
		}
		init.Config.AbiVersion = P6.AbiVersion;
		init.Config.StructSize = (uint)sizeof(P6Config);
		init.Config.Machine = MachineNumber(_syncSettings.Machine);
		init.Config.VideoStandard = _syncSettings.Region == Puae6Region.NTSC ? P6.VideoNtsc : P6.VideoPal;
		init.Config.AudioRateHz = 48000;
		init.Config.Flags = ConfigFlags(_syncSettings.Cartridge, _syncSettings.FloppyWrites, _syncSettings.Machine);
		init.Config.RandomSeed = _syncSettings.RandomSeed;
		init.Config.RtcEpochSeconds = _syncSettings.RtcEpochSeconds;
		init.Config.ChipKib = ChipKib(_syncSettings.ChipMemory);
		init.Config.SlowKib = SlowKib(_syncSettings.SlowMemory);
		init.Config.FastKib = FastKib(_syncSettings.FastMemory);
		init.Config.Z3Memory = Z3MemoryNumber(_syncSettings.Z3Memory);
		init.Config.FpuModel = FpuNumber(_syncSettings.FpuModel);
		init.Config.CpuModel = CpuModelNumber(_syncSettings.CpuModel);
		init.Config.CpuCompatibility = CpuCompatibilityNumber(_syncSettings.CpuCompatibility);
		init.Config.CollisionLevel = CollisionLevelNumber(_syncSettings.CollisionLevel);
		init.Config.CpuMultiplier = CpuMultiplierNumber(_syncSettings.CpuMultiplier);
		init.Config.HdfController = HdfControllerNumber(_syncSettings.HdfController);
		init.Config.SoundSeparation = SoundSeparationNumber(_syncSettings.SoundSeparation);
		init.Config.SoundInterpolation = SoundInterpolationNumber(_syncSettings.SoundInterpolation);
		init.Config.SoundFilter = SoundFilterNumber(_syncSettings.SoundFilter);
		init.Config.SoundFilterType = SoundFilterTypeNumber(_syncSettings.SoundFilterType);
		init.Config.FloppySpeed = FloppySpeedNumber(_syncSettings.FloppySpeed);
		init.Config.FloppyDrives = FloppyDrivesNumber(_syncSettings.PhysicalFloppyDrives);
		if (ConnectedFloppyDrives != 0 && init.Config.FloppyDrives == 0 && _syncSettings.FloppyDrives > 2) init.Config.FloppyDrives = (byte)_syncSettings.FloppyDrives;
		init.Config.VideoResolution = VideoResolutionNumber(_syncSettings.VideoResolution);
		init.Config.VideoLineMode = VideoLineModeNumber(_syncSettings.VideoLineMode);
		fixed (byte* rom = firmware)
		{
			init.Firmware = rom;
			init.FirmwareBytes = (ulong)firmware.Length;
			using var _ = _guest.Host.EnterExit();
			int result;
			try
			{
				uint model = CartridgeModel(_syncSettings.Cartridge);
				fixed (byte* raw = _parameters.CartridgeFirmware)
					result = model != 0
						? _guest.Lib.p6w_init_machine_cartridge((IntPtr)(&init), model, (IntPtr)raw, (ulong)_parameters.CartridgeFirmware!.Length)
						: _guest.Lib.p6w_init_machine((IntPtr)(&init));
			}
			finally { foreach (var pin in pins) pin.Free(); }
			if (result != P6.Ok)
				throw new InvalidOperationException($"the PUAE6 guest refused the machine ({P6.ResultName(result)}): {Mershul.PtrToStringUtf8((IntPtr)init.Error)}");
		}
	}

	// Disks that are in a drive at power-on: events queued before the first unit set the boot disks (docs/02-contracts.md).
	void MountBootDisks()
	{
		using var _ = _guest.Host.EnterExit();
		for (int drive = 0; drive < Math.Min(_syncSettings.FloppyDrives, _media.Count); ++drive)
		{
			EnqueueMedia(P6.MediaEventMount, (uint)drive, (uint)drive);
			_driveDisk[drive] = drive;
		}
	}

	/// <summary>How many media events the machine has been given: the last sequence number. A held button does not make more than one.</summary>
	public ulong MediaEventsSent => _mediaSequence;

	// The sequence number only grows, also across ResetCounters, because the machine accepts a strictly increasing one for the whole session; it moves when the
	// machine accepted the event, and is part of the state.
	void EnqueueMedia(uint type, uint drive, uint mediaIndex)
	{
		var call = new P6wMediaEvent { AbiVersion = P6.AbiVersion, StructSize = (uint)sizeof(P6wMediaEvent) };
		call.Event.AbiVersion = P6.AbiVersion;
		call.Event.StructSize = (uint)sizeof(P6MediaEvent);
		call.Event.Type = type;
		call.Event.Drive = drive;
		call.Event.MediaIndex = mediaIndex;
		call.Event.Sequence = _mediaSequence + 1;
		int result = _guest.Lib.p6w_media_event_enqueue((IntPtr)(&call));
		if (result != P6.Ok) throw new InvalidOperationException($"the machine refused a media event ({P6.ResultName(result)}): {Mershul.PtrToStringUtf8((IntPtr)call.Error)}");
		_mediaSequence = call.Event.Sequence;
	}

	string DiskName(int slot) => slot < _mediaNames.Count && !string.IsNullOrEmpty(_mediaNames[slot]) ? _mediaNames[slot] : $"disk {slot + 1}";

	// The disk buttons, by edge. Selection first (Next Drive, Next Slot), then the action on what is selected (Eject, else Insert), at the head of the unit.
	void ApplyDiskButtons(IController controller)
	{
		if (_parameters.CdSlotCount != 0) { ApplyCdButtons(controller); return; }
		if (_media.Count == 0) return;
		bool drive = controller.IsPressed(Controllers.NextDrive), slot = controller.IsPressed(Controllers.NextSlot);
		bool eject = controller.IsPressed(Controllers.EjectDisk), insert = controller.IsPressed(Controllers.InsertDisk);
		if (drive && !_driveHeld)
		{
			_currentDrive = (_currentDrive + 1) % (_syncSettings.PhysicalFloppyDrives == Puae6FloppyDrives.Auto ? _syncSettings.FloppyDrives : ConnectedFloppyDrives);
			CoreComm.Notify($"Selected drive DF{_currentDrive}: {(_driveDisk[_currentDrive] < 0 ? "empty" : DiskName(_driveDisk[_currentDrive]))}", 4);
		}
		if (slot && !_slotHeld)
		{
			_currentSlot = (_currentSlot + 1) % _media.Count;
			CoreComm.Notify($"Selected slot {_currentSlot}: {DiskName(_currentSlot)}", 4);
		}
		if (eject && !_ejectHeld)
		{
			EnqueueMedia(P6.MediaEventEject, (uint)_currentDrive, P6.MediaIndexEject);
			_driveDisk[_currentDrive] = -1;
			CoreComm.Notify($"Ejected drive DF{_currentDrive}", 4);
		}
		else if (insert && !_insertHeld)
		{
			EnqueueMedia(P6.MediaEventMount, (uint)_currentDrive, (uint)_currentSlot);
			_driveDisk[_currentDrive] = _currentSlot;
			CoreComm.Notify($"Inserted drive DF{_currentDrive}: {DiskName(_currentSlot)}", 4);
		}
		_driveHeld = drive; _slotHeld = slot; _ejectHeld = eject; _insertHeld = insert;
	}

	void ApplyCdButtons(IController controller)
	{
		bool slot = controller.IsPressed(Controllers.NextSlot), eject = controller.IsPressed(Controllers.EjectDisk), insert = controller.IsPressed(Controllers.InsertDisk);
		if (slot && !_slotHeld)
		{
			_currentSlot = (_currentSlot + 1) % _parameters.CdSlotCount;
			CoreComm.Notify($"Selected CD slot {_currentSlot}: {(_parameters.CdNames.Length == 0 ? "CD" : _parameters.CdNames[_currentSlot])}", 4);
		}
		bool Change(int ordinal, bool present)
		{
			int result = _guest.Cd!.Change(_guest, (uint)ordinal, present);
			if (result == P6.Busy && present && _syncSettings.Machine == Puae6Machine.CDTV) return false;
			if (result != P6.Ok) { _faulted = true; throw new InvalidOperationException($"CD change refused: {P6.ResultName(result)}"); }
			_cdOrdinal = ordinal; _cdPresent = present; ++_mediaSequence;
			return true;
		}
		if (eject && !_ejectHeld) { _cdPending = -1; Change(_cdOrdinal, false); }
		else if (insert && !_insertHeld)
		{
			int ordinal = _parameters.ReadCdCatalog is not null ? _parameters.CdSlots[_currentSlot] : 0;
			if (_cdPresent && ordinal != _cdOrdinal) Change(_cdOrdinal, false);
			_cdPending = ordinal;
		}
		// Selection alone cannot retarget an earlier insertion. BUSY applies nothing;
		// retry the captured intent at later boundaries, including after restore.
		if (_cdPending >= 0 && Change(_cdPending, true)) _cdPending = -1;
		_slotHeld = slot; _ejectHeld = eject; _insertHeld = insert;
	}

	StateIdentity MakeIdentity(byte[] firmware, byte[]? initialNvramHash = null)
		=> MakeIdentity(firmware, initialNvramHash, _imported?.Hash);

	StateIdentity MakeIdentity(byte[] firmware, byte[]? initialNvramHash, byte[]? saveRamHash)
	{
		var heaps = Heaps(_syncSettings, _hardFiles.Select(image => image.LongLength));
		// The memory enters the identity only when it is not the machine's own: the identity of a core with the stock memory is the one it always had.
		string memory = _syncSettings.ChipMemory == Puae6Chip.Auto && _syncSettings.SlowMemory == Puae6Slow.Auto && _syncSettings.FastMemory == Puae6Fast.Auto
			? "" : $";chip={_syncSettings.ChipMemory};slow={_syncSettings.SlowMemory};fast={_syncSettings.FastMemory}";
		// The same for the floppy writes (T28): a state of a core that can write its disks does not load in one that cannot, and a SaveRam that was stored changes the disks the
		// guest has, so what it was (its SHA-256) is part of what a state and a movie are valid for. A core without writes keeps the identity it always had.
		if (_syncSettings.FloppyWrites) memory += ";floppywrites=1";
		if (_syncSettings.Cartridge == Puae6Cartridge.HrtMon) memory += ";cartridge=hrtmon;cartsha=" + P6.HrtMonRawSha256;
		if (CartridgeModel(_syncSettings.Cartridge) != 0) memory += ";cartridge=" + _syncSettings.Cartridge + ";cartsha=" + GuestIdentity.Sha256Hex(_parameters.CartridgeFirmware!);
		// And for the FPU (T54): the model is in the config hash of the native state too, and a state or a movie of a machine with a 68882 means nothing to one without.
		if (_syncSettings.FpuModel != Puae6Fpu.None) memory += ";fpu=" + _syncSettings.FpuModel;
		// And for the CPU (T52): the native config hash derives from the resolved model when it is not the machine's own; the text names whatever the setting is off Auto, which is stricter (it also tells an
		// explicit 68000 from Auto on an A500) and never looser.
		if (_syncSettings.CpuModel != Puae6Cpu.Auto) memory += ";cpu=" + _syncSettings.CpuModel;
		if (_syncSettings.CpuCompatibility != Puae6CpuCompatibility.Exact) memory += ";cpuaccuracy=" + _syncSettings.CpuCompatibility;
		if (_syncSettings.CollisionLevel != Puae6CollisionLevel.Auto && _syncSettings.CollisionLevel != Puae6CollisionLevel.Full)
			memory += ";collisions=" + (CollisionLevelNumber(_syncSettings.CollisionLevel) - 1);
		if (_syncSettings.Z3Memory != Puae6Z3Memory.None) memory += ";z3=" + _syncSettings.Z3Memory;
		// And for its clock (T52): the same, off the machine's own.
		if (_syncSettings.CpuMultiplier != Puae6CpuClock.Auto) memory += ";cpuclock=" + _syncSettings.CpuMultiplier;
		// And for the controller of the hard files (T56): the native config hash derives from it only while a hard file is behind the IDE (Gayle is never emulated with nothing attached); the text names
		// the setting whenever it is off Uae, which is stricter (it also tells Ide with no hard file from the default) and never looser.
		if (_syncSettings.HdfController != Puae6HdfController.Uae) memory += ";hdfcontroller=" + _syncSettings.HdfController;
		// And for Paula's output (T73a): the native state's config hash carries the four options when one is off the profile's setting, and the samples a state goes on to produce depend on them. A core at the
		// profile's settings keeps the identity it always had; the text names only what differs, so it is stricter than the native hash (which also equates A500 with Auto on an A500) and never looser.
		string sound = (_syncSettings.SoundSeparation != Puae6SoundSeparation.P70 ? ",separation=" + _syncSettings.SoundSeparation : "") +
			(_syncSettings.SoundInterpolation != Puae6SoundInterpolation.Anti ? ",interpolation=" + _syncSettings.SoundInterpolation : "") +
			(_syncSettings.SoundFilter != Puae6SoundFilter.Emulated ? ",filter=" + _syncSettings.SoundFilter : "") +
			(_syncSettings.SoundFilterType != Puae6SoundFilterType.Auto ? ",filtertype=" + _syncSettings.SoundFilterType : "");
		if (sound.Length != 0) memory += ";sound=" + sound.Substring(1);
		// And for the floppy speed (T62): the native config hash carries it off the real drive's speed, and when every transfer ends depends on it.
		if (_syncSettings.FloppySpeed != Puae6FloppySpeed.P100) memory += ";floppyspeed=" + _syncSettings.FloppySpeed;
		if (ConnectedFloppyDrives != 2) memory += ";physicaldrives=" + ConnectedFloppyDrives;
		// And for the raster (T72): the resolution and the line mode are the engine's own and change the picture the machine draws and the state it goes on from; a core at the profile's raster keeps its identity.
		if (_syncSettings.VideoResolution != Puae6VideoResolution.Hires) memory += ";resolution=" + _syncSettings.VideoResolution;
		if (_syncSettings.VideoLineMode != Puae6VideoLineMode.Double) memory += ";linemode=" + _syncSettings.VideoLineMode;
		if (_syncSettings.ControllerPort3 != Puae6ParallelPort.None || _syncSettings.ControllerPort4 != Puae6ParallelPort.None)
            memory += $";parallel={_syncSettings.ControllerPort3},{_syncSettings.ControllerPort4}";
		if (_syncSettings.Machine == Puae6Machine.CD32) memory += ";nvram=" + BitConverter.ToString(initialNvramHash ?? _nvramInitialHash).Replace("-", "").ToLowerInvariant();
		if (saveRamHash is not null) memory += ";saveram=" + BitConverter.ToString(saveRamHash).Replace("-", "").ToLowerInvariant();
		string Config(Puae6Machine machine) => $"puae6-config-1;machine={machine};region={_syncSettings.Region};seed={_syncSettings.RandomSeed};epoch={_syncSettings.RtcEpochSeconds};" +
			$"port1={_syncSettings.ControllerPort1};port2={_syncSettings.ControllerPort2};drives={_syncSettings.FloppyDrives};audio=48000" + memory;
		byte[] ConfigHash(Puae6Machine machine) => StateEnvelope.Sha256(Encoding.UTF8.GetBytes(Config(machine)));
		// The same settings on each of the other machines: a state or a movie that carries one of these hashes was made on that Amiga, and the refusal can say so.
		var others = new Dictionary<string, byte[]>();
		foreach (Puae6Machine machine in Enum.GetValues(typeof(Puae6Machine)))
			if (machine != _syncSettings.Machine) others[machine.ToString()] = ConfigHash(machine);
		var media = new List<byte[]> { BitConverter.GetBytes(_media.Count) };
		foreach (var disk in _media) media.Add(StateEnvelope.Sha256(disk));
		// The hard files (T63b): how many and what each one is, as the core was given them. A core without hard files keeps the media hash it always had. What the guest wrote to them is state, not identity.
		if (_hardFiles.Count != 0)
		{
			media.Add(Encoding.ASCII.GetBytes("hard files"));
			media.Add(BitConverter.GetBytes(_hardFiles.Count));
			foreach (var image in _hardFiles) media.Add(StateEnvelope.Sha256(image));
		}
		if (_parameters.CdIdentity() is { } cdId)
		{
			media.Add(Encoding.ASCII.GetBytes("CD2448")); media.Add(cdId);
		}
		return new StateIdentity(_guest.Identity.Sha256, [ heaps.SbrkHeapSizeKB, heaps.SealedHeapSizeKB, heaps.InvisibleHeapSizeKB, heaps.PlainHeapSizeKB, heaps.MmapHeapSizeKB ],
			ConfigHash(_syncSettings.Machine), StateEnvelope.Sha256(firmware), StateEnvelope.Sha256(media.ToArray()), _syncSettings.Machine.ToString(), others);
	}

	// The areas of the machine (Chip RAM primary, Slow RAM, the ROM), as function hooks that run in the sandbox: the same objects BizHawk makes for
	// its own cores, over the guest's accesses, so a poke is made by the guest and never by managed code.
	MemoryDomainList CreateMemoryDomains()
	{
		using var _ = _guest.Host.EnterExit();
		var areas = new LibWaterboxCore.MemoryArea[P6wAreaCapacity];
		ulong count;
		fixed (LibWaterboxCore.MemoryArea* p = areas) count = _guest.Lib.p6w_memory_areas((IntPtr)p, (ulong)areas.Length);
		if (count == 0) throw new InvalidOperationException("the guest reported no memory areas");
		var domains = areas.Take((int)count).Select(a => (MemoryDomain)WaterboxMemoryDomain.Create(a, _guest.Host)).ToList();
		domains.Add(_guest.Host.GetPagesDomain());
		var list = new MemoryDomainList(domains);
		list.MainMemory = domains[0];
		return list;
	}

	const int P6wAreaCapacity = 6;

	// ----- IEmulator -----------------------------------------------------------------------------------------------------------------

	public IEmulatorServiceProvider ServiceProvider => _serviceProvider;
	public ControllerDefinition ControllerDefinition { get; }
	public string SystemId => VSystemID.Raw.Amiga;
	public bool DeterministicEmulation => true;
	public int Frame { get; private set; }
	/// <summary>Scheduler ticks of the machine, as p6_frame.master_ticks reports them: total elapsed emulation time in units of 1 / <see cref="ClockRate"/> seconds.</summary>
	public long CycleCount { get; private set; }

	/// <summary>
	/// Master ticks per second: the engine counts 512 ticks per colour clock (a PAL unit of 70825 colour clocks is 36262400 ticks) and the colour clock is the chipset
	/// clock, 3546895 Hz PAL and 28.63636 MHz / 8 NTSC. A movie records <see cref="CycleCount"/> at its end with this rate and EmuHawk checks it when the movie finishes.
	/// </summary>
	public double ClockRate => 512.0 * (_syncSettings.Region == Puae6Region.NTSC ? 3579545.4545454545 : 3546895.0);

	/// <summary>What a state or a movie of this core is valid for: guest build, heaps, settings, firmware and disks.</summary>
	public StateIdentity Identity => _identity;

	public void ResetCounters()
	{
		Frame = 0;
		LagCount = 0;
		IsLagFrame = false;
		CycleCount = 0;
	}

	public bool FrameAdvance(IController controller, bool render, bool renderSound = true)
	{
		// Reject unavailable injected controls before latches or the first-run guard change.
		if (_syncSettings.Machine == Puae6Machine.CDTV && (controller.IsPressed(Controllers.NextDrive)
			|| _parameters.CdSlotCount == 0 && (controller.IsPressed(Controllers.NextSlot) || controller.IsPressed(Controllers.EjectDisk) || controller.IsPressed(Controllers.InsertDisk))))
			throw new NotSupportedException("CDTV has one CD drive; disc controls require a loaded CD catalog");
		if (_movieStart is not null) throw new InvalidOperationException("load the movie's initial state before advancing");
		if (_faulted) throw new InvalidOperationException("a state load failed halfway and the machine is undefined; load a state that is whole, or create the core again");
        var packet = new P6InputV2 { Base = BuildInput(controller, out bool power) };
        if (_syncSettings.ControllerPort3 == Puae6ParallelPort.Joystick) packet.Port2 = JoystickPacket(controller, 3);
        if (_syncSettings.ControllerPort4 == Puae6ParallelPort.Joystick) packet.Port3 = JoystickPacket(controller, 4);
        if (_syncSettings.Cartridge != Puae6Cartridge.Disabled && controller.IsPressed(Controllers.Freeze)) packet.Commands = P6.CommandFreeze;
        if (packet.Port2.Type != 0 || packet.Port3.Type != 0 || packet.Commands != 0) { packet.Base.AbiVersion = 2; packet.Base.StructSize = (uint)sizeof(P6InputV2); }
		_started = true;
		var frame = new P6wFrame { AbiVersion = P6.AbiVersion, StructSize = (uint)sizeof(P6wFrame) };
		uint flags = (render ? P6.RenderVideo : 0) | (renderSound ? P6.RenderAudio : 0) | ((uint)_settings.DriveSoundVolume << P6.RenderDriveVolumeShift);
		using (_guest.Host.EnterExit())
		{
			ApplyDiskButtons(controller);
			// Order inside a boundary (docs/02-contracts.md): reset, media events, input, then the unit; both are queued here and run at its head.
			if (power || controller.IsPressed(Controllers.Reset))
			{
				int reset = power ? _guest.Lib.p6w_reset() : _guest.Lib.p6w_soft_reset();
				if (reset != P6.Ok) throw new InvalidOperationException($"the machine refused a reset ({P6.ResultName(reset)})");
			}
			int result = _guest.Lib.p6w_frame_advance((IntPtr)(&packet), flags, (IntPtr)(&frame));
			if (result != P6.Ok)
				throw new InvalidOperationException($"unit {Frame + 1}: {P6.ResultName(result)}: {Mershul.PtrToStringUtf8((IntPtr)frame.Error)}");
			CopyOut(in frame.Frame, render, renderSound);
		}

		++Frame;
		CycleCount = (long)frame.Frame.MasterTicks;
		if (IsLagFrame = (frame.Frame.Flags & P6.FrameLag) != 0) ++LagCount;
		DriveLightOn = (frame.Frame.Flags & P6.FrameDriveLight) != 0;
		return true;
	}

	// ----- input -----------------------------------------------------------------------------------------------------------------------

	P6Input BuildInput(IController controller, out bool power)
	{
		var input = new P6Input { AbiVersion = P6.AbiVersion, StructSize = (uint)sizeof(P6Input) };
		var keyButtons = Controllers.KeyButtons;
		for (int code = 0; code < keyButtons.Length; ++code)
		{
			string? button = keyButtons[code];
			if (button is null || code == Controllers.CapsLockCode) continue;
			if (controller.IsPressed(button)) input.Keys[code / 32] |= 1u << (code % 32);
		}
		bool caps = controller.IsPressed(keyButtons[Controllers.CapsLockCode]!);
		if (caps && !_capsHeld) _capsLatched = !_capsLatched;
		_capsHeld = caps;
		if (_capsLatched) input.Keys[Controllers.CapsLockCode / 32] |= 1u << (Controllers.CapsLockCode % 32);

		switch (_syncSettings.ControllerPort1)
		{
			case Puae6Port1.Mouse: input.Port0 = MousePacket(controller); break;
			case Puae6Port1.Joystick: input.Port0 = JoystickPacket(controller, 1); break;
			case Puae6Port1.Cd32: input.Port0 = JoystickPacket(controller, 1, true); break;
			case Puae6Port1.Lightgun: input.Port0 = PointerPacket(controller, 0, P6.PortLightgun); break;
			case Puae6Port1.Lightpen: input.Port0 = PointerPacket(controller, 0, P6.PortLightpen); break;
		}
		if (_syncSettings.ControllerPort2 == Puae6Port2.Joystick) input.Port1 = JoystickPacket(controller, 2);
		else if (_syncSettings.ControllerPort2 == Puae6Port2.Cd32) input.Port1 = JoystickPacket(controller, 2, true);
		else if (_syncSettings.ControllerPort2 == Puae6Port2.Mouse) input.Port1 = MousePacket(controller, 2);
		else if (_syncSettings.ControllerPort2 == Puae6Port2.Lightgun) input.Port1 = PointerPacket(controller, 1, P6.PortLightgun);
		else if (_syncSettings.ControllerPort2 == Puae6Port2.Lightpen) input.Port1 = PointerPacket(controller, 1, P6.PortLightpen);
		power = controller.IsPressed(Controllers.Power);
		return input;
	}

	static P6PortInput PointerPacket(IController controller, int port, uint type)
	{
		return new P6PortInput { Type = type,
			Buttons = (controller.IsPressed(Controllers.PointerButtons[port][0]) ? 1u : 0u) | (controller.IsPressed(Controllers.PointerButtons[port][1]) ? 2u : 0u),
			Dx = (short)Clamp(controller.AxisValue(Controllers.PointerAxes[port][0]), -32767, 32767),
			Dy = (short)Clamp(controller.AxisValue(Controllers.PointerAxes[port][1]), -32767, 32767) };
	}

	static P6PortInput JoystickPacket(IController controller, int port, bool cd32 = false)
	{
		var packet = new P6PortInput { Type = cd32 ? P6.PortCd32 : P6.PortJoystick };
		var buttons = (cd32 ? Controllers.Cd32Buttons : Controllers.JoystickButtons)[port - 1];
		var bits = cd32 ? Controllers.Cd32Bits : Controllers.JoystickBits;
		for (int i = 0; i < buttons.Length; ++i)
			if (controller.IsPressed(buttons[i])) packet.Buttons |= bits[i];
		return packet;
	}

	// The mouse of BizHawk is a position; the machine takes movement. The first frame only learns where the pointer is. At most 120 counts per axis and
	// unit reach the guest (docs/02-contracts.md): what does not fit is not lost, the reference moves only by what was sent and the rest follows.
	static int Clamp(int value, int low, int high) => value < low ? low : value > high ? high : value;

	P6PortInput MousePacket(IController controller, int port = 1)
	{
		var packet = new P6PortInput { Type = P6.PortMouse };
		var buttons = Controllers.MouseButtons[port - 1];
		var axes = Controllers.MouseAxes[port - 1];
		if (controller.IsPressed(buttons[0])) packet.Buttons |= P6.Button1;
		if (controller.IsPressed(buttons[1])) packet.Buttons |= P6.Button2;
		if (controller.IsPressed(buttons[2])) packet.Buttons |= P6.Button3;
		int x = controller.AxisValue(axes[0]), y = controller.AxisValue(axes[1]);
		if (port == 2) { x = Clamp(x, 0, Controllers.MouseFieldWidth); y = Clamp(y, 0, Controllers.MouseFieldHeight); }
		ref bool known = ref (port == 1 ? ref _mouseKnown : ref _mouse2Known);
		ref int referenceX = ref (port == 1 ? ref _mouseReferenceX : ref _mouse2ReferenceX);
		ref int referenceY = ref (port == 1 ? ref _mouseReferenceY : ref _mouse2ReferenceY);
		if (!known)
		{
			known = true;
			referenceX = x;
			referenceY = y;
		}
		int dx = Clamp(x - referenceX, -120, 120), dy = Clamp(y - referenceY, -120, 120);
		referenceX += dx;
		referenceY += dy;
		packet.Dx = (short)dx;
		packet.Dy = (short)dy;
		return packet;
	}

	// ----- output: video, audio, lag, drive light -----------------------------------------------------------------------------------------

	int _aspectNum, _aspectDen;

	// The guest's picture and PCM live in its memory until the next call into the guest: they are copied before leaving the sandbox.
	void CopyOut(in P6Frame frame, bool render, bool renderSound)
	{
		if (render && frame.Video != null)
		{
			if (frame.VideoFormat != P6.PixelXrgb8888) throw new InvalidOperationException($"the guest delivered pixel format {frame.VideoFormat}");
			// The buffer is the one the machine announced at the load (ADR 0006, T72a): constant for the life of the image, the same numbers p6_query_video gives for the settings.
			if (frame.VideoWidth != (uint)RasterWidth(_syncSettings) || frame.VideoHeight != (uint)RasterHeight(_syncSettings))
				throw new InvalidOperationException($"the guest delivered a buffer of {frame.VideoWidth}x{frame.VideoHeight}, not the {RasterWidth(_syncSettings)}x{RasterHeight(_syncSettings)} announced for {_syncSettings.Machine} in {_syncSettings.Region}");
			var active = frame.Active;
			if (active.Width > MaxWidth || active.Height > MaxHeight || active.X + active.Width > frame.VideoWidth || active.Y + active.Height > frame.VideoHeight
				|| (ulong)(active.Y + active.Height) * frame.VideoStrideBytes > frame.VideoBytes)
				throw new InvalidOperationException($"the guest delivered a picture of {active.Width}x{active.Height} that does not fit the profile's buffers");
			for (uint row = 0; row < active.Height; ++row)
			{
				var source = new ReadOnlySpan<int>(frame.Video + (active.Y + row) * frame.VideoStrideBytes + active.X * 4, (int)active.Width);
				PixelCopy.XrgbToArgb(source, _videoBuffer.AsSpan((int)(row * active.Width), (int)active.Width));     // XRGB8888 to BizHawk's ARGB
			}
			BufferWidth = (int)active.Width;
			BufferHeight = (int)active.Height;
			_aspectNum = (int)frame.AspectNum;
			_aspectDen = (int)frame.AspectDen;
			if (frame.CadenceNum != 0 && frame.CadenceDen != 0) (VsyncNumerator, VsyncDenominator) = ((int)frame.CadenceNum, (int)frame.CadenceDen);
		}
		if (renderSound)
		{
			if (frame.AudioFrames > MaxAudioFrames) throw new InvalidOperationException($"the guest delivered {frame.AudioFrames} audio frames in one unit");
			_soundFrames = (int)frame.AudioFrames;
			if (_soundFrames != 0) new ReadOnlySpan<short>(frame.Audio, _soundFrames * 2).CopyTo(_soundBuffer);
		}
		else
		{
			_soundFrames = 0;
		}
	}

	// IVideoProvider
	public int[] GetVideoBuffer() => _videoBuffer;
	public int BufferWidth { get; private set; }
	public int BufferHeight { get; private set; }
	public int VirtualWidth => (int)((long)BufferWidth * _aspectNum / Math.Max(_aspectDen, 1));
	public int VirtualHeight => BufferHeight;
	public int VsyncNumerator { get; private set; }
	public int VsyncDenominator { get; private set; }
	public int BackgroundColor => unchecked((int)0xFF000000);

	// ISoundProvider: synchronous, interleaved stereo, 48000 Hz
	public bool CanProvideAsync => false;
	public SyncSoundMode SyncMode => SyncSoundMode.Sync;
	public void SetSyncMode(SyncSoundMode mode)
	{
		if (mode == SyncSoundMode.Async) throw new NotSupportedException("Async mode is not supported.");
	}
	public void GetSamplesSync(out short[] samples, out int nsamp)
	{
		samples = _soundBuffer;
		nsamp = _soundFrames;
		_soundFrames = 0;
	}
	public void GetSamplesAsync(short[] samples) => throw new InvalidOperationException("Async mode is not supported.");
	public void DiscardSamples() => _soundFrames = 0;

	// IInputPollable: the lag comes from the machine (the guest read no input register during the unit); there is no callback per read
	// (docs/02-contracts.md: no callback from inside the engine), so InputCallbacks is part of the interface and never fires.
	public int LagCount { get; set; }
	public bool IsLagFrame { get; set; }
	public IInputCallbackSystem InputCallbacks { get; } = new InputCallbackSystem();

	// IRegionable
	public DisplayType Region => _syncSettings.Region == Puae6Region.NTSC ? DisplayType.NTSC : DisplayType.PAL;

	// IDriveLight
	public bool DriveLightEnabled => true;
	public bool DriveLightOn { get; private set; }
	public string DriveLightIconDescription => "Floppy drive activity";

	// Presentation stays at the user's current choice when a state loads.
	public Puae6Settings GetSettings() => _settings.Clone();
	public Puae6SyncSettings GetSyncSettings() => _syncSettings.Clone();
	public PutSettingsDirtyBits PutSettings(Puae6Settings settings) { _settings = settings.Clone(); return PutSettingsDirtyBits.None; }
	public PutSettingsDirtyBits PutSyncSettings(Puae6SyncSettings settings) => Puae6SyncSettings.NeedsReboot(_syncSettings, settings) ? PutSettingsDirtyBits.RebootCore : PutSettingsDirtyBits.None;

	// ----- IStatable -----------------------------------------------------------------------------------------------------------------
	// A state is an envelope (StateEnvelope: what it is valid for, its length and its checksum) around a payload: what the wrapper holds that the sandbox does not
	// (WrapperState, inventoried in manifests/state-inventory.json) and then the snapshot of the sandbox. A load validates all of it, the fields included, before
	// the sandbox is touched, so a state that is refused changes nothing.

	public bool AvoidRewind => false;

	/// <summary>How many bytes the wrapper's own fields take at the start of a state's payload; the snapshot of the sandbox follows. (A test compares the machine alone, without them.)</summary>
	public static readonly int WrapperStateBytes = MeasureWrapperState();

	static int MeasureWrapperState()
	{
		using var stream = new MemoryStream();
		using (var writer = new BinaryWriter(stream, Encoding.UTF8, true)) Write(writer, new WrapperState());
		return (int)stream.Length;
	}

	sealed class WrapperState
	{
		public int Frame, LagCount, BufferWidth, BufferHeight, VsyncNumerator, VsyncDenominator, AspectNum, AspectDen, MouseX, MouseY, Disk0, Disk1, Disk2, Disk3, CurrentDrive, CurrentSlot;
		public bool IsLagFrame, DriveLightOn, MouseKnown, CapsLatched, CapsHeld, EjectHeld, InsertHeld, SlotHeld, DriveHeld;
		public long CycleCount;
		public ulong MediaSequence;
		public bool Mouse2Known;
		public int Mouse2X, Mouse2Y;
		public int CdPending = -1, CdOrdinal;
		public bool CdPresent;
	}

	WrapperState Capture() => new()
	{
		Frame = Frame, LagCount = LagCount, IsLagFrame = IsLagFrame, CycleCount = CycleCount, BufferWidth = BufferWidth, BufferHeight = BufferHeight,
		VsyncNumerator = VsyncNumerator, VsyncDenominator = VsyncDenominator, AspectNum = _aspectNum, AspectDen = _aspectDen, DriveLightOn = DriveLightOn,
		MouseKnown = _mouseKnown, MouseX = _mouseReferenceX, MouseY = _mouseReferenceY, CapsLatched = _capsLatched, CapsHeld = _capsHeld,
		Disk0 = _driveDisk[0], Disk1 = _driveDisk[1], Disk2 = _driveDisk[2], Disk3 = _driveDisk[3], CurrentDrive = _currentDrive, CurrentSlot = _currentSlot,
		EjectHeld = _ejectHeld, InsertHeld = _insertHeld, SlotHeld = _slotHeld, DriveHeld = _driveHeld, MediaSequence = _mediaSequence,
		Mouse2Known = _mouse2Known, Mouse2X = _mouse2ReferenceX, Mouse2Y = _mouse2ReferenceY, CdPending = _cdPending, CdOrdinal = _cdOrdinal, CdPresent = _cdPresent,
	};

	static void Write(BinaryWriter w, WrapperState s)
	{
		w.Write(s.Frame); w.Write(s.LagCount); w.Write(s.IsLagFrame); w.Write(s.CycleCount);
		w.Write(s.BufferWidth); w.Write(s.BufferHeight); w.Write(s.VsyncNumerator); w.Write(s.VsyncDenominator); w.Write(s.AspectNum); w.Write(s.AspectDen); w.Write(s.DriveLightOn);
		w.Write(s.MouseKnown); w.Write(s.MouseX); w.Write(s.MouseY); w.Write(s.CapsLatched); w.Write(s.CapsHeld);
		w.Write(s.Disk0); w.Write(s.Disk1); w.Write(s.Disk2); w.Write(s.Disk3); w.Write(s.CurrentDrive); w.Write(s.CurrentSlot);
		w.Write(s.EjectHeld); w.Write(s.InsertHeld); w.Write(s.SlotHeld); w.Write(s.DriveHeld); w.Write(s.MediaSequence);
		w.Write(s.Mouse2Known); w.Write(s.Mouse2X); w.Write(s.Mouse2Y); w.Write(s.CdPending); w.Write(s.CdOrdinal); w.Write(s.CdPresent);
	}

	WrapperState Parse(BinaryReader r)
	{
		WrapperState s;
		try
		{
			s = new WrapperState
			{
				Frame = r.ReadInt32(), LagCount = r.ReadInt32(), IsLagFrame = r.ReadBoolean(), CycleCount = r.ReadInt64(),
				BufferWidth = r.ReadInt32(), BufferHeight = r.ReadInt32(), VsyncNumerator = r.ReadInt32(), VsyncDenominator = r.ReadInt32(), AspectNum = r.ReadInt32(),
				AspectDen = r.ReadInt32(), DriveLightOn = r.ReadBoolean(), MouseKnown = r.ReadBoolean(), MouseX = r.ReadInt32(), MouseY = r.ReadInt32(),
				CapsLatched = r.ReadBoolean(), CapsHeld = r.ReadBoolean(), Disk0 = r.ReadInt32(), Disk1 = r.ReadInt32(), Disk2 = r.ReadInt32(), Disk3 = r.ReadInt32(), CurrentDrive = r.ReadInt32(), CurrentSlot = r.ReadInt32(),
				EjectHeld = r.ReadBoolean(), InsertHeld = r.ReadBoolean(), SlotHeld = r.ReadBoolean(), DriveHeld = r.ReadBoolean(), MediaSequence = r.ReadUInt64(),
				Mouse2Known = r.ReadBoolean(), Mouse2X = r.ReadInt32(), Mouse2Y = r.ReadInt32(), CdPending = r.ReadInt32(), CdOrdinal = r.ReadInt32(), CdPresent = r.ReadBoolean(),
			};
		}
		catch (EndOfStreamException)
		{
			throw new StateRejectedException(StateRejection.BadFields, "the payload ends inside the wrapper's fields");
		}
		bool ok = s.Frame >= 0 && s.LagCount >= 0 && s.BufferWidth is >= 0 and <= MaxWidth && s.BufferHeight is >= 0 and <= MaxHeight && s.VsyncNumerator > 0 && s.VsyncDenominator > 0
			&& s.AspectNum > 0 && s.AspectDen > 0 && s.Disk0 >= -1 && s.Disk0 < _media.Count && s.Disk1 >= -1 && s.Disk1 < _media.Count
			&& s.Disk2 >= -1 && s.Disk2 < _media.Count && s.Disk3 >= -1 && s.Disk3 < _media.Count
			&& (ConnectedFloppyDrives > 0 || s.Disk0 == -1) && (ConnectedFloppyDrives > 1 || s.Disk1 == -1)
			&& (ConnectedFloppyDrives > 2 || s.Disk2 == -1) && (ConnectedFloppyDrives > 3 || s.Disk3 == -1)
			&& s.CurrentDrive >= 0 && s.CurrentDrive < Math.Max(1, ConnectedFloppyDrives) && s.CurrentSlot >= 0 && s.CurrentSlot < Math.Max(1, Math.Max(_media.Count, _parameters.CdSlotCount))
			&& s.CdOrdinal >= 0 && s.CdOrdinal < Math.Max(1, _parameters.CdCount) && (_parameters.CdCount != 0 || !s.CdPresent)
			&& s.CdPending >= -1 && (s.CdPending == -1 || _syncSettings.Machine == Puae6Machine.CDTV && !s.CdPresent && s.CdPending < _parameters.CdCount)
			&& s.Mouse2X is >= 0 and <= Controllers.MouseFieldWidth && s.Mouse2Y is >= 0 and <= Controllers.MouseFieldHeight
			&& (s.Mouse2Known || (s.Mouse2X == 0 && s.Mouse2Y == 0));
		if (!ok) throw new StateRejectedException(StateRejection.BadFields, "the state has a field that this machine cannot have");
		return s;
	}

	void Apply(WrapperState s)
	{
		Frame = s.Frame; LagCount = s.LagCount; IsLagFrame = s.IsLagFrame; CycleCount = s.CycleCount;
		BufferWidth = s.BufferWidth; BufferHeight = s.BufferHeight; VsyncNumerator = s.VsyncNumerator; VsyncDenominator = s.VsyncDenominator;
		_aspectNum = s.AspectNum; _aspectDen = s.AspectDen; DriveLightOn = s.DriveLightOn;
		_mouseKnown = s.MouseKnown; _mouseReferenceX = s.MouseX; _mouseReferenceY = s.MouseY; _capsLatched = s.CapsLatched; _capsHeld = s.CapsHeld;
		_mouse2Known = s.Mouse2Known; _mouse2ReferenceX = s.Mouse2X; _mouse2ReferenceY = s.Mouse2Y; _cdPending = s.CdPending; _cdOrdinal = s.CdOrdinal; _cdPresent = s.CdPresent;
		_driveDisk[0] = s.Disk0; _driveDisk[1] = s.Disk1; _driveDisk[2] = s.Disk2; _driveDisk[3] = s.Disk3; _currentDrive = s.CurrentDrive; _currentSlot = s.CurrentSlot;
		_ejectHeld = s.EjectHeld; _insertHeld = s.InsertHeld; _slotHeld = s.SlotHeld; _driveHeld = s.DriveHeld; _mediaSequence = s.MediaSequence;
		_soundFrames = 0;                          // pending output of the timeline that was left is not part of this one
	}

	public void SaveStateBinary(BinaryWriter writer)
	{
		if (_faulted) throw new InvalidOperationException("the machine is undefined after a failed state load; there is nothing to save");
		using var _ = _guest.Host.EnterExit();
		byte[] payload;
		using (var memory = new MemoryStream())
		{
			using (var inner = new BinaryWriter(memory, Encoding.UTF8, true))
			{
				Write(inner, Capture());
				inner.Flush();
				_guest.Host.SaveStateBinary(inner);                  // writes into the stream behind the writer
				inner.Flush();
			}
			payload = memory.ToArray();
		}
		StateEnvelope.Write(writer, _identity, payload);
	}

	public void LoadStateBinary(BinaryReader reader)
	{
		var movieStart = _movieStart;
		var payload = StateEnvelope.Read(reader, movieStart?.Identity ?? _identity); // the movie scope has already validated the whole initial state
		using var stream = new MemoryStream(payload, false);
		using var inner = new BinaryReader(stream);
		var fields = Parse(inner);                                    // and one whose fields cannot be
		using var _ = _guest.Host.EnterExit();
		_started = true;                                              // the machine is not the one a SaveRam could still change
		try
		{
			if (_guest.Cd is { } cd) cd.LoadSnapshot(_guest, inner);
			else _guest.Host.LoadStateBinary(inner);
		}
		catch
		{
			_faulted = true;                                          // the sandbox may be half restored: no unit and no save until a state loads whole
			throw;
		}
		Apply(fields);
		if (movieStart is not null)
		{
			_identity = movieStart.Identity;
			_nvramInitialHash = movieStart.NvramHash;
			_movieStart = null;
		}
		_faulted = false;
	}

	public void Dispose()
	{
		if (_disposed) return;
		_disposed = true;
		try { _guest.Dispose(); }
		finally { _parameters.CdOwner?.Dispose(); }
	}
}

public static class Puae6Names
{
	public const string Core = "PUAE6";
}
