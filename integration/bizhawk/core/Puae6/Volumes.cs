#nullable enable

using System.Collections.Generic;
using System.IO;
using System.IO.Compression;
using System.Linq;
using System.Text;

namespace Puae6;

/// <summary>An LHA archive or a ZIP file that is not a usable volume, or a tree that does not fit one. The message names what is wrong with the content, never a path.</summary>
public sealed class VolumeException : Exception
{
	public VolumeException(string message) : base(message) { }
}

/// <summary>One member of a volume: <see cref="Path"/> names in AmigaDOS bytes (ISO 8859-1, one char per byte) joined by '/', no leading or trailing '/'.</summary>
public sealed class VolumeFile
{
	public string Path { get; }
	public bool IsDirectory { get; }
	public byte[] Data { get; }

	public VolumeFile(string path, bool isDirectory, byte[]? data = null)
	{
		Path = path;
		IsDirectory = isDirectory;
		Data = data ?? [ ];
	}
}

/// <summary>
/// Volumes (T64b): an LHA or a ZIP of files the loader hands to the core becomes ONE FFS hard file in memory, the shape of a hard file without an RDB (32 sectors, 1 head, 2 reserved blocks, the DosType in the first long of
/// block 0), which the core mounts exactly like an .hdf (<see cref="Puae6Parameters.HardFiles"/>). It is the same algorithm as the libretro adapter (adapters/libretro/{ffs_volume,lha,volume}.h) and as the writer of the offline
/// WHDLoad preparation (tools/whdload/ffs.py, lha.py); the three give the same bytes for the same content (tests/waterbox-media/test_media_volume.py compares them). The host is never mounted: what enters the machine is the image,
/// whose identity is the hash of its bytes, so the same content from an LHA and from a ZIP is one medium (ADR 0011).
///
/// What decides the bytes is the content: the names and the bytes of the files, and the empty directories. Not the name of the archive, a date, a permission, the order of the members. Children and blocks are in the order of the bytes of
/// the names, every date is 1990-01-01 00:00:00, the volume is called PUAE6, the protection bits are 0, the type is DOS\1 (FFS) unless a name has a byte above 127 (DOS\3), and 1 MiB is left free for the guest's writes. Bounds, checked
/// before anything is allocated and never from a size the archive declares: 4096 entries, 48 MiB of files (25 bitmap pages in the root block, about 49 MiB), 64 MiB for the archive, 64 levels of directories, names of 30 bytes.
/// </summary>
public static class Puae6Volumes
{
	public const int MaxEntries = 4096;
	public const int MaxBytes = 48 * 1024 * 1024;
	public const int MaxArchiveBytes = 64 * 1024 * 1024;
	public const int FreeBlocks = 2048;

	const int Block = 512, Reserved = 2, HashSize = 72, Table = 72, BitmapLongs = 127, BitmapPages = 25, MaxName = 30, MaxDepth = 64, MaxNodes = 8192;
	const int MaxBlocks = Reserved + BitmapPages * BitmapLongs * 32;
	const uint Days = 4383;                         // 1990-01-01, in days since 1978-01-01
	const uint THeader = 2, TList = 16, StRoot = 1, StUserDir = 2, StFile = 0xFFFFFFFDu;
	const string Label = "PUAE6";

	/// <summary>Is this an LHA archive? The method field of the first header ("-lh5-"), at bytes 2 to 6.</summary>
	public static bool IsLha(byte[] data) => data.Length >= 24 && data[0] != 0 && data[2] == '-' && data[3] == 'l' && data[6] == '-';

	public static bool IsZip(byte[] data) => data.Length >= 4 && data[0] == 'P' && data[1] == 'K' && (data[2] == 3 && data[3] == 4 || data[2] == 5 && data[3] == 6);

	/// <summary>The parameters with each <see cref="Puae6Parameters.Volumes"/> archive built into a hard file and added after the hard files (the same parameters when there are none).</summary>
	public static Puae6Parameters Resolve(Puae6Parameters parameters)
	{
		if (parameters.Volumes.Count == 0) return parameters;
		var images = parameters.HardFiles.ToList();
		var names = parameters.HardFileNames.ToList();
		while (names.Count < images.Count) names.Add("");
		for (int i = 0; i < parameters.Volumes.Count; ++i)
		{
			var name = i < parameters.VolumeNames.Count && !string.IsNullOrEmpty(parameters.VolumeNames[i]) ? parameters.VolumeNames[i] : $"volume {i + 1}";
			try { images.Add(FromArchive(parameters.Volumes[i])); }
			catch (VolumeException e) { throw new ArgumentException($"{name} cannot be a volume: {e.Message}", nameof(parameters)); }
			names.Add(name);
		}
		return new Puae6Parameters
		{
			GuestDirectory = parameters.GuestDirectory, GuestFileName = parameters.GuestFileName, PinnedGuestSha256 = parameters.PinnedGuestSha256, Firmware = parameters.Firmware,
			SyncSettings = parameters.SyncSettings, Settings = parameters.Settings, Media = parameters.Media, MediaNames = parameters.MediaNames, HardFiles = images, HardFileNames = names,
		};
	}

	/// <summary>The hard file of an LHA archive or of a ZIP file of files. Throws <see cref="VolumeException"/> for anything else, damaged, unsafe or over a limit.</summary>
	public static byte[] FromArchive(byte[] archive) => Build(ReadArchive(archive));

