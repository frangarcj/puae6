using BizHawk.Client.Common;
using BizHawk.Emulation.Common;
using Puae6;

internal static unsafe partial class Program
{
 sealed class Buttons(ControllerDefinition definition) : IController
 {
  public int Frame;
  public ControllerDefinition Definition => definition;
  public bool IsPressed(string name) => name == Controllers.Cd32Buttons[1][Frame % 7];
  public int AxisValue(string name) => definition.Axes[name].Neutral;
  public IReadOnlyCollection<(string Name, int Strength)> GetHapticsSnapshot() => Array.Empty<(string, int)>();
  public void SetHapticChannelStrength(string name, int strength) { }
 }
 static MovieSession Session(string directory, Puae6Core core)
 {
  core.ControllerDefinition.BuildMnemonicsCache("Amiga");
  var states = new ZwinderStateManagerSettings {
   CurrentStoreType = IRewindSettings.BackingStoreType.TempFile, RecentStoreType = IRewindSettings.BackingStoreType.TempFile,
   GapsStoreType = IRewindSettings.BackingStoreType.TempFile, AncientStoreType = IRewindSettings.BackingStoreType.Memory,
   CurrentBufferSize = 64, RecentBufferSize = 64, GapsBufferSize = 64,
   CurrentTargetFrameLength = 1, RecentTargetFrameLength = 1, GapsTargetFrameLength = 1, AncientStateInterval = 1
  };
  var s = new MovieSession(new MovieConfig { EnableBackupMovies = false, DefaultTasStateManagerSettings = states },
   directory, null, () => { }, () => { }, (_, _) => { });
  s.MovieIn = new Buttons(core.ControllerDefinition); s.StickySource = new Idle(core.ControllerDefinition); return s;
 }
 static void Start(MovieSession s, IMovie movie, Puae6Core core, bool record)
 {
  s.QueueNewMovie(movie, "Amiga", "", new PathEntryCollection(), new Dictionary<string, string>());
  s.RunQueuedMovie(record, core);
 }
 static string[] Play(MovieSession s, Puae6Core core, bool suppressed = false)
 {
  var rows = new List<string>();
  while (core.Frame < 12) {
   ((Buttons)s.MovieIn).Frame = core.Frame; s.HandleFrameBefore();
   foreach (var name in core.ControllerDefinition.BoolButtons)
    if (s.MovieOut.IsPressed(name) != s.MovieIn.IsPressed(name)) throw new Exception($"movie input mismatch {core.Frame}:{name}");
   foreach (var name in core.ControllerDefinition.Axes.Keys)
    if (s.MovieOut.AxisValue(name) != s.MovieIn.AxisValue(name)) throw new Exception($"movie axis mismatch {core.Frame}:{name}");
   bool render = !suppressed || core.Frame >= 4;
   core.FrameAdvance(s.MovieOut, render, render);
   core.GetSamplesSync(out var samples, out int frames);
   var audio = new byte[frames * 4]; Buffer.BlockCopy(samples, 0, audio, 0, audio.Length);
   var pixels = new byte[core.BufferWidth * core.BufferHeight * 4]; Buffer.BlockCopy(core.GetVideoBuffer(), 0, pixels, 0, pixels.Length);
   rows.Add($"{core.Frame} {core.CycleCount} {Hash(pixels)} {Hash(audio)} {Hash(core.CloneSaveRam(false))}");
   s.HandleFrameAfter(true);
   if (core.Frame == 5) { File.WriteAllBytes(Path.Combine(s.BackupDirectory, "seek.state"), Save(core));
    if (s.Movie is ITasMovie t) t.TasStateManager.Capture(5, core, true); }
  }
  return rows.ToArray();
 }
 static void Movies(string[] args, Func<byte[], Puae6Core> open)
 {
  foreach (string extension in new[] { "bk2", "tasproj" }) foreach (string mode in new[] { "sram", "state", "power" }) {
   string path = Path.Combine(args[0], mode + "." + extension);
   if (args[5] == "moviesave") {
    var seed = Enumerable.Range(0, 1024).Select(i => (byte)(i * 17 + 9)).ToArray();
    IMovie movie; MovieSession s;
    using (var original = open(seed)) {
     Run(original, 120); original.ServiceProvider.GetService<IMemoryDomains>()["CD32 NVRAM"].PokeByte(13, 0xcc);
     s = Session(args[0], original); movie = s.Get(path, false);
     movie.StartsFromSavestate = mode == "state"; movie.StartsFromSaveRam = mode == "sram";
     if (mode == "state") movie.BinarySavestate = Save(original);
     if (mode == "sram") movie.SaveRam = original.CloneSaveRam(false);
     movie.EmulatorVersion = "PUAE6 test"; movie.SyncSettingsJson = ConfigService.SaveWithType(original.GetSyncSettings());
     // The same capture helper called by real MainForm, before it reboots the core.
     foreach (var h in Puae6Movie.HeaderForStart(original, movie.StartsFromSavestate, movie.SaveRam)) movie.HeaderEntries[h.Key] = h.Value;
    }
    using var recording = open(null); Start(s, movie, recording, true);
    Require(recording.Frame == 0);
    Require(recording.CloneSaveRam(false)[13] == (mode == "power" ? 0 : 0xcc));
    Puae6Movie.Validate(new Dictionary<string, string>(movie.HeaderEntries), recording);
    File.WriteAllLines(path + ".future", Play(s, recording)); File.WriteAllBytes(path + ".end", Save(recording));
    var result = movie.Save(); if (result.IsError) throw new Exception(result.UserFriendlyErrorMessage(), result.Exception);
    Require(movie.InputLogLength == 12); (movie as IDisposable)?.Dispose();
   }
   using var core = open(null); var session = Session(args[0], core); var loaded = session.Get(path, true);
   Require(loaded != null && loaded.InputLogLength == 12);
   Start(session, loaded, core, false);
   Require(core.Frame == 0 && core.CloneSaveRam(false)[13] == (mode == "power" ? 0 : 0xcc));
   var expected = File.ReadAllLines(path + ".future");
   var actual = Play(session, core);
   if (!actual.SequenceEqual(expected)) throw new Exception($"{extension}/{mode}: first row difference " +
    string.Join("\n", actual.Zip(expected).Where(x => x.First != x.Second).Take(1).Select(x => x.First + "\n" + x.Second)));
   var end = Save(core); var endExpected = File.ReadAllBytes(path + ".end");
   if (!end.SequenceEqual(endExpected)) throw new Exception($"{extension}/{mode}: end state differs at offset " +
    Enumerable.Range(196, Math.Min(end.Length, endExpected.Length) - 196).First(i => end[i] != endExpected[i]));
   if (loaded is ITasMovie tas) {
    Require(tas.TasStateManager.Settings is ZwinderStateManagerSettings && tas.TasStateManager.Count > 1);
    var state = tas.TasStateManager.GetStateClosestToFrame(4);
    using (var reader = new BinaryReader(state.Value)) core.LoadStateBinary(reader);
    if (state.Key == 0 && loaded.StartsFromSavestate) core.ResetCounters();
   } else Restore(core, File.ReadAllBytes(Path.Combine(args[0], "seek.state")));
   int at = core.Frame; Require(at <= 5);
   Require(Play(session, core).SequenceEqual(expected.Skip(at)));
   Require(Save(core).SequenceEqual(File.ReadAllBytes(path + ".end")));
   (loaded as IDisposable)?.Dispose();
   using var hidden = open(null); var suppressed = Session(args[0], hidden); var again = suppressed.Get(path, true);
   Start(suppressed, again, hidden, false);
   Require(Play(suppressed, hidden, true).Skip(4).SequenceEqual(expected.Skip(4)));
   Require(Save(hidden).SequenceEqual(File.ReadAllBytes(path + ".end"))); (again as IDisposable)?.Dispose();
   Console.WriteLine($"Real CD32 movie {extension}/{mode} record/load/seek/suppressed/new process PASS");
  }
  RejectMovieStarts(args, open);
 }
 static void RejectMovieStarts(string[] args, Func<byte[], Puae6Core> open)
 {
  foreach (var extension in new[] { "bk2", "tasproj" }) {
  using var core = open(null); var s = Session(args[0], core); var movie = s.Get(Path.Combine(args[0], "state." + extension), true);
  var header = new Dictionary<string, string>(movie.HeaderEntries); var before = Save(core);
  try { Restore(core, movie.BinarySavestate); throw new Exception("Normal state LOAD accepted another initial NVRAM identity"); }
  catch (StateRejectedException e) { Require(e.Reason == StateRejection.OtherConfig); }
  foreach (string key in new[] { Puae6Movie.GuestKey, Puae6Movie.ConfigKey, Puae6Movie.FirmwareKey, Puae6Movie.MediaKey, Puae6Movie.NvramKey }) {
   foreach (bool missing in new[] { false, true }) {
    if (missing) movie.HeaderEntries.Remove(key); else movie.HeaderEntries[key] = "bad";
    try { Start(s, movie, core, false); throw new Exception("Invalid identity accepted: " + key); }
    catch (MovieRejectedException) { Require(s.Movie == null && movie.Emulator == null); }
    s.AbortQueuedMovie(); movie.HeaderEntries[key] = header[key]; Require(before.SequenceEqual(Save(core)));
   }
  }
  var state = movie.BinarySavestate; movie.BinarySavestate = state.ToArray(); movie.BinarySavestate[^1] ^= 1;
  try { Start(s, movie, core, false); throw new Exception("Corrupt snapshot accepted"); }
  catch (StateRejectedException e) { Require(e.Reason == StateRejection.Corrupt); }
  s.AbortQueuedMovie(); Require(before.SequenceEqual(Save(core)));
  // Correct checksum, invalid wrapper: preflight must still reject before Attach or identity adoption.
  byte[] payload;
  using (var original = open(Enumerable.Range(0, 1024).Select(i => (byte)(i * 17 + 9)).ToArray())) {
   using var reader = new BinaryReader(new MemoryStream(state)); payload = StateEnvelope.Read(reader, original.Identity);
   Array.Fill(payload, (byte)255, 0, 4); using var stream = new MemoryStream();
   using var writer = new BinaryWriter(stream); StateEnvelope.Write(writer, original.Identity, payload); movie.BinarySavestate = stream.ToArray();
  }
  try { Start(s, movie, core, false); throw new Exception("Invalid wrapper accepted"); }
  catch (StateRejectedException e) { Require(e.Reason == StateRejection.BadFields); }
  s.AbortQueuedMovie(); Require(before.SequenceEqual(Save(core)));
  // A well-formed header with an invalid SaveRAM must fail before TAS Attach can import it.
  movie.StartsFromSavestate = false; movie.StartsFromSaveRam = true; movie.SaveRam = new byte[1023];
  try { Start(s, movie, core, false); throw new Exception("Bad SRAM accepted"); }
  catch (MovieRejectedException e) { Require(e.Reason == MovieRejection.BadStart); }
  s.AbortQueuedMovie(); Require(before.SequenceEqual(Save(core))); (movie as IDisposable)?.Dispose();
  Console.WriteLine($"Real CD32 movie {extension} preflight leaves core and MovieSession unchanged PASS");
  }
 }
}
