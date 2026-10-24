// T70: round trip every CD32 button through BizHawk's actual BK2 codec, without running a guest.
using BizHawk.Client.Common;
using BizHawk.Emulation.Common;
using Puae6;

internal static unsafe partial class Program
{
	static void Cd32Log()
	{
		var definition = Controllers.Define(new Puae6SyncSettings { ControllerPort1 = Puae6Port1.Cd32, ControllerPort2 = Puae6Port2.Cd32 }, 0);
		definition.BuildMnemonicsCache("Amiga");
		var source = new ScriptedController(definition);
		var decoded = new Bk2Controller(definition, Bk2LogEntryGenerator.GenerateLogKey(definition));
		foreach (var button in Controllers.Cd32Buttons.SelectMany(port => port))
		{
			Require(definition.MnemonicsCache![button] != '!', $"CD32 has a known mnemonic: {button}");
			source.Clear(); source.Press(button);
			decoded.SetFromMnemonic(Bk2LogEntryGenerator.GenerateLogEntry(source));
			Require(definition.BoolButtons.All(name => source.IsPressed(name) == decoded.IsPressed(name)), $"CD32 BK2 round trip: {button}");
		}
	}

	static void Mouse2Log()
	{
		var definition = Controllers.Define(new Puae6SyncSettings { ControllerPort1 = Puae6Port1.Mouse, ControllerPort2 = Puae6Port2.Mouse }, 0);
		definition.BuildMnemonicsCache("Amiga");
		var source = new ScriptedController(definition);
		var decoded = new Bk2Controller(definition, Bk2LogEntryGenerator.GenerateLogKey(definition));
		foreach (var button in Controllers.MouseButtons.SelectMany(port => port))
		{
			Require(definition.MnemonicsCache![button] != '!', $"mouse has a known mnemonic: {button}");
			source.Clear(); source.Press(button);
			decoded.SetFromMnemonic(Bk2LogEntryGenerator.GenerateLogEntry(source));
			Require(definition.BoolButtons.All(name => source.IsPressed(name) == decoded.IsPressed(name)), $"mouse BK2 round trip: {button}");
		}
		foreach (var axis in Controllers.MouseAxes.SelectMany(port => port))
		{
			source.Axis(axis, 123);
			decoded.SetFromMnemonic(Bk2LogEntryGenerator.GenerateLogEntry(source));
			Require(definition.Axes.Keys.All(name => source.AxisValue(name) == decoded.AxisValue(name)), $"mouse BK2 axis round trip: {axis}");
		}
	}

	static void PointerLog()
	{
		foreach (int device in new[] { 4, 5 })
		for (int port = 0; port < 2; ++port)
		{
			var settings = new Puae6SyncSettings { ControllerPort1 = port == 0 ? (Puae6Port1)device : Puae6Port1.None,
				ControllerPort2 = port == 1 ? (Puae6Port2)device : Puae6Port2.None };
			var definition = Controllers.Define(settings, 0); definition.BuildMnemonicsCache("Amiga");
			var source = new ScriptedController(definition);
			var decoded = new Bk2Controller(definition, Bk2LogEntryGenerator.GenerateLogKey(definition));
			foreach (var button in Controllers.PointerButtons[port])
			{
				Require(definition.MnemonicsCache![button] != '!', $"optical {device}:{port} mnemonic {button}");
				source.Clear(); source.Press(button);
				decoded.SetFromMnemonic(Bk2LogEntryGenerator.GenerateLogEntry(source));
				Require(definition.BoolButtons.All(n => source.IsPressed(n) == decoded.IsPressed(n)), $"optical {device}:{port} button {button}");
			}
			foreach (var axis in Controllers.PointerAxes[port])
			{
				source.Axis(axis, -23456); decoded.SetFromMnemonic(Bk2LogEntryGenerator.GenerateLogEntry(source));
				Require(definition.Axes.Keys.All(n => source.AxisValue(n) == decoded.AxisValue(n)), $"optical {device}:{port} axis {axis}");
			}
		}
	}
    static void ParallelLog()
    {
        Require(sizeof(P6Input) == 72 && sizeof(P6InputV2) == 108, "parallel ABI v1/v2 sizes");
        var definition = Controllers.Define(new Puae6SyncSettings { ControllerPort1 = Puae6Port1.Joystick, ControllerPort2 = Puae6Port2.Joystick,
            ControllerPort3 = Puae6ParallelPort.Joystick, ControllerPort4 = Puae6ParallelPort.Joystick }, 0);
        definition.BuildMnemonicsCache("Amiga");
        var source = new ScriptedController(definition);
        var decoded = new Bk2Controller(definition, Bk2LogEntryGenerator.GenerateLogKey(definition));
        foreach (int port in new[] { 3, 4 }) foreach (var button in Controllers.JoystickButtons[port - 1].Take(5))
        {
            Require(definition.MnemonicsCache![button] != '!', $"parallel mnemonic {button}");
            source.Clear(); source.Press(button); decoded.SetFromMnemonic(Bk2LogEntryGenerator.GenerateLogEntry(source));
            Require(definition.BoolButtons.All(n => source.IsPressed(n) == decoded.IsPressed(n)), $"parallel round trip {button}");
        }
    }

}
