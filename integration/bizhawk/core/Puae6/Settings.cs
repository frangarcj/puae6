#nullable enable

using BizHawk.Common;

namespace Puae6;

public enum Puae6Region { PAL, NTSC }
public enum Puae6WhdLoadMode { Auto, Disabled }

/// <summary>
/// The Amiga the core emulates: the A500 (OCS, 512 KiB Chip + 512 KiB Slow RAM, Kickstart 1.3) the A600 (ECS, 1 MiB Chip RAM, Kickstart 2.05, no IDE or PCMCIA) or the A1200 (68EC020, AGA, 2 MiB Chip RAM, Kickstart 3.1, no Gayle).
/// The names are the ones of the schema (config/schema/options.json). T50 adds the A500 OG (OCS, 512 KiB Chip, no Slow RAM, Kickstart 1.3), the A500+ (ECS, 1 MiB Chip, Kickstart 2.04), the A1000 (the 8361 and 8362 chips,
/// 512 KiB Chip, a Kickstart 1.x image as a ROM) and the A2000 OG (OCS, 512 KiB Chip + 512 KiB Slow, the clock and the mains TOD of the A2000) and the A2000 (ECS Agnus, 1 MiB Chip, Kickstart 3.1). The firmware each asks
/// of BizHawk's database is in Puae6Core.EmuHawk.cs (FirmwareOf): the database has the entries "A500" (Kickstart 1.3) and "A600" (2.05, 2.04 and the 3.1 r40.063 of the A600 and the A2000).
/// </summary>
public enum Puae6Machine { A500, A600, A1200, A500OG, A500PLUS, A1000, A2000OG, A2000, A4030 }

/// <summary>Chip RAM (restart): the machine's own (A500 512 KiB, A600 1 MiB, A1200 2 MiB) or a size; the A500 (OCS) has 512 KiB or 1 MiB, the other machines also 2 MiB.</summary>
public enum Puae6Chip { Auto, KB512, MB1, MB2 }

/// <summary>Slow RAM at $C00000 (restart): the machine's own (A500 512 KiB, A600 and A1200 none), none, or a size; 1.75 MiB is not available on the A1200 (AGA maps 1.5 MiB at most).</summary>
public enum Puae6Slow { Auto, None, KB512, MB1, MB1_5, MB1_75 }

/// <summary>Zorro II Fast RAM (restart): none, or one autoconfig board that the Kickstart configures, as PUAE5's A600 and A1200 have (8 MiB).</summary>
public enum Puae6Fast { None, MB1, MB2, MB4, MB8, Auto }

/// <summary>The floating point coprocessor (restart): none, or a 68881 or 68882, which the A1200 and A4030 can have (the 68000 has no coprocessor interface). SoftFloat computes it, so the results are the same on every host.</summary>
public enum Puae6Fpu { None, M68881, M68882 }

/// <summary>The CPU (restart): the machine's own (a 68000, the A1200's 68EC020 or the A4030's 68030), a 68010 in the socket of a machine with a 68000, or the A1200's 68020. Another combination is refused when the core starts, with the reason.</summary>
public enum Puae6Cpu { Auto, M68000, M68010, M68020, M68030 }

/// <summary>Guest collision detection (restart): Auto and Full retain every hardware collision; reduced levels can break games.</summary>
public enum Puae6CollisionLevel { Auto, None, Sprites, Playfields, Full }
public enum Puae6Z3Memory { None, MB16 }

/// <summary>CPU accuracy (restart): cycle exact by default, compatible prefetch, or normal instruction timing; always emulated time.</summary>
public enum Puae6CpuCompatibility { Exact, Compatible, Normal }

/// <summary>The clock of the cycle-exact CPU (restart), PUAE5's multipliers of the 3.55 MHz colour clock: Auto is the machine's own (7.09 MHz, 14.19 MHz on the A1200), X1 is 3.55 MHz and X16 56.75 MHz. It counts emulated time, never the host's.</summary>
public enum Puae6CpuClock { Auto, X1, X2, X4, X8, X10, X12, X16 }

/// <summary>The controller the hard files are on (restart): the UAE controller (uaehf.device, the default, any machine) or the IDE port of Gayle, which only the A600 and the A1200 have (another machine refuses it when the core starts, with the reason).
/// Gayle is switched on only while a hard file is behind it: Ide with no hard file is the machine without it.</summary>
public enum Puae6HdfController { Uae, Ide }

/// <summary>Paula's stereo separation (restart): how much of each side's channels stays on that side, 0 % (mono) to 100 %. 70 % is the profile's own setting.</summary>
public enum Puae6SoundSeparation { P0, P10, P20, P30, P40, P50, P60, P70, P80, P90, P100 }

/// <summary>How Paula's samples are resampled to the output rate (restart). Anti is the profile's own setting; sinc is not offered (its queue is not part of the machine's state).</summary>
public enum Puae6SoundInterpolation { None, Anti, RH, Crux }

/// <summary>Paula's low-pass output filter (restart): Emulated follows the power LED as the hardware does (the profile's own setting), Off never filters, On always filters.</summary>
public enum Puae6SoundFilter { Emulated, Off, On }

