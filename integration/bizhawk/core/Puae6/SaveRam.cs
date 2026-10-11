#nullable enable

using System.Buffers.Binary;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Security.Cryptography;
using System.Text;

namespace Puae6;

/// <summary>Why a SaveRam blob was refused. Nothing is applied before this is decided.</summary>
public enum SaveRamRejection
{
	/// <summary>Not a PUAE6 SaveRam (another core's, garbage, nothing).</summary>
	NotSaveRam,
	/// <summary>A PUAE6 SaveRam of a format this wrapper does not read.</summary>
	WrongVersion,
	/// <summary>Cut short, or followed by something that is not the zero padding a frontend may add.</summary>
	BadLength,
	/// <summary>The checksum does not match.</summary>
	Corrupt,
	/// <summary>A field that cannot be (a disk or a sector that does not exist, sectors out of order, more than the overlay holds).</summary>
	BadFields,
	/// <summary>It names a disk by content that is not the base this core was given at that index.</summary>
	OtherMedia,
	/// <summary>The machine has run (or loaded a state): the base disks cannot change any more.</summary>
	TooLate,
	/// <summary>This core was created without floppy writes: there is nothing to persist.</summary>
	WritesDisabled,
}

/// <summary>
/// A SaveRam the core refused (nothing changed). It derives from <see cref="IOException"/> on purpose: EmuHawk's MainForm.LoadSaveRam, which feeds the .SaveRAM file to StoreSaveRam,
/// catches only IOException (it shows "An error occurred while loading Sram"); any other type would leave a damaged or foreign file as an unhandled exception while a ROM is opening.
/// The core also notifies the reason on screen (<see cref="Puae6Core.StoreSaveRam"/>), which the generic message does not carry.
/// </summary>
public sealed class SaveRamRejectedException : IOException
{
	public SaveRamRejection Reason { get; }
	public SaveRamRejectedException(SaveRamRejection reason, string message) : base(message) => Reason = reason;
}

/// <summary>
/// The durable form of what a guest wrote to its floppies (T28) and to its hard files (T63b): a bounded list of the sectors (floppies) and 4 KiB chunks (hard files) that differ from
/// each disk's base, tagged with the base's content id (the SHA-256 of the image as it was given). It is the ISaveRam of the core. It is never a disk image: the base files are not part of it
/// and a disk without changes is not in it. Taking a SaveRam is not taking a state (docs/03-waterbox.md): the state carries the overlay for rollback and this is what the user chooses to keep.
///
///   "P6SR" | u32 version | u32 floppy disks (at most 16, in increasing index order)
///   per floppy disk: u32 index | 32 bytes content id of the base | u32 sectors (1..1760)
///     per sector, in increasing order: u16 sector | 512 bytes
///   version 2 only, after the floppy disks: u32 hard files (1..4, in increasing order of their number DH0...)
///   per hard file: u32 index | 32 bytes content id of the base | u64 image bytes | u32 chunk bytes (4096) | u32 chunks (1..the chunks of the image)
///     per chunk, in increasing order: u32 chunk | the bytes of the chunk (4096, fewer for the last chunk of an image that is not a multiple of it)
///   32 bytes: SHA-256 of everything before it
///
/// Version 1 is what a core without hard files writes (a floppy-only SaveRam is the same bytes as before hard files existed, and at least one disk is in it); version 2 is written when a hard
/// file changed and may have no floppy disk at all. All integers are little endian. Zero bytes after the checksum are ignored (a frontend may pad a file to the size it expects); anything else
/// after it refuses the blob. A hard file is as MacHawk's UAE core keeps it (a sparse list of the 4 KiB chunks the guest wrote) with the identity of the base added: a chunk is in it only if it
/// differs from the base, so a chunk the guest wrote with the same bytes it had is not.
/// </summary>
public static class SaveRam
{
	public const uint Version = 1, VersionWithHardFiles = 2;
	public const int SectorBytes = 512, SectorsPerDisk = P6.AdfBytes / SectorBytes, MaxSectors = 3520 /* the pool of the overlay (P6_OVERLAY_SECTORS) */;
	const int HeaderBytes = 12, DiskHeaderBytes = 4 + P6.ContentIdSize + 4, ChecksumBytes = 32, HardHeaderBytes = 4 + P6.ContentIdSize + 8 + 4 + 4;
	static readonly byte[] Magic = Encoding.ASCII.GetBytes("P6SR");

