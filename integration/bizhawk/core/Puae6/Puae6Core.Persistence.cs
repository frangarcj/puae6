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

	/// <summary>How many disks the core was given.</summary>
	public int MediaCount => _media.Count;

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
	/// The disk as it is now (the base the guest has with the overlay over it, 901120 bytes), copied into a new array. Changes nothing in the machine: no flush, no event, no state. Returns the p6_result
	/// (UNSUPPORTED without floppy writes or with a raw track on the disk, BAD_ARGUMENT for a disk the core does not have) and the guest's message; <paramref name="image"/> is null unless it is OK.
	/// </summary>
	public int ExportMedium(int mediaIndex, out byte[]? image, out string error)
	{
		if (_faulted) throw new InvalidOperationException("the machine is undefined after a failed state load");
		var buffer = new byte[P6.AdfBytes];
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
		var current = new byte[_media.Count][];
		for (int i = 0; i < _media.Count; ++i)
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
		return SaveRam.Build(_media, current);
	}

	bool SaveRamAvailable => _syncSettings.FloppyWrites && !ReplayInProgress && !_faulted && !_disposed;

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
	/// The bounded durable form of what the guest wrote (<see cref="SaveRam"/>): null when no disk differs from its base, when the core cannot write its disks, and during a replay. It is never a
	/// disk image. A disk with a track that is not AmigaDOS is left out (see <see cref="DisksWithRawTracks"/>) and the frontend is told. Taking it changes nothing in the machine.
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
	/// Gives the core a SaveRam it made: its sectors become part of the disks the machine has. Only before the first unit and before any state is loaded, and not during a replay: the base disks
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
		if (!_syncSettings.FloppyWrites) throw new SaveRamRejectedException(SaveRamRejection.WritesDisabled, "this core was created without floppy writes: there is no SaveRam");
		if (ReplayInProgress) throw new SaveRamRejectedException(SaveRamRejection.TooLate, "a movie is being replayed: the durable copy is not read into it");
		if (_started || _faulted) throw new SaveRamRejectedException(SaveRamRejection.TooLate, "the machine already ran or loaded a state: the SaveRam is stored before the first unit");
		var entries = SaveRam.Parse(data, _media, out int length);       // the zero padding after the data is not part of what the SaveRam is: two files that differ in it are one
		var disks = SaveRam.Apply(_media, entries);
		BootGuest(disks, new ImportedSaveRam { Hash = StateEnvelope.Sha256(data.Take(length).ToArray()), Disks = new HashSet<int>(entries.Select(e => e.Disk)) });
		_serviceProvider.Register<IMemoryDomains>(_domains);
		using var _ = _guest.Host.EnterExit();
		_saveRamClean = SaveRamHash(SerializeSaveRam(out var ignored));
	}
}
