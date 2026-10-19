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
}