	/// <summary>The validated LHA/ZIP members, without constructing an FFS image.</summary>
	public static List<VolumeFile> ReadArchive(byte[] archive)
	{
		if (archive.Length > MaxArchiveBytes) throw new VolumeException($"the archive is larger than the {MaxArchiveBytes} bytes this core reads as a volume");
		if (IsLha(archive)) return ReadLha(archive, MaxEntries, MaxBytes, MaxBytes).Select(e => new VolumeFile(e.Name, e.IsDirectory, e.Data)).ToList();
		if (IsZip(archive)) return ReadZip(archive);
		throw new VolumeException("it is neither an LHA archive nor a ZIP file");
	}

	public static byte[] FromLha(byte[] archive) => Build(ReadLha(archive, MaxEntries, MaxBytes, MaxBytes).Select(e => new VolumeFile(e.Name, e.IsDirectory, e.Data)).ToList());

	public static byte[] FromZip(byte[] archive) => Build(ReadZip(archive));

	/// <summary>A ZIP that holds files and no disk image: a volume. One with an .adf, .adz or .dms (or a disk in a format this core does not read) is a disk.</summary>
	public static bool IsVolumeZip(byte[] archive) => IsVolumeZip(archive, false);
	public static bool IsVolumeZip(byte[] archive, bool whdLoadAuto)
	{
		ZipEntry[] entries;
		try { entries = ReadZipDirectory(archive, out _); }
		catch (VolumeException) { return false; }
		// Installed packs may contain ADF data. Inspect only the central directory;
		// the bounded archive reader validates/decompresses it after classification.
		if (whdLoadAuto && entries.Any(e => !ZipIgnored(e) && Extension(BaseName(e.Name)) == "slave")) return true;
		bool files = false;
		foreach (var entry in entries)
		{
			if (ZipIgnored(entry)) continue;
			var ext = Extension(BaseName(entry.Name));
			if (ext is "adf" or "adz" or "dms" || UnsupportedDisk(ext)) return false;
			files = true;
		}
		return files;
	}

	static bool UnsupportedDisk(string ext) => ext is "ipf" or "ctr" or "raw" or "fdi" or "scp" or "dsk" or "img" or "ima" or "st" or "7z" or "rar" or "lzx";

	static string BaseName(string name) => name.Substring(name.LastIndexOf('/') + 1);

	static string Extension(string name)
	{
		int dot = name.LastIndexOf('.');
		return dot <= 0 ? "" : name.Substring(dot + 1).ToLowerInvariant();
	}

	// --- the writer -------------------------------------------------------------------------------------------------------------------------------

	sealed class Node
	{
		public string Name = "";
		public bool IsDirectory;
		public int Parent;
		public byte[] Data = [ ];
		public SortedDictionary<string, int> Children = new(StringComparer.Ordinal);
		public Dictionary<string, string> Spellings = new();
		public bool Seen;
		public uint Key, Chain;
		public List<uint> DataBlocks = new(), ExtBlocks = new();
	}

	static byte Upper(byte value, bool international) => (value >= 0x61 && value <= 0x7A) || (international && value >= 0xE0 && value <= 0xFE && value != 0xF7) ? (byte)(value - 0x20) : value;

	static uint NameHash(string name, bool international)
	{
		uint h = (uint)name.Length;
		foreach (var c in name) h = (h * 13 + Upper((byte)c, international)) & 0x7FF;
		return h % HashSize;
	}

	internal static string Folded(string name)
	{
		var chars = name.ToCharArray();
		for (int i = 0; i < chars.Length; ++i) chars[i] = (char)Upper((byte)chars[i], true);
		return new string(chars);
	}

	static bool ValidName(string name) => name.Length is >= 1 and <= MaxName && name.IndexOf(':') < 0 && name.IndexOf('/') < 0 && name.IndexOf('\0') < 0;

	static ulong DataBlockCount(long bytes) => (ulong)((bytes + Block - 1) / Block);

	static ulong ExtensionCount(ulong dataBlocks) => dataBlocks > Table ? (dataBlocks - Table + Table - 1) / Table : 0;

	// The checksum long of a block built in `longs` (128 of them): the sum of all the longs is zero. The block is written big endian at `offset`.
	static void Seal(uint[] longs, int checksumIndex, byte[] image, long offset)
	{
		longs[checksumIndex] = 0;
		uint sum = 0;
		foreach (var value in longs) sum += value;
		longs[checksumIndex] = 0u - sum;
		for (int i = 0; i < 128; ++i)
		{
			long at = offset + i * 4L;
			image[at] = (byte)(longs[i] >> 24);
			image[at + 1] = (byte)(longs[i] >> 16);
			image[at + 2] = (byte)(longs[i] >> 8);
			image[at + 3] = (byte)longs[i];
		}
	}

	static void PutBString(uint[] longs, int at, string name)
	{
		var raw = new byte[32];
		raw[0] = (byte)name.Length;
		for (int i = 0; i < name.Length; ++i) raw[1 + i] = (byte)name[i];
		for (int i = 0; i < 8; ++i) longs[at + i] = (uint)(raw[i * 4] << 24 | raw[i * 4 + 1] << 16 | raw[i * 4 + 2] << 8 | raw[i * 4 + 3]);
	}

	/// <summary>The FFS volume of a tree: the same bytes as ffs_volume.h's build_ffs_volume and tools/whdload/ffs.py for the same tree, 1 MiB free.</summary>
	public static byte[] Build(IReadOnlyList<VolumeFile> files) => Build(files, Label, FreeBlocks);

