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
	public Puae6SyncSettings SyncSettings { get; set; } = new();
	/// <summary>The ADF images the machine may use (880 KiB each, at most 16): the first <see cref="Puae6SyncSettings.FloppyDrives"/> are in a drive at power-on.</summary>
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
}

/// <summary>
/// PUAE6 as a BizHawk core: WinUAE 6 running as a Waterbox guest (puae6.wbx), advanced exactly one video unit per FrameAdvance through the same
/// p6_run_frame the headless and libretro adapters use. It is independent of the UAE core of BizHawk (its own core ID, its own guest, none of its
/// state) and speaks only the ABI of adapters/waterbox/p6w.h.
/// </summary>
[PortedCore(name: Puae6Names.Core, author: "WinUAE team and the PUAE6 project", portedVersion: "WinUAE 6.0.3", portedUrl: "https://github.com/tonioni/WinUAE", isReleased: false)]
public sealed unsafe partial class Puae6Core : IEmulator, IVideoProvider, ISoundProvider, IStatable, IInputPollable, IRegionable, IDriveLight, ICycleTiming, ISaveRam, ISettable<object, Puae6SyncSettings>
{
	// The profile's buffers (p6_caps of ABI v1): the delivered picture is at most 760x576 and one unit never delivers more than 8192 audio frames.
	public const int MaxWidth = 760, MaxHeight = 576, MaxAudioFrames = 8192;

	// The heaps are part of what a snapshot is compatible with; they are the budgets of manifests/waterbox-memory.json (a test compares them). The mmap heap holds
	// Chip, Slow and Fast RAM among other large blocks, so it grows with the memory the settings ask for beyond the machine's own (T49: twice the extra memory, which
	// keeps the rule of the manifest, a budget of at least twice what was measured; the measurements are recorded per configuration).
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
	static (uint Chip, uint Slow) OwnMemory(Puae6Machine machine) => machine switch { Puae6Machine.A1200 => (2048u, 0u), Puae6Machine.A600 => (1024u, 0u), _ => (512u, 512u) };

	static uint ChipKib(Puae6Chip chip) => chip switch { Puae6Chip.KB512 => 512u, Puae6Chip.MB1 => 1024u, Puae6Chip.MB2 => 2048u, _ => 0u };

	static uint SlowKib(Puae6Slow slow) => slow switch { Puae6Slow.None => P6.MemoryNone, Puae6Slow.KB512 => 512u, Puae6Slow.MB1 => 1024u, Puae6Slow.MB1_5 => 1536u, Puae6Slow.MB1_75 => 1792u, _ => 0u };

	static uint FpuNumber(Puae6Fpu fpu) => fpu switch { Puae6Fpu.M68881 => 68881u, Puae6Fpu.M68882 => 68882u, _ => 0u };

	static uint FastKib(Puae6Fast fast) => fast switch { Puae6Fast.MB1 => 1024u, Puae6Fast.MB2 => 2048u, Puae6Fast.MB4 => 4096u, Puae6Fast.MB8 => 8192u, _ => 0u };

	/// <summary>The memory the settings ask for beyond what the machine comes with (Chip and Slow above their own sizes, and all the Fast RAM), in KiB.</summary>
	static int ExtraMemoryKiB(Puae6SyncSettings settings)
	{
		long overlay = settings.FloppyWrites ? FloppyOverlayKiB : 0;
		var (ownChip, ownSlow) = OwnMemory(settings.Machine);
		uint chip = ChipKib(settings.ChipMemory), slow = SlowKib(settings.SlowMemory);
		long extra = FastKib(settings.FastMemory) + overlay;
		if (chip > ownChip) extra += chip - ownChip;
		if (slow != P6.MemoryNone && slow > ownSlow) extra += slow - ownSlow;
		return (int)extra;
	}


