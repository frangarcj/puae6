using System.Runtime.InteropServices;
using System.Security.Cryptography;
using System.Text.Json;
internal static unsafe class Program
{
    internal static void Require(bool ok, string detail) { if (!ok) throw new InvalidOperationException(detail); }
    static string Hash(byte[] data) => Convert.ToHexString(SHA256.HashData(data)).ToLowerInvariant();
    [UnmanagedFunctionPointer(CallingConvention.Cdecl)] delegate ulong HostCall(nint function, ulong seed, ulong argument);
    [UnmanagedFunctionPointer(CallingConvention.Cdecl)] delegate void Read(nint output);
    sealed class Probe : WaterboxSandbox
    {
        internal Probe(Native api, byte[] image) : base(api, image, Layout.Smoke, "entry-gpr.wbx")
        { In(() => { Require(Guest<Sandbox.Value>("smoke_constructor")() == 43 && Guest<Sandbox.Status>("smoke_init")() == 0, "Init"); api.Seal(host, out var result); result.Checked(); return 0; }); }
        internal ulong[] Capture(HostCall call, ulong seed, ulong argument, bool original) => In(() => {
            api.GetProc(host, "entry_capture", out var result);
            Require(call((nint)result.Checked(), seed, argument) == 0, "Host nonvolatile registers and guest return value");
            var values = new ulong[12]; fixed (ulong* p = values) Guest<Read>("entry_read")((nint)p);
            for (int i = 0; i < 9; i++) Require(values[i] == (original ? seed + (ulong)i : 0), "Guest entry x" + (19+i));
            Require(original ? values[9] != 0 : values[9] == 0, "Guest entry x29");
            Require(values[10] == argument, "Argument unchanged"); return values;
        });
        internal ulong Count() => In(() => { ulong* p = stackalloc ulong[12]; Guest<Read>("entry_read")((nint)p); return p[11]; });
        internal void CallbackAbi()
        {
            Exception? failure = null; int calls = 0; var nested = new ulong[3];
            Native.HostCallback callback = (a,b,c,d,e,f) => {
                try { calls++; Require(a==0x11 && b==0x22 && c==0x33 && d==0x44 && e==0x55 && f==0x66, "Callback args");
                    fixed (ulong* n = nested) Require(Guest<Sandbox.Identity>("smoke_identity")((nint)n,3)==0, "Reentry");
                    return 0xC0FFEF65; } catch (Exception error) { failure=error; return 0; }
            };
            var output = new ulong[30]; In(() => { fixed (ulong* p = output)
                Require(Guest<Sandbox.AbiProbe>("smoke_abi_probe")(Callback(callback,0),(nint)p,30)==0, "ABI export"); return 0; });
            GC.KeepAlive(callback); if (failure != null) throw failure;
            Require(calls==1 && output[0]==output[2] && output[1]!=0 && output[1]==output[3], "Callback/x18/x28");
            for (int i=0;i<9;i++) Require(output[4+i]==0xA113UL+(ulong)i,"Callback GPR");
            for (int i=0;i<8;i++) Require(output[18+i]==0xD808UL+(ulong)i,"Callback FP");
            Require(output[13]==0xC0FFEF65 && output[14]==output[15] && output[16]==output[17],"Callback result/x29/SP");
            Require(output[26]==0x7157 && output[27]==0x7157 && output[28]==0x1234 && output[29]==0x1234,"TLS/errno");
            Require(nested[0]==output[1] && nested[1]==0x7157 && nested[2]==0x1234,"Reentry identity");
        }
    }
    public static int Main(string[] args)
    {
        try {
            Require(args.Length==5 && Environment.Version.ToString()=="10.0.13", "Pinned invocation");
            string mode=args[0]; bool original=mode=="original"; Require(original || mode=="fixed" || mode=="restore","Mode");
            using var api=new Native(args[1]); api.SetAlwaysEvict(true); byte[] image=File.ReadAllBytes(args[2]);
            nint shim=NativeLibrary.Load(args[3]); var call=Marshal.GetDelegateForFunctionPointer<HostCall>(NativeLibrary.GetExport(shim,"entry_host_call"));
            using var a=new Probe(api,image);
            if (mode=="restore") {
                Require(a.Count()==0,"Fresh counter"); var baseline=File.ReadAllBytes(args[4]); a.Load(baseline);
                Require(a.Count()==1 && a.Save().SequenceEqual(baseline),"Fresh restore 0 to1");
                a.Capture(call,0x7711,2,false); Require(a.Count()==3,"Fresh continuation");
                Console.WriteLine("P6_ENTRY_GPR "+JsonSerializer.Serialize(new { mode, baseline=Hash(baseline),future=Hash(a.Save()) })); return 0;
            }
            using var b=new Probe(api,image); a.Capture(call,0x1199,1,original); b.Capture(call,0x7733,1,original);
            var state=a.Save(); Require(original ? !state.SequenceEqual(b.Save()) : state.SequenceEqual(b.Save()),"Two host seeds/full snapshots");
            if (original) { Console.WriteLine("P6_ENTRY_GPR {\"mode\":\"original\",\"contaminated\":true}"); return 0; }
            a.Capture(call,0x5511,2,false); var future=a.Save(); b.Capture(call,0x9933,2,false); Require(b.Count()==3,"Before restore");
            b.Load(state); Require(b.Count()==1 && b.Save().SequenceEqual(state),"Counter restore3 to1");
            b.Capture(call,0xAA11,2,false); Require(b.Count()==3 && b.Save().SequenceEqual(future) && !future.SequenceEqual(state),"Full future");
            File.WriteAllBytes(args[4],state); a.CallbackAbi(); b.CallbackAbi();
            Console.WriteLine("P6_ENTRY_GPR "+JsonSerializer.Serialize(new { mode, baseline=Hash(state),future=Hash(future),callbacks=2 })); return 0;
        } catch (Exception error) { Console.Error.WriteLine(error); return 2; }
    }
}
