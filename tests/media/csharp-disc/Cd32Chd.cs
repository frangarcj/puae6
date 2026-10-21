#if PUBLIC_CHD
using System.Reflection;
using BizHawk.Client.Common;
using BizHawk.Emulation.Common;
using Puae6;

internal static unsafe partial class Program
{
 static Puae6Guest ChdGuest(Puae6Core core) => (Puae6Guest)typeof(Puae6Core)
  .GetField("_guest", BindingFlags.Instance|BindingFlags.NonPublic).GetValue(core);
 static void ChdRead(Puae6Core core, byte[] expected)
 {
  var guest=ChdGuest(core);
  Require((bool)typeof(P6CdHost).GetField("regional",BindingFlags.Instance|BindingFlags.NonPublic).GetValue(guest.Cd));
  foreach(int at in new[] { 0, 19577, expected.Length-2448 }) {
   var output=new byte[2448]; Require(guest.Cd.ReadAt(guest,(ulong)at,output)==P6.Ok);
   Require(output.SequenceEqual(expected.Skip(at).Take(output.Length)));
  }
 }
 static string ChdRam(Puae6Core core)
 {
  var chip=core.ServiceProvider.GetService<IMemoryDomains>().MainMemory;
  return Hash(Enumerable.Range(0,32).Select(n=>chip.PeekByte(n*(chip.Size-1)/31)).ToArray());
 }
 static void PublicChd(string[] args)
 {
  LoadRuntime(args[2]);
  string work=args[0], xml=args[1], image=Path.ChangeExtension(xml,".chd"), file=Path.Combine(work,"public-chd.state");
  byte[] main=File.ReadAllBytes(args[6]), ext=File.ReadAllBytes(args[7]);
  byte[] expected=File.ReadAllBytes(Path.Combine(work,"wire.bin"));
  byte[] seed=Enumerable.Range(0,1024).Select(n=>(byte)(n*17+9)).ToArray();
  File.WriteAllText(Path.Combine(work,"mame_machines.txt"),"");
  BizHawk.Emulation.Cores.Arcades.MAME.MAMEMachineDB.Initialize(work);
  Require(!BizHawk.Emulation.Cores.Arcades.MAME.MAMEMachineDB.IsMAMEMachine(Path.Combine(work,"warm.zip")));
  Puae6Core Open(string path, byte[] nvram)
  {
   var config=new Config(); config.PreferredCores["Amiga"]="PUAE6";
   var loader=new RomLoader(config,null); var errors=new List<string>();
   loader.OnLoadSettings+=(_,e)=>e.Settings=new Puae6Settings();
   loader.OnLoadSyncSettings+=(_,e)=>e.Settings=new Puae6SyncSettings();
   loader.OnLoadError+=(_,e)=>errors.Add(e.Message);
   var firmware=new Firmware(main,ext,false);
   var comm=new CoreComm(_=>{},(_,_)=>{},firmware,CoreComm.CorePreferencesFlags.None,null);
   if(!loader.LoadRom(path,comm,null)) throw new Exception("Public CHD load: "+string.Join(" | ",errors));
   var core=loader.LoadedEmulator as Puae6Core ?? throw new Exception("XML did not select PUAE6");
   try {
    Require(core.GetSyncSettings().Machine==Puae6Machine.CD32 && core.Frame==0);
    Require(firmware.Asked.SequenceEqual(new[] { "CD32", "CD32ext" }));
    var guest=ChdGuest(core);
    core.StoreSaveRam(nvram); // CD32 imports EEPROM in place; immutable CD data and its owner stay bound.
    Require(ReferenceEquals(guest,ChdGuest(core)) && core.Frame==0);
    Require(core.CloneSaveRam(false).SequenceEqual(nvram) && !core.SaveRamModified);
    Require(OpenChd(Path.ChangeExtension(path,".chd"))==1);
    return core;
   } catch { core.Dispose(); throw; }
  }
  using(var core=Open(xml,seed)) {
   ChdRead(core,expected);
   byte[] cut; string[] future;
   if(args[5]=="chdpublicsave") {
    Run(core,300); cut=Save(core); File.WriteAllBytes(file,cut);
    File.WriteAllBytes(file+".nvram",core.CloneSaveRam());
    future=Run(core,15).Concat(new[] { ChdRam(core) }).ToArray();
    File.WriteAllLines(file+".future",future); File.WriteAllBytes(file+".end",Save(core));
    var nv=core.ServiceProvider.GetService<IMemoryDomains>()["CD32 NVRAM"];
    nv.PokeByte(13,(byte)(nv.PeekByte(13)^255)); Require(core.SaveRamModified);
   } else { cut=File.ReadAllBytes(file); future=File.ReadAllLines(file+".future"); }
   void Check(Puae6Core target)
   {
    Restore(target,cut); Require(target.CloneSaveRam(false).SequenceEqual(File.ReadAllBytes(file+".nvram")));
    ChdRead(target,expected);
    Require(Run(target,15).Concat(new[] { ChdRam(target) }).SequenceEqual(future));
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
     Require(Run(target,10).Concat(new[] { ChdRam(target) }).SequenceEqual(future.Skip(5)));
     Require(Save(target).SequenceEqual(File.ReadAllBytes(file+".end")));
    }
   }
   Check(core);
   // Close the old Disc owner as well as its sandbox before reopening through RomLoader.
  }
  Require(OpenChd(image)==0);
  using(var fresh=Open(xml,seed)) { // No frame before the cold LOAD, also in the second process.
   var cut=File.ReadAllBytes(file); Restore(fresh,cut); ChdRead(fresh,expected);
   Require(Run(fresh,15).Concat(new[] { ChdRam(fresh) }).SequenceEqual(File.ReadAllLines(file+".future")));
   Require(Save(fresh).SequenceEqual(File.ReadAllBytes(file+".end")));
   Require(fresh.CloneSaveRam(false).SequenceEqual(File.ReadAllBytes(file+".nvram")));
  }
  Require(OpenChd(image)==0);
  using(var durable=Open(xml,File.ReadAllBytes(file+".nvram"))) ChdRead(durable,expected);
  Require(OpenChd(image)==0);
  string otherXml=Path.Combine(work,"other.xml"), otherImage=Path.ChangeExtension(otherXml,".chd");
  using(var other=Open(otherXml,seed)) {
   var before=Save(other); var nvram=other.CloneSaveRam(false);
   try { Restore(other,File.ReadAllBytes(file)); throw new Exception("Other CHD accepted"); }
   catch(StateRejectedException e) { Require(e.Reason==StateRejection.OtherMedia); }
   Require(before.SequenceEqual(Save(other)) && nvram.SequenceEqual(other.CloneSaveRam(false)));
  }
  Require(OpenChd(otherImage)==0 && OpenChd(image)==0);
  Console.WriteLine("Public CHD XML Waterbox reads/SaveRAM/cold owner/new process/full future/FD close PASS");
 }
}
#endif