	/// <summary>The most bytes the floppy part of a SaveRam can have (without padding): the whole pool of the overlay in sectors, the disks that carry them and the checksum. Hard files add their own chunks, up to their size.</summary>
	public static int MaxBytes => HeaderBytes + P6.MaxMedia * DiskHeaderBytes + MaxSectors * (2 + SectorBytes) + ChecksumBytes;

	/// <summary>One changed sector.</summary>
	public readonly struct Entry
	{
		public int Disk { get; }
		public int Sector { get; }
		public byte[] Data { get; }
		public Entry(int disk, int sector, byte[] data) { Disk = disk; Sector = sector; Data = data; }
	}

	/// <summary>One changed 4 KiB chunk of a hard file (the last chunk of an image that is not a multiple of 4096 is shorter).</summary>
	public readonly struct HardEntry
	{
		public int HardFile { get; }
		public long Chunk { get; }
		public byte[] Data { get; }
		public HardEntry(int hardFile, long chunk, byte[] data) { HardFile = hardFile; Chunk = chunk; Data = data; }
	}

	/// <summary>What a SaveRam holds, once it was read and checked against the bases of a core.</summary>
	public sealed class Contents
	{
		public List<Entry> Floppy { get; } = new();
		public List<HardEntry> Hard { get; } = new();
		/// <summary>Bytes the blob takes (the padding after it is not counted).</summary>
		public int Length { get; set; }
	}

	public static byte[] ContentId(byte[] image)
	{
		using var sha = SHA256.Create();
		return sha.ComputeHash(image);
	}

	/// <summary>The sectors of <paramref name="current"/> that differ from <paramref name="baseImage"/> (both 901120 bytes).</summary>
	public static List<int> ChangedSectors(byte[] baseImage, byte[] current)
	{
		var changed = new List<int>();
		for (int sector = 0; sector < SectorsPerDisk; ++sector)
		{
			int at = sector * SectorBytes;
			bool same = true;
			for (int i = 0; i < SectorBytes && same; ++i) same = baseImage[at + i] == current[at + i];
			if (!same) changed.Add(sector);
		}
		return changed;
	}

	/// <summary>The 4 KiB chunks of <paramref name="current"/> that differ from <paramref name="baseImage"/> (both the same length).</summary>
	public static List<long> ChangedChunks(byte[] baseImage, byte[] current)
	{
		var changed = new List<long>();
		long chunks = ChunksOf(baseImage.LongLength);
		for (long chunk = 0; chunk < chunks; ++chunk)
		{
			int at = (int)(chunk * P6.HardFileChunkBytes), length = ChunkLength(baseImage.LongLength, chunk);
			if (!new ReadOnlySpan<byte>(baseImage, at, length).SequenceEqual(new ReadOnlySpan<byte>(current, at, length))) changed.Add(chunk);
		}
		return changed;
	}

	/// <summary>How many 4 KiB chunks an image of that many bytes has.</summary>
	public static long ChunksOf(long bytes) => (bytes + P6.HardFileChunkBytes - 1) / P6.HardFileChunkBytes;

	static int ChunkLength(long bytes, long chunk) => (int)Math.Min(P6.HardFileChunkBytes, bytes - chunk * P6.HardFileChunkBytes);

	/// <summary>The blob for the disks that changed; null when none did. <paramref name="current"/>[i] is null for a disk that cannot be exported (it is left out).</summary>
	public static byte[]? Build(IReadOnlyList<byte[]> bases, IReadOnlyList<byte[]?> current) => Build(bases, current, new List<byte[]>(), new List<byte[]?>());

