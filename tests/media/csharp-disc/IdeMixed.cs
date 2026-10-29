using System.Runtime.InteropServices;
using System.Security.Cryptography;
using System.Text;
using BizHawk.BizInvoke;
using BizHawk.Common;
using BizHawk.Emulation.Cores.Waterbox;
using Puae6;

// T84e4 exercises the private guest lifecycle directly. The public core cannot
// select the new controller until T84f; this harness nevertheless uses the same
// WaterboxHost, guest ABI and DiscSystem provider as that core will use.
internal static unsafe partial class Program
{
 sealed class IdeMixedGuest : IDisposable
 {
  public readonly WaterboxHost Host;
  public readonly LibPuae6 Lib;
  public readonly LibPuae6.CdCatalogRead Callback;
  readonly P6CdInfo[] descriptors;
  readonly Puae6DiscSource[] sources;
  public long Reads { get; private set; }
  public uint LargestRead { get; private set; }
  public long RefusedReads { get; private set; }

  public IdeMixedGuest(string guestPath, string sha, WaterboxOptions heaps, P6CdInfo[] catalog,
                       Puae6DiscSource[] providers, byte[] firmware, byte[][] media, uint[] types)
  {
   Require(catalog.Length == 2 && providers.Length == catalog.Length && media.Length == types.Length);
   Require(Convert.ToHexString(SHA256.HashData(File.ReadAllBytes(guestPath))).Equals(sha, StringComparison.OrdinalIgnoreCase));
   descriptors = (P6CdInfo[])catalog.Clone();
   sources = providers;
   Callback = Read;
   Host = new WaterboxHost(new WaterboxOptions {
    Path = Path.GetDirectoryName(guestPath), Filename = Path.GetFileName(guestPath),
    SbrkHeapSizeKB = heaps.SbrkHeapSizeKB, SealedHeapSizeKB = heaps.SealedHeapSizeKB,
    InvisibleHeapSizeKB = heaps.InvisibleHeapSizeKB, PlainHeapSizeKB = heaps.PlainHeapSizeKB,
    MmapHeapSizeKB = heaps.MmapHeapSizeKB,
   });
   try {
    using (Host.EnterExit()) {
     var adapter = CallingConventionAdapters.MakeWaterbox(new Delegate[] { Callback }, Host);
     Lib = BizInvoker.GetInvoker<LibPuae6>(Host, Host, adapter);
     Bind(false);
     Init(firmware, media, types);
     Host.Seal();
     int activated = Lib.p6w_cd_activate();
     if (activated != P6.Ok) {
      uint* status = stackalloc uint[4]; int statusResult = Lib.p6w_cd_status((IntPtr)status, 4);
      throw new Exception($"IDE CD activation {P6.ResultName(activated)}; status={P6.ResultName(statusResult)} " +
       $"{status[0]},{status[1]},{status[2]},{status[3]}");
     }
    }
    Require(Status().SequenceEqual(new uint[] { 2, 0, 1, 0 }));
   } catch { Host.Dispose(); throw; }
  }

  int Read(uint ordinal, ulong at, IntPtr destination, uint count)
  {
   try {
    if (ordinal >= sources.Length || count > 2448 || at + count < at) { ++RefusedReads; return -P6.Io; }
    var bytes = new byte[count];
    if (!sources[ordinal].ReadAt(at, bytes, 0, (int)count)) { ++RefusedReads; return -P6.Io; }
    Host.CopyFromExternal(bytes, destination); ++Reads; LargestRead = Math.Max(LargestRead, count); return (int)count;
   } catch { ++RefusedReads; return -P6.Io; }
  }

  void Bind(bool rebind)
  {
   fixed (P6CdInfo* p = descriptors)
    Require(Lib.p6w_cd_catalog_bind((IntPtr)p, (uint)descriptors.Length, Callback, rebind ? 1u : 0u) == P6.Ok);
  }

