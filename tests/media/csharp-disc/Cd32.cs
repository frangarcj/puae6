using System.Runtime.InteropServices;
using System.Security.Cryptography;
using System.Text;
using Puae6;
using BizHawk.Common;

internal static unsafe partial class Program
{
 static string CdHash(byte[] data) => Convert.ToHexString(SHA256.HashData(data));
 static void Cd32Waterbox(string[] args)
 {
  LoadRuntime(args[2]);
  string path = args[1], guestPath = args[3], sha = args[4];
  bool catalog = args[5].StartsWith("cd32catalog"), saving = args[5].EndsWith("save");
  string statePath = Path.Combine(args[0], "cd32.p6ws"), futurePath = statePath + ".future";
  if (saving && path == Path.Combine(args[0], "device.iso")) File.WriteAllBytes(path, new byte[512 * 2048]);
  byte[] rom = File.ReadAllBytes(args[7]).Concat(File.ReadAllBytes(args[6])).ToArray();
  using var disc = Mount(path);
  var source = new Puae6DiscSource(disc);
  string secondPath = Path.Combine(args[0], "catalog-b.iso");
  if (catalog) File.WriteAllBytes(secondPath, Enumerable.Repeat((byte)0x6d, 512 * 2048).ToArray());
  using var secondDisc = catalog ? Mount(secondPath) : null;
  var second = catalog ? new Puae6DiscSource(secondDisc) : null;
  var heaps = Puae6Core.Heaps(new Puae6SyncSettings { Machine = Puae6Machine.A1200 });
  StateIdentity Identity(byte[] id) => new(sha, new[] { heaps.SbrkHeapSizeKB, heaps.SealedHeapSizeKB, heaps.InvisibleHeapSizeKB,
   heaps.PlainHeapSizeKB, heaps.MmapHeapSizeKB }, new byte[32], SHA256.HashData(rom), id);
  var identity = Identity(source.ContentId);
  long reads = 0;
  bool fail = false, busy = false;
  Puae6Guest Open(Puae6DiscSource image, Puae6DiscSource other = null) {
   Puae6Guest g = null;
   P6CdHost cd;
   if (catalog) {
    var sources = new[] { image, other ?? second }; var descriptors = sources.Select(s => s.Descriptor()).ToArray();
    cd = new P6CdHost(descriptors, (index, at, data, count) => {
     ++reads;
     if (busy) { busy = false; Require(g.Cd.Change(g, 0, false) == P6.Busy); }
     return !fail && sources[index].ReadAt(at, data, 0, count);
    });
    descriptors[1].Id[0] ^= 1; // The host copied the caller's descriptor list.
   } else cd = new P6CdHost(image.Descriptor(), (at, data, count) => { ++reads; return image.ReadAt(at, data, 0, count); });
   g = Puae6Guest.Open(Path.GetDirectoryName(guestPath), Path.GetFileName(guestPath), sha, heaps, cd);
   try {
    Require(cd.Activate(g) == P6.BadState);
    if (catalog) Require(cd.Change(g, 0, false) == P6.BadState);
    using (g.Host.EnterExit()) fixed (byte* p = rom) {
     var call = new P6wInit { AbiVersion = P6.AbiVersion, StructSize = (uint)sizeof(P6wInit), Firmware = p, FirmwareBytes = (ulong)rom.Length };
     call.Config = new P6Config { AbiVersion = P6.AbiVersion, StructSize = (uint)sizeof(P6Config), Machine = 9,
      VideoStandard = P6.VideoPal, AudioRateHz = 48000, RandomSeed = 1, RtcEpochSeconds = 946684800 };
     if (g.Lib.p6w_init_machine((IntPtr)(&call)) != P6.Ok) throw new Exception(Marshal.PtrToStringAnsi((IntPtr)call.Error));
    }
    g.Seal();
    if (catalog) Require(cd.Change(g, 0, false) == P6.BadState);
    Require(cd.Activate(g) == P6.Ok); Require(cd.Activate(g) == P6.BadState);
    return g;
   } catch { g.Dispose(); throw; }
  }
  byte[] Save(Puae6Guest g) {
   using var raw = new MemoryStream(); using var writer = new BinaryWriter(raw, Encoding.UTF8, true);
   using (g.Host.EnterExit()) g.Host.SaveStateBinary(writer);
   using var outer = new MemoryStream(); using var wrapped = new BinaryWriter(outer);
   StateEnvelope.Write(wrapped, identity, raw.ToArray()); return outer.ToArray();
  }
  void Restore(Puae6Guest g, byte[] data, StateIdentity expected) {
   using var raw = new MemoryStream(data); using var reader = new BinaryReader(raw);
   g.Cd.Restore(g, reader, expected);
  }
  string Ram(Puae6Guest g) {
   var data = new byte[2 * 1024 * 1024];
   using (g.Host.EnterExit()) fixed (byte* p = data) g.Lib.p6w_memory_access(1, (IntPtr)p, 0, data.Length, 0);
   return CdHash(data);
  }
  string[] Run(Puae6Guest g, int count) {
   var rows = new string[count];
   for (int n = 0; n < count; ++n) {
    using var active = g.Host.EnterExit();
    var input = new P6Input { AbiVersion = P6.AbiVersion, StructSize = (uint)sizeof(P6Input) };
    var call = new P6wFrame { AbiVersion = P6.AbiVersion, StructSize = (uint)sizeof(P6wFrame) };
    if (g.Lib.p6w_frame_advance((IntPtr)(&input), P6.RenderVideo | P6.RenderAudio, (IntPtr)(&call)) != P6.Ok)
     throw new Exception(Marshal.PtrToStringAnsi((IntPtr)call.Error));
    var f = call.Frame;
    var video = new byte[f.Active.Width * f.Active.Height * 4];
    for (uint y = 0; y < f.Active.Height; ++y)
     Marshal.Copy((IntPtr)(f.Video + (y + f.Active.Y) * f.VideoStrideBytes + f.Active.X * 4), video,
      (int)(y * f.Active.Width * 4), (int)f.Active.Width * 4);
    var audio = new byte[f.AudioFrames * 4]; Marshal.Copy((IntPtr)f.Audio, audio, 0, audio.Length);
    rows[n] = $"{f.UnitIndex} {f.MasterTicks} {CdHash(video)} {CdHash(audio)}";
   }
   return rows;
  }
  if (catalog) {
   uint[] Status(Puae6Guest g) { var v = new uint[4]; Require(g.Cd.Status(g, v) == P6.Ok); return v; }
   void Data(Puae6Guest g, uint index, bool present) {
    var data = Enumerable.Repeat((byte)0xcc, 2448).ToArray(); long before = reads;
    using (g.Host.EnterExit()) fixed (byte* p = data) {
     int r = g.Lib.p6w_cd_read_sector(0, 2048, (IntPtr)p, 2448);
     if (present) Require(r == P6.Ok && data.Take(2048).All(b => b == (index == 0 ? 0 : 0x6d)));
     else Require(r == P6.BadState && before == reads && data.All(b => b == 0xcc));
    }
   }
   void Change(Puae6Guest g, uint index, bool present) {
    var previous = Status(g);
    if (previous[2] != 0 && previous[1] != index) Require(g.Cd.Change(g, previous[1], false) == P6.Ok);
    Require(g.Cd.Change(g, index, present) == P6.Ok);
    Require(Status(g).SequenceEqual(new uint[] { 2, index, present ? 1u : 0u, 1 })); Data(g, index, present);
   }
   using var g = Open(source);
   Require(g.Cd.Change(g, 1, true) == P6.BadState); // Never change owners through an inserted disc.
   Require(g.Cd.Change(g, 2, false) == P6.BadArgument);
   Run(g, 300); Require(reads > 0);
   foreach (string cut in new[] { "a", "b", "empty-b" }) {
    uint index = cut == "a" ? 0u : 1u; bool present = cut != "empty-b";
    string file = Path.Combine(args[0], "catalog-" + cut);
    if (saving) {
     Change(g, index, present); File.WriteAllBytes(file, Save(g));
     File.WriteAllLines(file + ".future", Run(g, 15).Concat(new[] { Ram(g) })); File.WriteAllBytes(file + ".end", Save(g));
    }
    void CheckCatalog(Puae6Guest target) {
     Change(target, 1, true); // A restore must override an actually inserted B.
     Restore(target, File.ReadAllBytes(file), identity);
     Require(Status(target).SequenceEqual(new uint[] { 2, index, present ? 1u : 0u, 1 })); Data(target, index, present);
     Require(Run(target, 15).Concat(new[] { Ram(target) }).SequenceEqual(File.ReadAllLines(file + ".future")));
     Require(Save(target).SequenceEqual(File.ReadAllBytes(file + ".end")));
    }
    CheckCatalog(g);
    using var freshA = Mount(path); using var freshB = Mount(secondPath);
    using var nextCatalog = Open(new Puae6DiscSource(freshA), new Puae6DiscSource(freshB)); CheckCatalog(nextCatalog);
   }
   Change(g, 0, true);
   var same = Save(g); Require(g.Cd.Change(g, 0, true) == P6.Ok); Require(Save(g).SequenceEqual(same));
   Change(g, 0, false); Change(g, 0, true); // Same-disc change retains the unit/open count.
   busy = true; Data(g, 0, true); Require(!busy);
   var sentinel = Enumerable.Repeat((byte)0xcc, 2448).ToArray(); fail = true;
   Require(g.Cd.ReadAt(g, 0, sentinel) == P6.Io && sentinel.All(b => b == 0xcc)); fail = false;
   var beforeReject = Save(g); long beforeReads = reads; int replacementReads = 0;
   LibPuae6.CdCatalogRead replacement = (_, _, _, _) => { ++replacementReads; return -P6.Io; };
   using (g.Host.EnterExit()) {
    var info = new[] { source.Descriptor(), second.Descriptor() };
    fixed (P6CdInfo* p = info) {
     Require(g.Lib.p6w_cd_catalog_bind((IntPtr)p, 0, replacement, 1) == P6.BadArgument);
     Require(g.Lib.p6w_cd_catalog_bind((IntPtr)p, 17, replacement, 1) == P6.BadArgument);
     Require(g.Lib.p6w_cd_change(0, 2) == P6.BadArgument);
    }
    for (int bad = 0; bad < 4; ++bad) {
     info = new[] { source.Descriptor(), second.Descriptor() };
     if (bad == 0) Array.Reverse(info);
     if (bad == 1) info[1].Id[0] ^= 1;
     if (bad == 2) info[1].Tracks[1]++;
     if (bad == 3) info[1] = info[0];
     fixed (P6CdInfo* p = info) Require(g.Lib.p6w_cd_catalog_bind((IntPtr)p, 2, replacement, 1) == (bad == 3 ? P6.BadArgument : P6.WrongMedia));
    }
   }
   Require(beforeReads == reads && beforeReject.SequenceEqual(Save(g))); Data(g, 0, true); Require(replacementReads == 0); GC.KeepAlive(replacement);
   // Loading a snapshot with a different internal catalog fails rebind and latches
   // the wrapper. This is not a public OtherMedia preflight claim (U5).
   using var wrongGuest = Open(second, source); var wrongState = Save(wrongGuest);
   try { Restore(g, wrongState, identity); throw new Exception("Mismatched internal catalog restored"); }
   catch (InvalidDataException) { Require(g.Cd.ReadAt(g, 0, sentinel) == P6.BadState && g.Cd.Change(g, 0, true) == P6.BadState); }
   Restore(g, beforeReject, identity); Data(g, 0, true); // A whole restore can recover.
   Console.WriteLine("Waterbox CD catalog A/B/absent/opens/exact future/new sandbox/new process/rebind transaction PASS");
   return;
  }
  using var guest = Open(source);
  byte[] saved;
  string[] expected;
  if (saving) {
   Run(guest, 300); Require(reads > 0);
   saved = Save(guest); File.WriteAllBytes(statePath, saved);
   var rows = Run(guest, 15); var ram = Ram(guest); var future = Save(guest);
   File.WriteAllBytes(futurePath + ".bin", future);
   expected = rows.Concat(new[] { ram }).ToArray();
   File.WriteAllLines(futurePath, expected);
   using (guest.Host.EnterExit()) Require(guest.Lib.p6w_reset() == P6.Ok);
   Run(guest, 1); // Actual reset must not retain Akiko's previous clock epoch.
  } else { saved = File.ReadAllBytes(statePath); expected = File.ReadAllLines(futurePath); }
  void Check(Puae6Guest g) {
   Restore(g, saved, identity);
   var rows = Run(g, 15); var ram = Ram(g); var future = Save(g);
   var actual = rows.Concat(new[] { ram }).ToArray();
   if (!actual.SequenceEqual(expected)) throw new Exception("CD32 restored future differs: " + string.Join(";", actual.Select((value, n) => (n, value, expected[n])).Where(p => p.value != p.Item3)));
   Require(future.SequenceEqual(File.ReadAllBytes(futurePath + ".bin")));
  }
  Check(guest);
  using var freshDisc = Mount(path); var fresh = new Puae6DiscSource(freshDisc);
  using var next = Open(fresh); Check(next);
  var before = Save(guest); var wrong = source.ContentId; wrong[0] ^= 1;
  try { Restore(guest, saved, Identity(wrong)); throw new Exception("Other CD accepted"); }
  catch (StateRejectedException e) { Require(e.Reason == StateRejection.OtherMedia); }
  Require(before.SequenceEqual(Save(guest)));
  using (guest.Host.EnterExit()) Require(guest.Lib.p6w_shutdown() == P6.Ok);
  Require(guest.Cd.ReadAt(guest, 0, new byte[2448]) == P6.BadState);
  Console.WriteLine($"Waterbox CD32 firmware/read/reset/snapshot/new owner PASS reads={reads}; game boot not asserted");
 }
}
