using System.Reflection;
using System.Runtime.CompilerServices;
using System.Runtime.InteropServices;
using BizHawk.Client.Common;
using BizHawk.Common;
using BizHawk.Emulation.Common;

// Real pinned state managers with an array source, not an emulated core or WB gate.
static class Program
{
 static readonly int[] Sizes = [4091, 4092, 4093, 16383, 16384, 16385];
 static void Require(bool condition) { if (!condition) throw new Exception("host state mismatch"); }
 static byte[] Payload(int frame, int size)
 {
  var result = new byte[size]; uint x = (uint)(frame + 1);
  for (int i = 0; i < size; i++) { x ^= x << 13; x ^= x >> 17; x ^= x << 5; result[i] = (byte)x; }
  return result;
 }
 sealed class Source(byte[] data) : IStatable
 {
  public bool AvoidRewind => false;
  public void SaveStateBinary(BinaryWriter writer) => writer.Write(data);
  public void LoadStateBinary(BinaryReader reader) => throw new NotSupportedException();
 }
 static void Bytes(Stream stream, byte[] expected)
 {
  using (stream) { using var copy = new MemoryStream(); stream.CopyTo(copy, 4093); Require(copy.ToArray().SequenceEqual(expected)); }
 }
 static PagedStateManager Paged() => new(new() {
  TotalMemoryLimitMB = 1, FramesBetweenNewStates = 1, FramesBetweenMidStates = 1,
  FramesBetweenOldStates = 1, FramesBetweenSavedStates = 1
 }, _ => false);
 static ZwinderBuffer Zwinder(bool memory) => new(new RewindConfig {
  BufferSize = 1, BackingStore = memory ? IRewindSettings.BackingStoreType.Memory : IRewindSettings.BackingStoreType.TempFile,
  UseCompression = true, UseFixedRewindInterval = true, TargetRewindInterval = 1
 });
 static void Memory()
 {
  // The fixed 4KiB global guard remains effective for its existing consumers.
  bool rejected = false;
  try { RuntimeHelpers.RunClassConstructor(typeof(MemoryBlockUtils).TypeHandle); }
  catch (TypeInitializationException e) { Require(e.InnerException?.Message == "Wrong page size"); rejected = true; }
  Require(rejected == (Environment.SystemPageSize != 4096));
  foreach (ulong size in new ulong[] {4096, (ulong)Environment.SystemPageSize}) {
   using var block = new MemoryBlock(size); Require(block.Size == size);
   Require(block.Start % (ulong)Environment.SystemPageSize == 0);
   for (ulong offset = 0; offset < size; offset += 4096) {
    block.Protect(block.Start + offset, 1, MemoryBlock.Protection.RW);
    using var stream = block.GetStream(block.Start + offset, 1, true); stream.WriteByte((byte)(offset / 4096 + 17));
   }
   block.Protect(block.Start, size, MemoryBlock.Protection.R);
   for (ulong offset = 0; offset < size; offset += 4096) {
    using var stream = block.GetStream(block.Start + offset, 1, false); Require(stream.ReadByte() == (int)(offset / 4096 + 17));
   }
  }
  Console.WriteLine($"host memory page={Environment.SystemPageSize} logical4K/global-guard/protection PASS");
 }
 static void PagedHistory(string directory, bool load)
 {
  using var manager = Paged(); var zero = Payload(0, Sizes[0]); manager.Engage(zero);
  string path = Path.Combine(directory, "paged.history");
  if (load) { using var reader = new BinaryReader(File.OpenRead(path)); manager.LoadStateHistory(reader); }
  else {
   foreach (int frame in new[] {5, 1, 6, 2, 4, 3}) manager.Capture(frame, new Source(Payload(frame, Sizes[frame - 1])), true);
   Require(manager.Count == 7); manager.InvalidateAfter(4); Require(manager.Count == 5);
   foreach (int frame in new[] {6, 5}) manager.Capture(frame, new Source(Payload(frame, Sizes[frame - 1])), true);
  }
  Require(manager.Count == 7 && manager.Last == 6);
  for (int frame = 0; frame <= 6; frame++) {
   var state = manager.GetStateClosestToFrame(frame); Require(state.Key == frame);
   Bytes(state.Value, frame == 0 ? zero : Payload(frame, Sizes[frame - 1]));
  }
  var latest = manager.GetStateClosestToFrame(99); Require(latest.Key == 6); latest.Value.Dispose();
  if (!load) { using var writer = new BinaryWriter(File.Create(path)); manager.SaveStateHistory(writer); }
  Console.WriteLine($"host Paged out-of-order/boundaries/invalidate/history {(load ? "cold" : "save")} PASS");
 }
 static void ZwinderHistory(string directory, bool memory, bool load)
 {
  using var buffer = Zwinder(memory); string path = Path.Combine(directory, memory ? "memory.history" : "temp.history");
  if (load) { using var reader = new BinaryReader(File.OpenRead(path)); buffer.Load(reader); }
  else {
   for (int frame = 0; frame < 16; frame++) {
    var data = Payload(frame, 200000); buffer.Capture(frame, s => s.Write(data, 0, data.Length), force: true);
   }
   Require(buffer.Count > 1 && buffer.Count < 16 && buffer.GetState(0).Frame > 0); // ring really wrapped/evicted
   Require(buffer.InvalidateAfter(14)); var last = Payload(15, 200000);
   buffer.Capture(15, s => s.Write(last, 0, last.Length), force: true);
  }
  Require(buffer.Count > 1 && buffer.GetState(buffer.Count - 1).Frame == 15);
  for (int i = 0; i < buffer.Count; i++) { var state = buffer.GetState(i); Bytes(state.GetReadStream(), Payload(state.Frame, 200000)); }
  if (!load) {
   using (var writer = new BinaryWriter(File.Create(path))) buffer.SaveStateBinary(writer);
   var wire = File.ReadAllBytes(path); int first = 5 + buffer.Count * 8;
   Require(wire.Skip(first).Take(4).SequenceEqual(new byte[] {0x28, 0xb5, 0x2f, 0xfd})); // Zstd frame magic
  }
  Console.WriteLine($"host Zwinder {(memory ? "Memory" : "TempFile")} zstd/wrap/seek/history {(load ? "cold" : "save")} PASS");
 }
 static int Main(string[] args)
 {
  try {
   // Separate entrypoints expose each baseline failure before candidate testing.
   if (args[0] == "mmap") {
    var type = typeof(MemoryBlock).Assembly.GetType("BizHawk.Common.MemoryBlockLinuxPal", true)!;
    using var pal = (IDisposable)Activator.CreateInstance(type, (ulong)Environment.SystemPageSize)!;
   } else if (args[0] == "codec") Require(LibZstd.MinCLevel() < LibZstd.MaxCLevel());
   else {
    Memory(); PagedHistory(args[1], args[0] == "load");
    ZwinderHistory(args[1], true, args[0] == "load"); ZwinderHistory(args[1], false, args[0] == "load");
   }
   return 0;
  } catch (Exception e) { Console.Error.WriteLine(e); return 1; }
 }
}
