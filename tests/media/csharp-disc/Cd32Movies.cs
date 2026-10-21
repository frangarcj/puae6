using BizHawk.Client.Common;
using BizHawk.Common;
using BizHawk.Emulation.DiscSystem;
using System.Reflection;
using BizHawk.Emulation.Common;
using Puae6;

internal static unsafe partial class Program
{
 sealed class Buttons(ControllerDefinition definition, bool swap = false) : IController
 {
  public int Frame; public bool Branch; public bool Swap => swap;
  public ControllerDefinition Definition => definition;
  public bool IsPressed(string name) => !swap ? name == Controllers.Cd32Buttons[1][Frame % 7] : name == (Frame switch {
   0 or 1 or 6 => Controllers.NextSlot, 2 or 3 or 7 or 8 => Controllers.EjectDisk,
   4 or 5 => Branch ? "" : Controllers.InsertDisk, 9 or 10 => Controllers.InsertDisk, _ => "" });
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
 static string[] Play(MovieSession s, Puae6Core core, bool suppressed = false, Func<string> observe = null, Action<int> checkpoint = null)
 {
  var rows = new List<string>();
  while (core.Frame < 12) {
   ((Buttons)s.MovieIn).Frame = core.Frame; s.HandleFrameBefore();
   foreach (var name in core.ControllerDefinition.BoolButtons)
    if (s.MovieOut.IsPressed(name) != s.MovieIn.IsPressed(name)) throw new Exception($"movie input mismatch {core.Frame}:{name}");
   foreach (var name in core.ControllerDefinition.Axes.Keys)
    if (s.MovieOut.AxisValue(name) != s.MovieIn.AxisValue(name)) throw new Exception($"movie axis mismatch {core.Frame}:{name}");
   bool render = !suppressed || core.Frame >= (((Buttons)s.MovieIn).Swap ? 9 : 4);
   core.FrameAdvance(s.MovieOut, render, render);
   core.GetSamplesSync(out var samples, out int frames);
   var audio = new byte[frames * 4]; Buffer.BlockCopy(samples, 0, audio, 0, audio.Length);
   var pixels = new byte[core.BufferWidth * core.BufferHeight * 4]; Buffer.BlockCopy(core.GetVideoBuffer(), 0, pixels, 0, pixels.Length);
   rows.Add($"{core.Frame} {core.CycleCount} {Hash(pixels)} {Hash(audio)} {Hash(core.CloneSaveRam(false))}{observe?.Invoke()}");
   s.HandleFrameAfter(true);
   if (checkpoint is not null) checkpoint(core.Frame);
   else if (core.Frame == 5) { File.WriteAllBytes(Path.Combine(s.BackupDirectory, "seek.state"), Save(core));
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
 sealed class SwapOwner : IDisposable
 {
  readonly List<Disc> discs=new(); public Puae6Core Core { get; } public long Reads;
  public SwapOwner(string[] args, byte[] main, byte[] ext, int variant=0)
  {
   string PathOf(string n) => Path.Combine(args[0],"swap-"+n+".iso");
   string[] names=variant switch { 1=>["b","a","b"],2=>["a","c","a"],3=>["a","b","b"],_=>args[5].EndsWith("load")?["alias","b","a"]:["a","b","alias"] };
   try {
    foreach(var name in names) discs.Add(Mount(PathOf(name)));
    var comm=new CoreComm(_=>{},(_,_)=>{},new Firmware(main,ext,true),CoreComm.CorePreferencesFlags.None,null);
    var p=CatalogParameters(comm,discs.ToArray()); p.GuestDirectory=Path.GetDirectoryName(args[3]); p.GuestFileName=Path.GetFileName(args[3]); p.PinnedGuestSha256=args[4];
    var read=p.ReadCdCatalog; p.ReadCdCatalog=(disc,at,data,count)=>{ ++Reads; return read(disc,at,data,count); }; Core=new Puae6Core(comm,p);
   } catch { foreach(var disc in discs) disc.Dispose(); throw; }
  }
  public void Dispose() { try { Core.Dispose(); } finally { foreach(var disc in discs) disc.Dispose(); } }
 }
 static string SwapObserve(SwapOwner owner, bool branch=false)
 {
  var core=owner.Core; int f=core.Frame-1; var g=Guest(core); var status=new uint[4]; Require(g.Cd.Status(g,status)==P6.Ok);
  int slot=f<6?1:2; uint ordinal=f<4||f>=9||branch?0u:1u; bool present=f<2||(!branch&&f>=4&&f<7)||f>=9;
  int selected=(int)typeof(Puae6Core).GetField("_currentSlot",BindingFlags.Instance|BindingFlags.NonPublic).GetValue(core);
  ulong requests=(ulong)(f<2?0:f<4?1:f<7?(branch?1:2):f<9?(branch?2:3):branch?3:4);
  if(!status.Take(3).SequenceEqual(new uint[]{2,ordinal,present?1u:0u})||selected!=slot||core.MediaEventsSent!=requests)
   throw new Exception($"CD movie f={f} branch={branch} status={string.Join(",",status)} slot={selected} requests={core.MediaEventsSent}");
  var data=Enumerable.Repeat((byte)0xcc,2048).ToArray(); long before=owner.Reads;
  using(g.Host.EnterExit()) fixed(byte* p=data) Require(g.Lib.p6w_cd_read_sector(0,2048,(IntPtr)p,2048)==(present?P6.Ok:P6.BadState));
  Require(present?data.All(b=>b==(ordinal==0?0:0x6d)):data.All(b=>b==0xcc)&&owner.Reads==before);
  return $" slot={selected} cd={string.Join(",",status)} requests={core.MediaEventsSent} data={Hash(data)}";
 }
 static void SwapMovies(string[] args, byte[] main, byte[] ext)
 {
  foreach(var (name,value) in new[]{("a",(byte)0),("b",(byte)0x6d),("c",(byte)0x39),("alias",(byte)0)})
   File.WriteAllBytes(Path.Combine(args[0],"swap-"+name+".iso"),Enumerable.Repeat(value,512*2048).ToArray());
  int[] cuts=[3,5,8]; bool saving=args[5].EndsWith("save");
  MovieSession SessionFor(Puae6Core c,bool branch=false) { var s=Session(args[0],c); s.MovieIn=new Buttons(c.ControllerDefinition,true) { Branch=branch }; return s; }
  foreach(string extension in new[]{"bk2","tasproj"}) {
   string path=Path.Combine(args[0],"swap."+extension), branchPath=Path.Combine(args[0],"swap-branch."+extension);
   if(saving) {
    using var recording=new SwapOwner(args,main,ext); var s=SessionFor(recording.Core); var movie=s.Get(path,false);
    movie.EmulatorVersion="PUAE6 test"; movie.SyncSettingsJson=ConfigService.SaveWithType(recording.Core.GetSyncSettings());
    foreach(var h in Puae6Movie.HeaderForStart(recording.Core,false,null)) movie.HeaderEntries[h.Key]=h.Value;
    Start(s,movie,recording.Core,true);
    if(movie is ITasMovie tas) foreach(int cut in cuts) tas.Markers.Add(new TasMovieMarker(cut+1,"CD cut "+cut)); // StartNewRecording clears earlier markers.
    File.WriteAllLines(path+".future",Play(s,recording.Core,observe:()=>SwapObserve(recording),checkpoint:frame=>{
     if(!cuts.Contains(frame)) return; File.WriteAllBytes(path+".cut"+frame,Save(recording.Core));
     if(movie is ITasMovie t) t.TasStateManager.Capture(frame,recording.Core,true);
    }));
    File.WriteAllBytes(path+".end",Save(recording.Core)); Require(movie.InputLogLength==12&&!movie.Save().IsError); (movie as IDisposable)?.Dispose();
   }
   using var owner=new SwapOwner(args,main,ext); var core=owner.Core; var replay=SessionFor(core); var loaded=replay.Get(path,true);
   if(loaded is ITasMovie history) foreach(int cut in cuts) if(!history.TasStateManager.HasState(cut)) throw new Exception($"CD movie {extension} lacks serialized cut {cut}"); // Deserialized history, before Attach or replay Capture.
   Start(replay,loaded,core,false); var expected=File.ReadAllLines(path+".future"); var end=File.ReadAllBytes(path+".end");
   string[] Continue(bool hidden=false,bool branch=false) => Play(replay,core,hidden,()=>SwapObserve(owner,branch),_=>{});
   Require(Continue().SequenceEqual(expected)&&Save(core).SequenceEqual(end));
   void Seek(int cut) {
    if(loaded is ITasMovie tas) { var state=tas.TasStateManager.GetStateClosestToFrame(cut);
     if(state.Key!=cut) throw new Exception($"CD movie {extension} seek {cut}: actual key {state.Key}, states {tas.TasStateManager.Count}");
     using var input=new BinaryReader(state.Value); core.LoadStateBinary(input); }
    else Restore(core,File.ReadAllBytes(path+".cut"+cut));
    Require(core.Frame==cut);
   }
   foreach(int cut in cuts) { Seek(cut); Require(Continue().SequenceEqual(expected.Skip(cut))&&Save(core).SequenceEqual(end)); }
   Seek(3); loaded.SwitchToRecord(); loaded.Truncate(3); replay.ReadOnly=false; ((Buttons)replay.MovieIn).Branch=true;
   var branchRows=Continue(branch:true); Require(!branchRows.SequenceEqual(expected.Skip(3)));
   if(saving) { loaded.Filename=branchPath; Require(!loaded.Save().IsError); File.WriteAllLines(branchPath+".future",expected.Take(3).Concat(branchRows)); File.WriteAllBytes(branchPath+".end",Save(core)); }
   (loaded as IDisposable)?.Dispose();
   using(var fresh=new SwapOwner(args,main,ext)) { var s=SessionFor(fresh.Core,true); var movie=s.Get(branchPath,true); Start(s,movie,fresh.Core,false);
    Require(Play(s,fresh.Core,observe:()=>SwapObserve(fresh,true),checkpoint:_=>{}).SequenceEqual(File.ReadAllLines(branchPath+".future"))&&Save(fresh.Core).SequenceEqual(File.ReadAllBytes(branchPath+".end"))); (movie as IDisposable)?.Dispose(); }
   using(var hidden=new SwapOwner(args,main,ext)) { var s=SessionFor(hidden.Core); var movie=s.Get(path,true); Start(s,movie,hidden.Core,false);
    Require(Play(s,hidden.Core,true,()=>SwapObserve(hidden),_=>{}).Skip(9).SequenceEqual(expected.Skip(9))&&Save(hidden.Core).SequenceEqual(end)); (movie as IDisposable)?.Dispose(); }
   foreach(int variant in new[]{1,2,3}) {
    using var wrong=new SwapOwner(args,main,ext,variant); var s=SessionFor(wrong.Core); var movie=s.Get(path,true); var seed=new byte[1024]; seed[13]=0x51;
    movie.StartsFromSaveRam=true; movie.SaveRam=seed; foreach(var h in Puae6Movie.HeaderForStart(core,false,seed)) movie.HeaderEntries[h.Key]=h.Value;
    var before=Save(wrong.Core); long reads=wrong.Reads; var nvram=wrong.Core.CloneSaveRam(false);
    try { Start(s,movie,wrong.Core,false); throw new Exception("Foreign CD movie accepted"); } catch(MovieRejectedException e) { Require(e.Reason==MovieRejection.OtherMedia); }
    Require(s.Movie==null&&movie.Emulator==null&&before.SequenceEqual(Save(wrong.Core))&&wrong.Reads==reads&&wrong.Core.CloneSaveRam(false).SequenceEqual(nvram)); s.AbortQueuedMovie(); (movie as IDisposable)?.Dispose();
   }
   Console.WriteLine($"Real CD32 movie swap {extension} record/load/seek/branch/suppressed/new process PASS");
  }
 }

}
