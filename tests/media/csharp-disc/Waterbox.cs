using System.Text;
using Puae6;
using BizHawk.Common;
using BizHawk.Emulation.Cores.Waterbox;
internal static unsafe partial class Program
{
 static void Waterbox(string[] args)
 {
  LoadRuntime(args[2]);
  string path = args[1], guestPath = args[3], sha = args[4], statePath = path + ".p6ws";
  if (args[5] == "save") {
   File.WriteAllBytes(Path.Combine(args[0], "disc.iso"), new byte[520 * 2048]);
   File.WriteAllBytes(Path.Combine(args[0], "data.bin"), new byte[4 * 2048]);
   Wave(Path.Combine(args[0], "audio.wav"));
   File.WriteAllText(Path.Combine(args[0], "stored.cue"), "FILE \"data.bin\" BINARY\n TRACK 01 MODE1/2048\n INDEX 01 00:00:00\nFILE \"audio.wav\" WAVE\n TRACK 02 AUDIO\n INDEX 00 00:00:00\n INDEX 01 00:00:02\n");
  }
  using var disc = Mount(path);
  var source = new Puae6DiscSource(disc);
  bool fail = false;
  var cd = new P6CdHost(source.Descriptor(), (at, data, count) => fail ? throw new IOException("provider failed") : source.ReadAt(at, data, 0, count));
  var heaps = Puae6Core.Heaps(new Puae6SyncSettings());
  StateIdentity Identity(byte[] id) => new(sha, new[] { heaps.SbrkHeapSizeKB, heaps.SealedHeapSizeKB, heaps.InvisibleHeapSizeKB, heaps.PlainHeapSizeKB, heaps.MmapHeapSizeKB }, new byte[32], new byte[32], id);
  var identity = Identity(source.ContentId);
  Puae6Guest Open(P6CdHost binding) => Puae6Guest.Open(Path.GetDirectoryName(guestPath), Path.GetFileName(guestPath), sha, heaps, binding);
  using var guest = Open(cd);
  var expected = new byte[2448]; Require(source.ReadAt(2439, expected, 0, expected.Length));
  var output = new byte[2448];
  void Check(Puae6Guest g, P6CdHost c) { Require(c.ReadAt(g, 2439, output) == P6.Ok && output.SequenceEqual(expected)); }
  byte[] Save(Puae6Guest g) {
   using var raw = new MemoryStream(); using var writer = new BinaryWriter(raw, Encoding.UTF8, true);
   using (g.Host.EnterExit()) g.Host.SaveStateBinary(writer);
   using var envelope = new MemoryStream(); using var wrapped = new BinaryWriter(envelope);
   StateEnvelope.Write(wrapped, identity, raw.ToArray()); return envelope.ToArray();
  }
  void Restore(Puae6Guest g, P6CdHost c, byte[] data, StateIdentity id) {
   using var raw = new MemoryStream(data); using var reader = new BinaryReader(raw); c.Restore(g, reader, id);
  }
  Check(guest, cd);
  using (guest.Host.EnterExit()) {
   foreach (uint view in new uint[] { 2048, 2352, 2448 }) {
    fixed (byte* p = output) Require(guest.Lib.p6w_cd_read_sector(0, view, (IntPtr)p, 2448) == P6.Ok);
    var wanted = new byte[view]; Require(source.ReadSector(0, (int)view, wanted)); Require(output.Take((int)view).SequenceEqual(wanted));
   }
   var bad = source.Descriptor(); bad.Size--;
   Require(cd.Bind(guest, true, bad) == P6.BadArgument);
   bad = source.Descriptor(); bad.Id[0] ^= 1; Require(cd.Bind(guest, true, bad) == P6.WrongMedia);
   bad = source.Descriptor(); bad.Tracks[1]++; Require(cd.Bind(guest, true, bad) == P6.WrongMedia);
   bad = source.Descriptor(); Require(guest.Lib.p6w_cd_bind((IntPtr)(&bad), null, 1) == P6.BadArgument);
   fixed (byte* p = output) {
    Array.Fill(output, (byte)0xcc);
    Require(guest.Lib.p6w_cd_read_at(0, (IntPtr)p, 2449) == P6.BadArgument);
    Require(guest.Lib.p6w_cd_read_at(ulong.MaxValue, (IntPtr)p, 1) == P6.BadArgument);
    fail = true; Require(cd.ReadAt(guest, 2439, output) == P6.Io && output.All(b => b == 0xcc)); fail = false;
   }
  }
  Check(guest, cd); guest.Host.Seal();
  var saved = args[5] == "save" ? Save(guest) : File.ReadAllBytes(statePath);
  Restore(guest, cd, saved, identity); Check(guest, cd);
  var wrong = source.ContentId; wrong[0] ^= 1;
  try { Restore(guest, cd, saved, Identity(wrong)); throw new Exception("Other CD accepted"); }
  catch (StateRejectedException e) { Require(e.Reason == StateRejection.OtherMedia); }
  Check(guest, cd);
  using var newDisc = Mount(path); var fresh = new Puae6DiscSource(newDisc);
  var binding = new P6CdHost(fresh.Descriptor(), (at, data, count) => fresh.ReadAt(at, data, 0, count));
  using var next = Open(binding); next.Host.Seal(); Restore(next, binding, saved, identity); Check(next, binding);
  // A valid envelope around a sandbox without a bound CD must fault the wrapper.
  using var empty = Open(null); empty.Host.Seal();
  try { Restore(guest, cd, Save(empty), identity); throw new Exception("Unbound snapshot accepted"); }
  catch (Exception e) when (e is InvalidDataException || e is InvalidOperationException) { Require(cd.ReadAt(guest, 2439, output) == P6.BadState); }
  Restore(guest, cd, saved, identity); Check(guest, cd);
  if (args[5] == "save") File.WriteAllBytes(statePath, saved);
  Console.WriteLine("Waterbox CD callback/layout/snapshot/rebind PASS");
 }
}