	/// <summary>The blob for the floppy disks and the hard files that changed; null when none did. <paramref name="hardCurrent"/>[i] is null for a hard file that was not touched.</summary>
	public static byte[]? Build(IReadOnlyList<byte[]> bases, IReadOnlyList<byte[]?> current, IReadOnlyList<byte[]> hardBases, IReadOnlyList<byte[]?> hardCurrent)
	{
		using var body = new MemoryStream();
		using (var w = new BinaryWriter(body, Encoding.UTF8, true))
		{
			var disks = new List<(int Index, List<int> Sectors)>();
			for (int i = 0; i < bases.Count; ++i)
			{
				if (current[i] is not { } image) continue;
				var changed = ChangedSectors(bases[i], image);
				if (changed.Count != 0) disks.Add((i, changed));
			}
			var hards = new List<(int Index, List<long> Chunks)>();
			for (int i = 0; i < hardBases.Count; ++i)
			{
				if (hardCurrent[i] is not { } image) continue;
				if (image.Length != hardBases[i].Length) throw new InvalidOperationException($"hard file {i} has {image.Length} bytes and its base {hardBases[i].Length}");
				var changed = ChangedChunks(hardBases[i], image);
				if (changed.Count != 0) hards.Add((i, changed));
			}
			if (disks.Count == 0 && hards.Count == 0) return null;
			if (disks.Sum(d => d.Sectors.Count) > MaxSectors) throw new InvalidOperationException("the disks differ from their bases in more sectors than the overlay can hold");
			w.Write(Magic);
			w.Write(hards.Count == 0 ? Version : VersionWithHardFiles);
			w.Write((uint)disks.Count);
			foreach (var (index, sectors) in disks)
			{
				w.Write((uint)index);
				w.Write(ContentId(bases[index]));
				w.Write((uint)sectors.Count);
				foreach (int sector in sectors)
				{
					w.Write((ushort)sector);
					w.Write(current[index]!, sector * SectorBytes, SectorBytes);
				}
			}
			if (hards.Count != 0)
			{
				w.Write((uint)hards.Count);
				foreach (var (index, chunks) in hards)
				{
					var image = hardCurrent[index]!;
					w.Write((uint)index);
					w.Write(ContentId(hardBases[index]));
					w.Write((ulong)image.LongLength);
					w.Write((uint)P6.HardFileChunkBytes);
					w.Write((uint)chunks.Count);
					foreach (long chunk in chunks)
					{
						w.Write((uint)chunk);
						w.Write(image, (int)(chunk * P6.HardFileChunkBytes), ChunkLength(image.LongLength, chunk));
					}
				}
			}
		}
		var bytes = body.ToArray();
		return bytes.Concat(Sha(bytes)).ToArray();
	}

	static byte[] Sha(byte[] data)
	{
		using var sha = SHA256.Create();
		return sha.ComputeHash(data);
	}

	/// <summary>Reads and validates a blob against the bases this core was given; nothing is applied. Throws <see cref="SaveRamRejectedException"/> for a blob that is not whole, not ours or not for these disks.</summary>
	public static List<Entry> Parse(byte[] data, IReadOnlyList<byte[]> bases) => Parse(data, bases, out _);

	/// <param name="length">Bytes the blob takes (the padding after it is not counted).</param>
	public static List<Entry> Parse(byte[] data, IReadOnlyList<byte[]> bases, out int length)
	{
		var contents = Parse(data, bases, new List<byte[]>());
		length = contents.Length;
		return contents.Floppy;
	}

