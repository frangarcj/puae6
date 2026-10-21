using System.Buffers.Binary;
using System.Runtime.InteropServices;
using System.Security.Cryptography;
using System.Text;
using BizHawk.BizInvoke;
using BizHawk.Common;
using Puae6;

internal static unsafe partial class Program
{
 static void CdtvWaterbox(string[] args)
 {
  Require(args.Length == 9 && args[5] is "cdtvsave" or "cdtvload");
  LoadRuntime(args[2]);
  string directory = args[0], path = args[1], guestPath = args[3], sha = args[4];
  bool saving = args[5] == "cdtvsave";
  byte[] rom = File.ReadAllBytes(args[7]).Concat(File.ReadAllBytes(args[6])).ToArray();
  byte[] nonce = Convert.FromHexString(args[8]), signature = Encoding.ASCII.GetBytes("P6CDTV_BOOT_v1!\0");
  Require(rom.Length == 524288 && nonce.Length == 16 && signature.Length == 16);
  var config = new P6Config { AbiVersion = P6.AbiVersion, StructSize = (uint)sizeof(P6Config), Machine = 10,
   VideoStandard = P6.VideoPal, AudioRateHz = 48000, RandomSeed = 1, RtcEpochSeconds = 946684800 };
  var hashConfig = config;
  var configId = SHA256.HashData(new ReadOnlySpan<byte>(&hashConfig, sizeof(P6Config)));
  var heaps = Puae6Core.Heaps(); // Existing stock heap budget; no public CDTV enum.
  StateIdentity Identity(byte[] id) => new(sha, new[] { heaps.SbrkHeapSizeKB, heaps.SealedHeapSizeKB,
   heaps.InvisibleHeapSizeKB, heaps.PlainHeapSizeKB, heaps.MmapHeapSizeKB }, configId, SHA256.HashData(rom), id);
  using var disc = Mount(path);
  var source = new Puae6DiscSource(disc); var identity = Identity(source.ContentId);
  long reads = 0;
  Puae6Guest Open(Puae6DiscSource image) {
   var cd = new P6CdHost(image.Descriptor(), (at, output, count) => { ++reads; return image.ReadAt(at, output, 0, count); });
   var g = Puae6Guest.Open(Path.GetDirectoryName(guestPath), Path.GetFileName(guestPath), sha, heaps, cd);
   try {
    Require(cd.Activate(g) == P6.BadState);
    using (g.Host.EnterExit()) fixed (byte* p = rom) {
     var call = new P6wInit { AbiVersion = P6.AbiVersion, StructSize = (uint)sizeof(P6wInit),
      Firmware = p, FirmwareBytes = (ulong)rom.Length, Config = config };
     int result = g.Lib.p6w_init_machine((IntPtr)(&call));
     if (result != P6.Ok) throw new Exception(Marshal.PtrToStringAnsi((IntPtr)call.Error));
    }
    g.Seal(); Require(cd.Activate(g) == P6.Ok && cd.Activate(g) == P6.BadState);
    return g;
   } catch { g.Dispose(); throw; }
  }
  byte[] Save(Puae6Guest g, StateIdentity id) {
   using var raw = new MemoryStream(); using var writer = new BinaryWriter(raw, Encoding.UTF8, true);
   using (g.Host.EnterExit()) g.Host.SaveStateBinary(writer);
   using var outer = new MemoryStream(); using var wrapped = new BinaryWriter(outer);
   StateEnvelope.Write(wrapped, id, raw.ToArray()); return outer.ToArray();
  }
  void Restore(Puae6Guest g, byte[] data, StateIdentity id) {
   using var stream = new MemoryStream(data); using var reader = new BinaryReader(stream);
   g.Cd.Restore(g, reader, id); Require(stream.Position == stream.Length);
  }
  byte[] Ram(Puae6Guest g) {
   var ram = new byte[1024 * 1024];
   using var active = g.Host.EnterExit();
   fixed (byte* p = ram) g.Lib.p6w_memory_access(P6.RegionChipRam, (IntPtr)p, 0, ram.Length, 0);
   Require(g.Lib.p6w_memory_refused() == 0); return ram;
  }
  uint Live(Puae6Guest g) {
   var ram = Ram(g); uint counter = 0; int found = 0;
   for (int at = 0; at + 36 <= ram.Length; at += 2) {
    if (!ram.AsSpan(at, 16).SequenceEqual(signature) || !ram.AsSpan(at + 16, 16).SequenceEqual(nonce)) continue;
    uint value = BinaryPrimitives.ReadUInt32BigEndian(ram.AsSpan(at + 32, 4));
    if (value != 0) { ++found; counter = value; }
   }
   Require(found <= 1); return counter;
  }
  string Step(Puae6Guest g, bool capture) {
   using var active = g.Host.EnterExit();
   var input = new P6Input { AbiVersion = P6.AbiVersion, StructSize = (uint)sizeof(P6Input) };
   var call = new P6wFrame { AbiVersion = P6.AbiVersion, StructSize = (uint)sizeof(P6wFrame) };
   if (g.Lib.p6w_frame_advance((IntPtr)(&input), P6.RenderVideo | P6.RenderAudio, (IntPtr)(&call)) != P6.Ok)
    throw new Exception(Marshal.PtrToStringAnsi((IntPtr)call.Error));
   var f = call.Frame; Require(f.UnitIndex > 0 && f.MasterTicks > 0 && f.AudioFrames > 0 && f.Video != null);
   if (!capture) return "";
   var video = new byte[checked((int)f.VideoBytes)]; var pcm = new byte[checked((int)f.AudioFrames * 4)];
   Marshal.Copy((IntPtr)f.Video, video, 0, video.Length); Marshal.Copy((IntPtr)f.Audio, pcm, 0, pcm.Length);
   // All scalar frame metadata, every video/PCM byte and all Chip RAM; no guest pointers.
   using var row = new MemoryStream(); using var writer = new BinaryWriter(row);
   foreach (ulong value in new ulong[] { f.AbiVersion, f.StructSize, f.UnitIndex, f.MasterTicks, f.VideoBytes, f.VideoFormat, f.VideoWidth, f.VideoHeight,
    f.VideoStrideBytes, f.Active.X, f.Active.Y, f.Active.Width, f.Active.Height, f.AspectNum, f.AspectDen,
    f.CadenceNum, f.CadenceDen, f.FieldParity, f.Flags, f.AudioFrames, f.Reserved[0], f.Reserved[1] }) writer.Write(value);
   writer.Write(video); writer.Write(pcm); writer.Write(Ram(g));
   return CdHash(row.ToArray());
  }
  string[] Future(Puae6Guest g) {
   uint before = Live(g); Require(before > 0); long count = reads;
   var rows = Enumerable.Range(0, 16).Select(_ => Step(g, true)).ToList();
   Require(Live(g) > before); rows.Add((reads - count).ToString(System.Globalization.CultureInfo.InvariantCulture));
   return rows.ToArray();
  }
  void RejectOperations(Puae6Guest g, Puae6DiscSource image) {
   var before = Save(g, identity); long count = reads;
   Require(g.Cd.Change(g, 0, false) == P6.Unsupported && g.Cd.Change(g, 0, true) == P6.Unsupported);
   using (g.Host.EnterExit()) {
    var raw = BizInvoker.GetInvoker<CatalogRebindProbe>(g.Host, g.Host, CallingConventionAdapters.MakeWaterboxDepartureOnly(g.Host));
    var descriptor = image.Descriptor();
    Require(raw.Bind((IntPtr)(&descriptor), 1, new IntPtr(1), 1) == P6.Unsupported);
   }
   Require(count == reads && before.SequenceEqual(Save(g, identity)));
  }
  void Close(Puae6Guest g) {
   using (g.Host.EnterExit()) Require(g.Lib.p6w_shutdown() == P6.Ok);
   long count = reads;
   var status = new uint[4]; Require(g.Cd.Status(g, status) == P6.BadState);
   var output = Enumerable.Repeat((byte)0xcc, 16).ToArray();
   Require(g.Cd.ReadAt(g, 0, output) == P6.BadState && output.All(b => b == 0xcc));
   Require(g.Cd.Activate(g) == P6.BadState && count == reads);
  }
  string cutPath = Path.Combine(directory, "cdtv.cut"), endPath = Path.Combine(directory, "cdtv.end");
  using var guest = Open(source);
  if (saving) {
   uint marker = 0; int units = 0;
   for (; units < 1000 && marker == 0; ++units) { Step(guest, false); if (units % 10 == 9) marker = Live(guest); }
   if (marker == 0 || reads == 0) throw new Exception($"CDTV nonce/Delay missing after {units} units and {reads} CD reads");
   RejectOperations(guest, source);
   File.WriteAllBytes(cutPath, Save(guest, identity));
   File.WriteAllLines(cutPath + ".future", Future(guest)); File.WriteAllBytes(endPath, Save(guest, identity));
   Console.WriteLine($"CDTV ISO/HUNK executed nonce and Delay by unit {units}");
  }
  var cut = File.ReadAllBytes(cutPath);
  void Check(Puae6Guest g, Puae6DiscSource image) {
   Restore(g, cut, identity); // A newly opened sandbox has not run a single frame.
   Require(Save(g, identity).SequenceEqual(cut) && Live(g) > 0);
   RejectOperations(g, image);
   Require(Future(g).SequenceEqual(File.ReadAllLines(cutPath + ".future")));
   Require(Save(g, identity).SequenceEqual(File.ReadAllBytes(endPath)));
  }
  Check(guest, source);
  using (var freshDisc = Mount(path)) {
   var fresh = new Puae6DiscSource(freshDisc); using var next = Open(fresh);
   Check(next, fresh); Close(next);
  }
  using (var otherDisc = Mount(Path.Combine(directory, "other.iso"))) {
   var other = new Puae6DiscSource(otherDisc); using var wrong = Open(other);
   var id = Identity(other.ContentId); var before = Save(wrong, id); long count = reads;
   try { Restore(wrong, cut, id); throw new Exception("Other nonce accepted"); }
   catch (StateRejectedException e) { Require(e.Reason == StateRejection.OtherMedia); }
   Require(before.SequenceEqual(Save(wrong, id)) && count == reads); Close(wrong);
  }
  Close(guest);
  Console.WriteLine("Private CDTV Waterbox nonce/Delay local/fresh sandbox/cold process/full future/rejects/teardown PASS");
 }
}
