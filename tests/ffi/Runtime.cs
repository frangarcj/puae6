using System.Runtime.InteropServices;

/// <summary>T34a: how every command of this host loads libwaterboxhost and what it tells it about eviction.</summary>
internal static unsafe partial class Program
{
	/// <summary>
	/// Loads the runtime by its path (BizHawk asks dlopen for the bare name afterwards, and macOS finds it because it is already loaded) and sets its eviction.
	/// P6_EVICT=1 keeps the runtime's own default (a sandbox is unmapped when it is deactivated and mapped again when it is activated, to catch dangling pointers);
	/// anything else, the variable unset included, is what a Release BizHawk sets in WaterboxHost's static constructor (#if !DEBUG): the sandbox stays mapped between
	/// calls. That host is a Debug build here, so it would not do it by itself. A Debug runtime (the Debug lane, P6_WATERBOX_RUNTIME=debug) ignores the call and
	/// always evicts, with its assertions on.
	/// </summary>
	static nint LoadRuntime(string path)
	{
		var runtime = NativeLibrary.Load(Path.GetFullPath(path));
		((delegate* unmanaged[Cdecl]<byte, void>)NativeLibrary.GetExport(runtime, "wbx_set_always_evict_blocks"))(Environment.GetEnvironmentVariable("P6_EVICT") == "1" ? (byte)1 : (byte)0);
		return runtime;
	}
}