  void Init(byte[] firmware, byte[][] media, uint[] types)
  {
   var pins = media.Select(bytes => GCHandle.Alloc(bytes, GCHandleType.Pinned)).ToArray();
   try {
    fixed (byte* rom = firmware) {
     var call = new P6wInit { AbiVersion = P6.AbiVersion, StructSize = (uint)sizeof(P6wInit),
      Firmware = rom, FirmwareBytes = (ulong)firmware.Length, MediaCount = (uint)media.Length };
     call.Config = new P6Config { AbiVersion = P6.AbiVersion, StructSize = (uint)sizeof(P6Config),
      Machine = P6.MachineA600, VideoStandard = P6.VideoPal, AudioRateHz = 48000,
      Flags = P6.ConfigFloppyWrites, RandomSeed = 1, RtcEpochSeconds = 946684800, FloppyDrives = 1 };
     // T84e1 deliberately kept ABI v1 and consumed reserved byte 71. The
     // managed name arrives with T84f; this backend probe writes its specified
     // wire value directly and asserts the native layout above/below it.
     ((byte*)(&call.Config))[71] = 4; // P6_CD_CONTROLLER_IDE
     for (int n = 0; n < media.Length; ++n) {
      var entry = call.Media.At(n); entry->Type = types[n]; entry->Data = (byte*)pins[n].AddrOfPinnedObject();
      entry->Bytes = (ulong)media[n].Length; var id = SHA256.HashData(media[n]);
      for (int b = 0; b < id.Length; ++b) entry->ContentId[b] = id[b];
     }
     int result = Lib.p6w_init_machine((IntPtr)(&call));
     if (result != P6.Ok) throw new Exception($"mixed IDE init {P6.ResultName(result)}: {Marshal.PtrToStringUTF8((IntPtr)call.Error)}");
    }
   } finally { foreach (var pin in pins) pin.Free(); }
  }

  public uint[] Status()
  {
   var values = new uint[4]; using var active = Host.EnterExit();
   fixed (uint* p = values) Require(Lib.p6w_cd_status((IntPtr)p, 4) == P6.Ok); return values;
  }

  public void Change(uint ordinal, bool present)
  {
   using var active = Host.EnterExit(); Require(Lib.p6w_cd_change(ordinal, present ? 1u : 0u) == P6.Ok);
  }

  public byte[] ReadSector(uint lba, uint view)
  {
   var bytes = new byte[view]; using var active = Host.EnterExit();
   fixed (byte* p = bytes) Require(Lib.p6w_cd_read_sector(lba, view, (IntPtr)p, view) == P6.Ok); return bytes;
  }

  public byte[] Export(uint index, int bytes)
  {
   var output = new byte[bytes]; using var active = Host.EnterExit(); fixed (byte* p = output) {
    var call = new P6wMediaExport { AbiVersion = P6.AbiVersion, StructSize = (uint)sizeof(P6wMediaExport),
     MediaIndex = index, Buffer = p, Capacity = (ulong)output.Length };
    Require(Lib.p6w_media_export((IntPtr)(&call)) == P6.Ok && call.Result == P6.Ok && call.Written == (ulong)output.Length);
   }
   return output;
  }

  public string[] Run(int count)
  {
   var rows = new string[count];
   for (int n = 0; n < count; ++n) using (Host.EnterExit()) {
    var input = new P6Input { AbiVersion = P6.AbiVersion, StructSize = (uint)sizeof(P6Input) };
    var call = new P6wFrame { AbiVersion = P6.AbiVersion, StructSize = (uint)sizeof(P6wFrame) };
    int result = Lib.p6w_frame_advance((IntPtr)(&input), P6.RenderVideo | P6.RenderAudio | P6.RenderCdAudioVolumeValid | (100u << P6.RenderCdAudioVolumeShift), (IntPtr)(&call));
    if (result != P6.Ok) throw new Exception($"mixed IDE frame {P6.ResultName(result)}: {Marshal.PtrToStringUTF8((IntPtr)call.Error)}");
    var f = call.Frame; var video = new byte[f.Active.Width * f.Active.Height * 4];
    for (uint y = 0; y < f.Active.Height; ++y)
     Marshal.Copy((IntPtr)(f.Video + (y + f.Active.Y) * f.VideoStrideBytes + f.Active.X * 4), video,
      (int)(y * f.Active.Width * 4), (int)f.Active.Width * 4);
    var audio = new byte[f.AudioFrames * 4]; Marshal.Copy((IntPtr)f.Audio, audio, 0, audio.Length);
    rows[n] = $"{f.UnitIndex}:{f.MasterTicks}:{Convert.ToHexString(SHA256.HashData(video))}:{Convert.ToHexString(SHA256.HashData(audio))}";
   }
   return rows;
  }

  public byte[] Save()
  {
   using var data = new MemoryStream(); using var writer = new BinaryWriter(data, Encoding.UTF8, true);
   using (Host.EnterExit()) Host.SaveStateBinary(writer); return data.ToArray();
  }

  public void Load(byte[] state)
  {
   using var data = new MemoryStream(state, false); using var reader = new BinaryReader(data);
   using var active = Host.EnterExit(); Host.LoadStateBinary(reader); Bind(true); Require(data.Position == data.Length);
  }