	/// <summary>The same writer, with the label and free blocks of one WHDLoad role.</summary>
	public static byte[] Build(IReadOnlyList<VolumeFile> files, string label, int freeBlocks)
	{
		if (!ValidName(label) || label.Any(c => c > 255 || c < 32 || c == 127)) throw new VolumeException("invalid volume label");
		if (freeBlocks < 0 || freeBlocks > MaxBlocks) throw new VolumeException("invalid free block count");
		var nodes = new List<Node> { new() { IsDirectory = true } };
		bool high = false;
		foreach (var file in files)
		{
			if (file.Path.Length == 0) throw new VolumeException("an entry without a name");
			int at = 0, depth = 0, current = 0;
			while (true)
			{
				int slash = file.Path.IndexOf('/', at);
				bool last = slash < 0;
				var part = last ? file.Path.Substring(at) : file.Path.Substring(at, slash - at);
				if (!ValidName(part)) throw new VolumeException($"the name \"{part}\" (in {file.Path}) cannot be an AmigaDOS name (1 to 30 bytes, no ':' or '/')");
				if (++depth > MaxDepth) throw new VolumeException($"{file.Path} is nested more than {MaxDepth} directories deep");
				foreach (var c in part) high |= c > 127;
				var key = Folded(part);
				if (nodes[current].Spellings.TryGetValue(key, out var twin) && twin != part) throw new VolumeException($"\"{twin}\" and \"{part}\" differ only in case");
				int child;
				if (!nodes[current].Children.TryGetValue(part, out child))
				{
					if (nodes.Count >= MaxNodes) throw new VolumeException($"the volume has more than {MaxNodes} files and directories");
					child = nodes.Count;
					nodes.Add(new Node { Name = part, Parent = current, IsDirectory = !last || file.IsDirectory });
					nodes[current].Children[part] = child;
					nodes[current].Spellings[key] = part;
				}
				if (last)
				{
					if (nodes[child].IsDirectory != file.IsDirectory) throw new VolumeException($"{file.Path} is both a file and a directory");
					if (!file.IsDirectory)
					{
						if (nodes[child].Seen) throw new VolumeException($"{file.Path} appears twice");
						nodes[child].Data = file.Data;
						nodes[child].Seen = true;
					}
					break;
				}
				if (!nodes[child].IsDirectory) throw new VolumeException($"{file.Path.Substring(0, slash)} is both a file and a directory");
				current = child;
				at = slash + 1;
			}
		}

		// The order of the walk: every node after its parent, the children by the bytes of their names.
		var order = new List<int>();
		var stack = new Stack<int>();
		foreach (var child in nodes[0].Children.Values.Reverse()) stack.Push(child);
		while (stack.Count > 0)
		{
			int n = stack.Pop();
			order.Add(n);
			foreach (var child in nodes[n].Children.Values.Reverse()) stack.Push(child);
		}
		ulong needed = Reserved + 1UL + (ulong)freeBlocks;
		foreach (int n in order)
		{
			needed += 1;
			if (!nodes[n].IsDirectory)
			{
				var data = DataBlockCount(nodes[n].Data.Length);
				needed += data + ExtensionCount(data);
			}
			if (needed > MaxBlocks) break;
		}
		if (needed > MaxBlocks) throw new VolumeException($"the tree does not fit in a volume of {BitmapPages} bitmap pages (about 49 MiB)");
		ulong pages = 1, wanted, blocks64;
		while (true)
		{
			blocks64 = Math.Max(needed + pages, 64UL);
			blocks64 = (blocks64 + 31) / 32 * 32;
			wanted = (blocks64 - Reserved + BitmapLongs * 32 - 1) / (BitmapLongs * 32);
			if (wanted <= pages) break;
			pages = wanted;
		}
		if (wanted > BitmapPages) throw new VolumeException($"a volume of {blocks64} blocks needs more than {BitmapPages} bitmap pages");
		uint blocks = (uint)blocks64;
		pages = (blocks - (ulong)Reserved + BitmapLongs * 32 - 1) / (BitmapLongs * 32);
		uint rootBlock = (blocks - 1 + Reserved) / 2;
		bool international = high;

		var taken = new bool[blocks];
		for (uint b = rootBlock; b < rootBlock + 1 + pages; ++b) taken[b] = true;
		uint cursor = Reserved;
		uint Allocate()
		{
			while (cursor < blocks && taken[cursor]) ++cursor;
			uint block = cursor;                    // the count made sure there is one
			taken[block] = true;
			return block;
		}
		foreach (int n in order)
		{
			var node = nodes[n];
			node.Key = Allocate();
			if (node.IsDirectory) continue;
			ulong count = DataBlockCount(node.Data.Length);
			for (ulong i = 0; i < count; ++i) node.DataBlocks.Add(Allocate());
			ulong extensions = ExtensionCount(count);
			for (ulong i = 0; i < extensions; ++i) node.ExtBlocks.Add(Allocate());
		}

		var image = new byte[(long)blocks * Block];
		image[0] = (byte)'D'; image[1] = (byte)'O'; image[2] = (byte)'S'; image[3] = (byte)(international ? 3 : 1);

		// The hash chains: the children of a directory in the order of their names, the last one named of a slot first.
		uint[] HashTable(int directory)
		{
			var table = new uint[HashSize];
			foreach (var childIndex in nodes[directory].Children.Values)
			{
				var child = nodes[childIndex];
				uint h = NameHash(child.Name, international);
				child.Chain = table[h];
				table[h] = child.Key;
			}
			return table;
		}
		var rootTable = HashTable(0);               // the chains of the top-level names are set before the nodes are written
		long OffsetOf(uint block) => (long)block * Block;
		void HeaderCommon(uint[] longs, Node node, uint parentKey, uint kind)
		{
			longs[0] = THeader;
			longs[1] = node.Key;
			longs[105] = Days;
			PutBString(longs, 108, node.Name);
			longs[124] = node.Chain;
			longs[125] = parentKey;
			longs[127] = kind;
		}
		foreach (int n in order)
		{
			var node = nodes[n];
			uint parentKey = node.Parent == 0 ? rootBlock : nodes[node.Parent].Key;
			var longs = new uint[128];
			if (node.IsDirectory)
			{
				HashTable(n).CopyTo(longs, 6);
				HeaderCommon(longs, node, parentKey, StUserDir);
				Seal(longs, 5, image, OffsetOf(node.Key));
				continue;
			}
			int listed = Math.Min(node.DataBlocks.Count, Table);
			longs[2] = (uint)listed;
			longs[4] = node.DataBlocks.Count == 0 ? 0 : node.DataBlocks[0];
			for (int i = 0; i < listed; ++i) longs[77 - i] = node.DataBlocks[i];
			longs[81] = (uint)node.Data.Length;
			longs[126] = node.ExtBlocks.Count == 0 ? 0 : node.ExtBlocks[0];
			HeaderCommon(longs, node, parentKey, StFile);
			Seal(longs, 5, image, OffsetOf(node.Key));
			for (int e = 0; e < node.ExtBlocks.Count; ++e)
			{
				var ext = new uint[128];
				int from = Table * (e + 1), count = Math.Min(node.DataBlocks.Count - from, Table);
				ext[0] = TList;
				ext[1] = node.ExtBlocks[e];
				ext[2] = (uint)count;
				for (int i = 0; i < count; ++i) ext[77 - i] = node.DataBlocks[from + i];
				ext[125] = node.Key;
				ext[126] = e + 1 < node.ExtBlocks.Count ? node.ExtBlocks[e + 1] : 0;
				ext[127] = StFile;
				Seal(ext, 5, image, OffsetOf(node.ExtBlocks[e]));
			}
			for (int i = 0; i < node.DataBlocks.Count; ++i)
			{
				int offset = i * Block, length = Math.Min(Block, node.Data.Length - offset);
				Buffer.BlockCopy(node.Data, offset, image, (int)OffsetOf(node.DataBlocks[i]), length);
			}
			node.Data = [ ];
		}

		var root = new uint[128];
		root[0] = THeader;
		root[3] = HashSize;
		rootTable.CopyTo(root, 6);
		root[78] = 0xFFFFFFFFu;
		for (uint page = 0; page < pages; ++page) root[79 + page] = rootBlock + 1 + page;
		foreach (int baseIndex in new[] { 105, 118, 121 }) root[baseIndex] = Days;
		PutBString(root, 108, label);
		root[127] = StRoot;
		Seal(root, 5, image, OffsetOf(rootBlock));
		for (uint page = 0; page < pages; ++page)
		{
			var longs = new uint[128];
			for (uint w = 0; w < BitmapLongs; ++w)
			{
				uint word = 0;
				for (uint bit = 0; bit < 32; ++bit)
				{
					ulong number = (ulong)Reserved + ((ulong)page * BitmapLongs + w) * 32 + bit;
					if (number < blocks && !taken[number]) word |= 1u << (int)bit;
				}
				longs[1 + w] = word;
			}
			Seal(longs, 0, image, OffsetOf(rootBlock + 1 + page));
		}
		return image;
	}

