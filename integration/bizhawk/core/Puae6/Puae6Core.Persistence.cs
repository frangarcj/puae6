#nullable enable

using System.Collections.Generic;
using System.Linq;
using System.Text;

using BizHawk.Common;
using BizHawk.Emulation.Common;

namespace Puae6;

/// <summary>The write overlay of one disk as p6w_media_write_status reports it. The overlay is memory of the sandbox: a state load changes these numbers, nothing else does.</summary>
public sealed class MediaWriteStatus
{
	public int Result { get; set; }
	public string Error { get; set; } = "";
	/// <summary>Sectors the pool holds (for all disks together).</summary>
	public ulong OverlayCapacity { get; set; }
	/// <summary>Sectors in use (for all disks together).</summary>
	public ulong OverlayUsed { get; set; }
	/// <summary>Sectors of this disk that differ from the base the guest has.</summary>
	public ulong MediumSectors { get; set; }
	/// <summary>Guest writes refused for lack of room (a well behaved guest never gets here: the drives report the disk protected before the pool is full).</summary>
	public ulong RefusedWrites { get; set; }
	/// <summary>Tracks of this disk that the guest wrote in a format that is not AmigaDOS: kept exactly by the machine, but an ADF has no place for them.</summary>
	public ulong RawTracks { get; set; }
}

// Floppy writes and their durable form (T28). Rollback is the snapshot: it carries the overlay and is what a state, rewind or a branch of a movie use. Persistence is ISaveRam: the
// sectors that differ from the base, taken by an explicit call (CloneSaveRam), never during the replay of a movie, and given back only before the first unit (StoreSaveRam).
public sealed unsafe partial class Puae6Core
{
	/// <summary>The core was created with floppy writes: the disks are writable and the machine has an overlay (a sync setting).</summary>
	public bool FloppyWrites => _syncSettings.FloppyWrites;

	/// <summary>How many floppy disks the core was given.</summary>
	public int MediaCount => _media.Count;

	// ----- hard files (T63b) -----------------------------------------------------------------------------------------------------------------------------

	/// <summary>The core has at least one hard file (MacHawk's UAE.HasHardDisk).</summary>
	public bool HasHardDisk => _hardFiles.Count != 0;

	/// <summary>How many hard files the core was given; hard file n is the p6 medium <see cref="MediaCount"/> + n.</summary>
	public int HardFileCount => _hardFiles.Count;

	/// <summary>The name of a hard file as the loader gave it (MacHawk's UAE.GetHardDiskName).</summary>
	public string GetHardDiskName(int index)
	{
		if (index < 0 || index >= _hardFiles.Count) throw new ArgumentOutOfRangeException(nameof(index));
		return index < _hardFileNames.Count && !string.IsNullOrEmpty(_hardFileNames[index]) ? _hardFileNames[index] : $"hard file {index + 1}";
	}

	/// <summary>The write overlay of a hard file: the chunks of 4 KiB the guest wrote (<see cref="MediaWriteStatus.MediumSectors"/> and <see cref="MediaWriteStatus.OverlayUsed"/> count chunks, <see cref="MediaWriteStatus.OverlayCapacity"/> the chunks of the image).</summary>
	public MediaWriteStatus GetHardFileWriteStatus(int index) => GetMediaWriteStatus(HardFileMedium(index));

	/// <summary>
	/// The hard file as it is now, the base the guest has with its writes over it (MacHawk's UAE.GetHardDiskContents), in a new array. Changes nothing in the machine. The image given to the core and every
	/// file stay as they were: making the writes durable is the caller's (export it, or keep the SaveRam).
	/// </summary>
	public byte[] GetHardDiskContents(int index)
	{
		int result = ExportMedium(HardFileMedium(index), out var image, out var error);
		if (image is null) throw new InvalidOperationException($"hard file {index} could not be exported ({P6.ResultName(result)}): {error}");
		return image;
	}

	int HardFileMedium(int index) => index >= 0 && index < _hardFiles.Count ? _media.Count + index : throw new ArgumentOutOfRangeException(nameof(index));