  public void Dispose()
  {
   Require(RefusedReads == 0 && LargestRead <= 2448 && Reads < 4096);
   using (Host.EnterExit()) Require(Lib.p6w_shutdown() == P6.Ok);
   Host.Dispose();
  }
 }

 static void IdeMixedWaterbox(string[] args)
 {
  LoadRuntime(args[2]);
  Require(args.Length == 11);
  string work = args[0], firstPath = args[1], guestPath = args[3], sha = args[4], mode = args[5];
  string secondPath = Path.Combine(work, "ide-mixed-b.iso");
  if (mode.EndsWith("save")) {
   File.WriteAllBytes(firstPath, new byte[512 * 2048]);
   File.WriteAllBytes(secondPath, Enumerable.Repeat((byte)0x6d, 512 * 2048).ToArray());
  }
  using var firstDisc = Mount(firstPath); using var secondDisc = Mount(secondPath);
  var sources = new[] { new Puae6DiscSource(firstDisc), new Puae6DiscSource(secondDisc) };
  var catalog = sources.Select(source => source.Descriptor()).ToArray();
  byte[] firmware = File.ReadAllBytes(args[6]);
  byte[][] media = args.Skip(7).Select(File.ReadAllBytes).ToArray();
  uint[] types = { P6.MediaAdf, P6.MediaHdf, P6.MediaHdf, P6.MediaHdf };
  var mediaHashes = media.Select(SHA256.HashData).Select(Convert.ToHexString).ToArray();
  var settings = new Puae6SyncSettings { Machine = Puae6Machine.A600, FloppyWrites = true };
  var heaps = Puae6Core.Heaps(settings, media.Skip(1).Select(bytes => (long)bytes.Length));
  IdeMixedGuest Open() => new(guestPath, sha, heaps, catalog, sources, firmware, media, types);
  string activePath = Path.Combine(work, "ide-mixed-active.p6ws");
  string absentPath = Path.Combine(work, "ide-mixed-absent.p6ws");
  string futurePath = Path.Combine(work, "ide-mixed.future");

  string[] Baselines(IdeMixedGuest guest) => media.Select((bytes, n) => Convert.ToHexString(SHA256.HashData(guest.Export((uint)n, bytes.Length)))).ToArray();
  string[] Future(IdeMixedGuest guest) {
   long before = guest.Reads; var rows = guest.Run(10).ToList();
   rows.Add("status:" + string.Join(',', guest.Status())); rows.Add("media:" + string.Join(',', Baselines(guest)));
   rows.Add("reads:" + (guest.Reads - before)); return rows.ToArray();
  }
  void CheckActive(IdeMixedGuest guest, byte[] state, string[] future) {
   guest.Load(state); Require(Future(guest).SequenceEqual(future));
  }
  void CheckAbsent(IdeMixedGuest guest, byte[] state) {
   guest.Load(state); Require(guest.Status().SequenceEqual(new uint[] { 2, 1, 0, 1 }));
   Require(Baselines(guest).SequenceEqual(mediaHashes));
   guest.Change(1, true); Require(guest.Status().SequenceEqual(new uint[] { 2, 1, 1, 1 }));
   Require(guest.ReadSector(0, 2048).All(value => value == 0x6d));
   Require(Baselines(guest).SequenceEqual(mediaHashes));
  }

  using var original = Open();
  original.Run(56); // T84e3's first frame with an active deterministic CDDA stream.
  byte[] active; string[] future;
  if (mode.EndsWith("save")) {
   active = original.Save(); File.WriteAllBytes(activePath, active);
   future = Future(original); File.WriteAllLines(futurePath, future);
  } else { active = File.ReadAllBytes(activePath); future = File.ReadAllLines(futurePath); }
  CheckActive(original, active, future);

  original.Load(active); original.Change(0, false); original.Change(1, false);
  byte[] absent = mode.EndsWith("save") ? original.Save() : File.ReadAllBytes(absentPath);
  if (mode.EndsWith("save")) File.WriteAllBytes(absentPath, absent);
  CheckAbsent(original, absent);
  using (var fresh = Open()) { CheckActive(fresh, active, future); CheckAbsent(fresh, absent); }
  Require(media.Select(SHA256.HashData).Select(Convert.ToHexString).SequenceEqual(mediaHashes));
  Console.WriteLine("Waterbox IDE mixed CD+ADF+2HDF+volume local/new-sandbox/new-process restore PASS");
 }
}
