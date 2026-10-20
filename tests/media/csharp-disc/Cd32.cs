using System.Runtime.InteropServices;
using System.Security.Cryptography;
using System.Text;
using Puae6;
using BizHawk.Common;
using BizHawk.BizInvoke;

// Raw callback argument is used only for rejects, never an executable binding.
public abstract class CatalogRebindProbe
{
 [BizImport(LibPuae6.CC, EntryPoint = "p6w_cd_catalog_bind")]
 public abstract int Bind(IntPtr info, uint count, IntPtr callback, uint rebind);
 [BizImport(LibPuae6.CC, EntryPoint = "p6w_cd_catalog_bind_v2")]
 public abstract int BindV2(IntPtr info, uint count, IntPtr callback, uint rebind);
 [BizImport(LibPuae6.CC, EntryPoint = "p6w_cd_bind")]
 public abstract int Single(IntPtr info, IntPtr callback, uint rebind);
 [BizImport(LibPuae6.CC, EntryPoint = "p6w_cd_bind_v2")]
 public abstract int SingleV2(IntPtr info, IntPtr callback, uint rebind);
}

internal static unsafe partial class Program
{
 static string CdHash(byte[] data) => Convert.ToHexString(SHA256.HashData(data));
 // Numeric test TOC over a complete canonical ISO stream, not DiscSystem metadata.
 static P6CdInfoV2 RegionalDescriptor(Puae6DiscSource image)
 {
  Require(image.Tracks.Count==1&&image.Leadout==520);
  var descriptor=new P6CdInfoV2 { Version=2, Size=(uint)sizeof(P6CdInfoV2), Bytes=image.Length, Count=2, Leadout=520 };
  uint* t=descriptor.Tracks;
  t[2]=512; t[3]=1; t[4]=4; t[6]=1; t[9]=3;
  t+=12; t[0]=512; t[1]=514; t[2]=520; t[3]=1; t[4]=4; t[6]=1; t[7]=1; t[8]=2; t[9]=39;
  using var metadata=new MemoryStream(); using(var writer=new BinaryWriter(metadata,Encoding.ASCII,true)) {
   writer.Write(Encoding.ASCII.GetBytes("WBREG2")); writer.Write(image.ContentId); writer.Write(descriptor.Bytes);
   writer.Write(descriptor.Count); writer.Write(descriptor.Leadout);
   for(int n=0;n<24;++n) writer.Write(descriptor.Tracks[n]);
  }
  var id=SHA256.HashData(metadata.ToArray()); for(int n=0;n<32;++n) descriptor.Id[n]=id[n];
  return descriptor;
 }
 static void Cd32Waterbox(string[] args)
 {
  LoadRuntime(args[2]);
  string path = args[1], guestPath = args[3], sha = args[4];
  bool regional=args[5].StartsWith("cd32regional"), catalog=args[5].StartsWith("cd32catalog")||args[5].StartsWith("cd32regionalcatalog"), saving=args[5].EndsWith("save");
  string statePath = Path.Combine(args[0], "cd32.p6ws"), futurePath = statePath + ".future";
  if (saving && path == Path.Combine(args[0], "device.iso")) File.WriteAllBytes(path, new byte[(regional?520:512)*2048]);
  byte[] rom = File.ReadAllBytes(args[7]).Concat(File.ReadAllBytes(args[6])).ToArray();
  using var disc = Mount(path);
  var source = new Puae6DiscSource(disc);
  string secondPath = Path.Combine(args[0], "catalog-b.iso");
  if (catalog) File.WriteAllBytes(secondPath, Enumerable.Repeat((byte)0x6d, (regional?520:512)*2048).ToArray());
  using var secondDisc = catalog ? Mount(secondPath) : null;
  var second = catalog ? new Puae6DiscSource(secondDisc) : null;
  var heaps = Puae6Core.Heaps(new Puae6SyncSettings { Machine = Puae6Machine.A1200 });
  StateIdentity Identity(byte[] id) => new(sha, new[] { heaps.SbrkHeapSizeKB, heaps.SealedHeapSizeKB, heaps.InvisibleHeapSizeKB,
   heaps.PlainHeapSizeKB, heaps.MmapHeapSizeKB }, new byte[32], SHA256.HashData(rom), id);
  var regionalId=new byte[32];
  if(regional) { var info=RegionalDescriptor(source); for(int n=0;n<32;++n) regionalId[n]=info.Id[n]; }
  var identity = Identity(regional?regionalId:source.ContentId);
  long reads = 0;
  bool fail = false, busy = false;
  Puae6Guest Open(Puae6DiscSource image, Puae6DiscSource other = null, bool legacy = false) {
   Puae6Guest g = null;
   P6CdHost cd;
   if (catalog) {
    var sources = new[] { image, other ?? second };
    bool Read(uint index, ulong at, byte[] data, int count) {
     ++reads;
     if (busy) { busy = false; Require(g.Cd.Change(g, 0, false) == P6.Busy); }
     return !fail && sources[index].ReadAt(at, data, 0, count);
    }
    if(regional&&!legacy) { var descriptors=sources.Select(RegionalDescriptor).ToArray(); cd=new P6CdHost(descriptors,Read); descriptors[1].Id[0]^=1; }
    else { var descriptors=sources.Select(s=>s.Descriptor()).ToArray(); cd=new P6CdHost(descriptors,Read); descriptors[1].Id[0]^=1; }
   } else {
    bool Read(ulong at, byte[] data, int count) { ++reads; return !fail&&image.ReadAt(at,data,0,count); }
    cd=regional&&!legacy?new P6CdHost(RegionalDescriptor(image),Read):new P6CdHost(image.Descriptor(),Read);
   }
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
  void Regions(Puae6Guest g, uint index) {
   if(!regional) return;
   var image=index==0?source:second; var expected=new byte[2448]; var output=new byte[2464];
   using var active=g.Host.EnterExit();
   for(uint lba=512;lba<520;++lba) foreach(uint view in new uint[] {2048,2352,2448}) {
    bool synth=lba==512||lba>=518, audio=lba<514, reject=(view==2048&&audio)||(view==2448&&synth);
    Array.Fill(output,(byte)0xcc); long before=reads;
    fixed(byte* p=output) Require(g.Lib.p6w_cd_read_sector(lba,view,(IntPtr)(p+8),view)==(reject?P6.Unsupported:P6.Ok));
    Require(output.Take(8).Concat(output.Skip(8+(int)view)).All(b=>b==0xcc));
    if(reject) Require(output.All(b=>b==0xcc)&&reads==before);
    else {
     if(lba==512) Array.Clear(expected); // Synthetic audio INDEX00.
     else Require((synth?source:image).ReadSector((int)lba,(int)view,expected)); // Source A supplies independent zero-data EDC/PQ.
     Require(output.AsSpan(8,(int)view).SequenceEqual(expected.AsSpan(0,(int)view))&&reads==before+(synth?0:1));
    }
   }
  }
  void RefuseV2OnV1() {
   using var legacy=Open(source,null,true); var saved=Save(legacy); long before=reads;
   using(legacy.Host.EnterExit()) {
    var raw=BizInvoker.GetInvoker<CatalogRebindProbe>(legacy.Host,legacy.Host,CallingConventionAdapters.MakeWaterboxDepartureOnly(legacy.Host));
    var descriptors=catalog?new[] {RegionalDescriptor(source),RegionalDescriptor(second)}:new[] {RegionalDescriptor(source)};
    fixed(P6CdInfoV2* p=descriptors) Require((catalog?raw.BindV2((IntPtr)p,2,new IntPtr(1),1):raw.SingleV2((IntPtr)p,new IntPtr(1),1))==P6.BadState);
   }
   Require(reads==before&&saved.SequenceEqual(Save(legacy)));
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
    Require(Status(g).SequenceEqual(new uint[] { 2, index, present ? 1u : 0u, 1 })); Data(g, index, present); if(present) Regions(g,index);
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
     Require(Status(target).SequenceEqual(new uint[] { 2, index, present ? 1u : 0u, 1 })); Data(target, index, present); if(present) Regions(target,index);
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
   var beforeReject = Save(g); long beforeReads = reads;
   var replacement = new IntPtr(1); // Non-null, so bounds failures cannot be hidden by a null callback check.
   using (g.Host.EnterExit()) {
    var raw = BizInvoker.GetInvoker<CatalogRebindProbe>(g.Host, g.Host, CallingConventionAdapters.MakeWaterboxDepartureOnly(g.Host));
    var info = new[] { source.Descriptor(), second.Descriptor() };
    if(!regional) fixed (P6CdInfo* p = info) {
     Require(raw.Bind((IntPtr)p, 0, replacement, 1) == P6.BadArgument);
     Require(raw.Bind((IntPtr)p, 17, replacement, 1) == P6.BadArgument);
     Require(g.Lib.p6w_cd_change(0, 2) == P6.BadArgument);
    }
    for (int bad = 0; !regional&&bad < 4; ++bad) {
     info = new[] { source.Descriptor(), second.Descriptor() };
     if (bad == 0) Array.Reverse(info);
     if (bad == 1) info[1].Id[0] ^= 1;
     if (bad == 2) info[1].Tracks[1]++;
     if (bad == 3) info[1] = info[0];
     fixed (P6CdInfo* p = info) Require(raw.Bind((IntPtr)p, 2, replacement, 1) == (bad == 3 ? P6.BadArgument : P6.WrongMedia));
    }
    if(regional) {
     fixed(P6CdInfo* p=info) Require(raw.Bind((IntPtr)p,2,replacement,1)==P6.BadState);
     for(int bad=0;bad<7;++bad) {
      var descriptors=new[] {RegionalDescriptor(source),RegionalDescriptor(second)};
      if(bad==0) Array.Reverse(descriptors); if(bad==1) descriptors[1].Id[0]^=1;
      if(bad==2) descriptors[1].Tracks[23]=2; if(bad==3) descriptors[1]=descriptors[0];
      if(bad==4) descriptors[1].Tracks[21]|=64;
      fixed(P6CdInfoV2* p=descriptors) Require(raw.BindV2((IntPtr)p,bad==5?0u:bad==6?17u:2u,replacement,1)==
       (bad==3||bad>=4?P6.BadArgument:P6.WrongMedia));
     }
    }
   }
   // Captured BSS includes the callback pointer: check it before a data read
   // could try to execute a wrongly published sentinel.
   Require(beforeReads == reads && beforeReject.SequenceEqual(Save(g)));
   Data(g, 0, true); Regions(g,0); if(regional) RefuseV2OnV1();
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
  Regions(guest,0);
  if(regional) {
   var beforeReject=Save(guest); long beforeReads=reads;
   using(guest.Host.EnterExit()) {
    var raw=BizInvoker.GetInvoker<CatalogRebindProbe>(guest.Host,guest.Host,CallingConventionAdapters.MakeWaterboxDepartureOnly(guest.Host));
    var legacy=source.Descriptor(); Require(raw.Single((IntPtr)(&legacy),new IntPtr(1),1)==P6.BadState);
    var bad=RegionalDescriptor(source); bad.Tracks[23]=2; Require(guest.Cd.BindV2(guest,true,bad)==P6.WrongMedia);
    bad=RegionalDescriptor(source); --bad.Size; Require(guest.Cd.BindV2(guest,true,bad)==P6.BadArgument);
    bad=RegionalDescriptor(source); Require(guest.Lib.p6w_cd_bind_v2((IntPtr)(&bad),null,1)==P6.BadArgument);
   }
   Require(beforeReads==reads&&beforeReject.SequenceEqual(Save(guest))); Regions(guest,0); RefuseV2OnV1();
  }
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
   Regions(g,0);
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
