using System.Security.Cryptography;
using BizHawk.Client.Common;
using BizHawk.Common;
using BizHawk.Emulation.Cores.Waterbox;
using System.Runtime.InteropServices;
using BizHawk.Emulation.Common;
using Puae6;

internal static unsafe partial class Program
{
 static string Phase = "host identity";
 static void Require(bool value, string detail = "")
 { if (!value) throw new Exception($"media movie {Phase}: {detail}"); }
 static byte[] Read(string directory, string name) => File.ReadAllBytes(Path.Combine(directory, name));
 static string Hash(byte[] data) => Convert.ToHexString(SHA256.HashData(data)).ToLowerInvariant();
 static byte[][] Bases(string directory) => [Read(directory, "A.adf"), Read(directory, "H.hdf"), Read(directory, "V.ffs")];
 static void Identity(string directory)
 {
  var bases = Bases(directory); var raw = Read(directory, "B.sram"); var original = bases.Select(Hash).ToArray();
  var canonical = Puae6MediaIdentity.CanonicalSaveRam(raw, [bases[0]], [bases[1], bases[2]], true)!;
  Require(Hash(canonical) == File.ReadAllText(Path.Combine(directory, "B.sha")) && canonical.Length == raw.Length - 37);
  Require(Puae6MediaIdentity.CanonicalSaveRam(null, [bases[0]], [bases[1], bases[2]], true) is null);
  var parsed = SaveRam.Parse(canonical, [bases[0]], [bases[1], bases[2]]);
  var applied = SaveRam.Apply([bases[0]], parsed.Floppy).Concat(SaveRam.ApplyHard([bases[1], bases[2]], parsed.Hard)).ToArray();
  for (int i = 0; i < 3; i++) Require(applied[i].SequenceEqual(Read(directory, $"B{i}.bin")));
  Require(Puae6Volumes.FromArchive(Read(directory, "volume.zip")).SequenceEqual(bases[2]));
  Require(Puae6Volumes.FromArchive(Read(directory, "volume.lha")).SequenceEqual(bases[2]));
  foreach (var invalid in new[] {raw[..20], raw.Concat(new byte[] {1}).ToArray()}) {
   try { Puae6MediaIdentity.CanonicalSaveRam(invalid, [bases[0]], [bases[1], bases[2]], true); throw new Exception("invalid SRAM accepted"); }
   catch (SaveRamRejectedException) { }
  }
  try { Puae6MediaIdentity.CanonicalSaveRam(raw, [bases[0]], [bases[1], bases[2]], false); throw new Exception("writes disabled accepted"); }
  catch (SaveRamRejectedException e) { Require(e.Reason == SaveRamRejection.WritesDisabled); }
  for (int changed = 0; changed < 3; changed++) {
   var foreign = bases.Select(b => b.ToArray()).ToArray(); foreign[changed][^1] ^= 1;
   try { Puae6MediaIdentity.CanonicalSaveRam(raw, [foreign[0]], [foreign[1], foreign[2]], true); throw new Exception("foreign base accepted"); }
   catch (SaveRamRejectedException e) { Require(e.Reason == SaveRamRejection.OtherMedia); }
  }
  Require(bases.Select(Hash).SequenceEqual(original) && raw.SequenceEqual(Read(directory, "B.sram")));
  Console.WriteLine("media movie host canonical/padding/three bases/ZIP-LHA/flags/rejection PASS");
 }
 static Puae6Core Open(string[] args, int changed = -1)
 {
  var bases = Bases(args[1]); if (changed is 0 or 1) bases[changed][^1] ^= 1;
  var comm = new CoreComm(_ => { }, (_, _) => { }, null, CoreComm.CorePreferencesFlags.None, null);
  return new Puae6Core(comm, new Puae6Parameters {
   Firmware = Read(args[1], "loop.rom"), GuestDirectory = Path.GetDirectoryName(args[3])!, GuestFileName = Path.GetFileName(args[3]), PinnedGuestSha256 = args[4],
   Media = [bases[0]], HardFiles = [bases[1]], Volumes = [Read(args[1], changed == 2 ? "other.zip" : "volume.zip")],
   SyncSettings = new Puae6SyncSettings { FloppyWrites = true, WhdLoad = Puae6WhdLoadMode.Disabled }
  });
 }
 sealed class Buttons(ControllerDefinition definition) : IController
 {
  public int Frame;
  public ControllerDefinition Definition => definition;
  public bool IsPressed(string name) => name == Definition.BoolButtons.First(n => n.StartsWith("P2 ")) && Frame % 3 == 1;
  public int AxisValue(string name) => Definition.Axes[name].Neutral;
  public IReadOnlyCollection<(string Name, int Strength)> GetHapticsSnapshot() => Array.Empty<(string, int)>();
  public void SetHapticChannelStrength(string name, int strength) { }
 }
 static MovieSession Session(string directory, Puae6Core core)
 {
  core.ControllerDefinition.BuildMnemonicsCache("Amiga");
  var session = new MovieSession(new MovieConfig {EnableBackupMovies = false, DefaultTasStateManagerSettings = new PagedStateManager.PagedSettings {
   TotalMemoryLimitMB = 64, FramesBetweenNewStates = 1, FramesBetweenSavedStates = 1, ForceSaveMarkerStates = true
  }}, directory, null, () => { }, () => { }, (_, _) => { });
  session.MovieIn = new Buttons(core.ControllerDefinition); session.StickySource = new Buttons(core.ControllerDefinition); return session;
 }
 static void Start(MovieSession session, IMovie movie, Puae6Core core, bool record)
 {
  session.QueueNewMovie(movie, "Amiga", "", new PathEntryCollection(), new Dictionary<string, string>());
  session.RunQueuedMovie(record, core);
 }
 static byte[] Save(Puae6Core core)
 {
  using var stream = new MemoryStream(); using var writer = new BinaryWriter(stream); core.SaveStateBinary(writer); return stream.ToArray();
 }
 static string Media(Puae6Core core)
 {
  var hashes = new List<string>();
  for (int i = 0; i < 3; i++) { Require(core.ExportMedium(i, out var image, out _) == P6.Ok); hashes.Add(Hash(image!)); }
  return string.Join(" ", hashes);
 }
 static string[] Play(MovieSession session, Puae6Core core)
 {
  var rows = new List<string>();
  while (core.Frame < 12) {
   ((Buttons)session.MovieIn).Frame = core.Frame; session.HandleFrameBefore();
   foreach (var name in core.ControllerDefinition.BoolButtons) Require(session.MovieOut.IsPressed(name) == session.MovieIn.IsPressed(name));
   Require(core.FrameAdvance(session.MovieOut, true, true));
   core.GetSamplesSync(out var samples, out int count); var pcm = new byte[count * 4]; Buffer.BlockCopy(samples, 0, pcm, 0, pcm.Length);
   var pixels = new byte[core.BufferWidth * core.BufferHeight * 4]; Buffer.BlockCopy(core.GetVideoBuffer(), 0, pixels, 0, pixels.Length);
   rows.Add($"{core.Frame} {core.CycleCount} {Hash(pixels)} {Hash(pcm)}"); session.HandleFrameAfter(true);
   if (core.Frame == 5 && session.Movie is ITasMovie tas) tas.TasStateManager.Capture(5, core, true);
  }
  return rows.ToArray();
 }
 static void Movies(string[] args)
 {
  foreach (string extension in new[] {"bk2", "tasproj"}) foreach (bool sram in new[] {false, true}) {
   string path = Path.Combine(args[1], $"{sram}.{extension}");
   Phase = $"{args[0]}/{extension}/sram={sram}/record";
   if (args[0] == "save") {
    IMovie movie; MovieSession session;
    using (var original = Open(args)) {
     original.StoreSaveRam(Read(args[1], "A.sram")); session = Session(args[1], original); movie = session.Get(path, false);
     movie.StartsFromSaveRam = sram; if (sram) movie.SaveRam = Read(args[1], "B.sram");
     movie.SyncSettingsJson = ConfigService.SaveWithType(original.GetSyncSettings());
     foreach (var h in Puae6Movie.HeaderForStart(original, false, sram ? movie.SaveRam : null)) movie.HeaderEntries[h.Key] = h.Value;
    }
    using var recording = Open(args); Start(session, movie, recording, true);
    // StartNewRecording clears markers; IsReserved keeps the state BEFORE its marker.
    if (movie is ITasMovie tas) tas.Markers.Add(new TasMovieMarker(6, "cut after state 5"));
    string expected = string.Join(" ", Enumerable.Range(0, 3).Select(i => Hash(sram ? Read(args[1], $"B{i}.bin") : Bases(args[1])[i])));
    Require(Media(recording) == expected); // Actual sealed guest bases after Attach AND ProcessSram.
    File.WriteAllLines(path + ".future", Play(session, recording)); File.WriteAllBytes(path + ".end", Save(recording));
    File.WriteAllText(path + ".media", Media(recording)); var result = movie.Save(); Require(!result.IsError); (movie as IDisposable)?.Dispose();
   }
   Phase = $"{args[0]}/{extension}/sram={sram}/replay";
   using var core = Open(args); var replay = Session(args[1], core); var loaded = replay.Get(path, true);
   if (loaded is ITasMovie history) Require(history.TasStateManager.HasState(5),
    $"history before Attach: requested=5 count={history.TasStateManager.Count} last={history.TasStateManager.Last}");
   Start(replay, loaded, core, false); var expectedRows = File.ReadAllLines(path + ".future");
   Require(Play(replay, core).SequenceEqual(expectedRows)); Require(Save(core).SequenceEqual(Read(args[1], $"{sram}.{extension}.end")));
   Require(Media(core) == File.ReadAllText(path + ".media"));
   if (loaded is ITasMovie movieTas) {
    Phase = $"{args[0]}/{extension}/sram={sram}/seek";
    var manager = movieTas.TasStateManager; var state = manager.GetStateClosestToFrame(5);
    Require(state.Key == 5, $"requested=5 key={state.Key} count={manager.Count} last={manager.Last}");
    using (var reader = new BinaryReader(state.Value)) core.LoadStateBinary(reader);
    Require(Play(replay, core).SequenceEqual(expectedRows.Skip(5)) && Save(core).SequenceEqual(File.ReadAllBytes(path + ".end")));
   }
   (loaded as IDisposable)?.Dispose(); Console.WriteLine($"real media movie {extension}/{sram} replay/seek/full state/exports/{args[0]} PASS");
  }
  Phase = $"{args[0]}/rejection"; Reject(args);
 }
 static void Reject(string[] args)
 {
  using var core = Open(args); var session = Session(args[1], core); var movie = session.Get(Path.Combine(args[1], "True.tasproj"), true);
  var before = Save(core); var header = new Dictionary<string, string>(movie.HeaderEntries); var blob = movie.SaveRam;
  void Refused() {
   var controller = session.MovieController;
   try { Start(session, movie, core, false); throw new Exception("bad movie accepted"); }
   catch (Exception e) when (e is MovieRejectedException or SaveRamRejectedException) { }
   Require(session.Movie == null && movie.Emulator == null && ReferenceEquals(controller, session.MovieController));
   session.AbortQueuedMovie(); Require(before.SequenceEqual(Save(core)));
  }
  foreach (string key in new[] {Puae6Movie.GuestKey, Puae6Movie.ConfigKey, Puae6Movie.FirmwareKey, Puae6Movie.MediaKey}) {
   movie.HeaderEntries[key] = "bad"; Refused(); movie.HeaderEntries[key] = header[key];
  }
  movie.SaveRam = blob[..20]; Refused(); movie.SaveRam = blob.ToArray(); movie.SaveRam[^38] ^= 1; Refused(); movie.SaveRam = blob;
  for (int changed = 0; changed < 3; changed++) {
   using var other = Open(args, changed); var otherSession = Session(args[1], other); var foreign = otherSession.Get(movie.Filename, true); var pristine = Save(other);
   try { Start(otherSession, foreign, other, false); throw new Exception("foreign medium accepted"); }
   catch (Exception e) when (e is MovieRejectedException or SaveRamRejectedException) { }
   Require(otherSession.Movie == null && foreign.Emulator == null && pristine.SequenceEqual(Save(other))); (foreign as IDisposable)?.Dispose();
  }
  (movie as IDisposable)?.Dispose();
  var valid = session.Get(Path.Combine(args[1], "False.tasproj"), true); Start(session, valid, core, false);
  Require(Play(session, core).SequenceEqual(File.ReadAllLines(Path.Combine(args[1], "False.tasproj.future"))));
  Require(Save(core).SequenceEqual(Read(args[1], "False.tasproj.end"))); (valid as IDisposable)?.Dispose();
  Console.WriteLine("real media movie rejects before Attach without mutation and preserves future PASS");
 }
 static void HrtConfig(string expectedSha)
 {
  var off = new Puae6SyncSettings(); Require(off.Cartridge == Puae6Cartridge.Disabled && Puae6Core.ConfigFlags(off.Cartridge,off.FloppyWrites,off.Machine) == 0, "default");
  Require(sizeof(P6InputV2) == 108 && (int)System.Runtime.InteropServices.Marshal.OffsetOf<P6InputV2>(nameof(P6InputV2.Commands)) == 104, "ABI2");
  Require(P6.HrtMonRawSha256 == expectedSha && P6.CommandFreeze == 1 && P6.ConfigHrtMon == 2, "pinned asset/ABI");
  foreach (bool writes in new[] {false,true}) foreach (var cartridge in new[] {Puae6Cartridge.Disabled,Puae6Cartridge.HrtMon}) {
   var settings = new Puae6SyncSettings {Machine = Puae6Machine.A1200, Cartridge = cartridge, FloppyWrites = writes};
   Require(Puae6Core.ConfigFlags(settings.Cartridge,settings.FloppyWrites,settings.Machine) == (uint)((writes ? 1 : 0) | (cartridge == Puae6Cartridge.HrtMon ? 2 : 0)), "flags OR");
   var buttons = Controllers.Define(settings,0); Require(buttons.BoolButtons.Contains(Controllers.Freeze) == (cartridge == Puae6Cartridge.HrtMon), "Freeze offered");
   buttons.BuildMnemonicsCache("Amiga"); var pressed = new HrtButtons(buttons) {Frame = -1};
   var session = new MovieSession(new MovieConfig(),Path.GetTempPath(),null,() => {},() => {},(_,_) => {});
   var decoded = session.GenerateMovieController(buttons); decoded.SetFromMnemonic(Bk2LogEntryGenerator.GenerateLogEntry(pressed));
   Require(decoded.IsPressed(Controllers.Freeze) == (cartridge == Puae6Cartridge.HrtMon), "BK2 Freeze level");
   var heaps = Puae6Core.Heaps(settings); Require(heaps.InvisibleHeapSizeKB == 16 && heaps.PlainHeapSizeKB == 16, "ordinary mutable heap");
  }
  var factory = Puae6Guest.CreateHost; int hosts = 0;
  Puae6Guest.CreateHost = _ => { ++hosts; throw new Exception("invalid configuration opened host"); };
  try {
   foreach (var settings in new[] {new Puae6SyncSettings {Cartridge=Puae6Cartridge.HrtMon},
                                    new Puae6SyncSettings {Machine=Puae6Machine.A1200, Cartridge=(Puae6Cartridge)1}}) {
    try { using var invalid = new Puae6Core(new CoreComm(_ => {}, (_,_) => {}, null, CoreComm.CorePreferencesFlags.None, null),
      new Puae6Parameters {Firmware=[1], SyncSettings=settings}); throw new Exception("invalid cartridge accepted"); }
    catch (ArgumentException) { }
   }
   Require(hosts == 0, "validation precedes host");
  } finally { Puae6Guest.CreateHost = factory; }
  Console.WriteLine("HRTMon managed default/flags/controls/BK2/ABI/SHA/heaps/preflight PASS");
 }
 static readonly Dictionary<int,string> HrtKeys = new();
 static readonly Dictionary<int,(byte First,byte Second)> HrtChecks = new();
 static int HrtRelease, HrtEnd;
 static int HrtType(int at, string text)
 {
  foreach (char c in text) { string key = "Key " + (c == ' ' ? "Space" : char.ToUpperInvariant(c).ToString());
   for (int n = 0; n < 3; n++) HrtKeys[at+n] = key; at += 6;
  }
  for (int n = 0; n < 3; n++) HrtKeys[at+n] = "Key Return"; return at + 15;
 }
 static void HrtSchedule()
 {
  int filled = HrtType(6,"o 140000 140004 5a"); HrtChecks[filled] = (0x5a,0);
  int exited = HrtType(filled+1,"x");
  int ignored = HrtType(exited+8,"o 140004 140008 7c"); HrtChecks[ignored] = (0x5a,0); // Held after x must not reenter.
  HrtRelease = ignored+1;
  int rearmed = HrtType(HrtRelease+81,"o 140004 140008 6b"); HrtChecks[rearmed] = (0x5a,0x6b);
  HrtEnd = HrtType(rearmed+1,"x")+8;
 }
 sealed class HrtButtons(ControllerDefinition definition) : IController
 {
  public int Frame;
  public ControllerDefinition Definition => definition;
  public bool IsPressed(string name) => definition.BoolButtons.Contains(name) &&
   ((name == Controllers.Freeze && Frame != HrtRelease) || HrtKeys.GetValueOrDefault(Frame) == name);
  public int AxisValue(string name) => definition.Axes[name].Neutral;
  public IReadOnlyCollection<(string Name,int Strength)> GetHapticsSnapshot() => [];
  public void SetHapticChannelStrength(string name,int strength) { }
 }
 static WaterboxHost HrtHost;
 [UnmanagedFunctionPointer(CallingConvention.Cdecl)] delegate nuint HrtRanges(ulong* output,nuint capacity);
 static void HrtBudget(string phase)
 {
  using var enter = HrtHost.EnterExit(); ulong* ranges = stackalloc ulong[16];
  var call = Marshal.GetDelegateForFunctionPointer<HrtRanges>(HrtHost.GetCallinProcAddr(HrtHost.GetProcAddrOrThrow("p6w_memory_info")));
  Require(call(ranges,16) == 16,"memory ranges"); var pages = HrtHost.GetPagesDomain();
  string[] names = ["sbrk","sealed","invisible","plain","mmap"];
  for (int heap = 3; heap < 8; heap++) {
   ulong committed = 0, first = (ranges[2*heap]-ranges[0])/4096, count = ranges[2*heap+1]/4096;
   for (ulong page = first; page < first+count; page++) if ((pages.PeekByte((long)page)&0x3f) != 0) committed += 4096;
   ulong capacity = ranges[2*heap+1]; Require(heap is 5 or 6 ? committed == 0 : (heap == 4 ? committed <= capacity : committed*2 <= capacity), $"{phase}/{names[heap-3]} committed={committed} budget={capacity}");
   Console.WriteLine($"HRTMon budget {phase}/{names[heap-3]}={committed}/{capacity}");
  }
 }
 static Puae6Core HrtOpen(string[] args, bool enabled = true)
 {
  var factory = Puae6Guest.CreateHost;
  try {
   Puae6Guest.CreateHost = options => HrtHost = factory(options);
   return new(
  new CoreComm(_ => {}, (_,_) => {}, null, CoreComm.CorePreferencesFlags.None, null), new Puae6Parameters {
   Firmware = File.ReadAllBytes(args[5]), GuestDirectory = Path.GetDirectoryName(args[3])!, GuestFileName = Path.GetFileName(args[3]), PinnedGuestSha256 = args[4],
   SyncSettings = new Puae6SyncSettings {Machine=Puae6Machine.A1200, Cartridge=enabled ? Puae6Cartridge.HrtMon : Puae6Cartridge.Disabled}
  });
  } finally { Puae6Guest.CreateHost = factory; }
 }
 static MemoryDomain HrtRam(Puae6Core core) => core.ServiceProvider.GetService<IMemoryDomains>()!["Chip RAM"];
 static byte[] HrtBytes(Puae6Core core) => Enumerable.Range(0,8).Select(n => HrtRam(core).PeekByte(0x140000+n)).ToArray();
 static MovieSession HrtSession(string directory,Puae6Core core)
 {
  var session = Session(directory,core);
  var settings = (PagedStateManager.PagedSettings)session.Settings.DefaultTasStateManagerSettings;
  settings.FramesBetweenNewStates = 40; settings.FramesBetweenSavedStates = 40;
  session.MovieIn = new HrtButtons(core.ControllerDefinition); return session;
 }
 static string[] HrtPlay(Puae6Core core, MovieSession session = null)
 {
  var rows = new List<string>(); var buttons = session is null ? new HrtButtons(core.ControllerDefinition) : (HrtButtons)session.MovieIn;
  while (core.Frame < HrtEnd) {
   buttons.Frame = core.Frame; session?.HandleFrameBefore(); IController input = session is null ? buttons : session.MovieOut;
   foreach (var name in core.ControllerDefinition.BoolButtons) Require(input.IsPressed(name) == buttons.IsPressed(name), $"input at {core.Frame}: {name}");
   Require(core.FrameAdvance(input,true,true), "frame advance"); core.GetSamplesSync(out var samples,out int count);
   var pcm = new byte[count*4]; Buffer.BlockCopy(samples,0,pcm,0,pcm.Length);
   var pixels = new byte[core.BufferWidth*core.BufferHeight*4]; Buffer.BlockCopy(core.GetVideoBuffer(),0,pixels,0,pixels.Length);
   if (HrtChecks.TryGetValue(core.Frame,out var check)) Require(HrtBytes(core).SequenceEqual(Enumerable.Repeat(check.First,4).Concat(Enumerable.Repeat(check.Second,4))), $"monitor command/held/rearm at {core.Frame}");
   rows.Add($"{core.Frame} {core.CycleCount} {Hash(pixels)} {Hash(pcm)} {Convert.ToHexString(HrtBytes(core))}"); session?.HandleFrameAfter(true);
  }
  return rows.ToArray();
 }
 static void HrtMovies(string[] args)
 {
  HrtSchedule(); string initial = Path.Combine(args[1],"hrt.initial"); Phase = args[0]+"/HRTMon";
  if (args[0] == "hrtsave") {
   using var original = HrtOpen(args); HrtBudget("sealed"); var idle = new HrtButtons(original.ControllerDefinition) {Frame=HrtRelease};
   for (int frame = 0; frame < 300; frame++) original.FrameAdvance(idle,true,true);
   idle.Frame = -1; for (int frame = 0; frame < 80; frame++) original.FrameAdvance(idle,true,true);
   for (int n = 0; n < 8; n++) HrtRam(original).PokeByte(0x140000+n,0);
   HrtBudget("held380"); File.WriteAllBytes(initial,Save(original)); original.ResetCounters();
   File.WriteAllLines(initial+".future",HrtPlay(original)); File.WriteAllBytes(initial+".end",Save(original)); HrtBudget("future");
  }
  var expected = File.ReadAllLines(initial+".future"); var end = File.ReadAllBytes(initial+".end");
  foreach (string extension in new[] {"bk2","tasproj"}) {
   Phase = args[0]+"/HRTMon/"+extension; string path = Path.Combine(args[1],"hrt."+extension);
   if (args[0] == "hrtsave") {
    using var recording = HrtOpen(args); var session = HrtSession(args[1],recording); var movie = session.Get(path,false);
    movie.StartsFromSavestate = true; movie.BinarySavestate = File.ReadAllBytes(initial);
    movie.SyncSettingsJson = ConfigService.SaveWithType(recording.GetSyncSettings());
    foreach (var h in Puae6Movie.HeaderForStart(recording,true,null)) movie.HeaderEntries[h.Key] = h.Value;
    Start(session,movie,recording,true); if (movie is ITasMovie tas) tas.Markers.Add(new TasMovieMarker(6,"held cut 5"));
    Require(HrtPlay(recording,session).SequenceEqual(expected) && Save(recording).SequenceEqual(end), "record vs live continuation");
    Require(!movie.Save().IsError,"movie write"); (movie as IDisposable)?.Dispose();
   }
   using var core = HrtOpen(args); var replay = HrtSession(args[1],core); var loaded = replay.Get(path,true);
   if (loaded is ITasMovie history) Require(history.TasStateManager.HasState(5),"serialized held cut");
   Start(replay,loaded,core,false); Require(HrtPlay(core,replay).SequenceEqual(expected) && Save(core).SequenceEqual(end),"replay vs live continuation");
   if (loaded is ITasMovie movieTas) {
    var state = movieTas.TasStateManager.GetStateClosestToFrame(5);
    Require(state.Key == 5,$"held seek key={state.Key} count={movieTas.TasStateManager.Count} last={movieTas.TasStateManager.Last}");
    using (var reader = new BinaryReader(state.Value)) core.LoadStateBinary(reader);
    Require(HrtPlay(core,replay).SequenceEqual(expected.Skip(5)) && Save(core).SequenceEqual(end),"held seek continuation");
   }
   using var off = HrtOpen(args,false); var before = Save(off); bool rejected = false;
   try { using var reader = new BinaryReader(File.OpenRead(initial)); off.LoadStateBinary(reader); }
   catch (StateRejectedException e) { rejected = e.Reason == StateRejection.OtherConfig; }
   Require(rejected && Save(off).SequenceEqual(before),"off state rejection before snapshot");
   var offSession = HrtSession(args[1],off); var foreign = offSession.Get(path,true); rejected = false;
   try { Start(offSession,foreign,off,false); } catch (MovieRejectedException e) { rejected = e.Reason == MovieRejection.OtherConfig; }
   Require(rejected && offSession.Movie == null && Save(off).SequenceEqual(before),"off movie rejection before Attach");
   (loaded as IDisposable)?.Dispose(); (foreign as IDisposable)?.Dispose();
   Console.WriteLine($"HRTMon real {extension}/{args[0]} commands/held/rearm/replay/seek/cold/off PASS");
  }
 }
 static void Main(string[] args) { if (args[0] == "identity") Identity(args[1]); else if (args[0] == "hrtconfig") HrtConfig(args[1]);
  else { LoadRuntime(args[2]); if (args[0].StartsWith("hrt")) HrtMovies(args); else Movies(args); } }
}