	// --- LHA --------------------------------------------------------------------------------------------------------------------------------------

	/// <summary>A member of an LHA archive: the relative path (ISO 8859-1 chars), whether it is a directory entry, its bytes.</summary>
	public sealed class LhaMember
	{
		public string Name = "";
		public bool IsDirectory;
		public byte[] Data = [ ];
	}

	const int KThreshold = 3, KNC = 510, KNT = 19, KTBit = 5, KCBit = 9, KMaxNamePart = 107;

	static readonly ushort[] Crc16Table = MakeCrc16Table();

	static ushort[] MakeCrc16Table()
	{
		var table = new ushort[256];
		for (uint n = 0; n < 256; ++n)
		{
			uint c = n;
			for (int k = 0; k < 8; ++k) c = (c & 1) != 0 ? (c >> 1) ^ 0xA001u : c >> 1;
			table[n] = (ushort)c;
		}
		return table;
	}

	static ushort Crc16(byte[] data)
	{
		uint crc = 0;
		foreach (var b in data) crc = (crc >> 8) ^ Crc16Table[(crc ^ b) & 0xFF];
		return (ushort)crc;
	}

	// MSB-first bit reader; past the end it yields zero bits and counts the bytes it invented.
	sealed class LhaBits
	{
		readonly byte[] _data;
		readonly int _offset, _size;
		int _pos;
		ulong _buf;
		int _count;

		public int Over { get; private set; }

		public LhaBits(byte[] data, int offset, int size)
		{
			_data = data;
			_offset = offset;
			_size = size;
		}

		public uint Peek(int n)
		{
			while (_count < n)
			{
				if (_pos < _size) _buf = ((_buf << 8) | _data[_offset + _pos++]) & 0xFFFFFFFFFFFFUL;
				else { _buf = (_buf << 8) & 0xFFFFFFFFFFFFUL; ++Over; }
				_count += 8;
			}
			return (uint)((_buf >> (_count - n)) & ((1UL << n) - 1));
		}

		public void Skip(int n)
		{
			Peek(n);
			_count -= n;
		}

		public uint Bits(int n)
		{
			if (n == 0) return 0;
			uint value = Peek(n);
			_count -= n;
			return value;
		}

		public void Drop(int n) => _count -= n;
	}

	// Canonical code: the codes of one length are consecutive and follow the symbol order. A constant is the tree of one symbol that costs no bits.
	sealed class LhaHuffman
	{
		bool _constant;
		uint _constantSymbol;
		int _longest;
		int[] _table = [ ];

