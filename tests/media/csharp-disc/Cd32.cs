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
  bool saving = args[5] == "cd32save";
  string statePath = Path.Combine(args[0], "cd32.p6ws"), futurePath = statePath + ".future";
  if (saving && path == Path.Combine(args[0], "device.iso")) File.WriteAllBytes(path, new byte[512 * 2048]);
  byte[] rom = File.ReadAllBytes(args[7]).Concat(File.ReadAllBytes(args[6])).ToArray();
  using var disc = Mount(path);
  var source = new Puae6DiscSource(disc);
  var heaps = Puae6Core.Heaps(new Puae6SyncSettings { Machine = Puae6Machine.A1200 });
  StateIdentity Identity(byte[] id) => new(sha, new[] { heaps.SbrkHeapSizeKB, heaps.SealedHeapSizeKB, heaps.InvisibleHeapSizeKB,
   heaps.PlainHeapSizeKB, heaps.MmapHeapSizeKB }, new byte[32], SHA256.HashData(rom), id);
  var identity = Identity(source.ContentId);
  long reads = 0;
  Puae6Guest Open(Puae6DiscSource image) {
   var cd = new P6CdHost(image.Descriptor(), (at, data, count) => { ++reads; return image.ReadAt(at, data, 0, count); });
   var g = Puae6Guest.Open(Path.GetDirectoryName(guestPath), Path.GetFileName(guestPath), sha, heaps, cd);
   try {
    Require(cd.Activate(g) == P6.BadState);
    using (g.Host.EnterExit()) fixed (byte* p = rom) {
     var call = new P6wInit { AbiVersion = P6.AbiVersion, StructSize = (uint)sizeof(P6wInit), Firmware = p, FirmwareBytes = (ulong)rom.Length };
     call.Config = new P6Config { AbiVersion = P6.AbiVersion, StructSize = (uint)sizeof(P6Config), Machine = 9,
      VideoStandard = P6.VideoPal, AudioRateHz = 48000, RandomSeed = 1, RtcEpochSeconds = 946684800 };
     if (g.Lib.p6w_init_machine((IntPtr)(&call)) != P6.Ok) throw new Exception(Marshal.PtrToStringAnsi((IntPtr)call.Error));
    }
    g.Seal(); Require(cd.Activate(g) == P6.Ok); Require(cd.Activate(g) == P6.BadState);
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
