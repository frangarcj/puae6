#nullable enable

using System.IO;
using System.Linq;
using System.Collections.Generic;
using System.Runtime.InteropServices;

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
}

/// <summary>
/// PUAE6 as a BizHawk core: WinUAE 6 running as a Waterbox guest (puae6.wbx), advanced exactly one video unit per FrameAdvance through the same
/// p6_run_frame the headless and libretro adapters use. It is independent of the UAE core of BizHawk (its own core ID, its own guest, none of its
/// state) and speaks only the ABI of adapters/waterbox/p6w.h.
/// </summary>
[PortedCore(name: Puae6Names.Core, author: "WinUAE team and the PUAE6 project", portedVersion: "WinUAE 6.0.3", portedUrl: "https://github.com/tonioni/WinUAE", isReleased: false)]
public sealed unsafe partial class Puae6Core : IEmulator, IVideoProvider, ISoundProvider, IStatable, IInputPollable, IRegionable, IDriveLight, ISettable<object, Puae6SyncSettings>
{
	// The profile's buffers (p6_caps of ABI v1): the delivered picture is at most 760x576 and one unit never delivers more than 8192 audio frames.
	public const int MaxWidth = 760, MaxHeight = 576, MaxAudioFrames = 8192;

	// The heaps are part of what a snapshot is compatible with; they are the budgets of manifests/waterbox-memory.json (a test compares them).
	public static WaterboxOptions Heaps() => new()
	{
		SbrkHeapSizeKB = 4096, SealedHeapSizeKB = 16384, InvisibleHeapSizeKB = 16, PlainHeapSizeKB = 16, MmapHeapSizeKB = 16384,
	};


	const uint StateFormat = 2;

	readonly Puae6Guest _guest;
	readonly Puae6SyncSettings _syncSettings;
	readonly BasicServiceProvider _serviceProvider;
	readonly int[] _videoBuffer = new int[MaxWidth * MaxHeight];
	readonly short[] _soundBuffer = new short[2 * MaxAudioFrames];
	readonly MemoryDomainList _domains;
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
	readonly int[] _driveDisk = [ -1, -1 ];
	int _currentDrive, _currentSlot;
	bool _ejectHeld, _insertHeld, _slotHeld, _driveHeld;
	ulong _mediaSequence;

	public Puae6Core(CoreComm comm, Puae6Parameters parameters)
	{
		CoreComm = comm;
		_syncSettings = parameters.SyncSettings.Clone();
		if (parameters.Firmware.Length == 0) throw new ArgumentException("the PUAE6 core needs a Kickstart image", nameof(parameters));
		if (_syncSettings.FloppyDrives is < 1 or > Drives) throw new ArgumentException($"the A500 has {Drives} floppy drives at most", nameof(parameters));
		if (parameters.Media.Count > P6.MaxMedia) throw new ArgumentException($"at most {P6.MaxMedia} disks are supported", nameof(parameters));
		foreach (var disk in parameters.Media)
			if (disk.Length != P6.AdfBytes) throw new ArgumentException($"an ADF has {P6.AdfBytes} bytes, this one {disk.Length}", nameof(parameters));
		_media = parameters.Media.Select(disk => (byte[])disk.Clone()).ToList();
		_mediaNames = parameters.MediaNames;
		ControllerDefinition = Controllers.Define(_syncSettings, _media.Count);
		BufferWidth = MaxWidth;
		BufferHeight = _syncSettings.Region == Puae6Region.NTSC ? 486 : MaxHeight;
		(VsyncNumerator, VsyncDenominator) = _syncSettings.Region == Puae6Region.NTSC ? (60, 1) : (50, 1);
		_aspectNum = 1; _aspectDen = 1;

		var heaps = Heaps();
		_guest = parameters.PinnedGuestSha256 is { } pin
			? Puae6Guest.Open(parameters.GuestDirectory, parameters.GuestFileName, pin, heaps)
			: Puae6Guest.Open(parameters.GuestDirectory, parameters.GuestFileName, heaps);
		try
		{
			InitializeMachine(parameters.Firmware);
			MountBootDisks();
			_domains = CreateMemoryDomains();
			_guest.Host.Seal();
			_serviceProvider = new BasicServiceProvider(this);
			_serviceProvider.Register<IMemoryDomains>(_domains);
		}
		catch
		{
			_guest.Dispose();
			throw;
		}
	}

	CoreComm CoreComm { get; }

