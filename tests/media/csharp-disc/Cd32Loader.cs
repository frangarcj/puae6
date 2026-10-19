using System.Reflection;
using System.Security.Cryptography;
using BizHawk.Emulation.Common;
using BizHawk.Emulation.Cores;
using BizHawk.Emulation.DiscSystem;
using Puae6;

internal static unsafe partial class Program
{
 static void Require(bool ok) { if (!ok) throw new Exception("Public CD32 assertion failed"); }
 sealed class DiscAsset(Disc disc) : IDiscAsset
 {
  public Disc DiscData => disc;
  public DiscType DiscType => default;
  public string DiscName { get; set; } = "CD32";
 }
 sealed class Firmware(byte[] main, byte[] ext, bool combined) : ICoreFileProvider
 {
  internal readonly List<string> Asked = new();
  public string GetRetroSaveRAMDirectory(string p) => throw new NotSupportedException();
  public string GetRetroSystemPath(string p) => throw new NotSupportedException();
  public string GetUserPath(string s, bool temp) => throw new NotSupportedException();
  public string GetPalettesPath(string s) => throw new NotSupportedException();
  public byte[] GetFirmware(FirmwareID id, string msg = null) => GetFirmwareOrThrow(id, msg);
  public (byte[], GameInfo) GetFirmwareWithGameInfoOrThrow(FirmwareID id, string msg = null) => (GetFirmwareOrThrow(id, msg), null);
  public byte[] GetFirmwareOrThrow(FirmwareID id, string msg = null)
  {
   Asked.Add(id.Firmware);
   return id.Firmware switch { "CD32" => combined ? ext.Concat(main).ToArray() : main,
    "CD32ext" => ext, _ => throw new Exception("Wrong firmware request: " + id) };
  }
 }
 sealed class Idle(ControllerDefinition definition) : IController
 {
  public ControllerDefinition Definition => definition;
  public IReadOnlyCollection<(string Name, int Strength)> GetHapticsSnapshot() => Array.Empty<(string, int)>();
  public bool IsPressed(string name) => false;
  public int AxisValue(string name) => definition.Axes.TryGetValue(name, out var axis) ? axis.Neutral : 0;
  public void SetHapticChannelStrength(string name, int strength) { }
 }
 static Disc Mount(string path)
 {
  var job = new DiscMountJob(path); job.Run();
  return job.OUT_Disc ?? throw new Exception("Cannot mount CD");
 }
 static byte[] Save(Puae6Core core)
 {
  using var data = new MemoryStream(); using var writer = new BinaryWriter(data);
  core.SaveStateBinary(writer); return data.ToArray();
 }
 static void Restore(Puae6Core core, byte[] data)
 {
  using var stream = new MemoryStream(data); using var reader = new BinaryReader(stream); core.LoadStateBinary(reader);
 }
 static string Hash(byte[] data) => Convert.ToHexString(SHA256.HashData(data));
 static string[] Run(Puae6Core core, int count)
 {
  var controller = new Idle(core.ControllerDefinition); var rows = new List<string>();
  for (int i = 0; i < count; ++i) {
   Require(core.FrameAdvance(controller, true, true));
   core.GetSamplesSync(out var samples, out int frames);
   var audio = new byte[frames * 4]; Buffer.BlockCopy(samples, 0, audio, 0, audio.Length);
   var video = core.GetVideoBuffer(); var pixels = new byte[core.BufferWidth * core.BufferHeight * 4];
   Buffer.BlockCopy(video, 0, pixels, 0, pixels.Length);
   rows.Add($"{core.Frame} {core.CycleCount} {Hash(pixels)} {Hash(audio)}");
  }
  return rows.ToArray();
 }
 static void Main(string[] args)
 {
  LoadRuntime(args[2]);
  string statePath = Path.Combine(args[0], "public-cd32.state");
  byte[] main = File.ReadAllBytes(args[6]), ext = File.ReadAllBytes(args[7]);
  using var disc = Mount(args[1]);
  long reads = 0;
  var seed = Enumerable.Range(0, 1024).Select(i => (byte)(i * 17 + 9)).ToArray();
  Puae6Core Open(Disc image, bool combined = false, byte[] imported = null) {
   var firmware = new Firmware(main, ext, combined);
   var comm = new CoreComm(_ => { }, (_, _) => { }, firmware, CoreComm.CorePreferencesFlags.None, null);
   var original = new Puae6SyncSettings { ControllerPort2 = Puae6Port2.Cd32 };
   var lp = new CoreLoadParameters<Puae6Settings, Puae6SyncSettings> { Comm = comm, SyncSettings = original };
   lp.Discs.Add(new DiscAsset(image));
   var method = typeof(Puae6Core).GetMethod("ParametersFrom", BindingFlags.NonPublic | BindingFlags.Static);
   Puae6Parameters Resolve() => (Puae6Parameters)method.Invoke(null, new object[] { lp });
   lp.Discs.Add(lp.Discs[0]);
   try { Resolve(); throw new Exception("Multiple CDs accepted"); }
   catch (TargetInvocationException e) when (e.InnerException is NotSupportedException) { Require(firmware.Asked.Count == 0); }
   lp.Discs.RemoveAt(1);
   var p = Resolve();
   Require(original.Machine == Puae6Machine.A500 && p.SyncSettings.Machine == Puae6Machine.CD32);
   Require(firmware.Asked.SequenceEqual(combined ? new[] { "CD32" } : new[] { "CD32", "CD32ext" }));
   p.GuestDirectory = Path.GetDirectoryName(args[3]); p.GuestFileName = Path.GetFileName(args[3]); p.PinnedGuestSha256 = args[4];
   var reader = p.ReadCd; p.ReadCd = (at, data, count) => { ++reads; return reader(at, data, count); };
   var core = new Puae6Core(comm, p);
   Require(core.GetSyncSettings().Machine == Puae6Machine.CD32);
   Require(!core.ControllerDefinition.BoolButtons.Contains(Controllers.NextDrive));
   Require(ReferenceEquals(core.ServiceProvider.GetService<ISaveRam>(), core));
   Require(!core.SaveRamModified && core.CloneSaveRam(false).SequenceEqual(new byte[1024]));
   var initialConfig = core.Identity.ConfigHash;
   Save(core); // Real TAS may SAVE before importing the same initial SRAM again.
   core.StoreSaveRam(new byte[1024]); Require(core.Identity.ConfigHash.SequenceEqual(initialConfig));
   foreach (int size in new[] { 0, 1023, 1025 }) {
    try { core.StoreSaveRam(new byte[size]); throw new Exception("Wrong SRAM length accepted"); }
    catch (SaveRamRejectedException e) { Require(e.Reason == SaveRamRejection.BadLength); }
   }
   Require(core.CloneSaveRam(false).SequenceEqual(new byte[1024]));
   var nv = core.ServiceProvider.GetService<IMemoryDomains>()["CD32 NVRAM"];
   Require(nv.Size == 1024 && nv.WordSize == 1 && nv.Writable);
   nv.PokeByte(13, 0x51); Require(core.SaveRamModified && core.CloneSaveRam(false)[13] == 0x51);
   Require(core.SaveRamModified); core.CloneSaveRam(); Require(!core.SaveRamModified);
   core.ReplayInProgress = true;
   Require(core.CloneSaveRam() == null && !core.SaveRamModified);
   try { core.StoreSaveRam(seed); throw new Exception("Replay SRAM import accepted"); }
   catch (SaveRamRejectedException e) { Require(e.Reason == SaveRamRejection.TooLate); }
   core.ReplayInProgress = false;
   core.StoreSaveRam(imported ?? seed);
   Require(!core.SaveRamModified && core.CloneSaveRam(false).SequenceEqual(imported ?? seed));
   Require(core.Identity.ConfigHash.SequenceEqual(initialConfig) == (imported ?? seed).All(b => b == 0));
   return core;
  }
  using var core = Open(disc);
  byte[] state;
  string[] future;
  if (args[5] == "publicsave") {
   Run(core, 300); Require(reads > 0);
   state = Save(core); File.WriteAllBytes(statePath, state);
   File.WriteAllBytes(statePath + ".nvram", core.CloneSaveRam());
   future = Run(core, 15); File.WriteAllLines(statePath + ".future", future);
   File.WriteAllBytes(statePath + ".bin", Save(core));
   var nv = core.ServiceProvider.GetService<IMemoryDomains>()["CD32 NVRAM"];
   nv.PokeByte(13, (byte)(nv.PeekByte(13) ^ 255)); Require(core.SaveRamModified);
  } else { state = File.ReadAllBytes(statePath); future = File.ReadAllLines(statePath + ".future"); }
  void Check(Puae6Core c) {
   Restore(c, state); Require(c.CloneSaveRam(false).SequenceEqual(File.ReadAllBytes(statePath + ".nvram")));
   try { c.StoreSaveRam(seed); throw new Exception("Late SRAM import accepted"); }
   catch (SaveRamRejectedException e) { Require(e.Reason == SaveRamRejection.TooLate); }
   Require(Run(c, 15).SequenceEqual(future));
   Require(Save(c).SequenceEqual(File.ReadAllBytes(statePath + ".bin")));
  }
  Check(core);
  using var freshDisc = Mount(args[1]); using var next = Open(freshDisc, true); Check(next);
  Puae6Movie.Validate(Puae6Movie.Header(core), next);
  string otherPath = Path.Combine(args[0], "different.iso"); File.WriteAllBytes(otherPath, new byte[2048 * 513]);
  using var otherDisc = Mount(otherPath); using var other = Open(otherDisc);
  var before = Save(other);
  try { Restore(other, state); throw new Exception("Other CD state accepted"); }
  catch (StateRejectedException e) { Require(e.Reason == StateRejection.OtherMedia); }
  try { Puae6Movie.Validate(Puae6Movie.Header(core), other); throw new Exception("Other CD movie accepted"); }
  catch (MovieRejectedException e) { Require(e.Reason == MovieRejection.OtherMedia); }
  Require(before.SequenceEqual(Save(other)));
  other.Dispose();
  using var durable = Open(disc, true, File.ReadAllBytes(statePath + ".nvram"));
  Require(durable.CloneSaveRam(false).SequenceEqual(File.ReadAllBytes(statePath + ".nvram")));
  Console.WriteLine($"Public BizHawk CD32 loader/firmware/model/reads/snapshot/new process/movie identity PASS reads={reads}; full game boot not asserted");
 }
}
