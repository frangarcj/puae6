using BizHawk.Common;
using System.Reflection;
using System.Security.Cryptography;
using BizHawk.Emulation.Common;
using BizHawk.Emulation.Cores;
using BizHawk.Emulation.DiscSystem;
using Puae6;
#if PUBLIC_CHD
using System.Diagnostics;
using BizHawk.Client.Common;
#endif

internal static unsafe partial class Program
{
 static void Require(bool ok) { if (!ok) throw new Exception("Public CD32 assertion failed"); }
 sealed class DiscAsset(Disc disc) : IDiscAsset
 {
  public Disc DiscData => disc;
  public DiscType DiscType => default;
  public string DiscName { get; set; } = "CD32";
 }
 sealed class MixedRom : IRomAsset
 {
  public byte[] FileData { get; } = new byte[P6.AdfBytes]; public byte[] RomData => FileData;
  public string Extension => ".adf"; public string RomPath => "mixed.adf"; public GameInfo Game => null;
 }

 sealed class Firmware(byte[] main, byte[] ext, bool combined) : ICoreFileProvider
 {
  internal readonly List<string> Asked = new();
  internal bool Refuse { get; set; }
  public string GetRetroSaveRAMDirectory(string p) => throw new NotSupportedException();
  public string GetRetroSystemPath(string p) => throw new NotSupportedException();
  public string GetUserPath(string s, bool temp) => throw new NotSupportedException();
  public string GetPalettesPath(string s) => throw new NotSupportedException();
  public byte[] GetFirmware(FirmwareID id, string msg = null) => GetFirmwareOrThrow(id, msg);
  public (byte[], GameInfo) GetFirmwareWithGameInfoOrThrow(FirmwareID id, string msg = null) => (GetFirmwareOrThrow(id, msg), null);
  public byte[] GetFirmwareOrThrow(FirmwareID id, string msg = null)
  {
   Asked.Add(id.Firmware);
   if (Refuse) throw new InvalidOperationException("host-stop-before-guest");
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
 static string[] Run(Puae6Core core, int count, IController controller = null, Action<short[], int> audioObserver = null)
 {
  controller ??= new Idle(core.ControllerDefinition); var rows = new List<string>();
  for (int i = 0; i < count; ++i) {
   Require(core.FrameAdvance(controller, true, true));
   core.GetSamplesSync(out var samples, out int frames);
   audioObserver?.Invoke(samples, frames);
   var audio = new byte[frames * 4]; Buffer.BlockCopy(samples, 0, audio, 0, audio.Length);
   var video = core.GetVideoBuffer(); var pixels = new byte[core.BufferWidth * core.BufferHeight * 4];
   Buffer.BlockCopy(video, 0, pixels, 0, pixels.Length);
   rows.Add($"{core.Frame} {core.CycleCount} {Hash(pixels)} {Hash(audio)}");
  }
  return rows.ToArray();
 }
 sealed class GameButtons(Puae6Core core) : IController
 {
  static readonly (int Unit, string Button)[] Phases = [
   (1400, "Red"), (1410, ""), (2100, "Red"), (2110, ""), (3100, "Red"), (3110, ""),
   (4501, "Down"), (4502, ""), (4504, "Down"), (4506, ""), (4508, "Down"), (4510, ""),
   (4520, "Red"), (4530, ""), (5501, "Red"), (5510, ""), (6501, "Right"), (6541, "") ];
  public ControllerDefinition Definition => core.ControllerDefinition;
  public bool IsPressed(string name) {
   string button = "";
   foreach (var phase in Phases) if (phase.Unit <= core.Frame + 1) button = phase.Button;
   return button != "" && name == $"P2 CD32 pad {button}";
  }
  public int AxisValue(string name) => Definition.Axes.TryGetValue(name, out var axis) ? axis.Neutral : 0;
  public IReadOnlyCollection<(string Name, int Strength)> GetHapticsSnapshot() => Array.Empty<(string, int)>();
  public void SetHapticChannelStrength(string name, int strength) { }
 }
 static ulong GamePicture(Puae6Core core)
 {
  Require(core.BufferWidth == 756 && core.BufferHeight == 576);
  // Same active XRGB8888 signature as libretro; ignore presentation alpha.
  // Only signatures of visually checked landmarks are kept, never game pixels.
  ulong hash = 1469598103934665603;
  foreach (uint pixel in core.GetVideoBuffer().Take(core.BufferWidth * core.BufferHeight).Select(p => (uint)p & 0xffffff))
   for (int shift = 0; shift < 32; shift += 8) hash = unchecked((hash ^ (byte)(pixel >> shift)) * 1099511628211);
  return hash;
 }
 static void GameRestore(string[] args, Disc disc, Func<Disc, Puae6Core> open)
 {
  const ulong menu = 0x5a9b63564f6b4283, level = 0x4ee0d34fdf9a835b;
  string file = Path.Combine(args[0], "brian.state");
  using var core = open(disc);
  byte[] cut; string[] future;
  if (args[5] == "gamesave") {
   Run(core, 1400, new GameButtons(core)); Require(GamePicture(core) == menu);
   Run(core, 5100, new GameButtons(core)); Require(GamePicture(core) == level); // First jungle level and HUD.
   cut = Save(core); File.WriteAllBytes(file, cut);
   bool activeAudio = false;
   future = Run(core, 80, new GameButtons(core), (samples, frames) => activeAudio |= samples.Take(frames * 2).Any(s => s != 0));
   Require(activeAudio);
   ulong moved = GamePicture(core);
   File.WriteAllLines(file + ".future", future); File.WriteAllBytes(file + ".end", Save(core));
   Restore(core, cut); Run(core, 80); Require(GamePicture(core) != moved); // Right really moves Brian.
  } else { cut = File.ReadAllBytes(file); future = File.ReadAllLines(file + ".future"); }
  void Check(Puae6Core c) {
   Restore(c, cut);
   Require(Run(c, 80, new GameButtons(c)).SequenceEqual(future));
   Require(Save(c).SequenceEqual(File.ReadAllBytes(file + ".end"))); // All RAM and causal state.
  }
  Check(core); using var freshDisc = Mount(args[1]); using var next = open(freshDisc); Check(next);
  Console.WriteLine("Public BizHawk CD32 Brian gameplay/Right interaction/active audio/ticks/full snapshot/new sandbox/new process PASS");
 }
 sealed class CdButtons(ControllerDefinition definition, params string[] pressed) : IController
 {
  public ControllerDefinition Definition => definition;
  public bool IsPressed(string name) => pressed.Contains(name);
  public int AxisValue(string name) => Definition.Axes.TryGetValue(name,out var axis) ? axis.Neutral : 0;
  public IReadOnlyCollection<(string Name,int Strength)> GetHapticsSnapshot() => Array.Empty<(string,int)>();
  public void SetHapticChannelStrength(string name,int strength) { }
 }
 static Puae6Guest Guest(Puae6Core core) => (Puae6Guest)typeof(Puae6Core).GetField("_guest",BindingFlags.Instance|BindingFlags.NonPublic).GetValue(core);
 static Puae6Parameters CatalogParameters(CoreComm comm, params Disc[] discs)
 {
  var lp=new CoreLoadParameters<Puae6Settings,Puae6SyncSettings> { Comm=comm, SyncSettings=new Puae6SyncSettings() };
  for(int n=0;n<discs.Length;++n) lp.Discs.Add(new DiscAsset(discs[n]) { DiscName="label"+n });
  var method=typeof(Puae6Core).GetMethod("ParametersFrom",BindingFlags.NonPublic|BindingFlags.Static);
  return (Puae6Parameters)method.Invoke(null,new object[]{lp});
 }
 static void CatalogCore(string[] args, byte[] main, byte[] ext)
 {
  string a=args[1], b=Path.Combine(args[0],"catalog-b.iso"), alias=Path.Combine(args[0],"alias-a.iso");
  File.WriteAllBytes(b,Enumerable.Repeat((byte)0x6d,512*2048).ToArray()); File.Copy(a,alias,true);
  using var da=Mount(a); using var db=Mount(b); using var daa=Mount(alias);
  long reads=0; var messages=new List<string>();
  Puae6Core Open(params Disc[] discs)
  {
   var comm=new CoreComm(_=>{},(m,_)=>messages.Add(m),new Firmware(main,ext,true),CoreComm.CorePreferencesFlags.None,null);
   var p=CatalogParameters(comm,discs);
   Require(p.CdCatalog.Length==2&&p.CdSlots.SequenceEqual(discs[2]==discs[1]?new[]{0,1,1}:new[]{0,1,0}));
   var mixed=Puae6WhdLoad.Resolve(p); mixed.CdInfo=new P6CdInfo();
   try { Puae6WhdLoad.Resolve(mixed); throw new Exception("Mixed CD binding modes accepted"); } catch(InvalidOperationException) { }
   p.GuestDirectory=Path.GetDirectoryName(args[3]); p.GuestFileName=Path.GetFileName(args[3]); p.PinnedGuestSha256=args[4];
   var read=p.ReadCdCatalog; p.ReadCdCatalog=(ordinal,at,data,count)=>{ ++reads; return read(ordinal,at,data,count); };
   var core=new Puae6Core(comm,p);
   // The caller's parameter arrays and UI names cannot mutate the captured catalog.
   p.CdSlots[0]=1; p.CdNames[1]="mutated"; fixed(P6CdInfo* info=p.CdCatalog) info[0].Id[0]^=1;
   Require(!core.ControllerDefinition.BoolButtons.Contains(Controllers.NextDrive));
   Require(new[]{Controllers.NextSlot,Controllers.EjectDisk,Controllers.InsertDisk}.All(core.ControllerDefinition.BoolButtons.Contains));
   return core;
  }
  uint[] Status(Puae6Core c) { var values=new uint[4]; Require(Guest(c).Cd.Status(Guest(c),values)==P6.Ok); return values; }
  void Data(Puae6Core c,uint ordinal,bool present)
  {
   var g=Guest(c); var data=Enumerable.Repeat((byte)0xcc,2048).ToArray(); long before=reads;
   using var active=g.Host.EnterExit(); fixed(byte* p=data) {
    Require(g.Lib.p6w_cd_read_sector(0,2048,(IntPtr)p,2048)==(present?P6.Ok:P6.BadState));
   }
   Require(present?data.All(v=>v==(ordinal==0?0:0x6d)):data.All(v=>v==0xcc)&&reads==before);
  }
  void Buttons(Puae6Core c,params string[] names) => Run(c,1,new CdButtons(c.ControllerDefinition,names));
  using var core=Open(da,db,daa);
  byte[] initial=Save(core);
  foreach(string cut in new[]{"a","b","empty-b"})
  {
   string file=Path.Combine(args[0],"public-catalog-"+cut);
   bool present=cut!="empty-b"; uint ordinal=cut=="a"?0u:1u;
   string[] held=cut=="a"?new[]{Controllers.NextSlot}:new[]{present?Controllers.InsertDisk:Controllers.EjectDisk};
   if(args[5]=="catalogsave") {
    Restore(core,initial); Run(core,300); Require(reads>0);
    if(cut!="a") { Buttons(core,Controllers.NextSlot); Buttons(core); Buttons(core,Controllers.InsertDisk);
     if(!present) { Buttons(core); Buttons(core,Controllers.EjectDisk); } }
    else Buttons(core,Controllers.NextSlot);
    if(!Status(core).Take(3).SequenceEqual(new uint[]{2,ordinal,present?1u:0u}))
     throw new Exception($"CD cut {cut}: status {string.Join(",",Status(core))}");
    Data(core,ordinal,present);
    File.WriteAllBytes(file,Save(core)); ulong requests=core.MediaEventsSent;
    File.WriteAllLines(file+".future",Run(core,15,new CdButtons(core.ControllerDefinition,held)));
    Require(core.MediaEventsSent==requests); File.WriteAllBytes(file+".end",Save(core));
   }
   void Check(Puae6Core target)
   {
    for(int n=0; n<3&&(Status(target)[1]!=1||Status(target)[2]==0); ++n) { Buttons(target); Buttons(target,Controllers.NextSlot); Buttons(target); Buttons(target,Controllers.InsertDisk); }
    Require(Status(target)[1]==1&&Status(target)[2]==1); Data(target,1,true); // Restore really replaces an inserted B.
    Restore(target,File.ReadAllBytes(file));
    Require(Status(target).Take(3).SequenceEqual(new uint[]{2,ordinal,present?1u:0u})); Data(target,ordinal,present);
    Require(Run(target,15,new CdButtons(target.ControllerDefinition,held)).SequenceEqual(File.ReadAllLines(file+".future")));
    Require(Save(target).SequenceEqual(File.ReadAllBytes(file+".end")));
   }
   Check(core);
   using var freshA=Mount(a); using var freshB=Mount(b); using var freshAlias=Mount(alias); using var fresh=Open(freshAlias,freshB,freshA); Check(fresh);
  }
  // Slot2 aliases A: selection changes independently of the applied canonical ordinal.
  Restore(core,initial); Buttons(core,Controllers.NextSlot); Buttons(core); Buttons(core,Controllers.NextSlot); Buttons(core);
  Buttons(core,Controllers.EjectDisk); Buttons(core); Buttons(core,Controllers.InsertDisk); Data(core,0,true);
  Require(Status(core)[1]==0&&messages.Any(m=>m.Contains("label1"))&&!messages.Any(m=>m.Contains("mutated")));
  foreach(var discs in new[]{new[]{db,da,db},new[]{da,db,db}}) {
   using var wrong=Open(discs); var before=Save(wrong); long beforeReads=reads;
   try { Restore(wrong,initial); throw new Exception("Other catalog/map accepted"); } catch(StateRejectedException e) { Require(e.Reason==StateRejection.OtherMedia); }
   try { Puae6Movie.Validate(Puae6Movie.Header(core),wrong); throw new Exception("Other movie catalog/map accepted"); } catch(MovieRejectedException e) { Require(e.Reason==MovieRejection.OtherMedia); }
   Require(reads==beforeReads&&before.SequenceEqual(Save(wrong)));
  }
  using(var raw=new MemoryStream(initial)) using(var reader=new BinaryReader(raw)) {
   var payload=StateEnvelope.Read(reader,core.Identity); Buffer.BlockCopy(BitConverter.GetBytes(2),0,payload,Puae6Core.WrapperStateBytes-5,4);
   using var state=new MemoryStream(); using(var writer=new BinaryWriter(state,System.Text.Encoding.UTF8,true)) StateEnvelope.Write(writer,core.Identity,payload);
   var before=Save(core); long beforeReads=reads;
   try { Restore(core,state.ToArray()); throw new Exception("Invalid CD ordinal accepted"); } catch(StateRejectedException e) { Require(e.Reason==StateRejection.BadFields); }
   Require(before.SequenceEqual(Save(core))&&beforeReads==reads);
  }
  Console.WriteLine("Public BizHawk CD32 catalog A/B/A-alias/buttons/held/absent/future/fullsnapshot/newowners/process/identity PASS");
 }

#if PUBLIC_CHD
 static object Hidden(object target, string name, params object[] args) => target.GetType().GetMethod(name, BindingFlags.Instance|BindingFlags.NonPublic).Invoke(target,args);
 static void HostCheck(bool value, string phase) { if(!value) throw new Exception("Public CD host: "+phase); }
 static XmlGame Xml(string path) { using var file=new HawkFile(path); return XmlGame.Create(file); }
 static int OpenCdFile(string path)
 {
  using var ls=Process.Start(new ProcessStartInfo("/usr/sbin/lsof",$"-a -p {Environment.ProcessId} -Fn") { UseShellExecute=false, RedirectStandardOutput=true });
  string files=ls.StandardOutput.ReadToEnd(); ls.WaitForExit(); HostCheck(ls.ExitCode==0,"host file descriptor query");
  return files.Split('\n').Count(line=>line=="n"+path);
 }
 static void HostCd(string path)
 {
  string database=Path.GetDirectoryName(path); File.WriteAllText(Path.Combine(database,"mame_machines.txt"),"");
  BizHawk.Emulation.Cores.Arcades.MAME.MAMEMachineDB.Initialize(database);
  HostCheck(!BizHawk.Emulation.Cores.Arcades.MAME.MAMEMachineDB.IsMAMEMachine(Path.Combine(database,"warm.zip")),"client database init");
  File.WriteAllText(Path.Combine(database,"gamedb.txt"),"");
  Database.InitializeDatabase(database,database,true); // MakeGameFromDisc uses the real database acquisition barrier.
  var config=new Config(); config.PreferredCores["Amiga"]="PUAE6";
  var loader=new RomLoader(config,null);
  loader.OnLoadSettings+=(_,e)=>e.Settings=new Puae6Settings();
  loader.OnLoadSyncSettings+=(_,e)=>e.Settings=new Puae6SyncSettings();
  var xml=Xml(path); string image=xml.Assets[0].Path, extension=Path.GetExtension(image).ToLowerInvariant();
  bool nrg=extension==".nrg"; string selector=nrg?"UsePuae6Cd":"UsePuae6Chd", directSelector=nrg?"UsePuae6DirectCd":"UsePuae6DirectChd";
  HostCheck(xml.GI.System=="Amiga"&&xml.Assets[0].FileData.Length==0,"standard XML");
  foreach(var item in new[] { ("Amiga",(string)null,true),("Amiga","PUAE6",true),("Amiga","UAE",false),("PSX",(string)null,false),((string)null,"PUAE6",true),((string)null,(string)null,false) })
   HostCheck((bool)Hidden(loader,selector,image,item.Item1,item.Item2)==item.Item3,"selection");
  var assets=(List<IDiscAsset>)Hidden(loader,"LoadXmlDiscAssets",xml,null);
  var lease=(IOwnedDiscAsset)assets.Single();
  try
  {
   var firmware=new Firmware(new byte[512*1024],new byte[512*1024],true);
   var comm=new CoreComm(_=>{},(_,_)=>{},firmware,CoreComm.CorePreferencesFlags.None,null);
   var lp=new CoreLoadParameters<Puae6Settings,Puae6SyncSettings> { Comm=comm, Discs=assets, SyncSettings=new Puae6SyncSettings() };
   var factory=typeof(Puae6Core).GetMethod("ParametersFrom",BindingFlags.Static|BindingFlags.NonPublic);
   var p=(Puae6Parameters)factory.Invoke(null,new object[] { lp });
   HostCheck(p.CdInfo==null&&p.CdInfoV2.HasValue&&p.SyncSettings.Machine==Puae6Machine.CD32&&lp.SyncSettings.Machine==Puae6Machine.A500,"factory V2");
   var clone=Puae6WhdLoad.Resolve(p); var id=(byte[])Hidden(p,"CdIdentity");
   HostCheck(ReferenceEquals(HiddenOwner(p),lease)&&ReferenceEquals(HiddenOwner(clone),lease),"owner clones");
   HostCheck(((byte[])Hidden(clone,"CdIdentity")).SequenceEqual(id),"identity clone");
   var host=(P6CdHost)Hidden(clone,"CreateCdHost");
   HostCheck((bool)typeof(P6CdHost).GetField("regional",BindingFlags.Instance|BindingFlags.NonPublic).GetValue(host),"reopen V2 host");
   var descriptor=p.CdInfoV2.Value; var expectedId=new byte[32]; for(int i=0;i<32;++i) expectedId[i]=descriptor.Id[i];
   HostCheck(id.SequenceEqual(expectedId),"identity source");
   using(var output=File.Create(path+".actual")) {
    var buffer=new byte[2448]; for(ulong at=0;at<descriptor.Bytes;at+=2448) { HostCheck(p.ReadCd(at,buffer,2448),"read"); output.Write(buffer); }
   }
   HostCheck(assets[0].DiscData.Regions.All(r=>r.Start>=0),"regions");
   Console.WriteLine(Convert.ToHexString(id));
   for(int bad=0;bad<3;++bad) {
    var invalid=Puae6WhdLoad.Resolve(p); if(bad==0) invalid.CdInfo=new P6CdInfo(); else if(bad==1) invalid.ReadCd=null; else invalid.SyncSettings.Machine=Puae6Machine.A500;
    try { Puae6WhdLoad.Resolve(invalid); throw new Exception("Accepted invalid CD params"); } catch(InvalidOperationException) { }
   }
   var legacy=new Puae6Parameters { CdInfo=new P6CdInfo { Version=1, Size=(uint)sizeof(P6CdInfo) }, ReadCd=p.ReadCd, Firmware=p.Firmware, SyncSettings=p.SyncSettings.Clone() };
   HostCheck(Puae6WhdLoad.Resolve(legacy).CdInfoV2==null&&((byte[])Hidden(legacy,"CdIdentity")).All(b=>b==0),"legacy identity");
   string iso=path+".legacy.iso"; File.WriteAllBytes(iso,new byte[512*2048]); using var legacyDisc=Mount(iso);
   assets.Add(new DiscAsset(legacyDisc)); assets.Add(assets[0]);
   var catalog=(Puae6Parameters)factory.Invoke(null,new object[] { lp });
   HostCheck(catalog.CdInfo==null&&catalog.CdInfoV2==null&&catalog.CdCatalog==null&&catalog.CdCatalogV2.Length==2&&catalog.CdSlots.SequenceEqual(new[]{0,1,0}),"mixed regional/legacy catalog");
   var legacyInfo=catalog.CdCatalogV2[1]; HostCheck(legacyInfo.Version==2&&legacyInfo.Tracks[6]==1&&legacyInfo.Tracks[9]==0,"V1 policy in V2 transport");
   var catalogClone=Puae6WhdLoad.Resolve(catalog); catalog.CdSlots[1]=0;
   HostCheck(catalogClone.CdSlots.SequenceEqual(new[]{0,1,0})&&((byte[])Hidden(catalogClone,"CdIdentity")).SequenceEqual((byte[])Hidden(Puae6WhdLoad.Resolve(catalogClone),"CdIdentity")),"catalog clone");
   var legacyBytes=new byte[2448]; HostCheck(catalogClone.ReadCdCatalog(1,0,legacyBytes,2448)&&legacyBytes[15]==1,"mixed catalog real ISO read");
   assets.RemoveRange(1,2);
   int opened=OpenCdFile(image); HostCheck(opened==1,"one owned CD handle"); lease.Dispose();
   HostCheck(OpenCdFile(image)==0,"real FD closed"); lease.Dispose();
   HostCheck(OpenCdFile(image)==0,"lease idempotent");
   var refused=Enumerable.Repeat((byte)0xa5,2448).ToArray(); HostCheck(!p.ReadCd(0,refused,2448)&&refused.All(b=>b==0xa5),"disposed source");
   HostCheck(!catalogClone.ReadCdCatalog(0,0,refused,2448)&&refused.All(b=>b==0xa5),"disposed catalog source");
   HostCheck(OpenCdFile(image)==0,"disposed reads never reopen the source");
   firmware.Refuse=true; var errors=new List<string>(); loader.OnLoadError+=(_,e)=>errors.Add(e.Message);
   opened=OpenCdFile(image);
   HostCheck(!loader.LoadRom(path,comm,null),"constructor failure");
   HostCheck(errors.Any(e=>e.Contains("host-stop-before-guest")),"constructor cause: "+string.Join(" | ",errors));
   HostCheck(OpenCdFile(image)==opened,"failed construction closes owner");
   string badXml=path+".bad.xml"; File.WriteAllText(badXml,$"<BizHawk-XMLGame System=\"Amiga\" Name=\"bad\"><LoadAssets><LoadAsset FileName=\"{Path.GetFileName(image)}\"/><LoadAsset FileName=\"bad{extension}\"/></LoadAssets></BizHawk-XMLGame>");
   File.WriteAllBytes(Path.Combine(Path.GetDirectoryName(path),"bad"+extension),new byte[12]);
   opened=OpenCdFile(image);
   try { Hidden(loader,"LoadXmlDiscAssets",Xml(badXml),null); throw new Exception("Accepted broken second CD"); } catch(TargetInvocationException e) when(e.InnerException is IOException or NRG_Format.NRGParseException) { }
   HostCheck(OpenCdFile(image)==opened,"partial mount cleanup");
   string cue=path+".bad.cue"; File.WriteAllText(cue,"FILE \"missing.bin\" BINARY\n TRACK 01 MODE1/2352\n INDEX 01 00:00:00\n");
   var reported=new List<string>(); loader.OnLoadError+=(_,e)=>reported.Add(e.AttemptedCoreLoad);
   HostCheck(Hidden(loader,"MountDiscAsset",cue,"PSX",null)==null&&reported.SequenceEqual(new[]{"PSX"}),"legacy XML error system");
   // No content probing: the explicit platform/core pair selects the direct backend before mounting.
   foreach(var item in new[] {
    ((string)null,"PUAE6",(string)null,false),("","PUAE6",(string)null,false),
    ("PSX","PUAE6",(string)null,false),("Amiga","UAE",(string)null,false),
    ("Amiga","PUAE6",(string)null,true),("Amiga","PUAE6","UAE",false),
    ((string)null,"UAE","PUAE6",true),("PSX","UAE","PUAE6",true) }) {
    config.PreferredPlatformsForExtensions.Remove(extension);
    if(item.Item1!=null) config.PreferredPlatformsForExtensions[extension]=item.Item1;
    config.PreferredCores["Amiga"]=item.Item2;
    foreach(string name in new[] { image,Path.ChangeExtension(image,extension.ToUpperInvariant()) })
     HostCheck((bool)Hidden(loader,directSelector,name,item.Item3)==item.Item4,"direct explicit selection");
    HostCheck(!(bool)Hidden(loader,directSelector,cue,item.Item3),"direct selection is supported CD only");
   }
   config.PreferredPlatformsForExtensions.Remove(extension);
   config.PreferredPlatformsForExtensions[extension.ToUpperInvariant()]="Amiga"; config.PreferredCores["Amiga"]="PUAE6";
   HostCheck(!(bool)Hidden(loader,directSelector,image,null),"config extension keys keep their lowercase contract");
   config.PreferredPlatformsForExtensions.Remove(extension.ToUpperInvariant());
   foreach(string forced in new[] { (string)null,"PUAE6" }) {
    // Forced PUAE6 supplies Amiga even if the extension preference names another platform.
    config.PreferredPlatformsForExtensions[extension]=forced==null?"Amiga":"PSX";
    errors.Clear(); reported.Clear(); firmware.Asked.Clear();
    HostCheck(!loader.LoadRom(image,comm,null,forced),"direct constructor failure");
    HostCheck(errors.Count==1&&errors[0].Contains("host-stop-before-guest"),"direct reaches PUAE6 without another core");
    HostCheck(reported.SequenceEqual(new[] { "Amiga" })&&firmware.Asked.SequenceEqual(new[] { "CD32" }),"direct Amiga inventory");
    HostCheck(OpenCdFile(image)==opened,"direct failed construction closes owner");
    errors.Clear(); reported.Clear(); firmware.Asked.Clear();
    string invalid=Path.Combine(database,"bad"+extension);
    HostCheck(!loader.LoadRom(invalid,comm,null,forced),"direct invalid CD refusal");
    HostCheck(errors.Count==1&&reported.SequenceEqual(new[] { "Amiga" })&&firmware.Asked.Count==0,"mount failure retains Amiga before firmware");
    HostCheck(OpenCdFile(image)==opened&&OpenCdFile(invalid)==0,"invalid direct mount closes all owners");
   }
  }
  finally { lease.Dispose(); }
 }
 static object HiddenOwner(Puae6Parameters p) => typeof(Puae6Parameters).GetProperty("CdOwner",BindingFlags.Instance|BindingFlags.NonPublic).GetValue(p);
#endif
 static void Main(string[] args)
 {
#if PUBLIC_CHD
  if(args.Length==2&&args[0] is "host-chd" or "host-nrg") { HostCd(args[1]); return; }
  if(args.Length>=8&&args[5].StartsWith("chdpublic")) { PublicCd(args,".chd"); return; }
  if(args.Length>=8&&args[5].StartsWith("nrgpublic")) { PublicCd(args,".nrg"); return; }
#endif
  LoadRuntime(args[2]);
  string statePath = Path.Combine(args[0], "public-cd32.state");
  byte[] main = File.ReadAllBytes(args[6]), ext = File.ReadAllBytes(args[7]);
#if MOVIES
  if (args[5].StartsWith("movieswap")) { SwapMovies(args, main, ext); return; }
#endif
  if (args[5].StartsWith("catalog")) { CatalogCore(args, main, ext); return; }
  using var disc = Mount(args[1]);
  long reads = 0;
  var seed = Enumerable.Range(0, 1024).Select(i => (byte)(i * 17 + 9)).ToArray();
  Puae6Core Open(Disc image, bool combined = false, byte[] imported = null, bool fast = false) {
   var firmware = new Firmware(main, ext, combined);
   var comm = new CoreComm(_ => { }, (_, _) => { }, firmware, CoreComm.CorePreferencesFlags.None, null);
   var original = new Puae6SyncSettings { ControllerPort2 = Puae6Port2.Cd32, FastMemory = fast ? Puae6Fast.MB8 : Puae6Fast.Auto };
   var lp = new CoreLoadParameters<Puae6Settings, Puae6SyncSettings> { Comm = comm, SyncSettings = original };
   lp.Discs.Add(new DiscAsset(image));
   var method = typeof(Puae6Core).GetMethod("ParametersFrom", BindingFlags.NonPublic | BindingFlags.Static);
   Puae6Parameters Resolve() => (Puae6Parameters)method.Invoke(null, new object[] { lp });
   lp.Discs.AddRange(Enumerable.Repeat(lp.Discs[0], 16));
   try { Resolve(); throw new Exception("More than16 CD slots accepted"); }
   catch (TargetInvocationException e) when (e.InnerException is NotSupportedException) { Require(firmware.Asked.Count == 0); }
   lp.Discs.RemoveRange(1, 16);
   lp.Roms.Add(new MixedRom());
   try { Resolve(); throw new Exception("CD/floppy mixture accepted"); }
   catch (TargetInvocationException e) when (e.InnerException is NotSupportedException) { Require(firmware.Asked.Count == 0); }
   lp.Roms.Clear();
   var p = Resolve();
   Require(original.Machine == Puae6Machine.A500 && p.SyncSettings.Machine == Puae6Machine.CD32);
   Require(p.SyncSettings.FastMemory == original.FastMemory);
   Require(firmware.Asked.SequenceEqual(combined ? new[] { "CD32" } : new[] { "CD32", "CD32ext" }));
   p.GuestDirectory = Path.GetDirectoryName(args[3]); p.GuestFileName = Path.GetFileName(args[3]); p.PinnedGuestSha256 = args[4];
   var reader = p.ReadCd; p.ReadCd = (at, data, count) => { ++reads; return reader(at, data, count); };
   var core = new Puae6Core(comm, p);
   if (args[5].StartsWith("game")) return core;
   if (args[5].StartsWith("fr")) { core.StoreSaveRam(seed); return core; }
   if (args[5].StartsWith("movie")) { if (imported != null) core.StoreSaveRam(imported); return core; }
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
  if (args[5].StartsWith("fr")) { FastCd32(args, fast => Open(disc, true, null, fast)); return; }
  if (args[5].StartsWith("game")) { GameRestore(args, disc, image => Open(image, true)); return; }
#if MOVIES
  if (args[5].StartsWith("movie")) { Movies(args, nv => Open(disc, true, nv)); return; }
#endif
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