/// <summary>The model of the output filter (restart): Auto is the A500 model on the A500 and the A600 and the A1200 model on the A1200; the others force one. It has no effect while the filter is Off.</summary>
public enum Puae6SoundFilterType { Auto, A500, A1200 }

/// <summary>The speed of the floppy drives (restart): the real drive (P100, the profile's own setting), twice, four or eight times as fast, or Turbo, which removes the rotation and transfers a whole DSKLEN at once. It is counted in emulated colour clocks, never in wall clock time.</summary>
public enum Puae6FloppySpeed { P100, P200, P400, P800, Turbo }
public enum Puae6FloppyDrives { Auto, One, Two, Three, Four }

/// <summary>The resolution of the picture (restart): the width the renderer draws, 378, 756 (Hires, the profile's own setting) or 1512 columns. It is the engine's own resolution, not a scaling.</summary>
public enum Puae6VideoResolution { Lores, Hires, SuperHires }

/// <summary>The line mode of the picture (restart): one row per line (Single, 287 rows on PAL; an interlaced program shows each field on its own) or two (Double, the profile's own setting, 574 rows; the fields are woven).</summary>
public enum Puae6VideoLineMode { Single, Double }

/// <summary>What is plugged into the first controller port of the Amiga (the one the mouse normally uses).</summary>
public enum Puae6Port1 { Mouse, Joystick, None, Cd32, Lightgun, Lightpen }

/// <summary>What is plugged into the second controller port.</summary>
public enum Puae6Port2 { Joystick, None, Cd32, Mouse, Lightgun, Lightpen }

/// <summary>One extra joystick on the real parallel adapter (D-pad and one fire button).</summary>
public enum Puae6ParallelPort { None, Joystick }

/// <summary>Live presentation choices, excluded from sync settings, movies and save-state identity.</summary>
public enum Puae6DriveSoundVolume { P0 = 0, P10 = 10, P25 = 25, P50 = 50, P75 = 75, P100 = 100 }

public sealed class Puae6Settings
{
	Puae6DriveSoundVolume _driveSoundVolume;
	/// <summary>Mechanical drive volume, 0 (silent) to 100 percent. The phase advances even when silent.</summary>
	public Puae6DriveSoundVolume DriveSoundVolume
	{
		get => _driveSoundVolume;
		set => _driveSoundVolume = Enum.IsDefined(typeof(Puae6DriveSoundVolume), value) ? value : throw new ArgumentOutOfRangeException(nameof(value), "Unknown drive sound volume");
	}
	public Puae6Settings Clone() => (Puae6Settings)MemberwiseClone();
}

/// <summary>
/// The settings that change what the machine does, so they are part of a movie and of a save state's identity (docs/02-contracts.md, "Configuracion"):
/// the same normalized values feed the guest, and a change needs a reboot of the core. No hardware option is exposed that the profile cannot run.
/// </summary>
public sealed class Puae6SyncSettings
{
	/// <summary>Restart option: Auto builds the three-volume WHDLoad bundle from one installed pack; Disabled keeps the generic volume loader.</summary>
	public Puae6WhdLoadMode WhdLoad { get; set; } = Puae6WhdLoadMode.Auto;
	public bool WhdLoadButtonWait { get; set; } = false;

	/// <summary>Which Amiga it is. A state or a movie of one machine is refused by the other; the Kickstart asked of BizHawk's firmware database follows it (FirmwareID Amiga "A500", "A600" or "A1200", the name of the member).</summary>
	public Puae6Machine Machine { get; set; } = Puae6Machine.A500;

	/// <summary>Chip RAM of the machine. A size the machine cannot have is refused when the core starts, with the reason.</summary>
	public Puae6Chip ChipMemory { get; set; } = Puae6Chip.Auto;

	/// <summary>Slow RAM of the machine.</summary>
	public Puae6Slow SlowMemory { get; set; } = Puae6Slow.Auto;

	/// <summary>Zorro II Fast RAM: Auto is the machine's own (8 MiB on A4030, none otherwise), None disables the board. The Waterbox heap of the core grows with it (<see cref="Puae6Core.Heaps(Puae6SyncSettings)"/>), measured in manifests/waterbox-memory.json.</summary>
	public Puae6Fast FastMemory { get; set; } = Puae6Fast.Auto;
	/// <summary>One physical Zorro III board, A4030 only (Normal CPU); requires restart.</summary>
	public Puae6Z3Memory Z3Memory { get; set; } = Puae6Z3Memory.None;

	/// <summary>The FPU of the machine (A1200 or A4030; another machine refuses it when the core starts). It enters the identity of states and movies when it is not <c>None</c>.</summary>
	public Puae6Fpu FpuModel { get; set; } = Puae6Fpu.None;

	/// <summary>The CPU of the machine. It changes the instruction set, the exception frames and the registers, so it enters the identity of states and movies when it is not <c>Auto</c>.</summary>
	public Puae6Cpu CpuModel { get; set; } = Puae6Cpu.Auto;

