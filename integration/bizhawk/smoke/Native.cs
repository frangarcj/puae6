using System.Runtime.InteropServices;
using System.Text;

// Exact C ABI from the captured waterboxhost/src/cinterface.rs, not BizHawk mocks.
[StructLayout(LayoutKind.Sequential)]
internal struct Layout
{
    public nuint Sbrk, Sealed, Invisible, Plain, Mmap;
    public static Layout Smoke => new() { Sbrk = 1 << 20, Sealed = 16384,
        Invisible = 16384, Plain = 32768, Mmap = 1 << 20 };
}

[StructLayout(LayoutKind.Sequential)]
internal unsafe struct Result
{
    public fixed byte Error[1024];
    public nuint Data;
    public readonly nuint Checked()
    {
        fixed (byte* error = Error)
        {
            int length = 0;
            while (length < 1024 && error[length] != 0) ++length;
            if (length != 0) throw new InvalidOperationException(Encoding.UTF8.GetString(error, length));
        }
        return Data;
    }
}

internal sealed class Native : IDisposable
{
    [UnmanagedFunctionPointer(CallingConvention.Cdecl)]
    internal delegate nint Read(nuint user, nint data, nuint size);
    [UnmanagedFunctionPointer(CallingConvention.Cdecl)]
    internal delegate int Write(nuint user, nint data, nuint size);
    [UnmanagedFunctionPointer(CallingConvention.Cdecl)]
    internal delegate void Create(ref Layout layout, [MarshalAs(UnmanagedType.LPUTF8Str)] string name,
        Read read, nuint user, out Result result);
    [UnmanagedFunctionPointer(CallingConvention.Cdecl)]
    internal delegate void Action(nuint host, out Result result);
    [UnmanagedFunctionPointer(CallingConvention.Cdecl)]
    internal delegate void Proc(nuint host, [MarshalAs(UnmanagedType.LPUTF8Str)] string name, out Result result);
    [UnmanagedFunctionPointer(CallingConvention.Cdecl)]
    internal delegate void Save(nuint host, Write write, nuint user, out Result result);
    [UnmanagedFunctionPointer(CallingConvention.Cdecl)]
    internal delegate void Load(nuint host, Read read, nuint user, out Result result);
    [UnmanagedFunctionPointer(CallingConvention.Cdecl)]
    internal delegate nuint HostCallback(nuint a, nuint b, nuint c, nuint d, nuint e, nuint f);
    [UnmanagedFunctionPointer(CallingConvention.Cdecl)]
    internal delegate void CallbackAddr(nuint host, nint callback, nuint slot, out Result result);
    [UnmanagedFunctionPointer(CallingConvention.Cdecl)]
    internal delegate void PageLen(nuint host, out Result result);
    [UnmanagedFunctionPointer(CallingConvention.Cdecl)]
    internal delegate void PageData(nuint host, nuint index, out Result result);

    readonly nint library;
    internal readonly Create CreateHost;
    internal readonly Action Destroy, Activate, Deactivate, Seal;
    internal readonly Proc GetProc;
    internal readonly Save SaveState;
    internal readonly Load LoadState;
    internal readonly CallbackAddr GetCallback;
    internal readonly PageLen GetPageLen;
    internal readonly PageData GetPageData;
    T Bind<T>(string name) where T : Delegate =>
        Marshal.GetDelegateForFunctionPointer<T>(NativeLibrary.GetExport(library, name));
    internal Native(string absolutePath)
    {
        if (!Path.IsPathFullyQualified(absolutePath)) throw new ArgumentException("Dylib path must be absolute");
        library = NativeLibrary.Load(absolutePath);
        CreateHost = Bind<Create>("wbx_create_host");
        Destroy = Bind<Action>("wbx_destroy_host");
        Activate = Bind<Action>("wbx_activate_host");
        Deactivate = Bind<Action>("wbx_deactivate_host");
        Seal = Bind<Action>("wbx_seal");
        GetProc = Bind<Proc>("wbx_get_proc_addr");
        SaveState = Bind<Save>("wbx_save_state");
        LoadState = Bind<Load>("wbx_load_state");
        GetCallback = Bind<CallbackAddr>("wbx_get_callback_addr");
        GetPageLen = Bind<PageLen>("wbx_get_page_len");
        GetPageData = Bind<PageData>("wbx_get_page_data");
    }
    public void Dispose() => NativeLibrary.Free(library);
}

