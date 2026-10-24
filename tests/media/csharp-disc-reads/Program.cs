using System.Text;
using BizHawk.Emulation.DiscSystem;

static class Program
{
 static void Require(bool condition) { if (!condition) throw new Exception("Disc read assertion failed"); }
 static void Truncated(Action read)
 {
  try { read(); } catch (EndOfStreamException) { return; }
  throw new Exception("Physical EOF was hidden");
 }
 static void Check(IBlob blob, byte[] expected)
 {
  var data = new byte[expected.Length + 4];
  Require(blob.Read(0, data, 2, expected.Length) == expected.Length);
  Require(data.AsSpan(2, expected.Length).SequenceEqual(expected));
  Require(blob.Read(expected.Length, data, 1, 0) == 0);
  Require(blob.Read(expected.Length - 1, data, 0, 1) == 1 && data[0] == expected[^1]);
  var padded = new Blob_ZeroPadAdapter(blob, expected.Length);
  Array.Fill(data, (byte)0xcc);
  Require(padded.Read(expected.Length - 2, data, 1, 4) == 4);
  Require(data[0] == 0xcc && data[1] == expected[^2] && data[2] == expected[^1]);
  Require(data[3] == 0 && data[4] == 0 && data[5] == 0xcc);
  Require(padded.Read(expected.Length, data, 0, 4) == 4);
  Require(data.AsSpan(0, 4).SequenceEqual(new byte[4]));
 }
 static void Raw(string path)
 {
  var bytes = Enumerable.Range(0, 32).Select(i => (byte)i).ToArray();
  File.WriteAllBytes(path, bytes);
  using (var blob = new Blob_RawFile { PhysicalPath = path }) Check(blob, bytes);
  // Length is captured before the backing file loses bytes, before any buffered read.
  using var truncated = new Blob_RawFile { PhysicalPath = path };
  using (var file = File.OpenWrite(path)) file.SetLength(31);
  Truncated(() => new Blob_ZeroPadAdapter(truncated, 32).Read(0, new byte[40], 0, 40));
 }
 static void Wave(string path)
 {
  var bytes = Enumerable.Range(1, 32).Select(i => (byte)i).ToArray();
  using (var file = File.Create(path))
  using (var writer = new BinaryWriter(file, Encoding.ASCII))
  {
   writer.Write(Encoding.ASCII.GetBytes("RIFF")); writer.Write(36 + bytes.Length);
   writer.Write(Encoding.ASCII.GetBytes("WAVEfmt ")); writer.Write(16);
   writer.Write((ushort)1); writer.Write((ushort)2); writer.Write(44100);
   writer.Write(176400); writer.Write((ushort)4); writer.Write((ushort)16);
   writer.Write(Encoding.ASCII.GetBytes("data")); writer.Write(bytes.Length); writer.Write(bytes);
  }
  // A real file stream returning small successful reads exercises the retry loop.
  using var fileStream = new ChunkedFile(path);
  using var blob = new Blob_WaveFile(); blob.Load(fileStream);
  Require(blob.Length == bytes.Length); Check(blob, bytes);
  fileStream.SetLength(44 + bytes.Length - 1);
  Truncated(() => new Blob_ZeroPadAdapter(blob, bytes.Length).Read(0, new byte[40], 0, 40));
 }
 sealed class ChunkedFile(string path) : FileStream(path, FileMode.Open, FileAccess.ReadWrite)
 {
  public override int Read(byte[] buffer, int offset, int count) => base.Read(buffer, offset, Math.Min(count, 3));
 }
 static void Main(string[] args)
 {
  Raw(Path.Combine(args[0], "disc.bin")); Wave(Path.Combine(args[0], "disc.wav"));
  Console.WriteLine("BIN/WAV exact reads, truncation and logical padding PASS");
 }
}
