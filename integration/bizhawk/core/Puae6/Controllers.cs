#nullable enable

using System.Linq;
using BizHawk.Common;
using BizHawk.Common.CollectionExtensions;
using BizHawk.Emulation.Common;

namespace Puae6;

/// <summary>The buttons, axes and keys of the PUAE6 machine, and what each one is in the p6_input packet (docs/02-contracts.md, "Entrada").</summary>
public static class Controllers
{
	public const string Power = "Power";
	public const string EjectDisk = "Eject Disk", InsertDisk = "Insert Disk", NextSlot = "Next Slot", NextDrive = "Next Drive";
	public const string MouseLeft = "Mouse Left Button", MouseMiddle = "Mouse Middle Button", MouseRight = "Mouse Right Button";
	public const string MouseX = "Mouse X", MouseY = "Mouse Y";
	/// <summary>The mouse axes are absolute positions in a field of this size; the wrapper turns them into the deltas the machine takes.</summary>
	public const int MouseFieldWidth = 760, MouseFieldHeight = 576;

	public static readonly string[] JoystickNames = [ "Up", "Down", "Left", "Right", "Button 1", "Button 2", "Button 3" ];
	public static readonly uint[] JoystickBits = [ P6.ButtonUp, P6.ButtonDown, P6.ButtonLeft, P6.ButtonRight, P6.Button1, P6.Button2, P6.Button3 ];

	/// <summary>The Amiga keyboard, by raw key code 0x00..0x67 (Amiga Hardware Reference Manual, Appendix G); null where the code is not a key. Codes
	/// 0x68..0x7F are not keys and the bridge refuses them.</summary>
	public static readonly string?[] KeyNames = BuildKeyNames();

	/// <summary>Caps Lock is a latch on the Amiga keyboard (its make code is sent when it turns on and its break code when it turns off); the
	/// controller button toggles it on each press instead of having to be held.</summary>
	public const int CapsLockCode = 0x62;

	/// <summary>The controller button of each key code ("Key Space"), built once: the core asks for every key at every unit and a string built per question was ~4 KB of garbage per unit (T34).
	/// Null where the code is not a key. <see cref="KeyButton"/> reads this table.</summary>
	public static readonly string?[] KeyButtons = BuildKeyButtons();

	/// <summary>The joystick buttons of port 1 and port 2, in the order of <see cref="JoystickNames"/> (the names <see cref="Joystick"/> makes), built once.</summary>
	public static readonly string[][] JoystickButtons = [ BuildJoystickButtons(1), BuildJoystickButtons(2) ];
	public static readonly string[] Cd32Names = [ "Up", "Down", "Left", "Right", "Red", "Blue", "Green", "Yellow", "Rewind", "Forward", "Play" ];
	public static readonly uint[] Cd32Bits = [ P6.ButtonUp, P6.ButtonDown, P6.ButtonLeft, P6.ButtonRight, P6.Button1, P6.Button2, P6.Button3, P6.Cd32Yellow, P6.Cd32Rewind, P6.Cd32Forward, P6.Cd32Play ];
	public static readonly string[][] Cd32Buttons = [ Cd32Names.Select(n => $"P1 CD32 pad {n}").ToArray(), Cd32Names.Select(n => $"P2 CD32 pad {n}").ToArray() ];

	/// <summary>The mouse buttons and axes of port 1 as the controller names them, built once.</summary>
	public static readonly string Port1MouseLeft = $"P1 {MouseLeft}", Port1MouseMiddle = $"P1 {MouseMiddle}", Port1MouseRight = $"P1 {MouseRight}", Port1MouseX = $"P1 {MouseX}", Port1MouseY = $"P1 {MouseY}";
	public static readonly string[][] MouseButtons = [ [Port1MouseLeft, Port1MouseRight, Port1MouseMiddle], [$"P2 {MouseLeft}", $"P2 {MouseRight}", $"P2 {MouseMiddle}"] ];
	public static readonly string[][] MouseAxes = [ [Port1MouseX, Port1MouseY], [$"P2 {MouseX}", $"P2 {MouseY}"] ];
	// Shared optical controls use BizHawk's standard BK2 names for both pen and gun.
	public static readonly string[][] PointerButtons = [ ["P1 Lightgun Trigger", "P1 Lightgun Offscreen Shot"], ["P2 Lightgun Trigger", "P2 Lightgun Offscreen Shot"] ];
	public static readonly string[][] PointerAxes = [ ["P1 Lightgun X", "P1 Lightgun Y"], ["P2 Lightgun X", "P2 Lightgun Y"] ];

	static string?[] BuildKeyButtons() => KeyNames.Select(name => name is null ? null : $"Key {name}").ToArray();

	static string[] BuildJoystickButtons(int port) => JoystickNames.Select(name => Joystick(port, name)).ToArray();