	void InitializeMachine(byte[] firmware)
	{
		var init = new P6wInit { AbiVersion = P6.AbiVersion, StructSize = (uint)sizeof(P6wInit) };
		var pins = new List<GCHandle>();
		init.MediaCount = (uint)_media.Count;
		for (int i = 0; i < _media.Count; ++i)
		{
			pins.Add(GCHandle.Alloc(_media[i], GCHandleType.Pinned));
			var entry = init.Media.At(i);
			entry->Type = P6.MediaAdf;
			byte[] id;
			using (var sha = System.Security.Cryptography.SHA256.Create()) id = sha.ComputeHash(_media[i]);
			for (int b = 0; b < P6.ContentIdSize; ++b) entry->ContentId[b] = id[b];
			entry->Data = (byte*)pins[i].AddrOfPinnedObject();
			entry->Bytes = (ulong)_media[i].Length;
		}
		init.Config.AbiVersion = P6.AbiVersion;
		init.Config.StructSize = (uint)sizeof(P6Config);
		init.Config.Machine = P6.MachineA500;
		init.Config.VideoStandard = _syncSettings.Region == Puae6Region.NTSC ? P6.VideoNtsc : P6.VideoPal;
		init.Config.AudioRateHz = 48000;
		init.Config.RandomSeed = _syncSettings.RandomSeed;
		init.Config.RtcEpochSeconds = _syncSettings.RtcEpochSeconds;
		fixed (byte* rom = firmware)
		{
			init.Firmware = rom;
			init.FirmwareBytes = (ulong)firmware.Length;
			using var _ = _guest.Host.EnterExit();
			int result = _guest.Lib.p6w_init_machine((IntPtr)(&init));
			foreach (var pin in pins) pin.Free();
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
	/// <summary>Scheduler ticks of the machine, as p6_frame.master_ticks reports them.</summary>
	public long CycleCount { get; private set; }

	public void ResetCounters()
	{
		Frame = 0;
		LagCount = 0;
		IsLagFrame = false;
		CycleCount = 0;
	}

	public bool FrameAdvance(IController controller, bool render, bool renderSound = true)
	{
		var input = BuildInput(controller, out bool power);
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

	// ----- IStatable: the snapshot of the sandbox and what the wrapper holds that the sandbox does not (T21 inventories and proves it) -------

	public bool AvoidRewind => false;

	public void SaveStateBinary(BinaryWriter writer)
	{
		using var _ = _guest.Host.EnterExit();
		_guest.Host.SaveStateBinary(writer);
		writer.Write(StateFormat);
		writer.Write(Frame); writer.Write(LagCount); writer.Write(IsLagFrame); writer.Write(CycleCount);
		writer.Write(BufferWidth); writer.Write(BufferHeight); writer.Write(VsyncNumerator); writer.Write(VsyncDenominator);
		writer.Write(_aspectNum); writer.Write(_aspectDen); writer.Write(DriveLightOn);
		writer.Write(_mouseKnown); writer.Write(_mouseReferenceX); writer.Write(_mouseReferenceY);
		writer.Write(_capsLatched); writer.Write(_capsHeld);
		writer.Write(_driveDisk[0]); writer.Write(_driveDisk[1]); writer.Write(_currentDrive); writer.Write(_currentSlot);
		writer.Write(_ejectHeld); writer.Write(_insertHeld); writer.Write(_slotHeld); writer.Write(_driveHeld); writer.Write(_mediaSequence);
	}

	public void LoadStateBinary(BinaryReader reader)
	{
		using var _ = _guest.Host.EnterExit();
		_guest.Host.LoadStateBinary(reader);
		if (reader.ReadUInt32() != StateFormat) throw new InvalidOperationException("the state was written by another version of the wrapper");
		Frame = reader.ReadInt32(); LagCount = reader.ReadInt32(); IsLagFrame = reader.ReadBoolean(); CycleCount = reader.ReadInt64();
		BufferWidth = reader.ReadInt32(); BufferHeight = reader.ReadInt32(); VsyncNumerator = reader.ReadInt32(); VsyncDenominator = reader.ReadInt32();
		_aspectNum = reader.ReadInt32(); _aspectDen = reader.ReadInt32(); DriveLightOn = reader.ReadBoolean();
		_mouseKnown = reader.ReadBoolean(); _mouseReferenceX = reader.ReadInt32(); _mouseReferenceY = reader.ReadInt32();
		_capsLatched = reader.ReadBoolean(); _capsHeld = reader.ReadBoolean();
		_driveDisk[0] = reader.ReadInt32(); _driveDisk[1] = reader.ReadInt32(); _currentDrive = reader.ReadInt32(); _currentSlot = reader.ReadInt32();
		_ejectHeld = reader.ReadBoolean(); _insertHeld = reader.ReadBoolean(); _slotHeld = reader.ReadBoolean(); _driveHeld = reader.ReadBoolean();
		_mediaSequence = reader.ReadUInt64();
		_soundFrames = 0;                          // pending output of the previous unit belongs to the timeline that was left
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
