using BizHawk.Common;
using BizHawk.Common.CollectionExtensions;
using BizHawk.Emulation.Common;

namespace Puae6;

/// <summary>The buttons, axes and keys of the PUAE6 machine, and what each one is in the p6_input packet (docs/02-contracts.md, "Entrada").</summary>
public static class Controllers
{
	public const string Power = "Power";
	public const string MouseLeft = "Mouse Left Button", MouseMiddle = "Mouse Middle Button", MouseRight = "Mouse Right Button";
	public const string MouseX = "Mouse X", MouseY = "Mouse Y";
	/// <summary>The mouse axes are absolute positions in a field of this size; the wrapper turns them into the deltas the machine takes.</summary>
	public const int MouseFieldWidth = 760, MouseFieldHeight = 576;

	public static readonly string[] JoystickNames = ["Up", "Down", "Left", "Right", "Button 1", "Button 2", "Button 3"];
	public static readonly uint[] JoystickBits = [P6.ButtonUp, P6.ButtonDown, P6.ButtonLeft, P6.ButtonRight, P6.Button1, P6.Button2, P6.Button3];

	/// <summary>The Amiga keyboard, by raw key code 0x00..0x67 (Amiga Hardware Reference Manual, Appendix G); null where the code is not a key. Codes
	/// 0x68..0x7F are not keys and the bridge refuses them.</summary>
	public static readonly string?[] KeyNames = BuildKeyNames();

	/// <summary>Caps Lock is a latch on the Amiga keyboard (its make code is sent when it turns on and its break code when it turns off); the
	/// controller button toggles it on each press instead of having to be held.</summary>
	public const int CapsLockCode = 0x62;

	static string?[] BuildKeyNames()
	{
		var names = new string?[0x68];
		void Set(int code, string name) => names[code] = name;
		Set(0x00, "Grave");
		string[] digits = ["1", "2", "3", "4", "5", "6", "7", "8", "9", "0"];
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

	public static ControllerDefinition Define(Puae6SyncSettings settings)
	{
		var controller = new ControllerDefinition("Amiga Controller");
		if (settings.ControllerPort1 == Puae6Port1.Mouse)
		{
			controller.BoolButtons.AddRange([$"P1 {MouseLeft}", $"P1 {MouseMiddle}", $"P1 {MouseRight}"]);
			controller
				.AddAxis($"P1 {MouseX}", 0.RangeTo(MouseFieldWidth), MouseFieldWidth / 2)
				.AddAxis($"P1 {MouseY}", 0.RangeTo(MouseFieldHeight), MouseFieldHeight / 2);
		}
		else if (settings.ControllerPort1 == Puae6Port1.Joystick)
		{
			foreach (var name in JoystickNames) controller.BoolButtons.Add(Joystick(1, name));
		}
		if (settings.ControllerPort2 == Puae6Port2.Joystick)
			foreach (var name in JoystickNames) controller.BoolButtons.Add(Joystick(2, name));
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

	public static string KeyButton(int code) => $"Key {KeyNames[code]}";
}
