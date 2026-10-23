using System.Reflection;
using BizHawk.Common;
using BizHawk.Client.Common;
using BizHawk.Emulation.Common;
using BizHawk.Emulation.Cores;
using Puae6;

internal static unsafe partial class Program
{
 sealed class CartridgeProvider(byte[] kick, byte[] cart, string name) : ICoreFileProvider
 {
  internal readonly List<string> Asked = new();
  public string GetRetroSaveRAMDirectory(string p) => throw new NotSupportedException();
  public string GetRetroSystemPath(string p) => throw new NotSupportedException();
  public string GetUserPath(string s, bool temp) => throw new NotSupportedException();
  public string GetPalettesPath(string s) => throw new NotSupportedException();
  public byte[] GetFirmware(FirmwareID id, string msg = null) => GetFirmwareOrThrow(id,msg);
  public (byte[],GameInfo) GetFirmwareWithGameInfoOrThrow(FirmwareID id, string msg = null) => (GetFirmwareOrThrow(id,msg),null);
  public byte[] GetFirmwareOrThrow(FirmwareID id, string msg = null) {
   Asked.Add(id.Firmware); return id.Firmware == name ? cart : id.Firmware == "A500" || id.Firmware == "A1200" ? kick : throw new Exception("Wrong firmware request: "+id);
  }
 }
 sealed class CartridgeDisk(byte[] bytes) : IRomAsset
 {
  public byte[] FileData => bytes; public byte[] RomData => bytes; public string Extension => ".hdf";
  public string RomPath => "H.hdf"; public GameInfo Game => null;
 }
 static Puae6SyncSettings CartridgeSettings(uint model) => new() { Machine=model==1200?Puae6Machine.A1200:Puae6Machine.A500,
  Cartridge=model switch {1=>Puae6Cartridge.Ar1,2=>Puae6Cartridge.Ar2,3=>Puae6Cartridge.Ar3,_=>Puae6Cartridge.Ar1200} };
 static CoreLoadParameters<Puae6Settings,Puae6SyncSettings> CartridgeParameters(string[] args, uint model, byte[] cart, out CartridgeProvider provider) {
  provider=new(Read(args[1],$"{model}.kick"),cart,"AR"+model);
  var lp=new CoreLoadParameters<Puae6Settings,Puae6SyncSettings> { Comm=new CoreComm(_=>{},(_,_)=>{},provider,CoreComm.CorePreferencesFlags.None,null), SyncSettings=CartridgeSettings(model) };
  lp.Roms.Add(new CartridgeDisk(Read(args[1],"H.hdf"))); return lp;
 }
 static void CartridgeConfig(string[] args)
 {
  Phase="Action Replay config"; int hosts=0; var factory=Puae6Guest.CreateHost;
  Puae6Guest.CreateHost=_=>{++hosts; throw new Exception("Unexpected host");};
  try {
   foreach(uint model in new uint[]{1,2,3,1200}) {
    var raw=Read(args[1],$"{model}.cart"); var lp=CartridgeParameters(args,model,raw,out var provider);
    var method=typeof(Puae6Core).GetMethod("ParametersFrom",BindingFlags.Static|BindingFlags.NonPublic);
    var p=(Puae6Parameters)method.Invoke(null,new object[]{lp});
    Require(provider.Asked.SequenceEqual(new[]{model==1200?"A1200":"A500","AR"+model}),"selected firmware only");
    Require(Puae6Core.CartridgeModel(p.SyncSettings.Cartridge)==model && Puae6Core.ConfigFlags(p.SyncSettings.Cartridge,false,p.SyncSettings.Machine)==0,"IDs never become flags");
    Require(Controllers.Define(p.SyncSettings,0).BoolButtons.Contains(Controllers.Freeze),"Freeze level offered");
    var disabled=p.SyncSettings.Clone();disabled.Cartridge=Puae6Cartridge.Disabled;
    Require(Puae6SyncSettings.NeedsReboot(p.SyncSettings,disabled),"cartridge setting requires reboot");
    var sha=new Dictionary<uint,string> { [1]="1EAD9DDA2DAD29146441F5EF7218375022E01248",[2]="255D6DF63A4EAB0A838EB1A16A267B0959DFF634",
     [3]="5D4987C2E3FFEA8B1B02E31430EF190F2DB76542",[1200]="0F6AB8342810094AC0642F62BA42F78BC0B07E6A" };
    var option=FirmwareDatabase.FirmwareOptions.Single(o=>o.ID==new FirmwareID("Amiga","AR"+model));
    Require(option.Hash==sha[model] && option.Size==raw.Length && option.Status==FirmwareOptionStatus.Ideal,"registered recognized raw firmware");
    var twice=Puae6WhdLoad.Resolve(Puae6WhdLoad.Resolve(p)); twice.Volumes.Add(Read(args[1],"volume.zip"));
    Require(Puae6Volumes.Resolve(twice).CartridgeFirmware.SequenceEqual(raw),"both Resolve copies retain firmware");
    foreach(int bad in Enumerable.Range(0,15)) {
     var s=p.SyncSettings.Clone(); byte[] image=raw;
     switch(bad) {
      case 0:s.Machine=model==1200?Puae6Machine.A500:Puae6Machine.A1200;break;
      case 1:s.CpuModel=Puae6Cpu.M68010;break; case 2:s.CpuMultiplier=Puae6CpuClock.X8;break;
      case 3:s.FastMemory=Puae6Fast.MB2;break; case 4:s.FloppyWrites=true;break;
      case 5:s.Cartridge=(Puae6Cartridge)1;break; case 6:image=null;break;
      case 7:image=raw[..^1];break; case 8:image=new byte[raw.Length];break;
      case 9:s.FpuModel=Puae6Fpu.M68881;break; case 10:s.SlowMemory=(Puae6Slow)999;break;
      case 11:s.Z3Memory=Puae6Z3Memory.MB16;break; case 12:s.HdfController=Puae6HdfController.Ide;break;
      case 13:s.Machine=Puae6Machine.A1200;s.Cartridge=Puae6Cartridge.HrtMon;break;
      case 14:image=(byte[])raw.Clone();image[0]=model==1200?(byte)'X':(byte)'A';
       if(model!=1200)System.Text.Encoding.ASCII.GetBytes("ATZ!HRT!").CopyTo(image,0);break;
     }
     bool rejected=false; try { using var core=new Puae6Core(lp.Comm,new Puae6Parameters {Firmware=p.Firmware,CartridgeFirmware=image,SyncSettings=s}); }
     catch(ArgumentException){rejected=true;} Require(rejected && hosts==0,"invalid cartridge refused before host");
    }

    var cd=new Puae6Parameters { Firmware=p.Firmware,CartridgeFirmware=raw,SyncSettings=p.SyncSettings.Clone(),
     CdInfo=new P6CdInfo { Version=1,Size=(uint)sizeof(P6CdInfo) },ReadCd=(_,_,_)=>false };
    cd.SyncSettings.Machine=Puae6Machine.CD32;
    bool cdRejected=false;try{using var invalid=new Puae6Core(lp.Comm,cd);}catch(ArgumentException){cdRejected=true;}
    Require(cdRejected && hosts==0,"CD and Action Replay refused before host");
   }
  } finally {Puae6Guest.CreateHost=factory;}
  Require((int)Puae6Cartridge.HrtMon==2 && Puae6Core.ConfigFlags(Puae6Cartridge.HrtMon,true,Puae6Machine.A1200)==3,"HRTMon/flags unchanged");
  Console.WriteLine("Action Replay public config/provider/Resolve/flags/preflight PASS");
 }
 sealed class CartridgeButtons(ControllerDefinition definition) : IController
 {
  internal bool Held; public ControllerDefinition Definition=>definition;
  public bool IsPressed(string name)=>name==Controllers.Freeze && Held;
  public int AxisValue(string name)=>definition.Axes[name].Neutral;
  public IReadOnlyCollection<(string Name,int Strength)> GetHapticsSnapshot()=>Array.Empty<(string,int)>();
  public void SetHapticChannelStrength(string name,int strength) { }
 }
 static void CartSignal(Puae6Core c,int address,byte value)=>HrtRam(c).PokeByte(address,value);
 static byte[] CartMarkers(Puae6Core c)=>Enumerable.Range(0,48).Select(n=>HrtRam(c).PeekByte(0x11000+n)).ToArray();
 static uint CartCount(Puae6Core c)=>System.Buffers.Binary.BinaryPrimitives.ReadUInt32BigEndian(CartMarkers(c).AsSpan(32,4));
 static Puae6Core CartridgeOpen(string[] args,uint model,bool foreign=false,bool off=false)
 {
  var raw=Read(args[1],$"{model}.cart"); if(foreign) {
   raw[0x800]^=1;
   if(model==1) { uint sum=0; for(int n=0;n<raw.Length-4;n+=2) sum=unchecked(sum+(uint)System.Buffers.Binary.BinaryPrimitives.ReadInt16BigEndian(raw.AsSpan(n,2)));
    System.Buffers.Binary.BinaryPrimitives.WriteUInt32BigEndian(raw.AsSpan(raw.Length-4),sum); }
  }
  var lp=CartridgeParameters(args,model,raw,out var provider); if(off) lp.SyncSettings.Cartridge=Puae6Cartridge.Disabled;
  // The actual public constructor resolves firmware and copies before the caller changes it.
  var core=new Puae6Core(lp); Array.Fill(raw,(byte)0xA5);
  var hard=Read(args[1],"H.hdf"); var changed=(byte[])hard.Clone(); changed[4096*5+11]^=17;
  core.StoreSaveRam(SaveRam.Build([],[],[hard],[changed]));
  Require(provider.Asked.Count==(off?1:2),"StoreSaveRam reboots without requesting firmware again"); return core;
 }
 static string[] CartridgeFuture(Puae6Core core,uint model)
 {
  var rows=new List<string>(); var buttons=new CartridgeButtons(core.ControllerDefinition) {Held=true}; uint initial=CartCount(core);
  for(int n=0;n<5;++n) {
   if(n==1) CartSignal(core,0x1100c,1); if(n==2)buttons.Held=false;
   if(n==3){CartSignal(core,0x1100c,0);buttons.Held=true;}
   core.FrameAdvance(buttons,true,true); core.GetSamplesSync(out var samples,out int frames);
   var pcm=new byte[frames*4];Buffer.BlockCopy(samples,0,pcm,0,pcm.Length);
   var picture=core.GetVideoBuffer();var pixels=new byte[core.BufferWidth*core.BufferHeight*4];Buffer.BlockCopy(picture,0,pixels,0,pixels.Length);
   rows.Add($"{core.Frame} {core.CycleCount} {Hash(pixels)} {Hash(pcm)} {Convert.ToHexString(CartMarkers(core))}");
   if(n==0)Require(CartCount(core)==initial,"held edge restored");
  }
  Require(CartCount(core)==initial+(model==1?2u:1u),"release/rearm reaches cartridge again"); return rows.ToArray();
 }
 static void CartridgeWrapper(string[] args)
 {
  uint model=uint.Parse(args[5]); Phase="Action Replay wrapper "+model;
  string path=Path.Combine(args[1],model+".held"); bool writing=args[0]=="arsave";
  using var core=CartridgeOpen(args,model);
  if(writing) {
   var buttons=new CartridgeButtons(core.ControllerDefinition);core.FrameAdvance(buttons,true,true);
   CartSignal(core,model==1?0x11010:0x1100c,1);core.FrameAdvance(buttons,true,true);CartSignal(core,0x1100c,0);
   buttons.Held=true;core.FrameAdvance(buttons,true,true);Require(CartCount(core)==(model is 2 or 3?2u:1u),"causal held cut");
   File.WriteAllBytes(path,Save(core)); var rows=CartridgeFuture(core,model);File.WriteAllLines(path+".future",rows);File.WriteAllBytes(path+".end",Save(core));
  }
  var state=File.ReadAllBytes(path);using(var r=new BinaryReader(new MemoryStream(state)))core.LoadStateBinary(r);
  Require(CartridgeFuture(core,model).SequenceEqual(File.ReadAllLines(path+".future")) && Save(core).SequenceEqual(File.ReadAllBytes(path+".end")),"cold/full wrapper future");
  using var other=CartridgeOpen(args,model,true);var pristine=Save(other);bool rejected=false;
  try{using var r=new BinaryReader(new MemoryStream(state));other.LoadStateBinary(r);}catch(StateRejectedException e){rejected=e.Reason==StateRejection.OtherConfig;}
  Require(rejected && Save(other).SequenceEqual(pristine),"foreign cartridge SHA rejected transactionally before snapshot");
  if(writing) {
   using var fresh=CartridgeOpen(args,model);using(var r=new BinaryReader(new MemoryStream(state)))fresh.LoadStateBinary(r);
   Require(CartridgeFuture(fresh,model).SequenceEqual(File.ReadAllLines(path+".future")) && Save(fresh).SequenceEqual(File.ReadAllBytes(path+".end")),"new sandbox wrapper future");
  }
  Console.WriteLine("Action Replay public wrapper/copy/StoreSaveRam/held/cold/full future PASS");
 }
}
