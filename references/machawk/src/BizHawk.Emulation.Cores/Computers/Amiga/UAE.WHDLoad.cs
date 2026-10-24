using System.Collections.Generic;
using System.IO;
using System.IO.Compression;
using System.Linq;

using BizHawk.Common;
using BizHawk.Emulation.Common;
using BizHawk.Emulation.Cores.Properties;

namespace BizHawk.Emulation.Cores.Computers.Amiga
{
	// WHDLoad packs (.lha, or a .zip holding a .slave) are mounted read-only as UAE archive volumes,
	// the same way libretro-uae does it. Boot comes from the WHDLoad.hdf image that ships with
	// libretro-uae: its startup-sequence finds the slave on DH0:, copies Kickstart images from a
	// volume named RASystem: (built here as a zip from the firmware database) and redirects all
	// game writes to WHDSaves:. Both images live in guest memory like any other hard disk, so
	// savegames travel in the SaveRAM diff and the archives are never modified.
	public partial class UAE
	{
		private const int WhdLoadHardDiskCount = 2;

		// file names S:KSCopy expects on RASystem:, keyed by the Kickstart image SHA-1
		private static readonly Dictionary<string, string> WhdLoadKickstarts = new()
		{
			["11F9E62CF299F72184835B7B2A70A16333FC0D88"] = "kick33180.A500",
			["891E9A547772FE0C6C19B610BAF8BC4EA7FCB785"] = "kick34005.A500",
			["3B7F1493B27E212830F989F26CA76C02049F09CA"] = "kick40063.A600",
			["E21545723FE8374E91342617604F1B3D703094F1"] = "kick40068.A1200",
		};

		private static class WhdLoadFiles
		{
			public const string Game = "WHDLoadGame";
			public const string System = "WHDLoadSystem.zip";
		}

		private long WhdLoadHeapKB()
		{
			if (_whdLoadArchives.Count == 0)
			{
				return 0;
			}

			// two 4 MB images plus room for the archive directory and unpacked files
			return 2 * 4096 + 64 + _whdLoadArchives.Sum(a => a.FileData.LongLength * 4 / 1024) + 16 * 1024;
		}

		private void MountWhdLoad(byte[] kickstartData)
		{
			var whdLoadImage = Zstd.DecompressZstdStream(new MemoryStream(Resources.UAE_WHDLOAD_HDF.Value)).ToArray();
			var whdSavesImage = Zstd.DecompressZstdStream(new MemoryStream(Resources.UAE_WHDSAVES_HDF.Value)).ToArray();
			MountHardDisk("WHDLoad.hdf", whdLoadImage, "WHDLoad", 0);
			MountHardDisk("WHDSaves.hdf", whdSavesImage, "WHDSaves", -128);

			for (var i = 0; i < _whdLoadArchives.Count; i++)
			{
				var archive = _whdLoadArchives[i];
				var fileName = WhdLoadFiles.Game + i + archive.Extension.ToLowerInvariant();
				_exe.AddReadonlyFile(archive.FileData, fileName);
				var volume = Path.GetFileNameWithoutExtension(GetFullName(archive));
				AppendSetting($"filesystem2=ro,DH{i}:{volume}:{fileName},-128");
			}

			_exe.AddReadonlyFile(BuildWhdLoadSystemVolume(kickstartData), WhdLoadFiles.System);
			AppendSetting($"filesystem2=ro,RASystem:RASystem:{WhdLoadFiles.System},-128");
		}

		/// <summary>Collects the Kickstart images the user has and builds the RASystem: volume from them.</summary>
		private byte[] BuildWhdLoadSystemVolume(byte[] kickstartData)
		{
			var kickstarts = new Dictionary<string, byte[]>();
			void Consider(byte[] data)
			{
				if (data is null)
				{
					return;
				}

				if (WhdLoadKickstarts.TryGetValue(SHA1Checksum.ComputeDigestHex(data), out var name))
				{
					kickstarts[name] = data;
				}
			}

			Consider(kickstartData);
			foreach (var id in new[] { "A500", "A600", "A1200" })
			{
				Consider(CoreComm.CoreFileProvider.GetFirmware(new FirmwareID(VSystemID.Raw.Amiga, id)));
			}

			foreach (var name in WhdLoadKickstarts.Values)
			{
				Console.WriteLine($"WHDLoad Kickstart {name}: {(kickstarts.ContainsKey(name) ? "available" : "missing")}");
			}

			return BuildWhdLoadSystemVolume(kickstarts, Resources.UAE_WHDLOAD_PREFS.Value);
		}

		/// <summary>
		/// Kickstart images and WHDLoad.prefs, as a zip UAE mounts as the RASystem: volume.
		/// </summary>
		/// <remarks>
		/// This has to come out byte-identical every time it is built: the guest gets it as a read-only file,
		/// and waterbox hashes those to decide whether a savestate belongs to this machine. A zip stamped with
		/// the current time made every state from an earlier session fail to load, which the user sees as the
		/// savestate/core build mismatch dialog. Hence the fixed timestamp and the fixed entry order.
		/// </remarks>
		internal static byte[] BuildWhdLoadSystemVolume(IReadOnlyDictionary<string, byte[]> kickstarts, byte[] prefsData)
		{
			// the earliest date a zip entry can carry, since the format stores MS-DOS timestamps
			var timestamp = new DateTimeOffset(1980, 1, 1, 0, 0, 0, TimeSpan.Zero);

			using var stream = new MemoryStream();
			using (var zip = new ZipArchive(stream, ZipArchiveMode.Create, leaveOpen: true))
			{
				void Add(string name, byte[] data)
				{
					var entry = zip.CreateEntry(name, CompressionLevel.NoCompression);
					entry.LastWriteTime = timestamp; // must be set before the entry is opened, Create mode seals it after that
					using var contents = entry.Open();
					contents.Write(data, 0, data.Length);
				}

				foreach (var name in kickstarts.Keys.OrderBy(name => name, StringComparer.Ordinal))
				{
					Add(name, kickstarts[name]);
				}

				Add("WHDLoad.prefs", prefsData);
			}

			return stream.ToArray();
		}
	}
}
