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
	public byte[] Firmware { get; set; } = [];
	public Puae6SyncSettings SyncSettings { get; set; } = new();
}

/// <summary>
/// PUAE6 as a BizHawk core: WinUAE 6 running as a Waterbox guest (puae6.wbx), advanced exactly one video unit per FrameAdvance through the same
/// p6_run_frame the headless and libretro adapters use. It is independent of the UAE core of BizHawk (its own core ID, its own guest, none of its
/// state) and speaks only the ABI of adapters/waterbox/p6w.h.
/// </summary>
[PortedCore(name: Puae6Names.Core, author: "WinUAE team and the PUAE6 project", portedVersion: "WinUAE 6.0.3", portedUrl: "https://github.com/tonioni/WinUAE", isReleased: false)]
public sealed unsafe class Puae6Core : IEmulator, IVideoProvider, ISoundProvider, IStatable, IInputPollable, IRegionable, IDriveLight, ISettable<object, Puae6SyncSettings>
{
	// The profile's buffers (p6_caps of ABI v1): the delivered picture is at most 760x576 and one unit never delivers more than 8192 audio frames.
	public const int MaxWidth = 760, MaxHeight = 576, MaxAudioFrames = 8192;

	// The heaps are part of what a snapshot is compatible with; they are the budgets of manifests/waterbox-memory.json (a test compares them).
	public static WaterboxOptions Heaps() => new()
	{
		SbrkHeapSizeKB = 4096, SealedHeapSizeKB = 16384, InvisibleHeapSizeKB = 16, PlainHeapSizeKB = 16, MmapHeapSizeKB = 16384,
	};

	const uint StateFormat = 1;

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

	public Puae6Core(CoreComm comm, Puae6Parameters parameters)
	{
		CoreComm = comm;
		_syncSettings = parameters.SyncSettings.Clone();
		if (parameters.Firmware.Length == 0) throw new ArgumentException("the PUAE6 core needs a Kickstart image", nameof(parameters));
		ControllerDefinition = Controllers.Define(_syncSettings);
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
			if (result != P6.Ok)
				throw new InvalidOperationException($"the PUAE6 guest refused the machine ({P6.ResultName(result)}): {Marshal.PtrToStringUTF8((IntPtr)init.Error)}");
		}
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
			// Order inside a boundary (docs/02-contracts.md): reset, then input, then the unit; the reset is queued here and runs at its head.
			if (power)
			{
				int reset = _guest.Lib.p6w_reset();
				if (reset != P6.Ok) throw new InvalidOperationException($"the machine refused a reset ({P6.ResultName(reset)})");
			}
			int result = _guest.Lib.p6w_frame_advance((IntPtr)(&input), flags, (IntPtr)(&frame));
			if (result != P6.Ok)
				throw new InvalidOperationException($"unit {Frame + 1}: {P6.ResultName(result)}: {Marshal.PtrToStringUTF8((IntPtr)frame.Error)}");
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
		int dx = Math.Clamp(x - _mouseReferenceX, -120, 120), dy = Math.Clamp(y - _mouseReferenceY, -120, 120);
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
	public object? GetSettings() => null;
	public Puae6SyncSettings GetSyncSettings() => _syncSettings.Clone();
	public PutSettingsDirtyBits PutSettings(object? settings) => PutSettingsDirtyBits.None;
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
