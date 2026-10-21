#if PUBLIC_CHD
using System.Reflection;
using BizHawk.Client.Common;
using BizHawk.Common;
using BizHawk.Emulation.Common;
using Puae6;

internal static unsafe partial class Program
{
 static Puae6Guest CdGuest(Puae6Core core) => (Puae6Guest)typeof(Puae6Core)
  .GetField("_guest", BindingFlags.Instance|BindingFlags.NonPublic).GetValue(core);
 static void CdRead(Puae6Core core, byte[] expected, int pregap, string boundaries)
 {
  var guest=CdGuest(core);
  Require((bool)typeof(P6CdHost).GetField("regional",BindingFlags.Instance|BindingFlags.NonPublic).GetValue(guest.Cd));
  var status=new uint[4]; Require(guest.Cd.Status(guest,status)==P6.Ok);
  if(!status.Take(3).SequenceEqual(new uint[] { 1,0,1 })) throw new Exception($"CD count/ordinal/presence: {string.Join(",",status)}");
  var offsets=new List<int> { 0,19577,expected.Length-2448 };
  foreach(int lba in boundaries.Split(',',StringSplitOptions.RemoveEmptyEntries).Select(int.Parse)) {
   offsets.Add(lba*2448);
   foreach(uint view in new uint[] { 2352,2448 }) {
    var output=new byte[view];
    using(guest.Host.EnterExit()) fixed(byte* p=output) Require(guest.Lib.p6w_cd_read_sector((uint)lba,view,(IntPtr)p,view)==P6.Ok);
    Require(output.SequenceEqual(expected.Skip(lba*2448).Take((int)view)));
   }
  }
  if(boundaries.Length!=0) {
   var info=(P6CdInfoV2)typeof(P6CdHost).GetField("infoV2",BindingFlags.Instance|BindingFlags.NonPublic).GetValue(guest.Cd);
   Require(info.Version==2 && info.Count==3 && info.Leadout==312);
   Require(info.Tracks[0]==0 && info.Tracks[1]==0 && info.Tracks[2]==3 && info.Tracks[3]==1 && info.Tracks[4]==4);
   Require(info.Tracks[12]==3 && info.Tracks[13]==153 && info.Tracks[14]==157 && info.Tracks[15]==2 && info.Tracks[16]==4);
   Require(info.Tracks[24]==157 && info.Tracks[25]==307 && info.Tracks[26]==312 && info.Tracks[27]==2 && info.Tracks[28]==4);
  }
  if(pregap!=0) {
   var info=(P6CdInfoV2)typeof(P6CdHost).GetField("infoV2",BindingFlags.Instance|BindingFlags.NonPublic).GetValue(guest.Cd);
   Require(info.Version==2 && info.Count==2 && info.Leadout==(uint)(pregap+12));
   Require(info.Tracks[0]==0 && info.Tracks[1]==pregap && info.Tracks[2]==pregap+3 && info.Tracks[3]==0 && info.Tracks[4]==0);
   Require(info.Tracks[12]==pregap+3 && info.Tracks[13]==pregap+3 && info.Tracks[15]==1 && info.Tracks[16]==4);
   foreach(int lba in new[] { 0,pregap-1,pregap }) {
    offsets.Add(lba*2448);
    foreach(uint view in new uint[] { 2352,2448 }) {
     var output=new byte[view];
     using(guest.Host.EnterExit()) fixed(byte* p=output) Require(guest.Lib.p6w_cd_read_sector((uint)lba,view,(IntPtr)p,view)==P6.Ok);
     if(!output.SequenceEqual(expected.Skip(lba*2448).Take((int)view))) throw new Exception($"HTOA sector {lba}/{view} differs");
    }
   }
  }
  foreach(int at in offsets) {
   var output=new byte[2448]; Require(guest.Cd.ReadAt(guest,(ulong)at,output)==P6.Ok);
   Require(output.SequenceEqual(expected.Skip(at).Take(output.Length)));
  }
 }
 static string CdRam(Puae6Core core)
 {
  var chip=core.ServiceProvider.GetService<IMemoryDomains>().MainMemory;
  return Hash(Enumerable.Range(0,32).Select(n=>chip.PeekByte(n*(chip.Size-1)/31)).ToArray());
 }
 static void PublicCd(string[] args, string extension)
 {
  LoadRuntime(args[2]);
  string work=args[0], xml=args[1], image=Path.ChangeExtension(xml,extension), file=Path.Combine(work,"public-cd.state");
  bool nrg=extension==".nrg";
  byte[] main=File.ReadAllBytes(args[6]), ext=File.ReadAllBytes(args[7]);
  byte[] expected=File.ReadAllBytes(Path.Combine(work,"wire.bin"));
  int pregap=!nrg&&args.Length>8?int.Parse(args[8]):0;
  string boundaries=nrg?args[8]:"";
  bool direct=Path.GetExtension(xml).Equals(extension,StringComparison.OrdinalIgnoreCase);
  byte[] seed=Enumerable.Range(0,1024).Select(n=>(byte)(n*17+9)).ToArray();
  File.WriteAllText(Path.Combine(work,"mame_machines.txt"),"");
  File.WriteAllText(Path.Combine(work,"gamedb.txt"),"");
  Database.InitializeDatabase(work,work,true); // Direct disc identification acquires the real game database.
  BizHawk.Emulation.Cores.Arcades.MAME.MAMEMachineDB.Initialize(work);
  Require(!BizHawk.Emulation.Cores.Arcades.MAME.MAMEMachineDB.IsMAMEMachine(Path.Combine(work,"warm.zip")));
  Puae6Core Open(string path, byte[] nvram)
  {
   var config=new Config(); config.PreferredCores["Amiga"]="PUAE6";
   if(direct) config.PreferredPlatformsForExtensions[extension]="Amiga";
   var loader=new RomLoader(config,null); var errors=new List<string>();
   loader.OnLoadSettings+=(_,e)=>e.Settings=new Puae6Settings();
   loader.OnLoadSyncSettings+=(_,e)=>e.Settings=new Puae6SyncSettings();
   loader.OnLoadError+=(_,e)=>errors.Add(e.Message);
   var firmware=new Firmware(main,ext,false);
   var comm=new CoreComm(_=>{},(_,_)=>{},firmware,CoreComm.CorePreferencesFlags.None,null);
   if(!loader.LoadRom(path,comm,null)) throw new Exception("Public CD load: "+string.Join(" | ",errors));
   var core=loader.LoadedEmulator as Puae6Core ?? throw new Exception("CD load did not select PUAE6");
   try {
    Require(core.GetSyncSettings().Machine==Puae6Machine.CD32 && core.Frame==0);
    Require(firmware.Asked.SequenceEqual(new[] { "CD32", "CD32ext" }));
    var guest=CdGuest(core);
    core.StoreSaveRam(nvram); // CD32 imports EEPROM in place; immutable CD data and its owner stay bound.
    Require(ReferenceEquals(guest,CdGuest(core)) && core.Frame==0);
    Require(core.CloneSaveRam(false).SequenceEqual(nvram) && !core.SaveRamModified);
    Require(OpenCdFile(Path.ChangeExtension(path,extension))==1);
    return core;
   } catch { core.Dispose(); throw; }
  }
  using(var core=Open(xml,seed)) {
   CdRead(core,expected,pregap,boundaries);
   byte[] cut; string[] future;
   if(args[5].EndsWith("save")) {
    Run(core,300); cut=Save(core); File.WriteAllBytes(file,cut);
    File.WriteAllBytes(file+".nvram",core.CloneSaveRam());
    future=Run(core,15).Concat(new[] { CdRam(core) }).ToArray();
    File.WriteAllLines(file+".future",future); File.WriteAllBytes(file+".end",Save(core));
    var nv=core.ServiceProvider.GetService<IMemoryDomains>()["CD32 NVRAM"];
    nv.PokeByte(13,(byte)(nv.PeekByte(13)^255)); Require(core.SaveRamModified);
   } else { cut=File.ReadAllBytes(file); future=File.ReadAllLines(file+".future"); }
   void Check(Puae6Core target)
   {
    Restore(target,cut); Require(target.CloneSaveRam(false).SequenceEqual(File.ReadAllBytes(file+".nvram")));
    CdRead(target,expected,pregap,boundaries);
    Require(Run(target,15).Concat(new[] { CdRam(target) }).SequenceEqual(future));
    Require(Save(target).SequenceEqual(File.ReadAllBytes(file+".end")));
    foreach(var (video,audio) in new[] { (false,false), (true,false), (false,true) }) {
     Restore(target,cut); var idle=new Idle(target.ControllerDefinition);
     for(int n=0;n<5;++n) {
      Require(target.FrameAdvance(idle,video,audio)); var row=future[n].Split(' ');
      Require(target.CycleCount.ToString()==row[1]);
      if(video) { var pixels=new byte[target.BufferWidth*target.BufferHeight*4]; Buffer.BlockCopy(target.GetVideoBuffer(),0,pixels,0,pixels.Length); Require(Hash(pixels)==row[2]); }
      target.GetSamplesSync(out var samples,out int frames);
      if(audio) { var pcm=new byte[frames*4]; Buffer.BlockCopy(samples,0,pcm,0,pcm.Length); Require(Hash(pcm)==row[3]); }
      else Require(frames==0);
     }
     Require(Run(target,10).Concat(new[] { CdRam(target) }).SequenceEqual(future.Skip(5)));
     Require(Save(target).SequenceEqual(File.ReadAllBytes(file+".end")));
    }
   }
   Check(core);
   // Close the old Disc owner as well as its sandbox before reopening through RomLoader.
  }
  Require(OpenCdFile(image)==0);
  using(var fresh=Open(xml,seed)) { // No frame before the cold LOAD, also in the second process.
   var cut=File.ReadAllBytes(file); Restore(fresh,cut); CdRead(fresh,expected,pregap,boundaries);
   Require(Run(fresh,15).Concat(new[] { CdRam(fresh) }).SequenceEqual(File.ReadAllLines(file+".future")));
   Require(Save(fresh).SequenceEqual(File.ReadAllBytes(file+".end")));
   Require(fresh.CloneSaveRam(false).SequenceEqual(File.ReadAllBytes(file+".nvram")));
  }
  Require(OpenCdFile(image)==0);
  using(var durable=Open(xml,File.ReadAllBytes(file+".nvram"))) CdRead(durable,expected,pregap,boundaries);
  Require(OpenCdFile(image)==0);
  foreach(string name in pregap==0&&!nrg?new[] { "other" }:new[] { "other","shifted" }) {
  string otherXml=Path.Combine(Path.GetDirectoryName(xml),name+(direct?extension:".xml")), otherImage=Path.ChangeExtension(otherXml,extension);
  using(var other=Open(otherXml,seed)) {
   var before=Save(other); var nvram=other.CloneSaveRam(false);
   try { Restore(other,File.ReadAllBytes(file)); throw new Exception("Other CD accepted"); }
   catch(StateRejectedException e) { Require(e.Reason==StateRejection.OtherMedia); }
   Require(before.SequenceEqual(Save(other)) && nvram.SequenceEqual(other.CloneSaveRam(false)));
  }
  Require(OpenCdFile(otherImage)==0 && OpenCdFile(image)==0);
  }
  Console.WriteLine($"Public {extension[1..].ToUpperInvariant()} Waterbox reads/SaveRAM/cold owner/new process/full future/FD close PASS");
 }
}
#endif
