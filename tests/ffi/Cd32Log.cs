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
			source.Clear(); source.Press(button);
			decoded.SetFromMnemonic(Bk2LogEntryGenerator.GenerateLogEntry(source));
			Require(definition.BoolButtons.All(name => source.IsPressed(name) == decoded.IsPressed(name)), $"CD32 BK2 round trip: {button}");
		}
	}
}
