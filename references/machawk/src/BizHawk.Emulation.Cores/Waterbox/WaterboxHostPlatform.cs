using BizHawk.Common;

namespace BizHawk.Emulation.Cores.Waterbox
{
	internal static class WaterboxHostPlatform
	{
		public static string NativeLibraryName => OSTailoredCode.CurrentOS switch
		{
			OSTailoredCode.DistinctOS.Windows => "waterboxhost.dll",
			OSTailoredCode.DistinctOS.macOS => "libwaterboxhost.dylib",
			_ => "libwaterboxhost.so",
		};
	}
}
