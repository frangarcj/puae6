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
 static Puae6Core OpenPublicCd(string path, byte[] nvram, byte[] main, byte[] ext, string extension, bool direct)
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
 static Func<long> WatchCd(Puae6Core core,bool catalog)
 {
  long calls=0; var host=CdGuest(core).Cd; var field=typeof(P6CdHost).GetField(catalog?"readCatalog":"read",BindingFlags.Instance|BindingFlags.NonPublic);
  if(catalog) { var read=(Func<uint,ulong,byte[],int,bool>)field.GetValue(host); field.SetValue(host,(Func<uint,ulong,byte[],int,bool>)((n,at,data,count)=>{++calls; return read(n,at,data,count);})); }
  else { var read=(Func<ulong,byte[],int,bool>)field.GetValue(host); field.SetValue(host,(Func<ulong,byte[],int,bool>)((at,data,count)=>{++calls; return read(at,data,count);})); }
  return ()=>calls;
 }
 static uint[] PublicCdStatus(Puae6Core core) { var status=new uint[4]; Require(CdGuest(core).Cd.Status(CdGuest(core),status)==P6.Ok); return status; }
 static void SelectPublicCd(Puae6Core core,uint slot)
 {
  Run(core,1,new CdButtons(core.ControllerDefinition,Controllers.EjectDisk)); Run(core,1);
  for(int n=0;n<4&&(int)typeof(Puae6Core).GetField("_currentSlot",BindingFlags.Instance|BindingFlags.NonPublic).GetValue(core)!=slot;++n) { Run(core,1,new CdButtons(core.ControllerDefinition,Controllers.NextSlot)); Run(core,1); }
  Run(core,1,new CdButtons(core.ControllerDefinition,Controllers.InsertDisk)); Run(core,1);
  Require(PublicCdStatus(core)[1]==(slot==3?0:slot)&&PublicCdStatus(core)[2]==1);
 }
 static void MultiCdParameters(Puae6Core core,bool catalog)
 {
  var captured=(Puae6Parameters)typeof(Puae6Core).GetField("_parameters",BindingFlags.Instance|BindingFlags.NonPublic).GetValue(core);
  var p=new Puae6Parameters { CdInfoV3=captured.CdInfoV3, CdCatalogV3=captured.CdCatalogV3?.ToArray(), CdSlots=(int[])captured.CdSlots.Clone(), CdNames=(string[])captured.CdNames.Clone(),
   ReadCd=captured.ReadCd, ReadCdCatalog=captured.ReadCdCatalog, Firmware=new byte[1], SyncSettings=core.GetSyncSettings() };
  var clone=Puae6WhdLoad.Resolve(p); var id=(byte[])Hidden(clone,"CdIdentity");
  if(catalog) { var changed=p.CdCatalogV3[0]; changed.Id[0]^=1; p.CdCatalogV3[0]=changed; p.CdSlots[0]=1; p.CdNames[0]="mutated"; Require(clone.CdSlots.SequenceEqual(new[]{0,1,2,0})&&!clone.CdNames.Contains("mutated")); }
  else { var changed=p.CdInfoV3.Value; changed.Id[0]^=1; p.CdInfoV3=changed; }
  Require(((byte[])Hidden(clone,"CdIdentity")).SequenceEqual(id));
  if(catalog) {
   Require(!ReferenceEquals(captured.CdCatalogV3,typeof(P6CdHost).GetField("catalogV3",BindingFlags.Instance|BindingFlags.NonPublic).GetValue(CdGuest(core).Cd)));
   foreach(int invalid in new[]{0,1,2}) {
    var bad=Puae6WhdLoad.Resolve(clone); if(invalid==0) bad.CdSlots[0]=1; else if(invalid==1) bad.CdCatalogV3[1]=bad.CdCatalogV3[0]; else bad.CdNames=Array.Empty<string>();
    try { Puae6WhdLoad.Resolve(bad); throw new Exception("Invalid V3 catalog accepted"); } catch(InvalidOperationException) { }
   }
  }
  for(int mode=0;mode<7;++mode) {
   var bad=Puae6WhdLoad.Resolve(clone);
   switch(mode) { case 0: bad.CdInfo=new P6CdInfo(); break; case 1: bad.CdInfoV2=new P6CdInfoV2(); break; case 2: bad.CdCatalog=Array.Empty<P6CdInfo>(); break;
    case 3: bad.CdCatalogV2=Array.Empty<P6CdInfoV2>(); break; case 4: if(catalog) bad.ReadCdCatalog=null; else bad.ReadCd=null; break; case 5: bad.SyncSettings.Machine=Puae6Machine.A500; break; case 6: if(catalog) bad.CdInfoV3=bad.CdCatalogV3[0]; else bad.CdCatalogV3=new[]{bad.CdInfoV3.Value}; break; }
   try { Puae6WhdLoad.Resolve(bad); throw new Exception("Invalid V3 params accepted"); } catch(InvalidOperationException) { }
  }
 }
 static void MultiCdRead(Puae6Core core,byte[] wire,string work,bool catalog)
 {
  var guest=CdGuest(core); var status=PublicCdStatus(core); Require(status[0]==(catalog?3u:1u)&&status[2]==1);
  var infos=catalog?(P6CdInfoV3[])typeof(P6CdHost).GetField("catalogV3",BindingFlags.Instance|BindingFlags.NonPublic).GetValue(guest.Cd):new[]{(P6CdInfoV3)typeof(P6CdHost).GetField("infoV3",BindingFlags.Instance|BindingFlags.NonPublic).GetValue(guest.Cd)};
  var ids=File.ReadAllLines(Path.Combine(work,"ids.txt"));
  for(int n=0;n<infos.Length;++n) { var info=infos[n]; Require(info.Version==3&&info.SessionCount==(n==0?2u:0u)); Require(new ReadOnlySpan<byte>(info.Id,32).SequenceEqual(Convert.FromHexString(ids[n]))); }
  // Only the test's independent expected table changes between DAO and ETNF global.
  var geometry=File.ReadAllLines(Path.Combine(work,"geometry.txt")).Select(row=>row.Split(' ',StringSplitOptions.RemoveEmptyEntries).Select(int.Parse).ToArray()).ToArray();
  Require(geometry.Length==6&&geometry[0].Length==2&&geometry[1].Length==10&&geometry[2].Length==4);
  Require(geometry[0][0]>0&&geometry[0][0]<=99&&geometry[3].Length>0&&geometry[3].Length<=32&&geometry[4].Length<=32&&geometry[5].Length>0&&geometry[5].Length<=32);
  var extents=geometry[2]; Require(extents[0]<extents[1]&&extents[1]<extents[2]&&extents[2]<extents[3]);
  Require(wire.Length==((extents[1]-extents[0])+(extents[3]-extents[2]))*2448);
  var first=infos[0]; Require(first.Count==(uint)geometry[0][0]&&first.Leadout==(uint)geometry[0][1]);
  Require(new ReadOnlySpan<uint>(first.Sessions,10).SequenceEqual(geometry[1].Select(n=>unchecked((uint)n)).ToArray()));
  Require(new ReadOnlySpan<byte>(first.Tracks,geometry[0][0]*48).SequenceEqual(File.ReadAllBytes(Path.Combine(work,"tracks.bin"))));
  var captured=(Puae6Parameters)typeof(Puae6Core).GetField("_parameters",BindingFlags.Instance|BindingFlags.NonPublic).GetValue(core);
  Require(Convert.ToHexString((byte[])Hidden(captured,"CdIdentity"))==ids[catalog?3:0]);
  if(catalog) { var iso=infos[1]; var chd=infos[2]; Require(iso.Tracks[6]==1&&iso.Tracks[9]==0&&chd.Tracks[9]==3); }
  if(status[1]!=0) {
   var bytes=File.ReadAllBytes(Path.Combine(work,status[1]==1?"iso-wire.bin":"chd-wire.bin")); var output=new byte[2448];
   using(guest.Host.EnterExit()) fixed(byte* p=output) Require(guest.Lib.p6w_cd_read_sector(0,2448,(IntPtr)p,2448)==P6.Ok);
   Require(output.SequenceEqual(bytes.Take(2448))); return;
  }
  foreach(int lba in geometry[3]) foreach(uint view in geometry[4].Contains(lba)?new uint[]{2048,2352,2448}:new uint[]{2352,2448}) {
   var output=new byte[view]; using(guest.Host.EnterExit()) fixed(byte* p=output) Require(guest.Lib.p6w_cd_read_sector((uint)lba,view,(IntPtr)p,view)==P6.Ok);
   Require((lba>=extents[0]&&lba<extents[1])||(lba>=extents[2]&&lba<extents[3]));
   int offset=(lba<extents[1]?lba-extents[0]:lba-extents[2]+extents[1]-extents[0])*2448;
   Require(output.SequenceEqual(wire.Skip(offset+(view==2048?16:0)).Take((int)view)));
  }
  var reads=WatchCd(core,catalog); var before=Save(core); long count=reads();
  foreach(int lba in geometry[5]) {
   var output=Enumerable.Repeat((byte)0xcc,2448).ToArray();
   using(guest.Host.EnterExit()) fixed(byte* p=output) Require(guest.Lib.p6w_cd_read_sector((uint)lba,2448,(IntPtr)p,2448)!=P6.Ok);
   Require(output.All(b=>b==0xcc));
  }
  Require(count==reads()); // Sector describe rejects holes before the callback.
  foreach(ulong at in geometry[5].Select(lba=>(ulong)lba*2448).Append((ulong)geometry[5][0]*2448-1)) {
   var output=Enumerable.Repeat((byte)0xcc,2448).ToArray(); Require(guest.Cd.ReadAt(guest,at,output)!=P6.Ok&&output.All(b=>b==0xcc));
  } // Raw transport may invoke the source preflight; CountingBlob covers physical IO in B.
  Require(before.SequenceEqual(Save(core)));
 }
 static void PublicCd(string[] args, string extension)
 {
  LoadRuntime(args[2]);
  string work=args[0], xml=args[1], image=Path.ChangeExtension(xml,extension), file=Path.Combine(work,"public-cd.state");
  bool nrg=extension==".nrg", multi=args[5].StartsWith("nrgmultipublic"), catalog=multi&&args[8]=="catalog";
  byte[] main=File.ReadAllBytes(args[6]), ext=File.ReadAllBytes(args[7]);
  byte[] expected=File.ReadAllBytes(Path.Combine(work,"wire.bin"));
  int pregap=!nrg&&args.Length>8?int.Parse(args[8]):0;
  string boundaries=nrg&&!multi?args[8]:"";
  bool direct=Path.GetExtension(xml).Equals(extension,StringComparison.OrdinalIgnoreCase);
  byte[] seed=Enumerable.Range(0,1024).Select(n=>(byte)(n*17+9)).ToArray();
  File.WriteAllText(Path.Combine(work,"mame_machines.txt"),"");
  File.WriteAllText(Path.Combine(work,"gamedb.txt"),"");
  Database.InitializeDatabase(work,work,true); // Direct disc identification acquires the real game database.
  BizHawk.Emulation.Cores.Arcades.MAME.MAMEMachineDB.Initialize(work);
  Require(!BizHawk.Emulation.Cores.Arcades.MAME.MAMEMachineDB.IsMAMEMachine(Path.Combine(work,"warm.zip")));
  Puae6Core Open(string path, byte[] nvram) => OpenPublicCd(path,nvram,main,ext,extension,direct);
  void Data(Puae6Core target) { if(multi) MultiCdRead(target,expected,work,catalog); else CdRead(target,expected,pregap,boundaries); }
  using(var core=Open(xml,seed)) {
   if(multi) MultiCdParameters(core,catalog);
   Data(core);
   byte[] cut; string[] future;
   if(args[5].EndsWith("save")) {
    if(catalog) { SelectPublicCd(core,3); Data(core); SelectPublicCd(core,2); Data(core); }
    Run(core,multi?2:300-core.Frame); cut=Save(core); File.WriteAllBytes(file,cut);
    File.WriteAllBytes(file+".nvram",core.CloneSaveRam());
    future=Run(core,15).Concat(new[] { CdRam(core) }).ToArray();
    File.WriteAllLines(file+".future",future); File.WriteAllBytes(file+".end",Save(core));
    var nv=core.ServiceProvider.GetService<IMemoryDomains>()["CD32 NVRAM"];
    nv.PokeByte(13,(byte)(nv.PeekByte(13)^255)); Require(core.SaveRamModified);
   } else { cut=File.ReadAllBytes(file); future=File.ReadAllLines(file+".future"); }
   void Check(Puae6Core target)
   {
    if(catalog&&args[5].EndsWith("save")) { SelectPublicCd(target,1); Data(target); }
    Restore(target,cut); Require(target.CloneSaveRam(false).SequenceEqual(File.ReadAllBytes(file+".nvram")));
    Data(target);
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
   var cut=File.ReadAllBytes(file); Restore(fresh,cut); Data(fresh);
   Require(Run(fresh,15).Concat(new[] { CdRam(fresh) }).SequenceEqual(File.ReadAllLines(file+".future")));
   Require(Save(fresh).SequenceEqual(File.ReadAllBytes(file+".end")));
   Require(fresh.CloneSaveRam(false).SequenceEqual(File.ReadAllBytes(file+".nvram")));
  }
  Require(OpenCdFile(image)==0);
  using(var durable=Open(xml,File.ReadAllBytes(file+".nvram"))) {
   Data(durable);
   if(multi) { ((IDisposable)HiddenOwner((Puae6Parameters)typeof(Puae6Core).GetField("_parameters",BindingFlags.Instance|BindingFlags.NonPublic).GetValue(durable))).Dispose();
    var output=Enumerable.Repeat((byte)0xcc,2448).ToArray(); Require(CdGuest(durable).Cd.ReadAt(CdGuest(durable),0,output)!=P6.Ok&&output.All(b=>b==0xcc)&&OpenCdFile(image)==0); }
  }
  Require(OpenCdFile(image)==0);
  if(multi) {
   if(args[5].EndsWith("save")) foreach(string name in catalog?new[]{"other","shifted","sbi","order","map"}:new[]{"other","shifted","sbi"}) {
    string path=Path.Combine(work,name,Path.GetFileName(xml)); using var other=Open(path,seed);
    var before=Save(other); var nvram=other.CloneSaveRam(false); var reads=WatchCd(other,catalog); long count=reads();
    try { Restore(other,File.ReadAllBytes(file)); throw new Exception("Other multisession CD accepted"); } catch(StateRejectedException e) { Require(e.Reason==StateRejection.OtherMedia); }
    Require(count==reads()&&before.SequenceEqual(Save(other))&&nvram.SequenceEqual(other.CloneSaveRam(false)));
   }
   foreach(string path in Directory.GetFiles(Path.GetDirectoryName(xml)).Where(p=>new[]{".nrg",".iso",".chd"}.Contains(Path.GetExtension(p)))) {
    int handles=OpenCdFile(path); HostCheck(handles==0,$"final FD close path={path} count={handles}");
   }
   Console.WriteLine("Public NRG V3 multisession/mixed catalog/SBI/cold process/full future/FD close PASS"); return;
  }
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
