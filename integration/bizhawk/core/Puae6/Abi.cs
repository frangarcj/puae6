#nullable enable

using System.Runtime.InteropServices;

namespace Puae6;

// C# mirror of bridge/include/p6.h and adapters/waterbox/p6w.h (ABI v1), field by field and with the natural alignment the C compiler gives
// them: no Pack, no padding written by hand. The offsets and sizes they have are checked against what the guest was compiled with
// (p6w_layout), and against what a C compiler makes of the headers (tests/ffi), in both directions. Strings are NUL-terminated UTF-8.

public static class P6
{
	public const uint AbiVersion = 1;
	public const int ErrorTextCapacity = 256;
	public const int ContentIdSize = 32;
	public const int MaxMedia = 16;

	// p6_result
	public const int Ok = 0, BadArgument = 1, BadState = 2, Busy = 3, Unsupported = 4, NeedFirmware = 5, WrongMedia = 6, Capacity = 7, Io = 8,
		CorruptState = 9, Incompatible = 10, Internal = 11;

	public const uint MachineA500 = 1, MachineA600 = 2, MachineA1200 = 3;
	public const uint VideoPal = 1, VideoNtsc = 2;
	public const uint MediaAdf = 1;
	public const uint PortNone = 0, PortJoystick = 1, PortMouse = 2;
	public const uint Button1 = 1u << 0, Button2 = 1u << 1, Button3 = 1u << 2, ButtonUp = 1u << 3, ButtonDown = 1u << 4, ButtonLeft = 1u << 5, ButtonRight = 1u << 6;
	public const uint PixelXrgb8888 = 1;
	public const uint FieldProgressive = 0, FieldEven = 1, FieldOdd = 2;
	public const uint FrameLag = 1u << 0, FrameNoVideo = 1u << 1, FrameDriveLight = 1u << 2;
	public const uint RenderVideo = 1u << 0, RenderAudio = 1u << 1;
	public const uint RegionChipRam = 1, RegionSlowRam = 2, RegionRom = 3;
	public const uint MediaEventMount = 1, MediaEventEject = 2, MediaIndexEject = uint.MaxValue;
	public const int AdfBytes = 901120;                          // the only medium of ABI v1: an 880 KiB ADF

	// p6w_memory_area.flags: the values of LibWaterboxCore.MemoryDomainFlags.
	public const long AreaWritable = 1, AreaPrimary = 8, AreaBigEndian = 16, AreaWordSize2 = 64, AreaFunctionHook = 1024;

	public static string ResultName(int result) => result switch
	{
		Ok => "OK", BadArgument => "BAD_ARGUMENT", BadState => "BAD_STATE", Busy => "BUSY", Unsupported => "UNSUPPORTED", NeedFirmware => "NEED_FIRMWARE",
		WrongMedia => "WRONG_MEDIA", Capacity => "CAPACITY", Io => "IO_ERROR", CorruptState => "CORRUPT_STATE", Incompatible => "INCOMPATIBLE_STATE",
		Internal => "INTERNAL", _ => $"p6_result {result}",
	};
}

[StructLayout(LayoutKind.Sequential)]
public unsafe struct P6Config
{
	public uint AbiVersion, StructSize;
	public uint Machine, VideoStandard, AudioRateHz, Flags;     // flags must be zero in ABI v1
	public ulong RandomSeed;
	public long RtcEpochSeconds;                                 // UTC; never read from the host clock
	public fixed ulong Reserved[4];
}

[StructLayout(LayoutKind.Sequential)]
public struct P6PortInput
{
	public uint Type, Buttons;
	public short Dx, Dy;
	public uint Reserved;
}

/// <summary>One unit's input: the host state, not the events (docs/02-contracts.md, "Entrada").</summary>
[StructLayout(LayoutKind.Sequential)]
public unsafe struct P6Input
{
	public uint AbiVersion, StructSize;
	public fixed uint Keys[4];                                   // key n is Keys[n / 32] bit (n % 32)
	public P6PortInput Port0, Port1;
	public fixed uint Reserved[4];
}

[StructLayout(LayoutKind.Sequential)]
public struct P6Rect
{
	public uint X, Y, Width, Height;
}

/// <summary>What p6_run_frame filled in. Video and Audio point into guest memory and live until the next call into the guest.</summary>
[StructLayout(LayoutKind.Sequential)]
public unsafe struct P6Frame
{
	public uint AbiVersion, StructSize;
	public byte* Video;
	public ulong VideoBytes;
	public uint VideoFormat, VideoWidth, VideoHeight, VideoStrideBytes;
	public P6Rect Active;
	public uint AspectNum, AspectDen;
	public short* Audio;
	public ulong AudioFrames;                                    // stereo frames, not int16 values
	public ulong MasterTicks, UnitIndex;
	public uint CadenceNum, CadenceDen;
	public uint FieldParity, Flags;
	public fixed ulong Reserved[2];
}

[StructLayout(LayoutKind.Sequential)]
public unsafe struct P6wMedia
{
	public uint Type, Flags;
	public fixed byte ContentId[P6.ContentIdSize];
	public byte* Data;                                           // host memory, copied into the sealed heap by the guest
	public ulong Bytes;
}