	static string?[] BuildKeyNames()
	{
		var names = new string?[0x68];
		void Set(int code, string name) => names[code] = name;
		Set(0x00, "Grave");
		string[] digits = [ "1", "2", "3", "4", "5", "6", "7", "8", "9", "0" ];
		for (int i = 0; i < 10; ++i) Set(0x01 + i, digits[i]);
		Set(0x0B, "Minus"); Set(0x0C, "Equals"); Set(0x0D, "Backslash"); Set(0x0F, "Keypad 0");
		string row1 = "QWERTYUIOP";
		for (int i = 0; i < row1.Length; ++i) Set(0x10 + i, row1[i].ToString());
		Set(0x1A, "Left Bracket"); Set(0x1B, "Right Bracket"); Set(0x1D, "Keypad 1"); Set(0x1E, "Keypad 2"); Set(0x1F, "Keypad 3");
		string row2 = "ASDFGHJKL";
		for (int i = 0; i < row2.Length; ++i) Set(0x20 + i, row2[i].ToString());
		Set(0x29, "Semicolon"); Set(0x2A, "Quote"); Set(0x2B, "Hash"); Set(0x2D, "Keypad 4"); Set(0x2E, "Keypad 5"); Set(0x2F, "Keypad 6");
		Set(0x30, "Less Than");
		string row3 = "ZXCVBNM";
		for (int i = 0; i < row3.Length; ++i) Set(0x31 + i, row3[i].ToString());
		Set(0x38, "Comma"); Set(0x39, "Period"); Set(0x3A, "Slash"); Set(0x3C, "Keypad Period"); Set(0x3D, "Keypad 7"); Set(0x3E, "Keypad 8"); Set(0x3F, "Keypad 9");
		Set(0x40, "Space"); Set(0x41, "Backspace"); Set(0x42, "Tab"); Set(0x43, "Keypad Enter"); Set(0x44, "Return"); Set(0x45, "Escape"); Set(0x46, "Delete");
		Set(0x4A, "Keypad Minus"); Set(0x4C, "Up"); Set(0x4D, "Down"); Set(0x4E, "Right"); Set(0x4F, "Left");
		for (int i = 0; i < 10; ++i) Set(0x50 + i, $"F{i + 1}");
		Set(0x5A, "Keypad Left Paren"); Set(0x5B, "Keypad Right Paren"); Set(0x5C, "Keypad Slash"); Set(0x5D, "Keypad Star"); Set(0x5E, "Keypad Plus"); Set(0x5F, "Help");
		Set(0x60, "Left Shift"); Set(0x61, "Right Shift"); Set(0x62, "Caps Lock"); Set(0x63, "Control"); Set(0x64, "Left Alt"); Set(0x65, "Right Alt");
		Set(0x66, "Left Amiga"); Set(0x67, "Right Amiga");
		return names;
	}

	public static string Joystick(int port, string name) => $"P{port} Joystick {name}";

	public static ControllerDefinition Define(Puae6SyncSettings settings, int mediaCount)
	{
		var controller = new ControllerDefinition("Amiga Controller");
		bool pointer0 = settings.ControllerPort1 is Puae6Port1.Lightgun or Puae6Port1.Lightpen;
		bool pointer1 = settings.ControllerPort2 is Puae6Port2.Lightgun or Puae6Port2.Lightpen;
		if (pointer0 && pointer1) throw new System.ArgumentException("only one optical sensor is supported", nameof(settings));
		for (int p = 0; p < 2; ++p)
			if (p == 0 ? pointer0 : pointer1)
			{
				controller.BoolButtons.AddRange(PointerButtons[p]);
				controller.AddAxis(PointerAxes[p][0], (-32767).RangeTo(32767), 0).AddAxis(PointerAxes[p][1], (-32767).RangeTo(32767), 0);
			}
		if (settings.ControllerPort1 == Puae6Port1.Mouse)
		{
			controller.BoolButtons.AddRange([ $"P1 {MouseLeft}", $"P1 {MouseMiddle}", $"P1 {MouseRight}" ]);
			controller
				.AddAxis($"P1 {MouseX}", 0.RangeTo(MouseFieldWidth), MouseFieldWidth / 2)
				.AddAxis($"P1 {MouseY}", 0.RangeTo(MouseFieldHeight), MouseFieldHeight / 2);
		}
		else if (settings.ControllerPort1 == Puae6Port1.Joystick)
		{
			foreach (var name in JoystickNames) controller.BoolButtons.Add(Joystick(1, name));
		}
		else if (settings.ControllerPort1 == Puae6Port1.Cd32) controller.BoolButtons.AddRange(Cd32Buttons[0]);
		if (settings.ControllerPort2 == Puae6Port2.Joystick)
			foreach (var name in JoystickNames) controller.BoolButtons.Add(Joystick(2, name));
		else if (settings.ControllerPort2 == Puae6Port2.Cd32) controller.BoolButtons.AddRange(Cd32Buttons[1]);
		else if (settings.ControllerPort2 == Puae6Port2.Mouse)
		{
			controller.BoolButtons.AddRange(MouseButtons[1]);
			controller.AddAxis(MouseAxes[1][0], 0.RangeTo(MouseFieldWidth), MouseFieldWidth / 2)
				.AddAxis(MouseAxes[1][1], 0.RangeTo(MouseFieldHeight), MouseFieldHeight / 2);
		}
		// The disk controls exist when there is something to put in a drive: drive and slot are selected first, then Eject or Insert acts on them.
		if (mediaCount > 0) controller.BoolButtons.AddRange([ NextDrive, NextSlot, EjectDisk, InsertDisk ]);
		// Hard reset only: the machine has one (p6_reset); a Reset button would be a different thing that nobody implemented.
		controller.BoolButtons.Add(Power);
		foreach (var name in KeyNames)
		{
			if (name is null) continue;
			controller.BoolButtons.Add($"Key {name}");
			controller.CategoryLabels[$"Key {name}"] = "Keyboard";
		}
		return controller.MakeImmutable();
	}

	public static string KeyButton(int code) => KeyButtons[code] ?? $"Key {KeyNames[code]}";
}
