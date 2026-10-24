using System.Collections.Generic;
using System.IO;

using BizHawk.Common;

namespace BizHawk.Emulation.Cores.Computers.Amiga
{
	// Hard disk images live in guest memory; SaveRAM is a sparse diff against the original image,
	// built from the per-chunk dirty bitmap the core keeps (which itself travels inside savestates).
	public partial class UAE
	{
		private const string HardDiskSaveRamMagic = "BIZHAWK_UAE_HARDDISK_DIFF";
		private const int HardDiskSaveRamVersion = 1;

		public bool HasHardDisk => _mountedHardDisks.Count > 0;

		public override bool SaveRamModified
		{
			get
			{
				if (_mountedHardDisks.Count == 0)
				{
					return base.SaveRamModified;
				}

				using (_exe.EnterExit())
				{
					for (var i = 0; i < _mountedHardDisks.Count; i++)
					{
						if (_uae.HardDiskModified(i, false))
						{
							return true;
						}
					}
				}

				return false;
			}
		}

		public override byte[] CloneSaveRam(bool clearDirty)
		{
			if (_mountedHardDisks.Count == 0)
			{
				return base.CloneSaveRam(clearDirty);
			}

			using (_exe.EnterExit())
			{
				using var stream = new MemoryStream();
				using var writer = new BinaryWriter(stream);
				writer.Write(HardDiskSaveRamMagic);
				writer.Write(HardDiskSaveRamVersion);
				writer.Write(_mountedHardDisks.Count);
				var chunk = new byte[LibUAE.HARDDISK_CHUNK];
				for (var i = 0; i < _mountedHardDisks.Count; i++)
				{
					var size = _uae.HardDiskSize(i);
					var chunks = _uae.HardDiskChunkCount(i);
					var map = new byte[(chunks + 7) / 8];
					_uae.HardDiskGetDirtyMap(i, map);
					var dirty = new List<long>();
					for (long c = 0; c < chunks; c++)
					{
						if ((map[c >> 3] & (1 << (int)(c & 7))) != 0)
						{
							dirty.Add(c);
						}
					}

					writer.Write(size);
					writer.Write(LibUAE.HARDDISK_CHUNK);
					writer.Write(dirty.Count);
					foreach (var c in dirty)
					{
						var offset = c * LibUAE.HARDDISK_CHUNK;
						var len = (int)Math.Min(LibUAE.HARDDISK_CHUNK, size - offset);
						_uae.HardDiskRead(i, offset, chunk, 0, len);
						writer.Write(c);
						writer.Write(len);
						writer.Write(chunk, 0, len);
					}

					if (clearDirty)
					{
						_uae.HardDiskModified(i, true);
					}
				}

				writer.Flush();
				return stream.ToArray();
			}
		}

		public override void StoreSaveRam(byte[] data)
		{
			if (_mountedHardDisks.Count == 0)
			{
				base.StoreSaveRam(data);
				return;
			}

			using var stream = new MemoryStream(data, false);
			using var reader = new BinaryReader(stream);
			if (reader.ReadString() != HardDiskSaveRamMagic || reader.ReadInt32() != HardDiskSaveRamVersion)
			{
				throw new InvalidOperationException("The UAE hard disk SaveRAM format is not recognized");
			}

			var count = reader.ReadInt32();
			if (count != _mountedHardDisks.Count)
			{
				throw new InvalidOperationException($"UAE expected {_mountedHardDisks.Count} hard disks in SaveRAM, got {count}");
			}

			using (_exe.EnterExit())
			{
				for (var i = 0; i < count; i++)
				{
					var size = reader.ReadInt64();
					var chunkSize = reader.ReadInt32();
					if (size != _uae.HardDiskSize(i) || chunkSize != LibUAE.HARDDISK_CHUNK)
					{
						throw new InvalidOperationException($"UAE hard disk {i} does not match the SaveRAM ({size} bytes, {chunkSize} byte chunks)");
					}

					var dirty = reader.ReadInt32();
					var chunk = new byte[LibUAE.HARDDISK_CHUNK];
					for (var n = 0; n < dirty; n++)
					{
						var c = reader.ReadInt64();
						var len = reader.ReadInt32();
						if (len < 0 || len > chunk.Length || reader.Read(chunk, 0, len) != len)
						{
							throw new EndOfStreamException($"UAE hard disk {i} SaveRAM is truncated");
						}

						if (!_uae.HardDiskWrite(i, c * LibUAE.HARDDISK_CHUNK, chunk, 0, len, true))
						{
							throw new InvalidOperationException($"UAE hard disk {i} SaveRAM chunk {c} is out of range");
						}
					}

					_uae.HardDiskModified(i, true);
				}
			}
		}

		/// <summary>Current contents of a mounted hard disk image, including any guest writes.</summary>
		public byte[] GetHardDiskContents(int index)
		{
			if (index < 0 || index >= _mountedHardDisks.Count)
			{
				throw new ArgumentOutOfRangeException(nameof(index));
			}

			using (_exe.EnterExit())
			{
				var size = _uae.HardDiskSize(index);
				var ret = new byte[size];
				const int step = 64 * 1024 * 1024;
				for (long offset = 0; offset < size; offset += step)
				{
					_uae.HardDiskRead(index, offset, ret, offset, (int)Math.Min(step, size - offset));
				}

				return ret;
			}
		}

		public string GetHardDiskName(int index) => _mountedHardDisks[index].Name;
	}
}