/// <summary>The sixteen p6w_media of p6w_init, as a fixed buffer (InlineArray does not exist for the net48 BizHawk builds on).</summary>
[StructLayout(LayoutKind.Sequential)]
public unsafe struct P6wMediaList
{
	public const int Bytes = P6.MaxMedia * 56;
	public fixed byte Data[Bytes];

	public P6wMedia* At(int index)
	{
		fixed (byte* p = Data) return (P6wMedia*)p + index;
	}
}

/// <summary>The argument of p6w_init_machine. Result and Error are outputs; the rest is read and left alone.</summary>
[StructLayout(LayoutKind.Sequential)]
public unsafe struct P6wInit
{
	public uint AbiVersion, StructSize;
	public P6Config Config;
	public byte* Firmware;                                       // host memory, copied into the sealed heap by the guest
	public ulong FirmwareBytes;
	public uint MediaCount, Reserved0;
	public P6wMediaList Media;
	public int Result;
	public uint Reserved1;
	public fixed byte Error[P6.ErrorTextCapacity];
}

/// <summary>The argument of p6w_frame_advance.</summary>
[StructLayout(LayoutKind.Sequential)]
public unsafe struct P6wFrame
{
	public uint AbiVersion, StructSize;
	public P6Frame Frame;
	public int Result;
	public uint Reserved;
	public fixed byte Error[P6.ErrorTextCapacity];
}

/// <summary>A media event for the next boundary (p6_media_event_enqueue).</summary>
[StructLayout(LayoutKind.Sequential)]
public unsafe struct P6MediaEvent
{
	public uint AbiVersion, StructSize;
	public uint Type, Drive, MediaIndex, Reserved0;
	public ulong Sequence;                                       // strictly increasing for the whole session
	public fixed ulong Reserved[2];
}

/// <summary>The argument of p6w_media_event_enqueue. Result and Error are outputs.</summary>
[StructLayout(LayoutKind.Sequential)]
public unsafe struct P6wMediaEvent
{
	public uint AbiVersion, StructSize;
	public P6MediaEvent Event;
	public int Result;
	public uint Reserved;
	public fixed byte Error[P6.ErrorTextCapacity];
}

/// <summary>The layout of LibWaterboxCore.MemoryArea, which is what p6w_memory_areas writes.</summary>
[StructLayout(LayoutKind.Sequential)]
public struct P6wMemoryArea
{
	public IntPtr Data;                                          // a guest function: void access(void *buffer, long address, long count, bool write)
	public IntPtr Name;                                          // NUL-terminated UTF-8 in guest memory
	public long Size;
	public long Flags;
}

/// <summary>The sizes and offsets this wrapper was written for, in the order of the guest's p6w_layout (P6W_LAYOUT_VALUES values).</summary>
public static unsafe class AbiLayout
{
	public const int Values = 22;

	public static ulong[] Compute()
	{
		P6wInit init = default;
		P6wFrame frame = default;
		P6wMemoryArea area = default;
		P6wMediaEvent call = default;
		ulong Offset<T, F>(T* owner, F* field) where T : unmanaged where F : unmanaged => (ulong)((byte*)field - (byte*)owner);
		return [
			(ulong)sizeof(P6wInit), Offset(&init, &init.Result), Offset(&init, init.Error), Offset(&init, (byte*)&init.Media), (ulong)sizeof(P6wMedia),
			(ulong)sizeof(P6wFrame), Offset(&frame, &frame.Frame), Offset(&frame, &frame.Result), Offset(&frame, frame.Error),
			(ulong)sizeof(P6Frame), (ulong)sizeof(P6Input), (ulong)sizeof(P6Config), Offset(&frame.Frame, (byte*)&frame.Frame.Audio), Offset(&frame.Frame, (byte*)&frame.Frame.MasterTicks),
			(ulong)sizeof(P6wMemoryArea), Offset(&area, (byte*)&area.Name), Offset(&area, (byte*)&area.Size), Offset(&area, (byte*)&area.Flags),
			(ulong)sizeof(P6wMediaEvent), Offset(&call, (byte*)&call.Event), Offset(&call, &call.Result), Offset(&call, call.Error),
		];
	}

	/// <summary>Names of the values, for messages.</summary>
	public static readonly string[] Names = [
		"sizeof(p6w_init)", "offsetof(p6w_init, result)", "offsetof(p6w_init, error)", "offsetof(p6w_init, media)", "sizeof(p6w_media)",
		"sizeof(p6w_frame)", "offsetof(p6w_frame, frame)", "offsetof(p6w_frame, result)", "offsetof(p6w_frame, error)",
		"sizeof(p6_frame)", "sizeof(p6_input)", "sizeof(p6_config)", "offsetof(p6_frame, audio)", "offsetof(p6_frame, master_ticks)",
		"sizeof(p6w_memory_area)", "offsetof(p6w_memory_area, name)", "offsetof(p6w_memory_area, size)", "offsetof(p6w_memory_area, flags)",
		"sizeof(p6w_media_event)", "offsetof(p6w_media_event, event)", "offsetof(p6w_media_event, result)", "offsetof(p6w_media_event, error)",
	];
}
