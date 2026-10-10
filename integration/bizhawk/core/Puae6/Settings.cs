#nullable enable

using BizHawk.Common;

namespace Puae6;

public enum Puae6Region { PAL, NTSC }

/// <summary>
/// The Amiga the core emulates: the A500 (OCS, 512 KiB Chip + 512 KiB Slow RAM, Kickstart 1.3) the A600 (ECS, 1 MiB Chip RAM, Kickstart 2.05, no IDE or PCMCIA) or the A1200 (68EC020, AGA, 2 MiB Chip RAM, Kickstart 3.1, no Gayle).
/// The names are the ones of the schema (config/schema/options.json) and of the BizHawk firmware database (FirmwareID "A500" / "A600").
/// </summary>
public enum Puae6Machine { A500, A600, A1200 }

/// <summary>Chip RAM (restart): the machine's own (A500 512 KiB, A600 1 MiB, A1200 2 MiB) or a size; the A500 (OCS) has 512 KiB or 1 MiB, the other machines also 2 MiB.</summary>
public enum Puae6Chip { Auto, KB512, MB1, MB2 }

/// <summary>Slow RAM at $C00000 (restart): the machine's own (A500 512 KiB, A600 and A1200 none), none, or a size; 1.75 MiB is not available on the A1200 (AGA maps 1.5 MiB at most).</summary>
public enum Puae6Slow { Auto, None, KB512, MB1, MB1_5, MB1_75 }

/// <summary>Zorro II Fast RAM (restart): none, or one autoconfig board that the Kickstart configures, as PUAE5's A600 and A1200 have (8 MiB).</summary>
public enum Puae6Fast { None, MB1, MB2, MB4, MB8 }

/// <summary>What is plugged into the first controller port of the Amiga (the one the mouse normally uses).</summary>
public enum Puae6Port1 { Mouse, Joystick, None }

/// <summary>What is plugged into the second controller port. A mouse there is not supported (docs/02-contracts.md).</summary>
public enum Puae6Port2 { Joystick, None }

/// <summary>
/// The settings that change what the machine does, so they are part of a movie and of a save state's identity (docs/02-contracts.md, "Configuracion"):
/// the same normalized values feed the guest, and a change needs a reboot of the core. No hardware option is exposed that the profile cannot run.
/// </summary>
public sealed class Puae6SyncSettings
{
	/// <summary>Which Amiga it is. A state or a movie of one machine is refused by the other; the Kickstart asked of BizHawk's firmware database follows it (FirmwareID Amiga "A500", "A600" or "A1200", the name of the member).</summary>
	public Puae6Machine Machine { get; set; } = Puae6Machine.A500;

	/// <summary>Chip RAM of the machine. A size the machine cannot have is refused when the core starts, with the reason.</summary>
	public Puae6Chip ChipMemory { get; set; } = Puae6Chip.Auto;

	/// <summary>Slow RAM of the machine.</summary>
	public Puae6Slow SlowMemory { get; set; } = Puae6Slow.Auto;

	/// <summary>Zorro II Fast RAM. The Waterbox heap of the core grows with it (<see cref="Puae6Core.Heaps(Puae6SyncSettings)"/>), measured in manifests/waterbox-memory.json.</summary>
	public Puae6Fast FastMemory { get; set; } = Puae6Fast.None;

	/// <summary>The video standard of the machine. It is not detected from anything.</summary>
	public Puae6Region Region { get; set; } = Puae6Region.PAL;

	/// <summary>Seed of the random sources of the emulated hardware (the start-up delay of the boot, for one). Serialized with the movie.</summary>
	public ulong RandomSeed { get; set; } = 1;

	/// <summary>The real-time clock's epoch, UTC seconds: 2000-01-01 by default. The host clock is never read.</summary>
	public long RtcEpochSeconds { get; set; } = 946684800;

	public Puae6Port1 ControllerPort1 { get; set; } = Puae6Port1.Mouse;

	public Puae6Port2 ControllerPort2 { get; set; } = Puae6Port2.Joystick;

	/// <summary>How many of the disks the core was given are in a drive at power-on (1 or 2, DF0 and DF1); the rest are slots for the Insert button.</summary>
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