	/// <summary>Reads and validates a blob against the floppy disks and the hard files of a core (the images as the core was given them); nothing is applied.</summary>
	public static Contents Parse(byte[] data, IReadOnlyList<byte[]> bases, IReadOnlyList<byte[]> hardBases)
	{
		void Fail(SaveRamRejection reason, string message) => throw new SaveRamRejectedException(reason, message);
		if (data.Length < 4 || !data.Take(4).SequenceEqual(Magic)) Fail(SaveRamRejection.NotSaveRam, "this is not a PUAE6 SaveRam");
		if (data.Length < HeaderBytes + ChecksumBytes) Fail(SaveRamRejection.BadLength, "the SaveRam ends before its header does");
		uint version = BinaryPrimitives.ReadUInt32LittleEndian(new ReadOnlySpan<byte>(data, 4, 4));
		if (version != Version && version != VersionWithHardFiles) Fail(SaveRamRejection.WrongVersion, $"the SaveRam is format {version}; this core reads {Version} and {VersionWithHardFiles}");
		bool hardSection = version == VersionWithHardFiles;
		uint disks = BinaryPrimitives.ReadUInt32LittleEndian(new ReadOnlySpan<byte>(data, 8, 4));
		if ((disks == 0 && !hardSection) || disks > P6.MaxMedia) Fail(SaveRamRejection.BadFields, $"a SaveRam names {disks} disks");
		var contents = new Contents();
		var entries = contents.Floppy;
		int at = HeaderBytes;
		long total = 0;
		int previousDisk = -1;
		for (uint d = 0; d < disks; ++d)
		{
			if (data.Length - at < DiskHeaderBytes + ChecksumBytes) Fail(SaveRamRejection.BadLength, "the SaveRam ends inside the header of a disk");
			uint index = BinaryPrimitives.ReadUInt32LittleEndian(new ReadOnlySpan<byte>(data, at, 4));
			var id = new byte[P6.ContentIdSize];
			Array.Copy(data, at + 4, id, 0, id.Length);
			uint count = BinaryPrimitives.ReadUInt32LittleEndian(new ReadOnlySpan<byte>(data, at + 4 + P6.ContentIdSize, 4));
			at += DiskHeaderBytes;
			if (index >= bases.Count || (int)index <= previousDisk) Fail(SaveRamRejection.BadFields, $"a SaveRam names disk {index} out of order or past the {bases.Count} this core has");
			if (count == 0 || count > SectorsPerDisk) Fail(SaveRamRejection.BadFields, $"a SaveRam carries {count} sectors of one disk");
			total += count;
			if (total > MaxSectors) Fail(SaveRamRejection.BadFields, "a SaveRam carries more sectors than the overlay holds");
			if ((long)data.Length - at - ChecksumBytes < (long)count * (2 + SectorBytes)) Fail(SaveRamRejection.BadLength, "the SaveRam ends inside the sectors of a disk");
			previousDisk = (int)index;
			int previousSector = -1;
			for (uint s = 0; s < count; ++s)
			{
				int sector = BinaryPrimitives.ReadUInt16LittleEndian(new ReadOnlySpan<byte>(data, at, 2));
				if (sector >= SectorsPerDisk || sector <= previousSector) Fail(SaveRamRejection.BadFields, $"disk {index}: sector {sector} is out of order or does not exist");
				previousSector = sector;
				var bytes = new byte[SectorBytes];
				Array.Copy(data, at + 2, bytes, 0, SectorBytes);
				entries.Add(new Entry((int)index, sector, bytes));
				at += 2 + SectorBytes;
			}
			if (!ContentId(bases[(int)index]).SequenceEqual(id))
				Fail(SaveRamRejection.OtherMedia, $"the SaveRam was made for another disk in slot {index}: its base is not the one this core was given");
		}
		if (hardSection)
		{
			if (data.Length - at < 4 + ChecksumBytes) Fail(SaveRamRejection.BadLength, "the SaveRam ends before its hard files");
			uint hards = BinaryPrimitives.ReadUInt32LittleEndian(new ReadOnlySpan<byte>(data, at, 4));
			at += 4;
			if (hards == 0 || hards > P6.MaxHardFiles) Fail(SaveRamRejection.BadFields, $"a SaveRam names {hards} hard files");
			int previousHard = -1;
			for (uint h = 0; h < hards; ++h)
			{
				if (data.Length - at < HardHeaderBytes + ChecksumBytes) Fail(SaveRamRejection.BadLength, "the SaveRam ends inside the header of a hard file");
				uint index = BinaryPrimitives.ReadUInt32LittleEndian(new ReadOnlySpan<byte>(data, at, 4));
				var id = new byte[P6.ContentIdSize];
				Array.Copy(data, at + 4, id, 0, id.Length);
				ulong bytes = BinaryPrimitives.ReadUInt64LittleEndian(new ReadOnlySpan<byte>(data, at + 4 + P6.ContentIdSize, 8));
				uint chunkBytes = BinaryPrimitives.ReadUInt32LittleEndian(new ReadOnlySpan<byte>(data, at + 12 + P6.ContentIdSize, 4));
				uint count = BinaryPrimitives.ReadUInt32LittleEndian(new ReadOnlySpan<byte>(data, at + 16 + P6.ContentIdSize, 4));
				at += HardHeaderBytes;
				if (index >= hardBases.Count || (int)index <= previousHard) Fail(SaveRamRejection.BadFields, $"a SaveRam names hard file {index} out of order or past the {hardBases.Count} this core has");
				previousHard = (int)index;
				var image = hardBases[(int)index];
				if (!ContentId(image).SequenceEqual(id) || bytes != (ulong)image.LongLength)
					Fail(SaveRamRejection.OtherMedia, $"the SaveRam was made for another hard file in slot {index}: its base is not the one this core was given");
				long chunks = ChunksOf(image.LongLength);
				if (chunkBytes != P6.HardFileChunkBytes || count == 0 || count > chunks) Fail(SaveRamRejection.BadFields, $"hard file {index}: {count} chunks of {chunkBytes} bytes");
				long previousChunk = -1;
				for (uint c = 0; c < count; ++c)
				{
					if (data.Length - at < 4 + ChecksumBytes) Fail(SaveRamRejection.BadLength, "the SaveRam ends inside the chunks of a hard file");
					long chunk = BinaryPrimitives.ReadUInt32LittleEndian(new ReadOnlySpan<byte>(data, at, 4));
					if (chunk >= chunks || chunk <= previousChunk) Fail(SaveRamRejection.BadFields, $"hard file {index}: chunk {chunk} is out of order or does not exist");
					previousChunk = chunk;
					int size = ChunkLength(image.LongLength, chunk);
					if (data.Length - at - 4 - ChecksumBytes < size) Fail(SaveRamRejection.BadLength, "the SaveRam ends inside a chunk of a hard file");
					var chunkData = new byte[size];
					Array.Copy(data, at + 4, chunkData, 0, size);
					contents.Hard.Add(new HardEntry((int)index, chunk, chunkData));
					at += 4 + size;
				}
			}
		}
		if (data.Length - at < ChecksumBytes) Fail(SaveRamRejection.BadLength, "the SaveRam ends before its checksum");
		if (!Sha(data.Take(at).ToArray()).SequenceEqual(data.Skip(at).Take(ChecksumBytes)))
			Fail(SaveRamRejection.Corrupt, "the SaveRam does not match its checksum");
		for (int i = at + ChecksumBytes; i < data.Length; ++i)
			if (data[i] != 0) Fail(SaveRamRejection.BadLength, "the SaveRam has something after its checksum that is not padding");
		contents.Length = at + ChecksumBytes;
		return contents;
	}

	/// <summary>The disks with the entries written over copies of their bases. The bases are not changed.</summary>
	public static List<byte[]> Apply(IReadOnlyList<byte[]> bases, IReadOnlyList<Entry> entries)
	{
		var result = bases.Select(b => (byte[])b.Clone()).ToList();
		foreach (var e in entries) Array.Copy(e.Data, 0, result[e.Disk], e.Sector * SectorBytes, SectorBytes);
		return result;
	}

	/// <summary>The hard files with the chunks written over copies of their bases; a hard file with no chunk is not copied (the base itself is returned). The bases are not changed.</summary>
	public static List<byte[]> ApplyHard(IReadOnlyList<byte[]> hardBases, IReadOnlyList<HardEntry> entries)
	{
		var result = hardBases.ToList();
		var copied = new HashSet<int>();
		foreach (var e in entries)
		{
			if (copied.Add(e.HardFile)) result[e.HardFile] = (byte[])hardBases[e.HardFile].Clone();
			Array.Copy(e.Data, 0, result[e.HardFile], e.Chunk * P6.HardFileChunkBytes, e.Data.Length);
		}
		return result;
	}
}