internal sealed unsafe class Sandbox : IDisposable
{
    [UnmanagedFunctionPointer(CallingConvention.Cdecl)] internal delegate ulong Value();
    [UnmanagedFunctionPointer(CallingConvention.Cdecl)] internal delegate int Status();
    [UnmanagedFunctionPointer(CallingConvention.Cdecl)] internal delegate int Frame(ulong input, nint output, nuint capacity);
    [UnmanagedFunctionPointer(CallingConvention.Cdecl)] internal delegate int Poison(byte value);
    [UnmanagedFunctionPointer(CallingConvention.Cdecl)] internal delegate int MemoryInfo(nint output, nuint capacity);
    readonly Native api;
    nuint host;
    internal Sandbox(Native api, byte[] image)
    {
        this.api = api;
        using var stream = new MemoryStream(image, writable: false);
        var layout = Layout.Smoke;
        Native.Read read = (_, data, size) => Read(stream, data, size);
        api.CreateHost(ref layout, "p6-smoke.wbx", read, 0, out var result);
        host = result.Checked();
        GC.KeepAlive(read);
        try
        {
            In(() => {
                Program.Require(Guest<Value>("smoke_constructor")() == 43, "C++ constructor");
                Program.Require(Guest<Status>("smoke_init")() == 0, "guest init / C++ allocation");
                api.Seal(host, out var sealedResult); sealedResult.Checked();
                return 0;
            });
        }
        catch { Dispose(); throw; }
    }
    static nint Read(Stream stream, nint data, nuint size)
    {
        try { return stream.Read(new Span<byte>((void*)data, checked((int)size))); }
        catch { return -1; } // No managed exception crosses the native ABI.
    }
    internal delegate int Identity(nint output, nuint capacity);
    internal delegate int AbiProbe(nint callback, nint output, nuint capacity);
    internal delegate int Touch(ulong which, ulong page, ulong offset, ulong value);
    internal delegate int Fill(ulong which, ulong seed);
    /// Guest-callable thunk for a managed callback. Keep `callback` alive while the guest can call it.
    internal nint Callback(Native.HostCallback callback, int slot)
    {
        api.GetCallback(host, Marshal.GetFunctionPointerForDelegate(callback), (nuint)slot, out var result);
        return (nint)result.Checked();
    }
    internal int PageCount { get { api.GetPageLen(host, out var result); return (int)result.Checked(); } }
    internal byte PageInfo(int index)
    {
        api.GetPageData(host, (nuint)index, out var result);
        return (byte)(result.Checked() & 0xff); // Return<u8>: only the low byte is defined.
    }
    internal T Guest<T>(string name) where T : Delegate
    {
        api.GetProc(host, name, out var result);
        var address = result.Checked();
        if (address == 0) throw new MissingMethodException(name);
        return Marshal.GetDelegateForFunctionPointer<T>((nint)address);
    }
    internal T In<T>(Func<T> action)
    {
        api.Activate(host, out var result); result.Checked();
        try { return action(); }
        finally { api.Deactivate(host, out result); result.Checked(); }
    }
    internal byte[] Save() => In(() => {
        using var stream = new MemoryStream();
        Native.Write write = (_, data, size) => {
            try { stream.Write(new ReadOnlySpan<byte>((void*)data, checked((int)size))); return 0; }
            catch { return -1; }
        };
        api.SaveState(host, write, 0, out var result); result.Checked(); GC.KeepAlive(write);
        return stream.ToArray();
    });
    internal void Load(byte[] state) => In(() => {
        using var stream = new MemoryStream(state, writable: false);
        Native.Read read = (_, data, size) => Read(stream, data, size);
        api.LoadState(host, read, 0, out var result); result.Checked(); GC.KeepAlive(read);
        Program.Require(stream.Position == stream.Length, "snapshot fully consumed");
        return 0;
    });
    public void Dispose()
    {
        if (host == 0) return;
        api.Destroy(host, out var result); host = 0; result.Checked();
    }
}