	static void ValidateHardFiles(Puae6Parameters parameters, int floppies)
	{
		var images = parameters.HardFiles;
		if (images.Count > P6.MaxHardFiles) throw new ArgumentException($"at most {P6.MaxHardFiles} hard files are supported ({images.Count} given)", nameof(parameters));
		if (floppies + images.Count > P6.MaxMedia) throw new ArgumentException($"at most {P6.MaxMedia} disks and hard files are supported", nameof(parameters));
		for (int i = 0; i < images.Count; ++i)
		{
			if (images[i].Length < 512 || images[i].Length % 512 != 0)
				throw new ArgumentException($"hard file {i} has {images[i].Length} bytes: a hard file is a whole number of 512-byte blocks, at least one", nameof(parameters));
			for (int j = 0; j < i; ++j)
				if (images[i].AsSpan().SequenceEqual(images[j])) throw new ArgumentException($"hard files {j} and {i} are the same image: a medium appears once", nameof(parameters));
		}
	}

	/// <summary>
	/// A guard that a frontend or a tool may raise while a movie plays: the SaveRam is then not read (CloneSaveRam gives null, SaveRamModified is false) and not replaced (StoreSaveRam is
	/// refused). Nothing in BizHawk sets it, and nothing needs to: EmuHawk keeps a movie's SaveRAM apart from the game's (<c>PathEntryCollectionExtensions.SaveRamAbsolutePath(game, movie)</c>, one file per
	/// movie), does not run LoadSaveRam when a movie is queued, and starts a movie "from SaveRAM" by calling StoreSaveRam before the first unit (IMovie.ProcessSram, TasMovie.Attach), which
	/// this property must therefore leave open. Only the session host of the tests (<c>run ... movie=</c>, <c>replay</c>) sets it, to prove the guard; a frontend that does not separate the
	/// files of a movie from the durable copy would have to.
	/// </summary>
	public bool ReplayInProgress { get; set; }

	/// <summary>The write overlay of one disk. Reads the machine only; it needs <see cref="FloppyWrites"/> (otherwise the result is UNSUPPORTED, from the guest).</summary>
	public MediaWriteStatus GetMediaWriteStatus(int mediaIndex)
	{
		if (_faulted) throw new InvalidOperationException("the machine is undefined after a failed state load");
		var call = new P6wMediaWriteStatus { AbiVersion = P6.AbiVersion, StructSize = (uint)sizeof(P6wMediaWriteStatus) };
		call.Status.AbiVersion = P6.AbiVersion;
		call.Status.StructSize = (uint)sizeof(P6MediaWriteStatus);
		call.Status.MediaIndex = (uint)mediaIndex;
		using var _ = _guest.Host.EnterExit();
		int result = _guest.Lib.p6w_media_write_status((IntPtr)(&call));
		return new MediaWriteStatus
		{
			Result = result, Error = result == P6.Ok ? "" : (Mershul.PtrToStringUtf8((IntPtr)call.Error) ?? ""),
			OverlayCapacity = call.Status.OverlayCapacity, OverlayUsed = call.Status.OverlayUsed, MediumSectors = call.Status.MediumSectors,
			RefusedWrites = call.Status.RefusedWrites, RawTracks = call.Status.RawTracks,
		};
	}

	/// <summary>
	/// The disk as it is now (the base the guest has with the overlay over it, 901120 bytes for a floppy and the size of the image for a hard file, whose medium index follows the floppies), copied into a new array. Changes nothing in the machine: no flush, no event, no state. Returns the p6_result
	/// (UNSUPPORTED without floppy writes or with a raw track on the disk, BAD_ARGUMENT for a disk the core does not have) and the guest's message; <paramref name="image"/> is null unless it is OK.
	/// </summary>
	public int ExportMedium(int mediaIndex, out byte[]? image, out string error)
	{
		if (_faulted) throw new InvalidOperationException("the machine is undefined after a failed state load");
		var buffer = new byte[mediaIndex >= _media.Count && mediaIndex - _media.Count < _hardFiles.Count ? _hardFiles[mediaIndex - _media.Count].Length : P6.AdfBytes];
		var call = new P6wMediaExport { AbiVersion = P6.AbiVersion, StructSize = (uint)sizeof(P6wMediaExport), MediaIndex = (uint)mediaIndex, Capacity = (ulong)buffer.Length };
		int result;
		fixed (byte* p = buffer)
		{
			call.Buffer = p;
			using var _ = _guest.Host.EnterExit();
			result = _guest.Lib.p6w_media_export((IntPtr)(&call));
		}
		error = result == P6.Ok ? "" : (Mershul.PtrToStringUtf8((IntPtr)call.Error) ?? "");
		image = result == P6.Ok && call.Written == (ulong)buffer.Length ? buffer : null;
		return result;
	}

	// ----- ISaveRam -------------------------------------------------------------------------------------------------------------------------

	byte[] _nvramInitialHash = StateEnvelope.Sha256(new byte[P6.Cd32NvramBytes]);

