using BizHawk.Emulation.Common;
using Puae6;

internal static unsafe partial class Program
{
 static void FastCd32(string[] args, Func<bool, Puae6Core> open)
 {
  string file = Path.Combine(args[0], "cd32fr.state");
  using var core = open(true);
  Require(Puae6Core.Heaps(core.GetSyncSettings()).MmapHeapSizeKB == 32768);
  var ram = core.ServiceProvider.GetService<IMemoryDomains>()["Fast RAM"];
  Require(ram.Size == 8 * 1024 * 1024 && ram.Writable);
  byte[] cut;
  string[] future;
  if (args[5] == "frsave") {
   Run(core, 300); ram.PokeByte(0x100, 0x51); ram.PokeByte(ram.Size - 1, 0xa9);
   cut = Save(core); File.WriteAllBytes(file, cut);
   future = Run(core, 15); File.WriteAllLines(file + ".future", future);
   File.WriteAllBytes(file + ".end", Save(core));
   ram.PokeByte(0x100, 0xaa); ram.PokeByte(ram.Size - 1, 0x55);
  } else { cut = File.ReadAllBytes(file); future = File.ReadAllLines(file + ".future"); }
  void Check(Puae6Core c) {
   Restore(c, cut); var fast = c.ServiceProvider.GetService<IMemoryDomains>()["Fast RAM"];
   Require(fast.PeekByte(0x100) == 0x51 && fast.PeekByte(fast.Size - 1) == 0xa9);
   Require(Run(c, 15).SequenceEqual(future)); Require(Save(c).SequenceEqual(File.ReadAllBytes(file + ".end")));
  }
  Check(core); using var other = open(true); Check(other);
  using var stock = open(false); var before = Save(stock);
  try { Restore(stock, cut); throw new Exception("CD32FR state loaded on stock CD32"); }
  catch (StateRejectedException e) { Require(e.Reason == StateRejection.OtherHeaps); }
  try { Puae6Movie.Validate(Puae6Movie.Header(core), stock); throw new Exception("CD32FR movie loaded on stock CD32"); }
  catch (MovieRejectedException e) { Require(e.Reason == MovieRejection.OtherConfig); }
  Require(before.SequenceEqual(Save(stock)));
  Console.WriteLine("Public BizHawk CD32FR Fast RAM/rollback/new sandbox/new process/identity PASS");
 }
}
