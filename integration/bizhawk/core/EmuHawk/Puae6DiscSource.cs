using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Security.Cryptography;
using System.Text;
using BizHawk.Emulation.DiscSystem;

namespace Puae6;

// Borrowed Disc lifetime; one emulation thread. No guest callback is installed here.
internal sealed class Puae6DiscSource
{
 internal readonly struct Track
 {
  internal readonly int Index0, Index1, End, Mode, Control;
  internal Track(int zero, int one, int end, int mode, int control)
   { Index0 = zero; Index1 = one; End = end; Mode = mode; Control = control; }
  internal ulong Offset => (ulong)Index0 * 2448;
 }
 DiscSectorReader reader;
 readonly byte[] sector = new byte[2448], staging = new byte[2448], identity;
 readonly Track[] tracks;
 internal IReadOnlyList<Track> Tracks { get; }
 internal int Leadout { get; }
 internal ulong Length => (ulong)Leadout * 2448;
 internal byte[] ContentId => (byte[])identity.Clone();

 internal Puae6DiscSource(Disc disc)
 {
  if (disc == null || disc.Sessions.Count != 2) throw new InvalidDataException("A single CD session is required");
  var session = disc.Session1;
  int count = session.InformationTrackCount;
  Leadout = session.LeadoutLBA;
  if (count < 1 || count > 99 || Leadout <= 0 || session.TOC.FirstRecordedTrackNumber != 1
   || session.TOC.LastRecordedTrackNumber != count) throw new InvalidDataException("Invalid CD TOC");
  reader = new DiscSectorReader(disc) { Policy = { DeinterleavedSubcode = true, DeterministicClearBuffer = true } };
  tracks = new Track[count];
  var q = new byte[12];
  for (int i = 0; i < count; ++i)
  {
   var t = session.Tracks[i + 1];
   int lower = i == 0 ? 0 : tracks[i - 1].Index1;
   if (t.Number != i + 1 || t.LBA < lower || t.LBA >= Leadout || t.Mode < 0 || t.Mode > 2
    || (int)t.Control < 0 || (int)t.Control > 15 || (t.Mode == 0) != (((int)t.Control & 4) == 0))
    throw new InvalidDataException("Invalid CD track");
   int zero = t.LBA, bcdTrack = ((i + 1) / 10 << 4) | (i + 1) % 10;
   for (int lba = t.LBA - 1; lba >= lower; --lba)
   {
    Array.Clear(q, 0, q.Length);
    if (reader.ReadLBA_SubQ(lba, q, 0) != 12 || (q[0] & 15) != 1)
     throw new InvalidDataException("Unusable INDEX00 subchannel Q");
    if (q[1] != bcdTrack || q[2] != 0) break;
    zero = lba;
   }
   tracks[i] = new Track(zero, t.LBA, Leadout, t.Mode, (int)t.Control);
   if (i == 0 && zero != 0) throw new InvalidDataException("CD layout must begin at LBA0");
   if (i > 0)
   {
    var previous = tracks[i - 1];
    if (zero <= previous.Index1) throw new InvalidDataException("Overlapping CD tracks");
    tracks[i - 1] = new Track(previous.Index0, previous.Index1, zero, previous.Mode, previous.Control);
   }
  }
  Tracks = Array.AsReadOnly(tracks);
  using var metadata = new MemoryStream();
  using (var writer = new BinaryWriter(metadata, Encoding.ASCII, true))
  {
   writer.Write(Encoding.ASCII.GetBytes("P6CD2448\0")); writer.Write(1); // Canonical policy version.
   writer.Write(Leadout); writer.Write(count);
   foreach (var t in tracks)
    { writer.Write(t.Index0); writer.Write(t.Index1); writer.Write(t.End); writer.Write(t.Mode); writer.Write(t.Control); }
  }
  using var sha = SHA256.Create();
  var bytes = metadata.ToArray(); sha.TransformBlock(bytes, 0, bytes.Length, bytes, 0);
  for (int lba = 0; lba < Leadout; ++lba)
   { Sector(lba); sha.TransformBlock(sector, 0, sector.Length, sector, 0); }
  sha.TransformFinalBlock(Array.Empty<byte>(), 0, 0); identity = sha.Hash;
 }
 void Sector(int lba)
 {
  Array.Clear(sector, 0, sector.Length);
  if (reader.ReadLBA_2448(lba, sector, 0) != 2448) throw new EndOfStreamException("Missing CD sector");
 }
 static bool Destination(byte[] output, int offset, int count) => output != null && count >= 0
  && count <= 2448 && offset >= 0 && offset <= output.Length - count;
 internal bool ReadAt(ulong offset, byte[] output, int destination, int count)
 {
  if (!Destination(output, destination, count) || offset > Length || (ulong)count > Length - offset) return false;
  try
  {
   int done = 0;
   while (done < count)
   {
    int within = (int)(offset % 2448), take = Math.Min(count - done, 2448 - within);
    Sector((int)(offset / 2448)); Buffer.BlockCopy(sector, within, staging, done, take);
    done += take; offset += (ulong)take;
   }
   Buffer.BlockCopy(staging, 0, output, destination, count); return true;
  }
  catch (IOException) { return false; }
  catch (InvalidOperationException) { return false; }
 }
 internal bool ReadSector(int lba, int view, byte[] output, int destination = 0)
 {
  if (lba < 0 || lba >= Leadout || (view != 2048 && view != 2352 && view != 2448)
   || !Destination(output, destination, view)) return false;
  try
  {
   Sector(lba);
   if (view == 2048 && (sector[15] != 1 || tracks.First(t => lba < t.End).Mode != 1)) return false;
   Buffer.BlockCopy(sector, view == 2048 ? 16 : 0, output, destination, view); return true;
  }
  catch (IOException) { return false; }
  catch (InvalidOperationException) { return false; }
 }
 internal bool Rebind(Disc replacement)
 {
  try
  {
   var candidate = new Puae6DiscSource(replacement);
   if (Length != candidate.Length || !identity.SequenceEqual(candidate.identity)) return false;
   reader = candidate.reader; return true;
  }
  catch (IOException) { return false; }
  catch (InvalidOperationException) { return false; }
  catch (ArgumentException) { return false; }
  catch (NotSupportedException) { return false; }
 }
}
