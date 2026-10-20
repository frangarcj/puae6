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
  internal readonly int Index0, Index1, End, Mode, Control, PregapMode;
  internal Track(int zero, int one, int end, int mode, int control, int pregapMode = -1)
   { Index0 = zero; Index1 = one; End = end; Mode = mode; Control = control; PregapMode = pregapMode < 0 ? mode : pregapMode; }
  internal ulong Offset => (ulong)Index0 * 2448;
 }
 DiscSectorReader reader;
 readonly byte[] sector = new byte[2448], staging = new byte[2448], identity;
 readonly Track[] tracks;
 readonly P6CdTrackV2[] regional;
 internal IReadOnlyList<Track> Tracks { get; }
 internal int Leadout { get; }
 internal ulong Length => (ulong)Leadout * 2448;
 internal byte[] ContentId => (byte[])identity.Clone();
 internal unsafe P6CdInfo Descriptor()
 {
  if (regional is not null) throw new InvalidOperationException("Typed regions require CD descriptor V2");
  var result = new P6CdInfo { Version = 1, Size = (uint)sizeof(P6CdInfo), Bytes = Length, Count = (uint)tracks.Length, Leadout = (uint)Leadout };
  for (int i = 0; i < 32; ++i) result.Id[i] = identity[i];
  for (int i = 0; i < tracks.Length; ++i) {
   var t = tracks[i]; uint* p = result.Tracks + i * 6;
   p[0] = (uint)t.Index0; p[1] = (uint)t.Index1; p[2] = (uint)t.End; p[3] = (uint)t.Mode; p[4] = (uint)t.Control;
  }
  return result;
 }
 internal unsafe P6CdInfoV2 DescriptorV2()
 {
  if (regional is null) throw new InvalidOperationException("Typed CD regions are required");
  var result = new P6CdInfoV2 { Version = 2, Size = (uint)sizeof(P6CdInfoV2), Bytes = Length, Count = (uint)tracks.Length, Leadout = (uint)Leadout };
  for (int i = 0; i < 32; ++i) result.Id[i] = identity[i];
  var records = (P6CdTrackV2*)result.Tracks;
  for (int i = 0; i < regional.Length; ++i) records[i] = regional[i];
  return result;
 }
 P6CdTrackV2[] RegionalTracks(Disc disc)
 {
  if (Leadout > int.MaxValue - 150) throw new InvalidDataException("CD regions exceed the LBA range");
  var result = new P6CdTrackV2[tracks.Length];
  int position = 0, cursor = 0;
  for (int n = 0; n < result.Length; ++n)
  {
   int start = cursor, one = -1, mode = 0, control = 0, post = 0, pgMode = 0, pgControl = 0;
   while (position < disc.Regions.Count && disc.Regions[position].Track == n + 1)
   {
    var r = disc.Regions[position++];
    if (r.Start != cursor || r.End <= r.Start || r.End > Leadout || (uint)r.Type > 7 || (uint)r.SubType > 2
     || r.Index < 0 || r.Index > 2 || r.Mode < 0 || r.Mode > 2 || r.Control < 0 || r.Control > 15
     || (r.Mode == 0) != ((r.Control & 4) == 0)) throw new InvalidDataException("Invalid CD region");
    if (r.Index == 0)
    {
     if (one >= 0 || (cursor != start && (r.Mode != pgMode || r.Control != pgControl)))
      throw new InvalidDataException("Unrepresentable CD pregap");
     pgMode = r.Mode; pgControl = r.Control;
    }
    else if (r.Index == 1)
    {
     if (one >= 0) throw new InvalidDataException("Duplicate CD program region");
     one = r.Start; mode = r.Mode; control = r.Control;
    }
    else
    {
     if (one < 0 || post != 0 || r.Mode != mode || r.Control != control)
      throw new InvalidDataException("Invalid CD postgap");
     post = r.End - r.Start;
    }
    cursor = r.End;
   }
   var t = disc.Session1.Tracks[n + 1];
   if (one < 0 || t.Number != n + 1 || t.LBA != one || t.Mode != mode || (int)t.Control != control)
    throw new InvalidDataException("CD regions disagree with TOC");
   // Every LBA and all P-W bytes already exist in the transported wire2448.
   // Physical Synthetic/SubType provenance must not request resynthesis here.
   bool pregap = one > start;
   result[n] = new P6CdTrackV2 { Index0 = (uint)start, Index1 = (uint)one, End = (uint)cursor,
    Mode = (uint)mode, Control = (uint)control, StoredPregap = pregap ? 1u : 0u, Postgap = (uint)post,
    Flags = 3u | (pregap ? 4u : 0u) | (post != 0 ? 56u : 0u), PregapMode = (uint)pgMode, PregapControl = (uint)pgControl };
   tracks[n] = new Track(start, one, cursor, mode, control, pgMode);
  }
  if (position != disc.Regions.Count || cursor != Leadout) throw new InvalidDataException("Incomplete CD regions");
  return result;
 }
 static void WriteRegional(BinaryWriter writer, P6CdTrackV2 t)
 {
  writer.Write(t.Index0); writer.Write(t.Index1); writer.Write(t.End); writer.Write(t.Mode); writer.Write(t.Control); writer.Write(t.Reserved);
  writer.Write(t.StoredPregap); writer.Write(t.SyntheticPregap); writer.Write(t.Postgap); writer.Write(t.Flags); writer.Write(t.PregapMode); writer.Write(t.PregapControl);
 }

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
  regional = disc.Regions.Count == 0 ? null : RegionalTracks(disc);
  var q = new byte[12];
  for (int i = 0; regional is null && i < count; ++i)
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
   writer.Write(Encoding.ASCII.GetBytes("P6CD2448\0")); writer.Write(regional is null ? 1 : 2);
   writer.Write(Leadout); writer.Write(count);
   if (regional is not null) foreach (var t in regional) WriteRegional(writer, t);
   else foreach (var t in tracks)
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
   if (view == 2048) {
    var t = tracks.First(t => lba < t.End);
    int mode = regional is not null && lba < t.Index1 ? t.PregapMode : t.Mode;
    if (sector[15] != 1 || mode != 1) return false;
   }
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
  catch (InvalidDataException) { return false; }
  catch (IOException) { return false; }
  catch (InvalidOperationException) { return false; }
  catch (ArgumentException) { return false; }
  catch (NotSupportedException) { return false; }
 }
}
