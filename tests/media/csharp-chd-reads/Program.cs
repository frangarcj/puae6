using BizHawk.Emulation.DiscSystem;

// Recording hunk provider for testing Blob_CHD's fragmentation, not a CHD decoder.
namespace BizHawk.Emulation.DiscSystem
{
 static class LibChd
 {
  public const int Size = 4 * 2448;
  public static readonly List<uint> Reads = new();
  public static int Closes;
  public static uint Fail = uint.MaxValue;
  public enum chd_error { CHDERR_NONE, CHDERR_READ_ERROR }
  public static byte Pattern(long position) => (byte)((position / Size) * 71 + (position % Size) * 13 + (position % Size) / 256);
  public static chd_error chd_read(IntPtr handle, uint hunk, byte[] output)
  {
   if (handle != new IntPtr(1) || output.Length != Size) throw new Exception("Invalid hunk request");
   Reads.Add(hunk);
   if (hunk == Fail) return chd_error.CHDERR_READ_ERROR;
   for (int i = 0; i < output.Length; i++) output[i] = Pattern((long)hunk * Size + i);
   return chd_error.CHDERR_NONE;
  }
  public static void chd_close(IntPtr handle)
  {
   if (handle != new IntPtr(1)) throw new Exception("Invalid close");
   Closes++;
  }
 }
}

static class Program
{
 static void Require(bool condition, string message) { if (!condition) throw new Exception(message); }
 static void Read(Blob_CHD blob, long position, int count)
 {
  var output = Enumerable.Repeat((byte)0xe7, count + 8).ToArray();
  Require(blob.Read(position, output, 4, count) == count, "Wrong count");
  for (int i = 0; i < count; i++)
   Require(output[i + 4] == LibChd.Pattern(position + i), "Cross-hunk payload differs");
  Require(output.Take(4).Concat(output.Skip(count + 4)).All(b => b == 0xe7), "Output bounds changed");
 }
 static int Main(string[] args)
 {
  try
  {
   using var blob = new Blob_CHD(new IntPtr(1), LibChd.Size);
   int size = LibChd.Size;
   switch (args[0])
   {
    case "cross":
     Read(blob, size - 17, size + 33);
     Require(LibChd.Reads.SequenceEqual(new uint[] { 0, 1, 2 }), "Wrong hunk sequence");
     break;
    case "cache":
     Read(blob, size - 1, 1); Read(blob, 0, 1); Read(blob, size, 0);
     Require(LibChd.Reads.SequenceEqual(new uint[] { 0 }), "Cache/empty read unexpectedly fetched");
     Read(blob, size, size); Read(blob, 2L * size, 1); Read(blob, size - 1, 2);
     Require(LibChd.Reads.SequenceEqual(new uint[] { 0, 1, 2, 0, 1 }), "Boundary/reverse seek failed");
     blob.Dispose(); blob.Dispose(); Require(LibChd.Closes == 1, "Handle closed more than once");
     break;
    case "error":
     LibChd.Fail = 1;
     try { Read(blob, size - 7, 15); throw new Exception("Provider error hidden"); }
     catch (IOException) { }
     Require(LibChd.Reads.SequenceEqual(new uint[] { 0, 1 }), "Failed hunk not requested");
     LibChd.Fail = uint.MaxValue; Read(blob, size, 15);
     Require(LibChd.Reads.SequenceEqual(new uint[] { 0, 1, 1 }), "Failed hunk was cached");
     break;
    default: throw new Exception("Unknown test case");
   }
   Console.WriteLine("Hunk fragmentation PASS (recording provider, no codec)");
   return 0;
  }
  catch (Exception error) { Console.Error.WriteLine(error.Message); return 1; }
 }
}