	Puae6Guest _guest = null!;                  // replaced once, before the first unit, when a SaveRam is stored (the base disks change: StoreSaveRam)
	readonly Puae6SyncSettings _syncSettings;
	readonly BasicServiceProvider _serviceProvider;
	readonly int[] _videoBuffer = new int[MaxWidth * MaxHeight];
	readonly short[] _soundBuffer = new short[2 * MaxAudioFrames];
	MemoryDomainList _domains = null!;
	int _soundFrames;
	bool _disposed;

	// What the wrapper knows that the machine does not: all of it is causal (it decides what the next p6_input holds) and is saved with the state.
	bool _mouseKnown;
	int _mouseReferenceX, _mouseReferenceY;     // the position the machine was last told about
	bool _capsLatched, _capsHeld;               // the Caps Lock latch, and whether the button was down in the previous frame
	// Media: the disk in each drive (an index into the media, -1 empty), what Next Drive and Next Slot selected, whether each disk button was down in the
	// previous frame (a press is an edge: a held button does not repeat) and the last sequence number given to the machine, which only ever grows.
	const int Drives = 2;
	readonly List<byte[]> _media;
	readonly IReadOnlyList<string> _mediaNames;
	// The hard files (T63b), as the core was given them: the bases of the SaveRam's diff and what the media hash covers. They are mounted at power-on and are the p6 media after the floppies
	// (the index of hard file n in p6w_media_export and the write status is _media.Count + n); the guest keeps the writes in its overlay, so these arrays never change.
	readonly List<byte[]> _hardFiles;
	readonly IReadOnlyList<string> _hardFileNames;
	readonly int[] _driveDisk = [ -1, -1 ];
	int _currentDrive, _currentSlot;
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
		internal HashSet<int> Disks = new();
		internal HashSet<int> HardFiles = new();
	}
	bool _faulted;                              // a state load failed after the sandbox was touched: the machine is undefined until a state loads whole

	public Puae6Core(CoreComm comm, Puae6Parameters parameters)
	{
		CoreComm = comm;
		_syncSettings = parameters.SyncSettings.Clone();
		if (parameters.Firmware.Length == 0) throw new ArgumentException("the PUAE6 core needs a Kickstart image", nameof(parameters));
		if (_syncSettings.FloppyDrives is < 1 or > Drives) throw new ArgumentException($"the Amiga has {Drives} floppy drives at most", nameof(parameters));
		if (parameters.Media.Count > P6.MaxMedia) throw new ArgumentException($"at most {P6.MaxMedia} disks are supported", nameof(parameters));
		foreach (var disk in parameters.Media)
			if (disk.Length != P6.AdfBytes) throw new ArgumentException($"an ADF has {P6.AdfBytes} bytes, this one {disk.Length}", nameof(parameters));
		_media = parameters.Media.Select(disk => (byte[])disk.Clone()).ToList();
		ValidateHardFiles(parameters, _media.Count);
		_hardFiles = parameters.HardFiles.Select(image => (byte[])image.Clone()).ToList();
		_hardFileNames = parameters.HardFileNames;
		_parameters = new Puae6Parameters
		{
			GuestDirectory = parameters.GuestDirectory, GuestFileName = parameters.GuestFileName, PinnedGuestSha256 = parameters.PinnedGuestSha256,
			Firmware = (byte[])parameters.Firmware.Clone(),
		};
		_mediaNames = parameters.MediaNames;
		ControllerDefinition = Controllers.Define(_syncSettings, _media.Count);
		BufferWidth = MaxWidth;
		// The buffer the machine announces at the load: 576 rows, except an A500 in NTSC (it cannot leave its standard); the A600 and the A1200 keep the PAL height in
		// NTSC too so that a programmed PAL beam fits (ADR 0006). The first picture replaces it with the size that was really drawn.
		BufferHeight = _syncSettings.Region == Puae6Region.NTSC && _syncSettings.Machine == Puae6Machine.A500 ? 486 : MaxHeight;
		(VsyncNumerator, VsyncDenominator) = _syncSettings.Region == Puae6Region.NTSC ? (60, 1) : (50, 1);
		_aspectNum = 1; _aspectDen = 1;

		BootGuest(_media, _hardFiles, null);
		_serviceProvider = new BasicServiceProvider(this);
		_serviceProvider.Register<IMemoryDomains>(_domains);
		// ISaveRam exists only when something can be written: without the flag every floppy is protected, and a hard file is always writable (T63b).
		if (!SaveRamPossible) _serviceProvider.Unregister<ISaveRam>();
		else _saveRamClean = SaveRamHash(null);
	}

	/// <summary>
	/// Opens the guest and brings the machine to the state it is in at the first unit (initialized with <paramref name="guestMedia"/>, boot disks queued, sealed), then makes it the
	/// core's. When the core already had a guest (a SaveRam changes the base disks before the first unit) the old one is disposed only once the new one is whole; a failure leaves the
	/// core as it was.
	/// </summary>
	void BootGuest(IReadOnlyList<byte[]> guestMedia, IReadOnlyList<byte[]> guestHardFiles, ImportedSaveRam? imported)
	{
		var heaps = Heaps(_syncSettings, _hardFiles.Select(image => image.LongLength));
		var guest = _parameters.PinnedGuestSha256 is { } pin
			? Puae6Guest.Open(_parameters.GuestDirectory, _parameters.GuestFileName, pin, heaps)
			: Puae6Guest.Open(_parameters.GuestDirectory, _parameters.GuestFileName, heaps);
		var previous = (Guest: _guest, Sequence: _mediaSequence, Disks: (int[])_driveDisk.Clone(), Imported: _imported);
		try
		{
			_guest = guest;
			_mediaSequence = 0;
			_driveDisk[0] = _driveDisk[1] = -1;
			_imported = imported;
			InitializeMachine(_parameters.Firmware, guestMedia, guestHardFiles);
			MountBootDisks();
			var identity = MakeIdentity(_parameters.Firmware);
			var domains = CreateMemoryDomains();
			guest.Host.Seal();
			_identity = identity;
			_domains = domains;
		}
		catch
		{
			_guest = previous.Guest;
			_mediaSequence = previous.Sequence;
			previous.Disks.CopyTo(_driveDisk, 0);
			_imported = previous.Imported;
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
		init.Config.Machine = _syncSettings.Machine switch { Puae6Machine.A600 => P6.MachineA600, Puae6Machine.A1200 => P6.MachineA1200, _ => P6.MachineA500 };
		init.Config.VideoStandard = _syncSettings.Region == Puae6Region.NTSC ? P6.VideoNtsc : P6.VideoPal;
		init.Config.AudioRateHz = 48000;
		init.Config.Flags = _syncSettings.FloppyWrites ? P6.ConfigFloppyWrites : 0;      // off: every disk is write protected, as in ABI v1
		init.Config.RandomSeed = _syncSettings.RandomSeed;
		init.Config.RtcEpochSeconds = _syncSettings.RtcEpochSeconds;
		init.Config.ChipKib = ChipKib(_syncSettings.ChipMemory);
		init.Config.SlowKib = SlowKib(_syncSettings.SlowMemory);
		init.Config.FastKib = FastKib(_syncSettings.FastMemory);
		init.Config.FpuModel = FpuNumber(_syncSettings.FpuModel);
		fixed (byte* rom = firmware)
		{
			init.Firmware = rom;
			init.FirmwareBytes = (ulong)firmware.Length;
			using var _ = _guest.Host.EnterExit();
			int result;
			try { result = _guest.Lib.p6w_init_machine((IntPtr)(&init)); }
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
		if (_media.Count == 0) return;
		bool drive = controller.IsPressed(Controllers.NextDrive), slot = controller.IsPressed(Controllers.NextSlot);
		bool eject = controller.IsPressed(Controllers.EjectDisk), insert = controller.IsPressed(Controllers.InsertDisk);
		if (drive && !_driveHeld)
		{
			_currentDrive = (_currentDrive + 1) % _syncSettings.FloppyDrives;
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

	StateIdentity MakeIdentity(byte[] firmware)
	{
		var heaps = Heaps(_syncSettings, _hardFiles.Select(image => image.LongLength));
		// The memory enters the identity only when it is not the machine's own: the identity of a core with the stock memory is the one it always had.
		string memory = _syncSettings.ChipMemory == Puae6Chip.Auto && _syncSettings.SlowMemory == Puae6Slow.Auto && _syncSettings.FastMemory == Puae6Fast.None
			? "" : $";chip={_syncSettings.ChipMemory};slow={_syncSettings.SlowMemory};fast={_syncSettings.FastMemory}";
		// The same for the floppy writes (T28): a state of a core that can write its disks does not load in one that cannot, and a SaveRam that was stored changes the disks the
		// guest has, so what it was (its SHA-256) is part of what a state and a movie are valid for. A core without writes keeps the identity it always had.
		if (_syncSettings.FloppyWrites) memory += ";floppywrites=1";
		// And for the FPU (T54): the model is in the config hash of the native state too, and a state or a movie of a machine with a 68882 means nothing to one without.
		if (_syncSettings.FpuModel != Puae6Fpu.None) memory += ";fpu=" + _syncSettings.FpuModel;
		if (_imported is not null) memory += ";saveram=" + BitConverter.ToString(_imported.Hash).Replace("-", "").ToLowerInvariant();
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

	const int P6wAreaCapacity = 4;

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
		if (_faulted) throw new InvalidOperationException("a state load failed halfway and the machine is undefined; load a state that is whole, or create the core again");
		var input = BuildInput(controller, out bool power);
		_started = true;
		var frame = new P6wFrame { AbiVersion = P6.AbiVersion, StructSize = (uint)sizeof(P6wFrame) };
		uint flags = (render ? P6.RenderVideo : 0) | (renderSound ? P6.RenderAudio : 0);
		using (_guest.Host.EnterExit())
		{
			ApplyDiskButtons(controller);
			// Order inside a boundary (docs/02-contracts.md): reset, media events, input, then the unit; both are queued here and run at its head.
			if (power)
			{
				int reset = _guest.Lib.p6w_reset();
				if (reset != P6.Ok) throw new InvalidOperationException($"the machine refused a reset ({P6.ResultName(reset)})");
			}
			int result = _guest.Lib.p6w_frame_advance((IntPtr)(&input), flags, (IntPtr)(&frame));
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
		for (int code = 0; code < Controllers.KeyNames.Length; ++code)
		{
			if (Controllers.KeyNames[code] is null || code == Controllers.CapsLockCode) continue;
			if (controller.IsPressed(Controllers.KeyButton(code))) input.Keys[code / 32] |= 1u << (code % 32);
		}
		bool caps = controller.IsPressed(Controllers.KeyButton(Controllers.CapsLockCode));
		if (caps && !_capsHeld) _capsLatched = !_capsLatched;
		_capsHeld = caps;
		if (_capsLatched) input.Keys[Controllers.CapsLockCode / 32] |= 1u << (Controllers.CapsLockCode % 32);

		switch (_syncSettings.ControllerPort1)
		{
			case Puae6Port1.Mouse: input.Port0 = MousePacket(controller); break;
			case Puae6Port1.Joystick: input.Port0 = JoystickPacket(controller, 1); break;
		}
		if (_syncSettings.ControllerPort2 == Puae6Port2.Joystick) input.Port1 = JoystickPacket(controller, 2);
		power = controller.IsPressed(Controllers.Power);
		return input;
	}

	static P6PortInput JoystickPacket(IController controller, int port)
	{
		var packet = new P6PortInput { Type = P6.PortJoystick };
		for (int i = 0; i < Controllers.JoystickNames.Length; ++i)
			if (controller.IsPressed(Controllers.Joystick(port, Controllers.JoystickNames[i]))) packet.Buttons |= Controllers.JoystickBits[i];
		return packet;
	}

	// The mouse of BizHawk is a position; the machine takes movement. The first frame only learns where the pointer is. At most 120 counts per axis and
	// unit reach the guest (docs/02-contracts.md): what does not fit is not lost, the reference moves only by what was sent and the rest follows.
	static int Clamp(int value, int low, int high) => value < low ? low : value > high ? high : value;

	P6PortInput MousePacket(IController controller)
	{
		var packet = new P6PortInput { Type = P6.PortMouse };
		if (controller.IsPressed($"P1 {Controllers.MouseLeft}")) packet.Buttons |= P6.Button1;
		if (controller.IsPressed($"P1 {Controllers.MouseRight}")) packet.Buttons |= P6.Button2;
		if (controller.IsPressed($"P1 {Controllers.MouseMiddle}")) packet.Buttons |= P6.Button3;
		int x = controller.AxisValue($"P1 {Controllers.MouseX}"), y = controller.AxisValue($"P1 {Controllers.MouseY}");
		if (!_mouseKnown)
		{
			_mouseKnown = true;
			_mouseReferenceX = x;
			_mouseReferenceY = y;
		}
		int dx = Clamp(x - _mouseReferenceX, -120, 120), dy = Clamp(y - _mouseReferenceY, -120, 120);
		_mouseReferenceX += dx;
		_mouseReferenceY += dy;
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
			var active = frame.Active;
			if (active.Width > MaxWidth || active.Height > MaxHeight || active.X + active.Width > frame.VideoWidth || active.Y + active.Height > frame.VideoHeight
				|| (ulong)(active.Y + active.Height) * frame.VideoStrideBytes > frame.VideoBytes)
				throw new InvalidOperationException($"the guest delivered a picture of {active.Width}x{active.Height} that does not fit the profile's buffers");
			for (uint row = 0; row < active.Height; ++row)
			{
				var source = new ReadOnlySpan<int>(frame.Video + (active.Y + row) * frame.VideoStrideBytes + active.X * 4, (int)active.Width);
				var target = _videoBuffer.AsSpan((int)(row * active.Width), (int)active.Width);
				for (int i = 0; i < target.Length; ++i) target[i] = source[i] | unchecked((int)0xFF000000);     // XRGB8888 to BizHawk's ARGB
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

	// ISettable: no presentation settings yet; the sync settings are the ones the machine was built with
	public object GetSettings() => null!;
	public Puae6SyncSettings GetSyncSettings() => _syncSettings.Clone();
	public PutSettingsDirtyBits PutSettings(object settings) => PutSettingsDirtyBits.None;
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
		public int Frame, LagCount, BufferWidth, BufferHeight, VsyncNumerator, VsyncDenominator, AspectNum, AspectDen, MouseX, MouseY, Disk0, Disk1, CurrentDrive, CurrentSlot;
		public bool IsLagFrame, DriveLightOn, MouseKnown, CapsLatched, CapsHeld, EjectHeld, InsertHeld, SlotHeld, DriveHeld;
		public long CycleCount;
		public ulong MediaSequence;
	}

	WrapperState Capture() => new()
	{
		Frame = Frame, LagCount = LagCount, IsLagFrame = IsLagFrame, CycleCount = CycleCount, BufferWidth = BufferWidth, BufferHeight = BufferHeight,
		VsyncNumerator = VsyncNumerator, VsyncDenominator = VsyncDenominator, AspectNum = _aspectNum, AspectDen = _aspectDen, DriveLightOn = DriveLightOn,
		MouseKnown = _mouseKnown, MouseX = _mouseReferenceX, MouseY = _mouseReferenceY, CapsLatched = _capsLatched, CapsHeld = _capsHeld,
		Disk0 = _driveDisk[0], Disk1 = _driveDisk[1], CurrentDrive = _currentDrive, CurrentSlot = _currentSlot,
		EjectHeld = _ejectHeld, InsertHeld = _insertHeld, SlotHeld = _slotHeld, DriveHeld = _driveHeld, MediaSequence = _mediaSequence,
	};

	static void Write(BinaryWriter w, WrapperState s)
	{
		w.Write(s.Frame); w.Write(s.LagCount); w.Write(s.IsLagFrame); w.Write(s.CycleCount);
		w.Write(s.BufferWidth); w.Write(s.BufferHeight); w.Write(s.VsyncNumerator); w.Write(s.VsyncDenominator); w.Write(s.AspectNum); w.Write(s.AspectDen); w.Write(s.DriveLightOn);
		w.Write(s.MouseKnown); w.Write(s.MouseX); w.Write(s.MouseY); w.Write(s.CapsLatched); w.Write(s.CapsHeld);
		w.Write(s.Disk0); w.Write(s.Disk1); w.Write(s.CurrentDrive); w.Write(s.CurrentSlot);
		w.Write(s.EjectHeld); w.Write(s.InsertHeld); w.Write(s.SlotHeld); w.Write(s.DriveHeld); w.Write(s.MediaSequence);
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
				CapsLatched = r.ReadBoolean(), CapsHeld = r.ReadBoolean(), Disk0 = r.ReadInt32(), Disk1 = r.ReadInt32(), CurrentDrive = r.ReadInt32(), CurrentSlot = r.ReadInt32(),
				EjectHeld = r.ReadBoolean(), InsertHeld = r.ReadBoolean(), SlotHeld = r.ReadBoolean(), DriveHeld = r.ReadBoolean(), MediaSequence = r.ReadUInt64(),
			};
		}
		catch (EndOfStreamException)
		{
			throw new StateRejectedException(StateRejection.BadFields, "the payload ends inside the wrapper's fields");
		}
		bool ok = s.Frame >= 0 && s.LagCount >= 0 && s.BufferWidth is >= 0 and <= MaxWidth && s.BufferHeight is >= 0 and <= MaxHeight && s.VsyncNumerator > 0 && s.VsyncDenominator > 0
			&& s.AspectNum > 0 && s.AspectDen > 0 && s.Disk0 >= -1 && s.Disk0 < _media.Count && s.Disk1 >= -1 && s.Disk1 < _media.Count
			&& s.CurrentDrive >= 0 && s.CurrentDrive < _syncSettings.FloppyDrives && s.CurrentSlot >= 0 && s.CurrentSlot < Math.Max(1, _media.Count);
		if (!ok) throw new StateRejectedException(StateRejection.BadFields, "the state has a field that this machine cannot have");
		return s;
	}

	void Apply(WrapperState s)
	{
		Frame = s.Frame; LagCount = s.LagCount; IsLagFrame = s.IsLagFrame; CycleCount = s.CycleCount;
		BufferWidth = s.BufferWidth; BufferHeight = s.BufferHeight; VsyncNumerator = s.VsyncNumerator; VsyncDenominator = s.VsyncDenominator;
		_aspectNum = s.AspectNum; _aspectDen = s.AspectDen; DriveLightOn = s.DriveLightOn;
		_mouseKnown = s.MouseKnown; _mouseReferenceX = s.MouseX; _mouseReferenceY = s.MouseY; _capsLatched = s.CapsLatched; _capsHeld = s.CapsHeld;
		_driveDisk[0] = s.Disk0; _driveDisk[1] = s.Disk1; _currentDrive = s.CurrentDrive; _currentSlot = s.CurrentSlot;
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
		var payload = StateEnvelope.Read(reader, _identity);          // refuses, before anything is touched, a state that is not this machine's, whole and unaltered
		using var stream = new MemoryStream(payload, false);
		using var inner = new BinaryReader(stream);
		var fields = Parse(inner);                                    // and one whose fields cannot be
		using var _ = _guest.Host.EnterExit();
		_started = true;                                              // the machine is not the one a SaveRam could still change
		try
		{
			_guest.Host.LoadStateBinary(inner);
		}
		catch
		{
			_faulted = true;                                          // the sandbox may be half restored: no unit and no save until a state loads whole
			throw;
		}
		Apply(fields);
		_faulted = false;
	}

	public void Dispose()
	{
		if (_disposed) return;
		_disposed = true;
		_guest.Dispose();
	}
}

public static class Puae6Names
{
	public const string Core = "PUAE6";
}