		public void Build(uint[] lengths, bool hasConstant, uint constant)
		{
			_constant = hasConstant;
			_constantSymbol = constant;
			_longest = 0;
			_table = [ ];
			if (hasConstant) return;
			foreach (var length in lengths) _longest = Math.Max(_longest, (int)length);
			if (_longest > 16) throw new VolumeException("code length over 16");
			if (_longest == 0) return;
			_table = new int[1 << _longest];
			for (int i = 0; i < _table.Length; ++i) _table[i] = -1;
			var counts = new uint[_longest + 1];
			foreach (var length in lengths) if (length != 0) ++counts[length];
			var symbols = new List<uint>();
			for (int length = 1; length <= _longest; ++length)
				for (int i = 0; i < lengths.Length; ++i) if (lengths[i] == length) symbols.Add((uint)i);
			ulong code = 0;
			int index = 0;
			for (int length = 1; length <= _longest; ++length)
			{
				code = length > 1 ? (code + counts[length - 1]) << 1 : 0;
				if (code + counts[length] > (1UL << length)) throw new VolumeException("over-subscribed Huffman code");
				int span = 1 << (_longest - length);
				for (uint k = 0; k < counts[length]; ++k, ++index)
				{
					int start = (int)(code + k) * span;
					for (int j = 0; j < span; ++j) _table[start + j] = (int)((symbols[index] << 5) | (uint)length);
				}
			}
		}

		public uint Read(LhaBits bits)
		{
			if (_constant) return _constantSymbol;
			if (_longest == 0) throw new VolumeException("Huffman table without codes");
			int entry = _table[bits.Peek(_longest)];
			if (entry < 0) throw new VolumeException("invalid Huffman code");
			bits.Drop(entry & 31);
			return (uint)entry >> 5;
		}
	}

	// Code lengths of a small alphabet; `special` is the index after which a run of zeros may be coded.
	static uint[] LengthTable(LhaBits bits, int size, int nbit, int special, out bool hasConstant, out uint constant)
	{
		uint n = bits.Bits(nbit);
		var lengths = new uint[size];
		hasConstant = false;
		constant = 0;
		if (n == 0)
		{
			hasConstant = true;
			constant = bits.Bits(nbit);
			return lengths;
		}
		if (n > size) throw new VolumeException("length table too long");
		int i = 0;
		while (i < n)
		{
			uint c = bits.Peek(3);
			if (c == 7)
			{
				uint window = bits.Peek(16);
				uint mask = 0x1000;
				while ((window & mask) != 0)
				{
					mask >>= 1;
					++c;
					if (mask == 0) throw new VolumeException("length code too long");
				}
				bits.Skip((int)c - 3);
			}
			else
			{
				bits.Skip(3);
			}
			lengths[i++] = c;
			if (i == special)
			{
				uint run = bits.Bits(2);
				if (i + run > size) throw new VolumeException("length table overflow");
				i += (int)run;
			}
		}
		return lengths;
	}

	// The outSize bytes that the LH5/6/7 stream expands to.
	static byte[] LhaDecode(byte[] data, int offset, int size, int methodBits, int outSize)
	{
		int positionSymbols = methodBits + 1, positionBits = methodBits == 13 ? 4 : 5;
		long window = 1L << methodBits;
		var bits = new LhaBits(data, offset, size);
		var output = new List<byte>(Math.Min(outSize, size * 8 + 1024));         // never from the size the header claims: the output grows with what is really decoded
		uint block = 0;
		var literal = new LhaHuffman();
		var position = new LhaHuffman();
		var meta = new LhaHuffman();
		while (output.Count < outSize)
		{
			if (block == 0)
			{
				block = bits.Bits(16);
				if (block == 0) throw new VolumeException("empty block");
				meta.Build(LengthTable(bits, KNT, KTBit, 3, out var hasConstant, out var constant), hasConstant, constant);
				uint n = bits.Bits(KCBit);
				if (n == 0)
				{
					literal.Build(new uint[KNC], true, bits.Bits(KCBit));
				}
				else
				{
					if (n > KNC) throw new VolumeException("too many literal lengths");
					var literalLengths = new uint[KNC];
					uint i = 0;
					while (i < n)
					{
						uint c = meta.Read(bits);
						if (c <= 2)
						{
							uint run = c == 0 ? 1 : c == 1 ? bits.Bits(4) + 3 : bits.Bits(KCBit) + 20;
							if (i + run > KNC) throw new VolumeException("literal length run overflow");
							i += run;
						}
						else
						{
							literalLengths[i++] = c - 2;
						}
					}
					literal.Build(literalLengths, false, 0);
				}
				position.Build(LengthTable(bits, positionSymbols, positionBits, -1, out hasConstant, out constant), hasConstant, constant);
			}
			--block;
			uint symbol = literal.Read(bits);
			if (symbol < 256)
			{
				output.Add((byte)symbol);
			}
			else
			{
				int length = (int)symbol - 256 + KThreshold;
				uint j = position.Read(bits);
				long distance = (j < 2 ? j : (1L << (int)(j - 1)) + bits.Bits((int)j - 1)) + 1;
				if (distance > output.Count || distance > window) throw new VolumeException("match points before the start of the data");
				if (output.Count + length > outSize) throw new VolumeException("data longer than the header says");
				int from = output.Count - (int)distance;
				for (int k = 0; k < length; ++k) output.Add(output[from + k]);
			}
			if (bits.Over > 8) throw new VolumeException("compressed data ends early");
		}
		return output.ToArray();
	}

	static string Latin1(byte[] data, int offset, int length)
	{
		var chars = new char[length];
		for (int i = 0; i < length; ++i) chars[i] = (char)data[offset + i];
		return new string(chars);
	}

