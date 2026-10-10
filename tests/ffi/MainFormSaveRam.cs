using BizHawk.Emulation.Common;

using Puae6;

/// <summary>
/// T28b: the two ways EmuHawk touches a core's .SaveRAM file, written out as MainForm.cs does them (../MacHawk/src/BizHawk.Client.EmuHawk/MainForm.cs, LoadSaveRam and FlushSaveRAM, at the
/// commit plan/machawk.reference.json pins), so that a test can run the exact algorithm EmuHawk runs without EmuHawk (WinForms, not run here):
///
///   mainflush NAME PATH                       FlushSaveRAM: if the emulator has ISaveRam, CloneSaveRam() (clearDirty), nothing when null, else the bytes to the file
///   mainload NAME PATH [stock|patched]        LoadSaveRam for the file at PATH. "stock": MainForm as it is (the file goes whole only to the fixed list of cores; any other core gets a buffer
///                                             the size of CloneSaveRam() of the core it has just created, and the load is skipped when that is null). "patched": integration/bizhawk/patches
///                                             adds PUAE6 to the fixed list, so the file goes whole. Only IOException is caught, as in MainForm; any other exception ends the program.
///
/// Prints "mainflush NAME wrote=BYTES|null|nosaveram", "mainload NAME loaded|skipped-nosaveram|skipped-null|error=REASON" and, for a message the core notified, "notice TEXT".
/// </summary>
internal static partial class Program
{
	static void MainFormFlush(Puae6Core core, string name, string path)
	{
		var provider = core.ServiceProvider;
		if (!provider.HasService<ISaveRam>()) { Console.WriteLine($"mainflush {name} nosaveram"); return; }
		var saveram = provider.GetService<ISaveRam>()!.CloneSaveRam();
		if (saveram == null) { Console.WriteLine($"mainflush {name} null"); return; }
		File.WriteAllBytes(path, saveram);        // MainForm: FileWriter.Write(path, saveram, path + ".bak"), which writes a temporary file and renames it over the old one; the bytes are the same
		Console.WriteLine($"mainflush {name} wrote={saveram.Length}");
	}

	static void MainFormLoad(Puae6Core core, string name, string path, bool patched)
	{
		var provider = core.ServiceProvider;
		if (!provider.HasService<ISaveRam>()) { Console.WriteLine($"mainload {name} skipped-nosaveram"); return; }
		var saveramToLoad = new FileInfo(path);
		try
		{
			byte[] sram;
			if (patched)
			{
				sram = File.ReadAllBytes(saveramToLoad.FullName);
			}
			else
			{
				var oldRam = provider.GetService<ISaveRam>()!.CloneSaveRam();
				if (oldRam is null) { Console.WriteLine($"mainload {name} skipped-null"); return; }
				sram = new byte[oldRam.Length];
				using var fs = saveramToLoad.OpenRead();
				_ = fs.Read(sram, 0, sram.Length);
			}
			provider.GetService<ISaveRam>()!.StoreSaveRam(sram);
			Console.WriteLine($"mainload {name} loaded");
		}
		catch (IOException e)
		{
			Console.WriteLine($"mainload {name} error={e.GetType().Name}");     // MainForm: AddOnScreenMessage("An error occurred while loading Sram"); Console.Error.WriteLine(e);
		}
	}
}
