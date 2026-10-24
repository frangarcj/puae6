using System.Runtime.InteropServices;

using BizHawk.BizInvoke;
using BizHawk.Common;
using BizHawk.Emulation.Cores.Waterbox;

using static BizHawk.Emulation.Cores.Waterbox.WaterboxHostNative;

public abstract class SmokeExports
{
	[BizImport(CallingConvention.Cdecl)]
	public abstract int add(int a, int b);

	[BizImport(CallingConvention.Cdecl)]
	public abstract void increment();

	[BizImport(CallingConvention.Cdecl)]
	public abstract int get_counter();
}

internal sealed class GuestExportResolver(WaterboxHostNative native, IntPtr host) : IImportResolver
{
	public IntPtr GetProcAddrOrZero(string entryPoint)
	{
		native.wbx_get_proc_addr(host, entryPoint, out var result);
		return result.GetDataOrThrow();
	}

	public IntPtr GetProcAddrOrThrow(string entryPoint)
	{
		var result = GetProcAddrOrZero(entryPoint);
		return result != IntPtr.Zero
			? result
			: throw new InvalidOperationException($"missing guest export: {entryPoint}");
	}
}

internal sealed class StreamCallbacks
{
	private readonly Stream _stream;

	public StreamCallbacks(Stream stream) => _stream = stream;

	public unsafe IntPtr Read(IntPtr userdata, IntPtr data, UIntPtr size)
	{
		var span = new Span<byte>((void*)data, checked((int)size));
		return (IntPtr)_stream.Read(span);
	}

	public unsafe int Write(IntPtr userdata, IntPtr data, UIntPtr size)
	{
		var span = new ReadOnlySpan<byte>((void*)data, checked((int)size));
		_stream.Write(span);
		return 0;
	}
}

internal static class Posix
{
	private const string LibSystem = "libSystem.B.dylib";

	[DllImport(LibSystem)]
	internal static extern int close(int fd);

	[DllImport(LibSystem)]
	internal static extern int dup(int fd);

	[DllImport(LibSystem)]
	internal static extern int dup2(int source, int destination);

	[DllImport(LibSystem)]
	internal static extern int open([MarshalAs(UnmanagedType.LPUTF8Str)] string path, int flags);
}

internal static class Program
{
	private const int Stdout = 1;
	private const int OWriteOnly = 1;

	private static int Main()
	{
		Directory.SetCurrentDirectory(AppContext.BaseDirectory);
		var nativeName = WaterboxHostPlatform.NativeLibraryName;
		using var library = new DynamicLibraryImportResolver(nativeName);
		var native = BizInvoker.GetInvoker<WaterboxHostNative>(library, CallingConventionAdapters.Native);

		var image = File.ReadAllBytes("smoke.wbx");
		using var imageStream = new MemoryStream(image, writable: false);
		var imageCallbacks = new StreamCallbacks(imageStream);
		ReadCallback readImage = imageCallbacks.Read;

		var layout = new MemoryLayoutTemplate
		{
			sbrk_size = (UIntPtr)0x4000,
			sealed_size = (UIntPtr)0x4000,
			invis_size = (UIntPtr)0x4000,
			plain_size = (UIntPtr)0x4000,
			mmap_size = (UIntPtr)0x4000,
		};

		Console.Out.Flush();
		var savedStdout = Posix.dup(Stdout);
		var nullStdout = Posix.open("/dev/null", OWriteOnly);
		Posix.dup2(nullStdout, Stdout);
		Posix.close(nullStdout);

		native.wbx_create_host(layout, "smoke.wbx", readImage, IntPtr.Zero, out var createResult);
		var host = createResult.GetDataOrThrow();
		native.wbx_activate_host(host, out var activateResult);
		activateResult.GetDataOrThrow();

		var guest = BizInvoker.GetInvoker<SmokeExports>(new GuestExportResolver(native, host), CallingConventionAdapters.Native);
		var sum = guest.add(2, 3);

		native.wbx_seal(host, out var sealResult);
		sealResult.GetDataOrThrow();
		guest.increment();
		var beforeSave = guest.get_counter();

		using var stateStream = new MemoryStream();
		var stateCallbacks = new StreamCallbacks(stateStream);
		WriteCallback writeState = stateCallbacks.Write;
		native.wbx_save_state(host, writeState, IntPtr.Zero, out var saveResult);
		saveResult.GetDataOrThrow();

		guest.increment();
		var afterMutation = guest.get_counter();
		stateStream.Position = 0;
		ReadCallback readState = stateCallbacks.Read;
		native.wbx_load_state(host, readState, IntPtr.Zero, out var loadResult);
		loadResult.GetDataOrThrow();
		var restored = guest.get_counter();

		native.wbx_deactivate_host(host, out var deactivateResult);
		deactivateResult.GetDataOrThrow();
		native.wbx_destroy_host(host, out var destroyResult);
		destroyResult.GetDataOrThrow();

		Console.Out.Flush();
		Posix.dup2(savedStdout, Stdout);
		Posix.close(savedStdout);

		var runtimeOk = RuntimeInformation.ProcessArchitecture == Architecture.Arm64;
		var osOk = OSTailoredCode.CurrentOS == OSTailoredCode.DistinctOS.macOS;
		var stateOk = beforeSave == 1 && afterMutation == 2 && restored == 1;
		var passed = runtimeOk && osOk && sum == 5 && stateOk;

		Console.WriteLine($"runtime: .NET {RuntimeInformation.ProcessArchitecture.ToString().ToLowerInvariant()}");
		Console.WriteLine("os: macOS");
		Console.WriteLine($"native: {nativeName}\n");
		Console.WriteLine("dlopen: OK");
		Console.WriteLine("wbx_create_host: OK");
		Console.WriteLine("resolve add: OK");
		Console.WriteLine($"add(2, 3): {sum}\n");
		Console.WriteLine($"save/load through managed API: {(stateOk ? "OK" : "FAIL")}\n");
		Console.WriteLine(passed ? "PASS" : "FAIL");
		return passed ? 0 : 1;
	}
}
