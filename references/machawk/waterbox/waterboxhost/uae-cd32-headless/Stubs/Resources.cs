// Stand-in for BizHawk.Emulation.Cores' generated resource accessor: reads the same files from the source tree.
namespace BizHawk.Emulation.Cores.Properties
{
	internal static class Resources
	{
		private static byte[] Read(string name)
		{
			var dir = Environment.GetEnvironmentVariable("BIZHAWK_RESOURCES")
				?? Path.Combine(AppContext.BaseDirectory, "../../../../../../../src/BizHawk.Emulation.Cores/Resources");
			return File.ReadAllBytes(Path.Combine(dir, name));
		}

		internal static readonly Lazy<byte[]> UAE_WHDLOAD_HDF = new(() => Read("uae-whdload.hdf.zst"));
		internal static readonly Lazy<byte[]> UAE_WHDSAVES_HDF = new(() => Read("uae-whdsaves.hdf.zst"));
		internal static readonly Lazy<byte[]> UAE_WHDLOAD_PREFS = new(() => Read("uae-whdload.prefs"));
	}
}