	// The header name with 0xFF, '\' and '/' as separators, split into parts.
	static List<string> PartsOf(string raw)
	{
		var parts = new List<string> { "" };
		foreach (var c in raw)
		{
			if (c == 'ÿ' || c == '\\' || c == '/') parts.Add("");
			else parts[parts.Count - 1] += c;
		}
		return parts;
	}

	static string MemberName(string directoryRaw, string nameRaw, bool isDirectory)
	{
		var parts = new List<string>();
		if (directoryRaw.Length != 0) parts = PartsOf(directoryRaw);
		if (parts.Count > 0 && parts[parts.Count - 1].Length == 0) parts.RemoveAt(parts.Count - 1);       // the directory header of a member ends with a separator
		if (nameRaw.Length != 0) parts.AddRange(PartsOf(nameRaw));
		if (isDirectory) parts = parts.Where(p => p.Length != 0).ToList();
		var joined = new StringBuilder();
		foreach (var part in parts)
		{
			bool unsafeName = part.Length == 0 || part == "." || part == ".." || part.Length > KMaxNamePart || part.IndexOf(':') >= 0 || part.Any(c => c < 32);
			if (unsafeName) throw new VolumeException("unsafe member name");
			if (joined.Length != 0) joined.Append('/');
			joined.Append(part);
		}
		if (parts.Count == 0) throw new VolumeException("member without a name");
		return joined.ToString();
	}

	// Walks the chain of extended headers that starts at the 2-byte size at `cursor`: the file name and directory name, the bytes used after the first size, and the end.
	static void Extended(byte[] blob, int cursor, int limit, out string name, out string directory, out int used, out int end)
	{
		name = directory = "";
		used = 0;
		if (cursor + 2 > blob.Length || cursor + 2 > limit) throw new VolumeException("bad extended header");
		int size = blob[cursor] | blob[cursor + 1] << 8;
		cursor += 2;
		while (size != 0)
		{
			if (size < 3 || cursor + size > limit || cursor + size > blob.Length) throw new VolumeException("bad extended header");
			byte kind = blob[cursor];
			var body = Latin1(blob, cursor + 1, size - 3);
			if (kind == 0x01) name = body;
			else if (kind == 0x02) directory = body;
			used += size;
			cursor += size - 2;
			if (cursor + 2 > blob.Length || cursor + 2 > limit) throw new VolumeException("bad extended header");
			size = blob[cursor] | blob[cursor + 1] << 8;
			cursor += 2;
		}
		end = cursor;
	}

	static uint Le32(byte[] data, int at) => (uint)(data[at] | data[at + 1] << 8 | data[at + 2] << 16 | data[at + 3] << 24);

	/// <summary>The members of an LHA archive in archive order (headers of level 0, 1 and 2; -lh0-, -lh5-, -lh6-, -lh7-, -lhd-). Throws <see cref="VolumeException"/> for anything unsafe, damaged, unsupported or over a limit.</summary>
	public static List<LhaMember> ReadLha(byte[] blob, int maxEntries, int maxFile, int maxTotal)
	{
		var entries = new List<LhaMember>();
		int pos = 0;
		long total = 0;
		while (pos < blob.Length && blob[pos] != 0)
		{
			if (entries.Count >= maxEntries) throw new VolumeException("too many entries");
			if (blob.Length - pos < 24) throw new VolumeException("truncated header");
			int level = blob[pos + 20];
			var method = Latin1(blob, pos + 2, 5);
			string nameRaw = "", directoryRaw = "";
			int skip = 0, headerLength;
			ushort fileCrc;
			if (level == 0 || level == 1)
			{
				headerLength = blob[pos] + 2;
				if (headerLength < 24 || headerLength > blob.Length - pos) throw new VolumeException("truncated header");
				int sum = 0;
				for (int i = 2; i < headerLength; ++i) sum += blob[pos + i];
				if ((sum & 0xFF) != blob[pos + 1]) throw new VolumeException("header checksum");
				int nameLength = blob[pos + 21], tail = pos + 22 + nameLength;
				if (tail + (level == 0 ? 2 : 5) > pos + headerLength) throw new VolumeException("name beyond the header");
				nameRaw = Latin1(blob, pos + 22, nameLength);
				fileCrc = (ushort)(blob[tail] | blob[tail + 1] << 8);
				if (level == 1)
				{
					Extended(blob, tail + 3, blob.Length, out var extName, out var extDirectory, out skip, out _);
					if (extName.Length != 0) nameRaw = extName;
					directoryRaw = extDirectory;
				}
			}
			else if (level == 2)
			{
				headerLength = blob[pos] | blob[pos + 1] << 8;
				if (headerLength < 26 || headerLength > blob.Length - pos) throw new VolumeException("truncated header");
				fileCrc = (ushort)(blob[pos + 21] | blob[pos + 22] << 8);
				Extended(blob, pos + 24, pos + headerLength, out nameRaw, out directoryRaw, out _, out int end);
				if (end > pos + headerLength) throw new VolumeException("extended headers beyond the header");
			}
			else
			{
				throw new VolumeException($"header level {level} is not supported");
			}
			long packed = Le32(blob, pos + 7);
			long original = Le32(blob, pos + 11);
			if (skip > packed) throw new VolumeException("extended headers longer than the packed size");
			packed -= skip;
			long dataAt = (long)pos + headerLength + skip;
			bool isDirectory = method == "-lhd-";
			int methodBits = method == "-lh5-" ? 13 : method == "-lh6-" ? 15 : method == "-lh7-" ? 16 : 0;
			if (!isDirectory && method != "-lh0-" && methodBits == 0) throw new VolumeException($"method {method} is not supported");
			if (dataAt > blob.Length || packed > blob.Length - dataAt) throw new VolumeException("packed data beyond the end of the archive");
			if (original > maxFile) throw new VolumeException("member larger than the limit");
			total += original;
			if (total > maxTotal) throw new VolumeException("archive larger than the limit");
			var entry = new LhaMember { Name = MemberName(directoryRaw, nameRaw, isDirectory), IsDirectory = isDirectory };
			if (!isDirectory)
			{
				if (method == "-lh0-")
				{
					if (packed != original) throw new VolumeException("stored member with different sizes");
					entry.Data = new byte[packed];
					Buffer.BlockCopy(blob, (int)dataAt, entry.Data, 0, (int)packed);
				}
				else
				{
					try { entry.Data = LhaDecode(blob, (int)dataAt, (int)packed, methodBits, (int)original); }
					catch (VolumeException e) { throw new VolumeException($"{e.Message} ({entry.Name})"); }
				}
				if (Crc16(entry.Data) != fileCrc) throw new VolumeException($"CRC of {entry.Name}");
			}
			entries.Add(entry);
			pos = (int)(dataAt + packed);
		}
		return entries;
	}

