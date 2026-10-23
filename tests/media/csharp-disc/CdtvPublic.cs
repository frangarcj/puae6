#if PUBLIC_CHD
using System.Buffers.Binary;
using System.Text;
using System.Security.Cryptography;
using System.Reflection;
using BizHawk.Client.Common;
using BizHawk.Common;
using BizHawk.Emulation.Common;
using BizHawk.Emulation.Cores;
using Puae6;

internal static unsafe partial class Program
{
 static void CdtvRequire(bool ok,[System.Runtime.CompilerServices.CallerLineNumber] int line=0) { if(!ok) throw new Exception($"Public CDTV assertion line {line}"); }
 static Puae6Core OpenCdtv(string[] args, string path, Puae6SyncSettings settings=null, Firmware provider=null)
 {
  provider??=new Firmware(File.ReadAllBytes(args[6]),File.ReadAllBytes(args[7]),false);
  var comm=new CoreComm(_=>{},(_,_)=>{},provider,CoreComm.CorePreferencesFlags.None,null);
  settings??=new Puae6SyncSettings { Machine=Puae6Machine.CDTV };
  if(path==null) return new Puae6Core(new CoreLoadParameters<Puae6Settings,Puae6SyncSettings> { Comm=comm,SyncSettings=settings });
  var config=new Config(); config.PreferredCores["Amiga"]="PUAE6";
  var loader=new RomLoader(config,null); var errors=new List<string>();
  loader.OnLoadSettings+=(_,e)=>e.Settings=new Puae6Settings(); loader.OnLoadSyncSettings+=(_,e)=>e.Settings=settings;
  loader.OnLoadError+=(_,e)=>errors.Add(e.Message);
  if(!loader.LoadRom(path,comm,null,"PUAE6")) throw new InvalidOperationException("Public CDTV load: "+string.Join(" | ",errors));
  return loader.LoadedEmulator as Puae6Core ?? throw new Exception("CDTV did not use PUAE6");
 }
 static byte[] CdtvRam(Puae6Core core)
 {
  var ram=core.ServiceProvider.GetService<IMemoryDomains>().MainMemory; CdtvRequire(ram.Size==1024*1024);
  var bytes=new byte[ram.Size]; ram.BulkPeekByte(0L.RangeTo(ram.Size-1),bytes); return bytes;
 }
 static uint CdtvLive(Puae6Core core,byte[] nonce)
 {
  var ram=CdtvRam(core); var signature=Encoding.ASCII.GetBytes("P6CDTV_BOOT_v1!\0"); uint value=0; int found=0;
  for(int at=0;at+36<=ram.Length;at+=2)
   if(ram.AsSpan(at,16).SequenceEqual(signature)&&ram.AsSpan(at+16,16).SequenceEqual(nonce)) {
    uint count=BinaryPrimitives.ReadUInt32BigEndian(ram.AsSpan(at+32,4)); if(count!=0) { ++found; value=count; }
   }
  CdtvRequire(found<=1); return value;
 }
 static int CdtvPending(Puae6Core core) => (int)typeof(Puae6Core).GetField("_cdPending",BindingFlags.NonPublic|BindingFlags.Instance).GetValue(core);
 static uint[] CdtvStatus(Puae6Core core) { var words=new uint[4]; CdtvRequire(CdGuest(core).Cd.Status(CdGuest(core),words)==P6.Ok); return words; }
 static string[] CdtvFuture(Puae6Core core,byte[] nonce,Func<long> reads,bool catalog=false)
 {
  uint before=CdtvLive(core,nonce); CdtvRequire(before>0); long count=reads(); var rows=new List<string>();
  ulong sequence=core.MediaEventsSent;
  for(int i=0;i<(catalog?120:16);++i) {
   var input=catalog?new CdButtons(core.ControllerDefinition,i==0?new[]{Controllers.InsertDisk,Controllers.NextSlot}:new[]{Controllers.InsertDisk}):null;
   int samples=0; long ticks=core.CycleCount; var av=Run(core,1,input,audioObserver:(_,n)=>samples=n).Single();
   CdtvRequire(samples>0&&core.CycleCount>ticks&&core.BufferWidth>0&&core.BufferHeight>0);
   rows.Add($"{av} {core.BufferWidth} {core.BufferHeight} {core.VsyncNumerator} {core.VsyncDenominator} {core.LagCount} {core.IsLagFrame} {samples} {Hash(CdtvRam(core))}");
  }
  if(catalog) {
   CdtvRequire(CdtvPending(core)==-1&&CdtvStatus(core).SequenceEqual(new uint[]{2,1,1,1})&&core.MediaEventsSent==sequence+1);
   var data=new byte[2048]; using var active=CdGuest(core).Host.EnterExit();
   var parameters=(Puae6Parameters)typeof(Puae6Core).GetField("_parameters",BindingFlags.NonPublic|BindingFlags.Instance).GetValue(core);
   fixed(byte* p=data) CdtvRequire(CdGuest(core).Lib.p6w_cd_read_sector(parameters.CdCatalog[0].Leadout+5,2048,(IntPtr)p,2048)==P6.Ok);
   CdtvRequire(data.All(b=>b==0xa5));
  }
  CdtvRequire(CdtvLive(core,nonce)>before); rows.Add((reads()-count).ToString(System.Globalization.CultureInfo.InvariantCulture)); return rows.ToArray();
 }
 static void CdtvCatalogEdges(Puae6Core core,byte[] cut,Func<long> reads)
 {
  void Buttons(params string[] names) => Run(core,1,new CdButtons(core.ControllerDefinition,names));
  Restore(core,cut); Buttons(Controllers.EjectDisk); Run(core,120);
  CdtvRequire(CdtvPending(core)==-1&&CdtvStatus(core).SequenceEqual(new uint[]{2,0,0,1}));
  Restore(core,cut); Buttons(Controllers.NextSlot);
  CdtvRequire(CdtvPending(core)==1&&CdtvStatus(core)[2]==0);
  Buttons(Controllers.InsertDisk); CdtvRequire(CdtvPending(core)==0&&CdtvStatus(core)[2]==0);
  Run(core,120); CdtvRequire(CdtvPending(core)==-1&&CdtvStatus(core).SequenceEqual(new uint[]{2,0,1,1}));
  void Refuse(byte[] invalid,StateRejection reason) {
   var before=Save(core); long count=reads();
   try { Restore(core,invalid); throw new Exception("Invalid CDTV state accepted"); }
   catch(StateRejectedException e) { CdtvRequire(e.Reason==reason); }
   CdtvRequire(reads()==count&&before.SequenceEqual(Save(core)));
  }
  foreach(int pending in new[]{-2,2,1}) {
   using var input=new BinaryReader(new MemoryStream(cut)); var payload=StateEnvelope.Read(input,core.Identity);
   BitConverter.GetBytes(pending).CopyTo(payload,Puae6Core.WrapperStateBytes-9);
   if(pending==1) payload[Puae6Core.WrapperStateBytes-1]=1; // Pending and present cannot coexist.
   using var output=new MemoryStream(); using(var writer=new BinaryWriter(output,Encoding.UTF8,true)) StateEnvelope.Write(writer,core.Identity,payload);
   Refuse(output.ToArray(),StateRejection.BadFields);
  }
  var old=cut.ToArray(); BitConverter.GetBytes(1u).CopyTo(old,4); Refuse(old,StateRejection.WrongVersion);
 }
 static void CdtvControls(Puae6Core core)
 {
  var started=typeof(Puae6Core).GetField("_started",BindingFlags.NonPublic|BindingFlags.Instance); bool wasStarted=(bool)started.GetValue(core);
  var before=Save(core); Func<long> reads=CdGuest(core).Cd==null?()=>0L:WatchCd(core,false); long count=reads();
  foreach(string name in new[]{Controllers.NextDrive,Controllers.NextSlot,Controllers.EjectDisk,Controllers.InsertDisk}) {
   if(CdGuest(core).Cd!=null&&name!=Controllers.NextDrive) { CdtvRequire(core.ControllerDefinition.BoolButtons.Contains(name)); continue; }
   CdtvRequire(!core.ControllerDefinition.BoolButtons.Contains(name));
   try { core.FrameAdvance(new CdButtons(core.ControllerDefinition,name),true,true); throw new Exception("CDTV control accepted"); }
   catch(NotSupportedException e) { CdtvRequire(e.Message.Contains("CDTV")); }
   CdtvRequire(before.SequenceEqual(Save(core))&&reads()==count&&(bool)started.GetValue(core)==wasStarted);
  }
  CdtvRequire(core.CloneSaveRam(false)==null&&!core.SaveRamModified&&core.ServiceProvider.GetService<ISaveRam>()==null);
  try { core.StoreSaveRam(new byte[1024]); throw new Exception("Fake CDTV EEPROM accepted"); }
  catch(SaveRamRejectedException e) { CdtvRequire(e.Reason==SaveRamRejection.WritesDisabled); }
  CdtvRequire(before.SequenceEqual(Save(core)));
 }
 static void CdtvPublic(string[] args)
 {
  LoadRuntime(args[2]); string work=args[0], path=args[1];
  File.WriteAllText(Path.Combine(work,"mame_machines.txt"),"");
  File.WriteAllText(Path.Combine(work,"gamedb.txt"),"");
  Database.InitializeDatabase(work,work,true);
  BizHawk.Emulation.Cores.Arcades.MAME.MAMEMachineDB.Initialize(work);
  CdtvRequire(!BizHawk.Emulation.Cores.Arcades.MAME.MAMEMachineDB.IsMAMEMachine(Path.Combine(work,"warm.zip")));
  byte[] main=File.ReadAllBytes(args[6]),ext=File.ReadAllBytes(args[7]);
  if(args[5]=="cdtvpublicnegative") { CdtvNegatives(args,main,ext); return; }
  byte[] nonce=Convert.FromHexString(args[8]); string state=Path.Combine(work,"cdtv-public.state");
  bool catalog=args[5].StartsWith("cdtvpubliccatalog");
  var provider=new Firmware(main,ext,false); int opened=OpenCdFile(Path.Combine(work,"original.iso"));
  using(var core=OpenCdtv(args,path,provider:provider)) {
   CdtvRequire(core.Frame==0&&core.GetSyncSettings().Machine==Puae6Machine.CDTV&&core.Identity.Machine=="CDTV");
   CdtvRequire(provider.Asked.SequenceEqual(new[]{"CDTV","CDTVext27"}));
   var captured=(Puae6Parameters)typeof(Puae6Core).GetField("_parameters",BindingFlags.NonPublic|BindingFlags.Instance).GetValue(core);
   CdtvRequire(catalog ? captured.CdCatalog?.Length==2&&captured.CdSlots.SequenceEqual(new[]{0,1,0}) : captured.CdInfo.HasValue&&!captured.CdInfoV2.HasValue&&!captured.CdInfoV3.HasValue);
   CdtvRequire(core.Identity.FirmwareSha256.SequenceEqual(SHA256.HashData(ext.Concat(main).ToArray())));
   CdtvControls(core); var reads=WatchCd(core,catalog);
   Array.Fill(main,(byte)0xa5); Array.Fill(ext,(byte)0x5a); // The provider's caller no longer owns the machine's bytes.
   if(args[5].EndsWith("save")) {
    uint live=0; int units=0;
    for(;units<1000&&live==0;++units) { Run(core,1); if(units%10==9) live=CdtvLive(core,nonce); }
    CdtvRequire(live>0&&reads()>0);
    if(catalog) {
     Run(core,1,new CdButtons(core.ControllerDefinition,Controllers.NextSlot)); Run(core,1);
     ulong count=core.MediaEventsSent; Run(core,1,new CdButtons(core.ControllerDefinition,Controllers.InsertDisk));
     CdtvRequire(CdtvPending(core)==1&&CdtvStatus(core).SequenceEqual(new uint[]{2,0,0,1})&&core.MediaEventsSent==count+1);
    }
    File.WriteAllBytes(state,Save(core));
    File.WriteAllLines(state+".future",CdtvFuture(core,nonce,reads,catalog)); File.WriteAllBytes(state+".end",Save(core));
    Console.WriteLine($"Public CDTV nonce/Delay boot by unit {units}");
   }
   void Check(Puae6Core target) {
    var count=WatchCd(target,catalog); Restore(target,File.ReadAllBytes(state)); CdtvRequire(Save(target).SequenceEqual(File.ReadAllBytes(state)));
    CdtvControls(target); CdtvRequire(CdtvFuture(target,nonce,count,catalog).SequenceEqual(File.ReadAllLines(state+".future")));
    CdtvRequire(Save(target).SequenceEqual(File.ReadAllBytes(state+".end")));
   }
   Check(core);
   using(var fresh=OpenCdtv(args,catalog?path:Path.Combine(work,"original.cue"))) { CdtvRequire(fresh.Frame==0); Check(fresh); }
   if(catalog) CdtvCatalogEdges(core,File.ReadAllBytes(state),reads);
   using(var other=OpenCdtv(args,Path.Combine(work,"other.iso"))) {
    var before=Save(other); var count=WatchCd(other,false); long calls=count();
    try { Restore(other,File.ReadAllBytes(state)); throw new Exception("Other CDTV image accepted"); }
    catch(StateRejectedException e) { CdtvRequire(e.Reason==StateRejection.OtherMedia); }
    CdtvRequire(before.SequenceEqual(Save(other))&&calls==count());
   }
  }
  CdtvRequire(OpenCdFile(Path.Combine(work,"original.iso"))==opened&&OpenCdFile(Path.Combine(work,"other.iso"))==0);
  Console.WriteLine("Public CDTV loader/nonce/Delay/local/new sandbox/new process/full future/identity/lease PASS");
  if(catalog) Console.WriteLine("Public CDTV catalog/pending/held/selection/cancel/retarget/cold/fullfuture PASS");
 }
 static void CdtvNegatives(string[] args,byte[] main,byte[] ext)
 {
  string work=args[0],path=args[1],image=Path.Combine(work,"original.iso"); int opened=OpenCdFile(image);
  void Refuse(string content,Puae6SyncSettings settings,Firmware firmware,string reason) {
   try { using var unexpected=OpenCdtv(args,content,settings,firmware); throw new Exception("Invalid CDTV load accepted"); }
   catch(Exception e) when(e is InvalidOperationException or System.IO.InvalidDataException) {
    if(!e.Message.Contains(reason)) throw new Exception($"Expected CDTV refusal containing '{reason}', received {e.GetType().Name}: {e.Message}; firmware requests: {string.Join(", ",firmware.Asked)}",e);
   }
   CdtvRequire(OpenCdFile(image)==opened&&OpenCdFile(Path.Combine(work,"other.iso"))==0);
  }
  foreach(string missing in new[]{"CDTV","CDTVext27"}) Refuse(path,null,new Firmware(main,ext,false) { Missing=missing },"Missing firmware "+missing);
  foreach(bool first in new[]{true,false}) {
   byte[] a=main.ToArray(),b=ext.ToArray(); (first?a:b)[17]^=1;
   Refuse(path,null,new Firmware(a,b,false),"raw KS1.3 ID6");
  }
  Refuse(path,null,new Firmware(main[..^1],ext,false),"raw KS1.3 ID6");
  Refuse(path,null,new Firmware(main,ext[..^1],false),"raw KS1.3 ID6");
  foreach(var settings in new[]{
   new Puae6SyncSettings { Machine=Puae6Machine.CDTV,CpuCompatibility=Puae6CpuCompatibility.Compatible },
   new Puae6SyncSettings { Machine=Puae6Machine.CDTV,FastMemory=Puae6Fast.MB8 },
   new Puae6SyncSettings { Machine=Puae6Machine.CDTV,ChipMemory=Puae6Chip.MB2 },
   new Puae6SyncSettings { Machine=Puae6Machine.CDTV,PhysicalFloppyDrives=Puae6FloppyDrives.One },
   new Puae6SyncSettings { Machine=Puae6Machine.CDTV,Cartridge=Puae6Cartridge.HrtMon },
   new Puae6SyncSettings { Machine=Puae6Machine.CDTV,FloppyWrites=true },
   new Puae6SyncSettings { Machine=Puae6Machine.CDTV,SlowMemory=Puae6Slow.KB512 },
   new Puae6SyncSettings { Machine=Puae6Machine.CDTV,FpuModel=Puae6Fpu.M68881 },
   new Puae6SyncSettings { Machine=Puae6Machine.CDTV,Z3Memory=Puae6Z3Memory.MB16 }}) {
   var provider=new Firmware(main,ext,false); Refuse(path,settings,provider,"CDTV requires stock exact 68000"); CdtvRequire(provider.Asked.Count==0);
  }
  using(var empty=OpenCdtv(args,null)) {
   CdtvRequire(empty.Frame==0&&empty.Identity.Machine=="CDTV"&&empty.ServiceProvider.GetService<ISaveRam>()==null);
   CdtvControls(empty); Run(empty,1); CdtvRequire(empty.Frame==1&&empty.CloneSaveRam()==null&&!empty.SaveRamModified);
  }
  using(var core=OpenCdtv(args,path)) {
   CdtvControls(core); var before=Save(core);
   using var other=OpenCdtv(args,path,new Puae6SyncSettings { Machine=Puae6Machine.CDTV,RandomSeed=2 });
   var saved=Save(other); try { Restore(core,saved); throw new Exception("Other CDTV settings accepted"); }
   catch(StateRejectedException e) { CdtvRequire(e.Reason==StateRejection.OtherConfig); }
   CdtvRequire(before.SequenceEqual(Save(core)));
  }
  CdtvRequire(OpenCdFile(image)==opened&&OpenCdFile(Path.Combine(work,"other.iso"))==0); Console.WriteLine("Public CDTV firmware/profile/fixed controls/no content/identity/close PASS");
 }
}
#endif
