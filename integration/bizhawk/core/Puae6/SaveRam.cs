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
/// The durable form of what a guest wrote to its floppies (T28): a bounded list of the sectors that differ from each disk's base, tagged with the base's content id
/// (the SHA-256 of the image as it was given). It is the ISaveRam of the core. It is never a disk image: the base files are not part of it and a disk without changes is not
/// in it. Taking a SaveRam is not taking a state (docs/03-waterbox.md): the state carries the overlay for rollback and this is what the user chooses to keep.
///
///   "P6SR" | u32 version = 1 | u32 disks (at most 16, in increasing index order)
///   per disk: u32 index | 32 bytes content id of the base | u32 sectors (1..1760)
///     per sector, in increasing order: u16 sector | 512 bytes
///   32 bytes: SHA-256 of everything before it
///
/// All integers are little endian. Zero bytes after the checksum are ignored (a frontend may pad a file to the size it expects); anything else after it refuses the blob.
/// </summary>
public static class SaveRam
{
	public const uint Version = 1;
	public const int SectorBytes = 512, SectorsPerDisk = P6.AdfBytes / SectorBytes, MaxSectors = 3520 /* the pool of the overlay (P6_OVERLAY_SECTORS) */;
	const int HeaderBytes = 12, DiskHeaderBytes = 4 + P6.ContentIdSize + 4, ChecksumBytes = 32;
	static readonly byte[] Magic = Encoding.ASCII.GetBytes("P6SR");

	/// <summary>The most bytes a SaveRam can have (without padding): the whole pool of the overlay in sectors, the disks that carry them and the checksum.</summary>
	public static int MaxBytes => HeaderBytes + P6.MaxMedia * DiskHeaderBytes + MaxSectors * (2 + SectorBytes) + ChecksumBytes;

	/// <summary>One changed sector.</summary>
	public readonly struct Entry
	{
		public int Disk { get; }
		public int Sector { get; }
		public byte[] Data { get; }
		public Entry(int disk, int sector, byte[] data) { Disk = disk; Sector = sector; Data = data; }
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

	/// <summary>The blob for the disks that changed; null when none did. <paramref name="current"/>[i] is null for a disk that cannot be exported (it is left out).</summary>
	public static byte[]? Build(IReadOnlyList<byte[]> bases, IReadOnlyList<byte[]?> current)
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
			if (disks.Count == 0) return null;
			if (disks.Sum(d => d.Sectors.Count) > MaxSectors) throw new InvalidOperationException("the disks differ from their bases in more sectors than the overlay can hold");
			w.Write(Magic);
			w.Write(Version);
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
		void Fail(SaveRamRejection reason, string message) => throw new SaveRamRejectedException(reason, message);
		if (data.Length < 4 || !data.Take(4).SequenceEqual(Magic)) Fail(SaveRamRejection.NotSaveRam, "this is not a PUAE6 SaveRam");
		if (data.Length < HeaderBytes + ChecksumBytes) Fail(SaveRamRejection.BadLength, "the SaveRam ends before its header does");
		uint version = BinaryPrimitives.ReadUInt32LittleEndian(new ReadOnlySpan<byte>(data, 4, 4));
		if (version != Version) Fail(SaveRamRejection.WrongVersion, $"the SaveRam is format {version}; this core reads {Version}");
		uint disks = BinaryPrimitives.ReadUInt32LittleEndian(new ReadOnlySpan<byte>(data, 8, 4));
		if (disks == 0 || disks > P6.MaxMedia) Fail(SaveRamRejection.BadFields, $"a SaveRam names {disks} disks");
		var entries = new List<Entry>();
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
		if (data.Length - at < ChecksumBytes) Fail(SaveRamRejection.BadLength, "the SaveRam ends before its checksum");
		if (!Sha(data.Take(at).ToArray()).SequenceEqual(data.Skip(at).Take(ChecksumBytes)))
			Fail(SaveRamRejection.Corrupt, "the SaveRam does not match its checksum");
		for (int i = at + ChecksumBytes; i < data.Length; ++i)
			if (data[i] != 0) Fail(SaveRamRejection.BadLength, "the SaveRam has something after its checksum that is not padding");
		length = at + ChecksumBytes;
		return entries;
	}

	/// <summary>The disks with the entries written over copies of their bases. The bases are not changed.</summary>
	public static List<byte[]> Apply(IReadOnlyList<byte[]> bases, IReadOnlyList<Entry> entries)
	{
		var result = bases.Select(b => (byte[])b.Clone()).ToList();
		foreach (var e in entries) Array.Copy(e.Data, 0, result[e.Disk], e.Sector * SectorBytes, SectorBytes);
		return result;
	}
}