	// --- ZIP ----------------------------------------------------------------------------------------------------------------------------------------

	sealed class ZipEntry
	{
		public byte[] RawName = [ ];
		public string Name = "";
		public int Method, Flags;
		public uint Crc, Compressed, Uncompressed, Offset;
	}

	static ushort Le16(byte[] data, int at) => (ushort)(data[at] | data[at + 1] << 8);

	static bool ZipIgnored(ZipEntry entry)
	{
		var name = entry.Name;
		var bare = BaseName(name);
		return bare.Length == 0 || name.StartsWith("__MACOSX/", StringComparison.Ordinal) || bare.StartsWith("._", StringComparison.Ordinal);
	}

	// The central directory of a ZIP file, which is the authority (sizes in a local header may be zero when a data descriptor follows). Every entry is listed.
	static ZipEntry[] ReadZipDirectory(byte[] file, out int directoryOffset)
	{
		int size = file.Length;
		if (size < 22) throw new VolumeException("the ZIP file is truncated");
		// The end-of-central-directory record is within 22 + 65535 bytes of the end (its comment is the only variable part).
		int eocd = size;
		int floor = size > 22 + 65535 ? size - 22 - 65535 : 0;
		for (int at = size - 22; at >= floor; --at)
			if (file[at] == 'P' && file[at + 1] == 'K' && file[at + 2] == 5 && file[at + 3] == 6 && at + 22 + Le16(file, at + 20) <= size) { eocd = at; break; }
		if (eocd == size) throw new VolumeException("the ZIP file is truncated or damaged (no central directory)");
		if (Le16(file, eocd + 4) != 0 || Le16(file, eocd + 6) != 0) throw new VolumeException("multi-volume ZIP files are not supported");
		int count = Le16(file, eocd + 10);
		uint directorySize = Le32(file, eocd + 12), offset = Le32(file, eocd + 16);
		if (count == 0xFFFF || directorySize == 0xFFFFFFFFu || offset == 0xFFFFFFFFu) throw new VolumeException("ZIP64 files are not supported");
		if (offset > eocd || directorySize > eocd - offset) throw new VolumeException("the ZIP file is damaged (central directory out of range)");
		if (count == 0) throw new VolumeException("the ZIP file is empty");
		directoryOffset = (int)offset;
		var entries = new ZipEntry[count];
		long pos = offset, directoryEnd = (long)offset + directorySize;
		for (int i = 0; i < count; ++i)
		{
			if (directoryEnd - pos < 46 || file[pos] != 'P' || file[pos + 1] != 'K' || file[pos + 2] != 1 || file[pos + 3] != 2) throw new VolumeException("the ZIP file is damaged (bad central directory entry)");
			int e = (int)pos;
			int nameLength = Le16(file, e + 28), extraLength = Le16(file, e + 30), commentLength = Le16(file, e + 32);
			if (directoryEnd - pos - 46 < nameLength + extraLength + commentLength) throw new VolumeException("the ZIP file is damaged (entry out of range)");
			var raw = new byte[nameLength];
			Buffer.BlockCopy(file, e + 46, raw, 0, nameLength);
			entries[i] = new ZipEntry
			{
				RawName = raw, Flags = Le16(file, e + 8), Method = Le16(file, e + 10), Crc = Le32(file, e + 16), Compressed = Le32(file, e + 20), Uncompressed = Le32(file, e + 24), Offset = Le32(file, e + 42),
			};
			entries[i].Name = Latin1(raw, 0, raw.Length);
			pos += 46 + nameLength + extraLength + commentLength;
		}
		return entries;
	}

	static readonly uint[] Crc32Table = MakeCrc32Table();

	static uint[] MakeCrc32Table()
	{
		var table = new uint[256];
		for (uint n = 0; n < 256; ++n)
		{
			uint c = n;
			for (int k = 0; k < 8; ++k) c = (c & 1) != 0 ? 0xEDB88320u ^ (c >> 1) : c >> 1;
			table[n] = c;
		}
		return table;
	}

	internal static uint Crc32(byte[] data)
	{
		uint crc = 0xFFFFFFFFu;
		foreach (var b in data) crc = Crc32Table[(crc ^ b) & 0xFF] ^ (crc >> 8);
		return ~crc;
	}

