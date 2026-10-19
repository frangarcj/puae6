using System.Text;
using BizHawk.Emulation.DiscSystem;
using Puae6;

internal static unsafe partial class Program
{
 static void Require(bool value) { if (!value) throw new Exception("CD source assertion failed"); }
 static Disc Mount(string path)
 {
  var job = new DiscMountJob(path); job.Run();
  return job.OUT_Disc ?? throw new Exception("DiscSystem failed to mount fixture");
 }
 static void Refused(byte[] output, Func<bool> read)
 {
  Array.Fill(output, (byte)0xcc); Require(!read()); Require(output.All(b => b == 0xcc));
 }
 static void Iso(string directory)
 {
  string path = Path.Combine(directory, "game.iso"), same = Path.Combine(directory, "same.iso"), changed = Path.Combine(directory, "tail.iso");
  var bytes = Enumerable.Range(0, 520 * 2048).Select(i => (byte)(i * 13)).ToArray();
  File.WriteAllBytes(path, bytes); File.WriteAllBytes(same, bytes);
  bytes[^1] ^= 1; File.WriteAllBytes(changed, bytes);
  using var disc = Mount(path); using var identical = Mount(same); using var tail = Mount(changed);
  var source = new Puae6DiscSource(disc); var output = new byte[2449];
  Require(source.Tracks.Count == 1 && source.Tracks[0].Index0 == 0 && source.Leadout == 520);
  Require(source.ReadSector(0, 2048, output, 1) && output.AsSpan(1, 2048).SequenceEqual(bytes.AsSpan(0, 2048)));
  var direct = new DiscSectorReader(disc) { Policy = { DeinterleavedSubcode = true } };
  var raw = new byte[4896]; Require(direct.ReadLBA_2448(0, raw, 0) == 2448 && direct.ReadLBA_2448(1, raw, 2448) == 2448);
  Require(source.ReadAt(2439, output, 1, 2448) && output.AsSpan(1, 2448).SequenceEqual(raw.AsSpan(2439, 2448)));
  Require(source.ReadSector(0, 2352, output) && output.AsSpan(0, 2352).SequenceEqual(raw.AsSpan(0, 2352)));
  Require(source.ReadAt(source.Length, Array.Empty<byte>(), 0, 0));
  Refused(output, () => source.ReadAt(source.Length, output, 0, 1));
  Refused(output, () => source.ReadAt(ulong.MaxValue, output, 0, 1));
  Refused(output, () => source.ReadAt(0, output, 0, 2449));
  Refused(output, () => source.ReadAt(0, output, -1, 1));
  Refused(output, () => source.ReadSector(-1, 2448, output));
  Refused(output, () => source.ReadSector(520, 2448, output));
  Require(!source.Rebind(tail)); // Difference lies beyond OldHash's first 512 sectors.
  Require(source.ReadSector(519, 2048, output) && output[2047] != bytes[^1]);
  File.WriteAllBytes(Path.ChangeExtension(same, ".sbi"), new byte[] { 83, 66, 73, 0, 0, 2, 0, 2, 1, 0x23, 0x45 });
  using var subchannel = Mount(same);
  Require(!source.Rebind(subchannel)); // Same data/TOC; only the effective subchannel changed.
  subchannel.Dispose();
  Require(source.Rebind(identical)); disc.Dispose();
  Require(source.ReadSector(0, 2448, output) && output.AsSpan(0, 2448).SequenceEqual(raw.AsSpan(0, 2448)));
  var id = source.ContentId; id[0] ^= 255; Require(!id.SequenceEqual(source.ContentId));
  tail.Dispose(); using (var file = File.OpenWrite(changed)) file.SetLength(0);
  Require(!source.Rebind(tail)); // Failed candidate leaves the preceding binding usable.
  Require(source.ReadSector(0, 2048, output));
  identical.Dispose(); using (var file = File.OpenWrite(same)) file.SetLength(0);
  Refused(output, () => source.ReadAt(2439, output, 0, 2448));
 }
 static byte[] Wave(string path)
 {
  var payload = Enumerable.Range(0, 2352 * 4).Select(i => (byte)(i + i / 2352 * 17)).ToArray();
  using var file = File.Create(path); using var w = new BinaryWriter(file, Encoding.ASCII);
  w.Write(Encoding.ASCII.GetBytes("RIFF")); w.Write(36 + payload.Length); w.Write(Encoding.ASCII.GetBytes("WAVEfmt "));
  w.Write(16); w.Write((ushort)1); w.Write((ushort)2); w.Write(44100); w.Write(176400);
  w.Write((ushort)4); w.Write((ushort)16); w.Write(Encoding.ASCII.GetBytes("data")); w.Write(payload.Length); w.Write(payload);
  return payload;
 }
 static void Cue(string directory)
 {
  File.WriteAllBytes(Path.Combine(directory, "data.bin"), new byte[2048 * 4]);
  var payload = Wave(Path.Combine(directory, "audio.wav"));
  string prefix = "FILE \"data.bin\" BINARY\n TRACK 01 MODE1/2048\n INDEX 01 00:00:00\nFILE \"audio.wav\" WAVE\n TRACK 02 AUDIO\n";
  string path = Path.Combine(directory, "stored.cue"), other = Path.Combine(directory, "other.cue"), synthetic = Path.Combine(directory, "synthetic.cue");
  File.WriteAllText(path, prefix + " INDEX 00 00:00:00\n INDEX 01 00:00:02\n");
  File.WriteAllText(other, prefix + " INDEX 00 00:00:00\n INDEX 01 00:00:01\n");
  File.WriteAllText(synthetic, prefix + " PREGAP 00:00:03\n INDEX 01 00:00:00\n");
  using var disc = Mount(path); using var moved = Mount(other); using var gap = Mount(synthetic);
  var source = new Puae6DiscSource(disc); var track = source.Tracks[1]; var output = new byte[2448];
  Require(track.Index0 == 4 && track.Index1 == 6 && track.End == 8 && track.Offset == 4UL * 2448);
  Require(source.ReadSector(4, 2352, output) && output.AsSpan(0, 2352).SequenceEqual(payload.AsSpan(0, 2352)));
  Require(source.ReadSector(6, 2352, output) && output.AsSpan(0, 2352).SequenceEqual(payload.AsSpan(4704, 2352)));
  Refused(output, () => source.ReadSector(6, 2048, output));
  Require(!source.Rebind(moved) && source.ReadSector(6, 2352, output)); // Same length, changed TOC.
  var synthesized = new Puae6DiscSource(gap);
  Require(synthesized.Tracks[1].Index0 == 4 && synthesized.Tracks[1].Index1 == 7);
  Require(synthesized.ReadSector(4, 2352, output) && output.AsSpan(0, 2352).ToArray().All(b => b == 0));
  var mode2 = new byte[2352]; mode2[15] = 2; mode2[18] = 0x20;
  File.WriteAllBytes(Path.Combine(directory, "mode2.bin"), mode2);
  string modePath = Path.Combine(directory, "mode2.cue");
  File.WriteAllText(modePath, "FILE \"mode2.bin\" BINARY\n TRACK 01 MODE2/2352\n INDEX 01 00:00:00\n");
  using var modeDisc = Mount(modePath); var raw = new Puae6DiscSource(modeDisc);
  Require(raw.ReadSector(0, 2352, output) && output.AsSpan(0, 2352).SequenceEqual(mode2));
  Refused(output, () => raw.ReadSector(0, 2048, output));
 }
 static void Main(string[] args) {
  if (args.Length > 1) { if (args[5].StartsWith("cd32")) Cd32Waterbox(args); else Waterbox(args); return; }
  Iso(args[0]); Cue(args[0]); Console.WriteLine("DiscSystem ISO/CUE offset, identity and rebind PASS");
 }
}
