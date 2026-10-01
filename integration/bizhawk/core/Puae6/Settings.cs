#nullable enable

using BizHawk.Common;

namespace Puae6;

public enum Puae6Region { PAL, NTSC }

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
	/// <summary>The video standard of the machine. It is not detected from anything.</summary>
	public Puae6Region Region { get; set; } = Puae6Region.PAL;

	/// <summary>Seed of the random sources of the emulated hardware (the start-up delay of the boot, for one). Serialized with the movie.</summary>
	public ulong RandomSeed { get; set; } = 1;

	/// <summary>The real-time clock's epoch, UTC seconds: 2000-01-01 by default. The host clock is never read.</summary>
	public long RtcEpochSeconds { get; set; } = 946684800;

	public Puae6Port1 ControllerPort1 { get; set; } = Puae6Port1.Mouse;

	public Puae6Port2 ControllerPort2 { get; set; } = Puae6Port2.Joystick;

	public Puae6SyncSettings Clone() => (Puae6SyncSettings)MemberwiseClone();

	public static bool NeedsReboot(Puae6SyncSettings x, Puae6SyncSettings y) => !DeepEquality.DeepEquals(x, y);
}