	byte[] Nvram(byte[]? stored = null)
	{
		var data = stored ?? new byte[P6.Cd32NvramBytes];
		using var active = _guest.Host.EnterExit();
		var refused = _guest.Lib.p6w_memory_refused();
		fixed (byte* p = data) _guest.Lib.p6w_memory_access(P6.RegionNvram, (IntPtr)p, 0, data.Length, stored is null ? 0u : 1u);
		if (refused != _guest.Lib.p6w_memory_refused()) throw new InvalidOperationException("CD32 EEPROM transfer refused");
		return data;
	}

	static readonly byte[] NoSaveRamHash = StateEnvelope.Sha256([ ]);

	static byte[] SaveRamHash(byte[]? blob) => blob is null ? NoSaveRamHash : StateEnvelope.Sha256(blob);

	/// <summary>Disks whose writes the SaveRam cannot hold because the guest wrote a track that is not AmigaDOS (the machine keeps it; an ADF cannot). They are left out of <see cref="CloneSaveRam"/>.</summary>
	public IReadOnlyList<int> DisksWithRawTracks()
	{
		var disks = new List<int>();
		if (_syncSettings.FloppyWrites)
			for (int i = 0; i < _media.Count; ++i)
				if (GetMediaWriteStatus(i).RawTracks != 0) disks.Add(i);
		return disks;
	}

	// The SaveRam as it is now: the sectors of each disk that differ from the disk the core was GIVEN (the bases, not what the guest has: after StoreSaveRam the guest's base already
	// holds the stored sectors, and they stay part of what is kept).
	byte[]? SerializeSaveRam(out List<int> leftOut)
	{
		leftOut = new List<int>();
		if (_syncSettings.Machine == Puae6Machine.CD32) return Nvram();
		var current = new byte[_media.Count][];
		for (int i = 0; i < _media.Count && _syncSettings.FloppyWrites; ++i)
		{
			var status = GetMediaWriteStatus(i);
			if (status.Result != P6.Ok) throw new InvalidOperationException($"the write status of disk {i} is not available ({P6.ResultName(status.Result)}): {status.Error}");
			bool touched = status.MediumSectors != 0 || (_imported?.Disks.Contains(i) ?? false);
			if (status.RawTracks != 0) { leftOut.Add(i); continue; }
			if (!touched) continue;
			int result = ExportMedium(i, out var image, out var error);
			if (result != P6.Ok || image is null) throw new InvalidOperationException($"disk {i} could not be exported ({P6.ResultName(result)}): {error}");
			current[i] = image;
		}
		// The hard files (T63b): the guest's overlay says how many chunks it wrote; none and nothing imported means nothing differs and nothing is exported. Otherwise the whole image is exported and
		// compared with the base chunk by chunk, so what is kept is what differs (a chunk written with the bytes it had is not), and the cost is the size of the image, not the number of chunks written.
		var hardCurrent = new byte[_hardFiles.Count][];
		for (int i = 0; i < _hardFiles.Count; ++i)
		{
			var status = GetMediaWriteStatus(_media.Count + i);
			if (status.Result != P6.Ok) throw new InvalidOperationException($"the write status of hard file {i} is not available ({P6.ResultName(status.Result)}): {status.Error}");
			if (status.MediumSectors == 0 && !(_imported?.HardFiles.Contains(i) ?? false)) continue;
			hardCurrent[i] = GetHardDiskContents(i);
		}
		return SaveRam.Build(_media, current, _hardFiles, hardCurrent);
	}

	/// <summary>Something of this core can be made durable: its floppies when they are writable, its hard files (always writable), and CD32 EEPROM.</summary>
	bool SaveRamPossible => _syncSettings.Machine == Puae6Machine.CD32 || _syncSettings.FloppyWrites || _hardFiles.Count != 0;

	bool SaveRamAvailable => SaveRamPossible && !ReplayInProgress && !_faulted && !_disposed;

	/// <summary>Whether the sectors that differ from the base are not the ones last kept (<see cref="CloneSaveRam"/> with clearDirty, or <see cref="StoreSaveRam"/>). False during a replay.</summary>
	public bool SaveRamModified
	{
		get
		{
			if (!SaveRamAvailable) return false;
			using var _ = _guest.Host.EnterExit();
			return !SaveRamHash(SerializeSaveRam(out var ignored)).SequenceEqual(_saveRamClean ?? NoSaveRamHash);
		}
	}

