using System.Runtime.InteropServices;

using System.Reflection;

using BizHawk.BizInvoke;
using BizHawk.Common;
using BizHawk.Emulation.Cores.Waterbox;

namespace Puae6;

/// <summary>The exports of puae6.wbx that the wrapper calls (adapters/waterbox/p6w.h). Pointers are sandbox or host addresses as the header says.</summary>
public abstract class LibPuae6
{
	public const CallingConvention CC = CallingConvention.Cdecl;

	[BizImport(CC)] public abstract uint p6w_abi_version();
	/// <summary>Writes P6W_LAYOUT_VALUES values and returns how many; zero when <paramref name="capacity"/> is too small.</summary>
	[BizImport(CC)] public abstract ulong p6w_layout(IntPtr output, ulong capacity);
	[BizImport(CC)] public abstract int p6w_init_machine(IntPtr args);
	[BizImport(CC)] public abstract int p6w_frame_advance(IntPtr input, uint renderFlags, IntPtr output);
	[BizImport(CC)] public abstract int p6w_reset();
	[BizImport(CC)] public abstract int p6w_shutdown();
	/// <summary>The memory areas of the loaded machine (P6wMemoryArea), up to <paramref name="capacity"/>; zero before p6w_init_machine succeeded.</summary>
	[BizImport(CC)] public abstract ulong p6w_memory_areas(IntPtr output, ulong capacity);
	[BizImport(CC)] public abstract void p6w_memory_access(uint type, IntPtr buffer, long address, long count, uint write);
	[BizImport(CC)] public abstract ulong p6w_memory_refused();
}

/// <summary>The guest was loaded but is not the one the wrapper was written for.</summary>
public sealed class GuestLayoutException : Exception
{
	public GuestLayoutException(string message) : base(message) { }
}

/// <summary>
/// puae6.wbx loaded in a Waterbox host of BizHawk: identity checked first (<see cref="GuestIdentity"/>), then the host created by the real
/// WaterboxHost, then the layout the guest was compiled with compared with the one the wrapper has (<see cref="AbiLayout"/>). It is not the
/// emulator core; it is the one place that knows how to open the guest.
/// </summary>
public sealed class Puae6Guest : IDisposable
{
	/// <summary>How the host is created. Tests replace it to prove that a refused guest never gets as far as <c>wbx_create_host</c>.</summary>
	public static Func<WaterboxOptions, WaterboxHost> CreateHost { get; set; } = options => new WaterboxHost(options);

	/// <summary>The SHA-256 of the guest this wrapper was built with, from the build (<c>Puae6GuestSha256</c> metadata). Empty when the build had none.</summary>
	public static string BuiltWithSha256 { get; } = typeof(Puae6Guest).Assembly
		.GetCustomAttributes<System.Reflection.AssemblyMetadataAttribute>().FirstOrDefault(a => a.Key == "Puae6GuestSha256")?.Value ?? "";

	/// <summary>Opens the guest this wrapper was built with. A build that did not record one cannot open anything.</summary>
	public static Puae6Guest Open(string directory, string fileName, WaterboxOptions heaps)
		=> Open(directory, fileName, BuiltWithSha256.Length != 0 ? BuiltWithSha256
			: throw new InvalidOperationException("this build of the wrapper has no pinned guest hash (Puae6GuestSha256)"), heaps);

	public WaterboxHost Host { get; }
	public LibPuae6 Lib { get; }
	public GuestIdentity.VerifiedGuest Identity { get; }

	Puae6Guest(WaterboxHost host, LibPuae6 lib, GuestIdentity.VerifiedGuest identity)
	{
		Host = host;
		Lib = lib;
		Identity = identity;
	}

	/// <param name="directory">Where <paramref name="fileName"/> is.</param>
	/// <param name="pinnedSha256">SHA-256 of the guest this wrapper was built with.</param>
	/// <param name="heaps">Heap sizes, which are part of what a snapshot is compatible with; <c>Path</c> and <c>Filename</c> are set here.</param>
	public static Puae6Guest Open(string directory, string fileName, string pinnedSha256, WaterboxOptions heaps)
	{
		var identity = GuestIdentity.Verify(Path.Combine(directory, fileName), pinnedSha256);
		var options = new WaterboxOptions
		{
			Path = directory, Filename = fileName,
			SbrkHeapSizeKB = heaps.SbrkHeapSizeKB, SealedHeapSizeKB = heaps.SealedHeapSizeKB, InvisibleHeapSizeKB = heaps.InvisibleHeapSizeKB,
			PlainHeapSizeKB = heaps.PlainHeapSizeKB, MmapHeapSizeKB = heaps.MmapHeapSizeKB,
		};
		var host = CreateHost(options);
		try
		{
			LibPuae6 lib;
			using (host.EnterExit())
			{
				var adapter = CallingConventionAdapters.MakeWaterbox([], host);
				lib = BizInvoker.GetInvoker<LibPuae6>(host, host, adapter);
			}
			var guest = new Puae6Guest(host, lib, identity);
			guest.CheckLayout();
			return guest;
		}
		catch
		{
			host.Dispose();
			throw;
		}
	}

	/// <summary>Throws when the guest's ABI version or any size or offset differs from the wrapper's.</summary>
	void CheckLayout()
	{
		using var _ = Host.EnterExit();
		uint version = Lib.p6w_abi_version();
		if (version != P6.AbiVersion) throw new GuestLayoutException($"the guest speaks ABI version {version}, the wrapper {P6.AbiVersion}");
		var expected = AbiLayout.Compute();
		var reported = new ulong[expected.Length];
		unsafe
		{
			fixed (ulong* p = reported)
				if (Lib.p6w_layout((IntPtr)p, (ulong)reported.Length) != (ulong)reported.Length)
					throw new GuestLayoutException("the guest did not report its layout");
		}
		for (int i = 0; i < expected.Length; ++i)
			if (reported[i] != expected[i])
				throw new GuestLayoutException($"{AbiLayout.Names[i]} is {reported[i]} in the guest and {expected[i]} in the wrapper");
	}

	public void Dispose() => Host.Dispose();
}
