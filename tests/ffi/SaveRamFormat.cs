using Puae6;

/// <summary>
/// T63b: the format of the SaveRam (SaveRam.cs) with floppy disks and hard files, on in-memory images and without a guest: what a core writes with only floppies is the version 1 it always wrote, a hard
/// file brings version 2 with the 4 KiB chunks that differ (the last chunk of an image that is not a multiple of 4096 is short), a chunk written with the bytes it had is not in it, and whatever
/// is cut, flipped, reordered or for other images is refused with a SaveRamRejectedException (never another exception) before anything is applied.
///   Ffi.dll saveramformat
/// </summary>
internal static unsafe partial class Program
{
	static byte[] Pattern(int length, int seed)
	{
		var bytes = new byte[length];
		new Random(seed).NextBytes(bytes);
		return bytes;
	}

	static SaveRamRejection? Refusal(Action action)
	{
		try { action(); return null; }
		catch (SaveRamRejectedException e) { return e.Reason; }
	}

	static void SaveRamFormat()
	{
		var floppyA = Pattern(P6.AdfBytes, 1);
		var floppyB = Pattern(P6.AdfBytes, 2);
		var bases = new List<byte[]> { floppyA, floppyB };
		var hardA = Pattern(10000, 3);                        // 3 chunks, the last one 1808 bytes
		var hardB = Pattern(3 * 4096, 4);
		var hardBases = new List<byte[]> { hardA, hardB };

		// floppies only: the version 1 of T28, byte for byte what Build made before hard files existed
		var changedA = (byte[])floppyA.Clone();
		changedA[512 * 5] ^= 1;
		changedA[512 * 9 + 100] ^= 1;
		var v1 = SaveRam.Build(bases, [changedA, null], hardBases, [null, null])!;
		Require(BitConverter.ToUInt32(v1, 4) == 1 && v1.Length == 12 + 40 + 2 * 514 + 32, "floppy-only SaveRam is version 1 and 12 + 40 + 514 n + 32 bytes");
		Require(v1.SequenceEqual(SaveRam.Build(bases, [changedA, null])!), "the two overloads of Build agree");
		var parsed = SaveRam.Parse(v1, bases, hardBases);
		Require(parsed.Floppy.Count == 2 && parsed.Hard.Count == 0 && parsed.Length == v1.Length, "version 1 reads back its two sectors and no chunk");
		Require(SaveRam.Build(bases, [null, null], hardBases, [null, null]) is null, "nothing changed gives null");
		Require(SaveRam.Build(bases, [floppyA, null], hardBases, [hardA, null]) is null, "an image equal to its base gives null");

		// a hard file: the chunks that differ, the short last chunk, none for a chunk written with the bytes it had
		var writtenA = (byte[])hardA.Clone();
		writtenA[0] ^= 0xFF;                                  // chunk 0
		writtenA[9999] ^= 0xFF;                               // chunk 2, the short one
		var v2 = SaveRam.Build(bases, [null, null], hardBases, [writtenA, hardB])!;
		Require(BitConverter.ToUInt32(v2, 4) == 2, "a hard file makes version 2");
		Require(v2.Length == 12 + 4 + (4 + 32 + 8 + 4 + 4) + (4 + 4096) + (4 + 1808) + 32, "version 2 with two chunks: header, one hard file, 4096 + 1808 bytes of data, checksum");
		var back = SaveRam.Parse(v2, bases, hardBases);
		Require(back.Floppy.Count == 0 && back.Hard.Select(e => (e.HardFile, e.Chunk, e.Data.Length)).SequenceEqual([(0, 0L, 4096), (0, 2L, 1808)]), "and reads back chunks 0 and 2 of hard file 0 only (chunk 1 did not change)");
		var applied = SaveRam.ApplyHard(hardBases, back.Hard);
		Require(applied[0].SequenceEqual(writtenA) && !ReferenceEquals(applied[0], hardA) && hardA[0] != writtenA[0], "applying them gives the written image and leaves the base alone");
		Require(ReferenceEquals(applied[1], hardB), "a hard file without chunks is not copied");

		// floppies and a hard file together; a core that only has hard files
		var both = SaveRam.Build(bases, [changedA, null], hardBases, [null, writtenOf(hardB)])!;
		var mixed = SaveRam.Parse(both, bases, hardBases);
		Require(BitConverter.ToUInt32(both, 4) == 2 && mixed.Floppy.Count == 2 && mixed.Hard.Count == 1 && mixed.Hard[0].HardFile == 1 && mixed.Hard[0].Chunk == 1, "floppies and a hard file in one SaveRam");
		Require(Refusal(() => SaveRam.Parse(both, [], hardBases)) == SaveRamRejection.BadFields, "a SaveRam with floppy sectors is refused by a core without that floppy");
		Require(Refusal(() => SaveRam.Parse(v2, bases, [])) == SaveRamRejection.BadFields, "and one with hard file chunks by a core without hard files");
		Require(SaveRam.Parse(v2, [], hardBases).Hard.Count == 2, "a core with hard files only reads it");

		// refused: other images (same size, other bytes), a changed header field, every cut, every flipped byte, padding
		var otherHard = new List<byte[]> { Pattern(10000, 9), hardB };
		Require(Refusal(() => SaveRam.Parse(v2, bases, otherHard)) == SaveRamRejection.OtherMedia, "another hard file of the same size is OtherMedia");
		Require(Refusal(() => SaveRam.Parse(v2, bases, [Pattern(8192, 3), hardB])) == SaveRamRejection.OtherMedia, "and one of another size");
		Require(Refusal(() => SaveRam.Parse(v1, [floppyB, floppyB], hardBases)) == SaveRamRejection.OtherMedia, "another floppy is still OtherMedia");
		var padded = v2.Concat(new byte[100]).ToArray();
		Require(SaveRam.Parse(padded, bases, hardBases).Length == v2.Length, "zero padding after the checksum is ignored");
		Require(Refusal(() => SaveRam.Parse(v2.Concat([ (byte)1 ]).ToArray(), bases, hardBases)) == SaveRamRejection.BadLength, "anything else after it is refused");
		int refusedCuts = 0;
		for (int cut = 0; cut < v2.Length; ++cut)
		{
			var reason = Refusal(() => SaveRam.Parse(v2[..cut], bases, hardBases));
			if (reason is null) throw new InvalidOperationException($"FAIL: a version 2 SaveRam cut to {cut} bytes was accepted");
			++refusedCuts;
		}
		Require(refusedCuts == v2.Length, $"all {refusedCuts} cuts of a version 2 SaveRam are refused with a SaveRamRejectedException");
		int refusedFlips = 0;
		for (int at = 0; at < v2.Length; at += 7)
		{
			var damaged = (byte[])v2.Clone();
			damaged[at] ^= 0x01;
			if (Refusal(() => SaveRam.Parse(damaged, bases, hardBases)) is null) throw new InvalidOperationException($"FAIL: a version 2 SaveRam with byte {at} flipped was accepted");
			++refusedFlips;
		}
		Require(refusedFlips > 0, $"{refusedFlips} flipped bytes of a version 2 SaveRam are refused");

		// the fields that a checksum cannot protect (the checksum is recomputed): chunks out of order, outside the image, a chunk size that is not 4096, a zero count, too many hard files
		void Reseal(byte[] blob) => StateEnvelope.Sha256(blob[..^32]).CopyTo(blob, blob.Length - 32);
		var chunk0At = 12 + 4 + (4 + 32 + 8 + 4 + 4);          // the first chunk record of v2
		var swapped = (byte[])v2.Clone();
		BitConverter.GetBytes(2u).CopyTo(swapped, chunk0At);                   // chunk 0 -> 2: now 2 then 2
		Reseal(swapped);
		Require(Refusal(() => SaveRam.Parse(swapped, bases, hardBases)) == SaveRamRejection.BadFields, "chunks out of order are BadFields");
		var beyond = (byte[])v2.Clone();
		BitConverter.GetBytes(3u).CopyTo(beyond, chunk0At + 4 + 4096);         // the second chunk record: chunk 3 does not exist in a 3-chunk image
		Reseal(beyond);
		Require(Refusal(() => SaveRam.Parse(beyond, bases, hardBases)) == SaveRamRejection.BadFields, "a chunk past the end of the image is BadFields");
		var size = (byte[])v2.Clone();
		BitConverter.GetBytes(512u).CopyTo(size, 12 + 4 + 4 + 32 + 8);
		Reseal(size);
		Require(Refusal(() => SaveRam.Parse(size, bases, hardBases)) == SaveRamRejection.BadFields, "a chunk size that is not 4096 is BadFields");
		var none = (byte[])v2.Clone();
		BitConverter.GetBytes(0u).CopyTo(none, 12);                            // zero hard files
		Reseal(none);
		Require(Refusal(() => SaveRam.Parse(none, bases, hardBases)) == SaveRamRejection.BadFields, "zero hard files in a version 2 SaveRam is BadFields");
		var version3 = (byte[])v2.Clone();
		BitConverter.GetBytes(3u).CopyTo(version3, 4);
		Reseal(version3);
		Require(Refusal(() => SaveRam.Parse(version3, bases, hardBases)) == SaveRamRejection.WrongVersion, "version 3 is WrongVersion");
		var noDisks = (byte[])v1.Clone();
		BitConverter.GetBytes(0u).CopyTo(noDisks, 8);
		Reseal(noDisks);
		Require(Refusal(() => SaveRam.Parse(noDisks, bases, hardBases)) == SaveRamRejection.BadFields, "a version 1 SaveRam without disks is BadFields");

		// a byte-for-byte reproduction: Build is a function of the bases and the images
		Require(v2.SequenceEqual(SaveRam.Build(bases, [null, null], hardBases, [(byte[])writtenA.Clone(), (byte[])hardB.Clone()])!), "Build twice gives the same bytes");
		Console.WriteLine("saveramformat complete");

		static byte[] writtenOf(byte[] image)
		{
			var copy = (byte[])image.Clone();
			copy[4096 + 17] ^= 0x55;                                          // chunk 1
			return copy;
		}
	}
}