	/// <summary>
	/// CD32 returns its complete 1024-byte EEPROM. Other models return the bounded durable form of what the guest wrote (<see cref="SaveRam"/>): null when no disk or hard file differs from its base, when the core cannot write anything, and during a replay. It is never a
	/// disk image (a hard file contributes the 4 KiB chunks that differ, not its image). A disk with a track that is not AmigaDOS is left out (see <see cref="DisksWithRawTracks"/>) and the frontend is told. Taking it changes nothing in the machine.
	/// </summary>
	public byte[]? CloneSaveRam(bool clearDirty = true)
	{
		if (!SaveRamAvailable) return null;
		byte[]? blob;
		List<int> leftOut;
		using (var _ = _guest.Host.EnterExit()) blob = SerializeSaveRam(out leftOut);
		if (leftOut.Count != 0)
			CoreComm.Notify($"SaveRAM: disk {string.Join(", ", leftOut)} has a track that is not AmigaDOS; an ADF cannot hold it, so its writes are not kept", 8);
		if (clearDirty) _saveRamClean = SaveRamHash(blob);
		return blob;
	}

	/// <summary>
	/// CD32 takes exactly 1024 raw EEPROM bytes without rebuilding the machine. Other models take a SaveRam they made: its sectors become part of the disks the machine has. Only before the first unit and before any state is loaded, and not during a replay: the base disks
	/// live in the sealed heap, so the machine is built again with the disks the SaveRam makes (as a new core would be) and what the SaveRam was becomes part of the identity of states and movies.
	/// A SaveRam that is not whole, not ours or not for these disks is refused with its reason (also notified on screen) and nothing changes; the exception is an IOException, which is what
	/// EmuHawk's LoadSaveRam catches. Zero bytes after the end of the data are padding and are ignored.
	/// </summary>
	public void StoreSaveRam(byte[] data)
	{
		try
		{
			StoreSaveRamChecked(data);
		}
		catch (SaveRamRejectedException e)
		{
			CoreComm.Notify($"SaveRAM refused ({e.Reason}): {e.Message}", 8);          // the frontend's own message (EmuHawk: "An error occurred while loading Sram") does not say why
			throw;
		}
	}

	void StoreSaveRamChecked(byte[] data)
	{
		if (!SaveRamPossible) throw new SaveRamRejectedException(SaveRamRejection.WritesDisabled, "this core was created without floppy writes and without hard files: there is no SaveRam");
		if (ReplayInProgress) throw new SaveRamRejectedException(SaveRamRejection.TooLate, "a movie is being replayed: the durable copy is not read into it");
		if (_started || _faulted) throw new SaveRamRejectedException(SaveRamRejection.TooLate, "the machine already ran or loaded a state: the SaveRam is stored before the first unit");
		if (_syncSettings.Machine == Puae6Machine.CD32)
		{
			if (data is null || data.Length != P6.Cd32NvramBytes) throw new SaveRamRejectedException(SaveRamRejection.BadLength, "CD32 SaveRAM must contain exactly 1024 bytes");
			var hash = StateEnvelope.Sha256(data);
			Nvram(data);
			_nvramInitialHash = hash;
			_identity = MakeIdentity(_parameters.Firmware);
			_saveRamClean = hash;
			return;
		}
		var contents = SaveRam.Parse(data, _media, _hardFiles);          // the zero padding after the data is not part of what the SaveRam is: two files that differ in it are one
		if (contents.Floppy.Count != 0 && !_syncSettings.FloppyWrites)
			throw new SaveRamRejectedException(SaveRamRejection.WritesDisabled, "the SaveRam holds floppy writes and this core was created without them");
		// The disks and the hard files the guest starts with are the bases with the SaveRam over them (a new core with other media, as for floppies): the chunks are then part of the base, which is the baseline
		// of the sandbox's snapshot, and the overlay starts empty. SerializeSaveRam keeps them because it compares with the bases the core was given.
		var disks = SaveRam.Apply(_media, contents.Floppy);
		var hardFiles = SaveRam.ApplyHard(_hardFiles, contents.Hard);
		BootGuest(disks, hardFiles, new ImportedSaveRam
		{
			Hash = StateEnvelope.Sha256(data.Take(contents.Length).ToArray()),
			Disks = new HashSet<int>(contents.Floppy.Select(e => e.Disk)), HardFiles = new HashSet<int>(contents.Hard.Select(e => e.HardFile)),
		});
		_serviceProvider.Register<IMemoryDomains>(_domains);
		using var _ = _guest.Host.EnterExit();
		_saveRamClean = SaveRamHash(SerializeSaveRam(out var ignored));
	}
}