	// The bytes of one entry, checked (encryption, method, size against `limit`, CRC).
	static byte[] ExtractZipEntry(byte[] file, int directoryOffset, ZipEntry entry, long limit)
	{
		if ((entry.Flags & 1) != 0) throw new VolumeException("the ZIP entry is encrypted");
		if (entry.Method != 0 && entry.Method != 8) throw new VolumeException($"the ZIP entry uses compression method {entry.Method} (only stored and deflate are read)");
		if (entry.Uncompressed > limit) throw new VolumeException("the ZIP entry is larger than any file a volume takes");
		if (entry.Offset > file.Length || file.Length - entry.Offset < 30 || file[entry.Offset] != 'P' || file[entry.Offset + 1] != 'K' || file[entry.Offset + 2] != 3 || file[entry.Offset + 3] != 4)
			throw new VolumeException("the ZIP file is damaged (bad local header)");
		long data = (long)entry.Offset + 30 + Le16(file, (int)entry.Offset + 26) + Le16(file, (int)entry.Offset + 28);
		if (data > directoryOffset || entry.Compressed > directoryOffset - data) throw new VolumeException("the ZIP file is truncated (entry data out of range)");
		byte[] result;
		if (entry.Method == 0)
		{
			if (entry.Compressed != entry.Uncompressed) throw new VolumeException("the ZIP entry is damaged (stored sizes differ)");
			result = new byte[entry.Compressed];
			Buffer.BlockCopy(file, (int)data, result, 0, (int)entry.Compressed);
		}
		else
		{
			// Inflate into a buffer that grows with what comes out: one byte more than declared is read, to know that the stream says more than the directory.
			using var source = new MemoryStream(file, (int)data, (int)entry.Compressed, false);
			using var inflater = new DeflateStream(source, CompressionMode.Decompress);
			using var output = new MemoryStream();
			var chunk = new byte[16384];
			long wanted = (long)entry.Uncompressed + 1;
			try
			{
				while (output.Length < wanted)
				{
					int got = inflater.Read(chunk, 0, (int)Math.Min(chunk.Length, wanted - output.Length));
					if (got <= 0) break;
					output.Write(chunk, 0, got);
				}
			}
			catch (InvalidDataException) { throw new VolumeException("the ZIP entry is corrupt"); }
			if (output.Length > entry.Uncompressed) throw new VolumeException("the ZIP entry decompresses to more than it declares");
			if (output.Length < entry.Uncompressed) throw new VolumeException("the ZIP entry is damaged (it decompresses to less than it declares)");
			result = output.ToArray();
		}
		if (Crc32(result) != entry.Crc) throw new VolumeException("the ZIP entry is corrupt (CRC mismatch)");
		return result;
	}

	// The bytes of an ISO 8859-1 name from the UTF-8 of a zip entry. False for bytes that are not UTF-8 and for any character above U+00FF.
	static bool Latin1FromUtf8(string bytesAsChars, out string name)
	{
		var sb = new StringBuilder();
		name = "";
		for (int i = 0; i < bytesAsChars.Length; ++i)
		{
			int c = bytesAsChars[i];
			if (c < 0x80) sb.Append((char)c);
			else if ((c & 0xE0) == 0xC0 && i + 1 < bytesAsChars.Length && (bytesAsChars[i + 1] & 0xC0) == 0x80)
			{
				int code = ((c & 0x1F) << 6) | (bytesAsChars[i + 1] & 0x3F);
				if (code < 0x80 || code > 0xFF) return false;
				sb.Append((char)code);
				++i;
			}
			else return false;
		}
		name = sb.ToString();
		return true;
	}

	static bool IgnoredName(string name) => name == ".DS_Store" || name == "__MACOSX" || name.StartsWith("._", StringComparison.Ordinal);

	static List<VolumeFile> ReadZip(byte[] archive)
	{
		var entries = ReadZipDirectory(archive, out int directoryOffset);
		long declared = 0;
		var items = new List<(ZipEntry Entry, string Path, bool IsDirectory)>();
		foreach (var entry in entries)
		{
			var name = entry.Name;
			bool isDirectory = name.Length != 0 && name[name.Length - 1] == '/';
			if (isDirectory) name = name.Substring(0, name.Length - 1);
			if ((entry.Flags & 0x800) != 0)
			{
				if (!Latin1FromUtf8(name, out var converted)) throw new VolumeException($"the name \"{name}\" in the ZIP file cannot be an AmigaDOS name: it has a character outside ISO 8859-1");
				name = converted;
			}
			bool skip = false;
			int at = 0;
			while (true)
			{
				int slash = name.IndexOf('/', at);
				var part = slash < 0 ? name.Substring(at) : name.Substring(at, slash - at);
				if (part.Length == 0 || part == "." || part == ".." || part.Any(c => c < 32 || c == 127))
					throw new VolumeException($"the ZIP file has an unsafe name (\"{name}\"): empty, \".\", \"..\" or control characters in a component");
				skip |= IgnoredName(part);
				if (slash < 0) break;
				at = slash + 1;
			}
			if (skip) continue;
			if (items.Count >= MaxEntries) throw new VolumeException($"the ZIP file holds more than {MaxEntries} files and directories");
			if (!isDirectory)
			{
				declared += entry.Uncompressed;
				if (declared > MaxBytes) throw new VolumeException($"the files of the ZIP file together are larger than the {MaxBytes >> 20} MiB a volume takes");
			}
			items.Add((entry, name, isDirectory));
		}
		var files = new List<VolumeFile>();
		foreach (var item in items)
		{
			byte[] data = [ ];
			if (!item.IsDirectory)
			{
				try { data = ExtractZipEntry(archive, directoryOffset, item.Entry, MaxBytes); }
				catch (VolumeException e) { throw new VolumeException($"\"{item.Entry.Name}\": {e.Message}"); }
			}
			files.Add(new VolumeFile(item.Path, item.IsDirectory, data));
		}
		if (files.Count == 0) throw new VolumeException("the ZIP file holds no files");
		return files;
	}
}