	/// <summary>A4030 requires Normal; its compatible/exact caches are not implemented. CPU accuracy changes hardware timing and is part of state/movie identity; changing it requires a reboot.</summary>
	public Puae6CpuCompatibility CpuCompatibility { get; set; } = Puae6CpuCompatibility.Exact;

	/// <summary>Restart collision level. Auto and Full preserve the profile; reduced levels change CLXDAT and state/movie identity.</summary>
	public Puae6CollisionLevel CollisionLevel { get; set; } = Puae6CollisionLevel.Auto;

	/// <summary>The clock of the CPU. It changes when everything the CPU does meets the chipset, so it enters the identity of states and movies when it is not <c>Auto</c>.</summary>
	public Puae6CpuClock CpuMultiplier { get; set; } = Puae6CpuClock.Auto;

	/// <summary>The controller of the hard files. It is part of the machine (the registers of Gayle and its drives against the UAE controller's board), so it enters the identity of states and movies when it is not <c>Uae</c>.</summary>
	public Puae6HdfController HdfController { get; set; } = Puae6HdfController.Uae;

	/// <summary>Paula's stereo separation. It changes every sample the machine hands over, so it is a sync setting: it enters the identity of states and movies when it is not the profile's 70 %.</summary>
	public Puae6SoundSeparation SoundSeparation { get; set; } = Puae6SoundSeparation.P70;

	/// <summary>The interpolation of Paula's output (its integrator is part of the machine's state). It enters the identity when it is not <c>Anti</c>.</summary>
	public Puae6SoundInterpolation SoundInterpolation { get; set; } = Puae6SoundInterpolation.Anti;

	/// <summary>Paula's output filter (its state is part of the machine's state). It enters the identity when it is not <c>Emulated</c>.</summary>
	public Puae6SoundFilter SoundFilter { get; set; } = Puae6SoundFilter.Emulated;

	/// <summary>The model of the output filter. It enters the identity when it is not <c>Auto</c>.</summary>
	public Puae6SoundFilterType SoundFilterType { get; set; } = Puae6SoundFilterType.Auto;

	/// <summary>The speed of the floppy drives. It changes when every disk transfer ends, so it is a sync setting: it enters the identity of states and movies when it is not <c>P100</c>.</summary>
	public Puae6FloppySpeed FloppySpeed { get; set; } = Puae6FloppySpeed.P100;

	/// <summary>The resolution of the picture. It is the engine's own and changes what is drawn and the size of the picture, so it is a sync setting: it enters the identity of states and movies when it is not <c>Hires</c>.</summary>
	public Puae6VideoResolution VideoResolution { get; set; } = Puae6VideoResolution.Hires;

	/// <summary>The line mode of the picture. Like the resolution it is part of the machine: it enters the identity when it is not <c>Double</c>.</summary>
	public Puae6VideoLineMode VideoLineMode { get; set; } = Puae6VideoLineMode.Double;

	/// <summary>The video standard of the machine. It is not detected from anything.</summary>
	public Puae6Region Region { get; set; } = Puae6Region.PAL;

	/// <summary>Seed of the random sources of the emulated hardware (the start-up delay of the boot, for one). Serialized with the movie.</summary>
	public ulong RandomSeed { get; set; } = 1;

	/// <summary>The real-time clock's epoch, UTC seconds: 2000-01-01 by default. The host clock is never read.</summary>
	public long RtcEpochSeconds { get; set; } = 946684800;

	public Puae6Port1 ControllerPort1 { get; set; } = Puae6Port1.Mouse;

	public Puae6Port2 ControllerPort2 { get; set; } = Puae6Port2.Joystick;

	public Puae6ParallelPort ControllerPort3 { get; set; } = Puae6ParallelPort.None;
	public Puae6ParallelPort ControllerPort4 { get; set; } = Puae6ParallelPort.None;

	public Puae6FloppyDrives PhysicalFloppyDrives { get; set; } = Puae6FloppyDrives.Auto;

	/// <summary>How many disks are in drives at power-on (1..4). Auto connects at least two drives, growing to fit this number; the rest are slots for Insert.</summary>
	public int FloppyDrives { get; set; } = 1;

	/// <summary>
	/// The floppy disks can be written (P6_CONFIG_FLOPPY_WRITES; off by default: every disk is write protected, as always). What the guest writes goes to a reversible overlay in the sandbox,
	/// part of every state; the disks the core was given and every file stay as they were. It is a sync setting: a state and a movie of one setting do not load in the other, and the Waterbox
	/// heaps grow by what the overlay needs (<see cref="Puae6Core.Heaps(Puae6SyncSettings)"/>). Making the writes durable is explicit (ISaveRam, <see cref="SaveRam"/>).
	/// </summary>
	public bool FloppyWrites { get; set; }

	public Puae6SyncSettings Clone() => (Puae6SyncSettings)MemberwiseClone();

	public static bool NeedsReboot(Puae6SyncSettings x, Puae6SyncSettings y) => !DeepEquality.DeepEquals(x, y);
}
