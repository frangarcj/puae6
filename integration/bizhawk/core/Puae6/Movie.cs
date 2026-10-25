#nullable enable

using System.Collections.Generic;
using System.Linq;

namespace Puae6;

/// <summary>Why a movie was refused. Nothing is created or played before this is decided.</summary>
public enum MovieRejection
{
	/// <summary>Not recorded with this core (the UAE core of BizHawk, another core, no core at all). There is no conversion between the two.</summary>
	WrongCore,
	/// <summary>Not an Amiga movie.</summary>
	WrongSystem,
	/// <summary>A key that the header of a PUAE6 movie always has is missing.</summary>
	MissingKey,
	/// <summary>Recorded with another build of the guest.</summary>
	OtherGuest,
	/// <summary>Recorded with other sync settings (machine, standard, seed, clock, ports, drives, memory, floppy writes, stored SaveRam).</summary>
	OtherConfig,
	/// <summary>Recorded with another Kickstart.</summary>
	OtherFirmware,
	/// <summary>Recorded with other disks.</summary>
	OtherMedia,
	/// <summary>Invalid or inconsistent movie starting state/SaveRAM.</summary>
	BadStart,
}

public sealed class MovieRejectedException : Exception
{
	public MovieRejection Reason { get; }
	public MovieRejectedException(MovieRejection reason, string message) : base(message) => Reason = reason;
}

/// <summary>
/// The part of a BizHawk movie header that belongs to PUAE6 (the keys of a BK2 header are free text and BizHawk keeps the ones it does not know), and the rule that decides
/// whether a movie may be played by a core. A movie of the UAE core of BizHawk is refused: it was recorded with other buttons, another machine and another guest, and
/// nothing converts it. docs/03-waterbox.md ("Peliculas") has the scope: the A500 and A600 profiles with the firmware and disks that were tested.
/// </summary>
public static class Puae6Movie
{
	public const string Platform = "Amiga";
	public const string GuestKey = "PUAE6Guest", ConfigKey = "PUAE6Config", FirmwareKey = "PUAE6Firmware", MediaKey = "PUAE6Media";
	public const string NvramKey = "PUAE6InitialNvram";

	static string Hex(byte[] bytes) => BitConverter.ToString(bytes).Replace("-", "").ToLowerInvariant();

	/// <summary>The keys that tie a movie to this core: BizHawk's own Core and Platform, and what the core was built and configured with.</summary>
	public static Dictionary<string, string> Header(Puae6Core core)
	{
		return Header(core.Identity, core.Identity.Machine == "CD32" ? core.InitialNvramHash : null);
	}

	static Dictionary<string, string> Header(StateIdentity identity, byte[]? nvramHash)
	{
		var header = new Dictionary<string, string>
		{
			["Core"] = Puae6Names.Core,
			["Platform"] = Platform,
			[GuestKey] = Hex(identity.GuestSha256),
			[ConfigKey] = Hex(identity.ConfigHash),
			[FirmwareKey] = Hex(identity.FirmwareSha256),
			[MediaKey] = Hex(identity.MediaHash),
		};
		if (nvramHash is not null) header[NvramKey] = Hex(nvramHash);
		return header;
	}

	/// <summary>Capture before the frontend reboots: a SRAM movie starts from the captured bytes, a state movie from the original boot identity.</summary>
	public static Dictionary<string, string> HeaderForStart(Puae6Core core, bool fromState, byte[]? saveRam)
	{
		if (core.Identity.Machine != "CD32") return Header(fromState ? core.Identity : core.MediaMovieIdentity(saveRam), null);
		var hash = fromState ? core.InitialNvramHash : StateEnvelope.Sha256(saveRam ?? new byte[P6.Cd32NvramBytes]);
		return Header(core.MovieIdentity(hash), hash);
	}

	/// <summary>Preflight before Movie.Attach (TAS Attach can already import SRAM). Only CD32 may project an initial snapshot identity.</summary>
	public static IDisposable? PrepareStart(IReadOnlyDictionary<string, string> header, Puae6Core core, bool fromState, byte[]? state, bool fromSram, byte[]? saveRam)
	{
		core.CheckMovieBoot();
		if (fromState && fromSram) throw new MovieRejectedException(MovieRejection.BadStart, "conflicting movie start modes");
		if (core.Identity.Machine != "CD32")
		{
			core.CheckMediaMovieBoot();
			if (fromSram && saveRam is null) throw new MovieRejectedException(MovieRejection.BadStart, "the movie has no initial SaveRAM");
			var mediaIdentity = core.MediaMovieIdentity(fromState || fromSram ? saveRam : null);
			Validate(header, mediaIdentity, null);
			if (fromState)
			{
				core.ValidateMediaMovieState(state ?? throw new MovieRejectedException(MovieRejection.BadStart, "a binary initial state is required"), mediaIdentity);
				if (saveRam is not null) core.StoreSaveRam(saveRam); // All input checks precede this transactional rebuild.
			}
			return null;
		}
		if (!header.TryGetValue(NvramKey, out var text)) throw new MovieRejectedException(MovieRejection.MissingKey, $"the movie has no {NvramKey}");
		if (text.Length != 64 || text.Any(c => !Uri.IsHexDigit(c))) throw new MovieRejectedException(MovieRejection.BadStart, "invalid initial NVRAM hash");
		var hash = Enumerable.Range(0, 32).Select(i => Convert.ToByte(text.Substring(i * 2, 2), 16)).ToArray();
		var identity = core.MovieIdentity(hash);
		Validate(header, identity, hash);
		if (fromState)
			return core.PrepareMovieState(state ?? throw new MovieRejectedException(MovieRejection.BadStart, "a binary initial state is required"), identity, hash);
		var initial = fromSram ? saveRam : new byte[P6.Cd32NvramBytes];
		if (initial is null || initial.Length != P6.Cd32NvramBytes || !StateEnvelope.Sha256(initial).SequenceEqual(hash)
			|| (!fromSram && !core.CloneSaveRam(false)!.SequenceEqual(initial)))
			throw new MovieRejectedException(MovieRejection.BadStart, "the movie's initial SaveRAM does not match its identity");
		return null;
	}

	/// <summary>Throws <see cref="MovieRejectedException"/> unless the header is the one of a movie recorded with exactly this core setup.</summary>
	public static void Validate(IReadOnlyDictionary<string, string> header, Puae6Core core)
		=> Validate(header, core.Identity, core.Identity.Machine == "CD32" ? core.InitialNvramHash : null);

	static void Validate(IReadOnlyDictionary<string, string> header, StateIdentity identity, byte[]? nvramHash)
	{
		string Get(string key) => header.TryGetValue(key, out var value) ? value : throw new MovieRejectedException(MovieRejection.MissingKey, $"the movie has no {key}");
		string recorded = Get("Core");
		if (recorded != Puae6Names.Core)
			throw new MovieRejectedException(MovieRejection.WrongCore, $"this movie was recorded with the core {recorded}; {Puae6Names.Core} plays only its own movies (there is no conversion)");
		if (Get("Platform") != Platform) throw new MovieRejectedException(MovieRejection.WrongSystem, $"this is a movie for {Get("Platform")}, not for the Amiga");
		var expected = Header(identity, nvramHash);
		void Same(string key, MovieRejection reason, string what)
		{
			if (!string.Equals(Get(key), expected[key], StringComparison.OrdinalIgnoreCase)) throw new MovieRejectedException(reason, $"the movie was recorded with {what}");
		}
		Same(GuestKey, MovieRejection.OtherGuest, "another build of the guest");
		if (!string.Equals(Get(ConfigKey), expected[ConfigKey], StringComparison.OrdinalIgnoreCase))
		{
			// The same settings on the other machine have a hash of their own: a movie that carries it was recorded on that Amiga, and the message says so.
			var written = Get(ConfigKey);
			var other = identity.ConfigHashOfOtherMachines.Where(x => string.Equals(Hex(x.Value), written, StringComparison.OrdinalIgnoreCase)).Select(x => x.Value).FirstOrDefault();
			throw new MovieRejectedException(MovieRejection.OtherConfig, identity.ConfigMismatch(other ?? new byte[0], "movie"));
		}
		Same(FirmwareKey, MovieRejection.OtherFirmware, "another firmware");
		Same(MediaKey, MovieRejection.OtherMedia, "other disks");
		if (nvramHash is not null) Same(NvramKey, MovieRejection.OtherConfig, "other initial NVRAM");
	}
}


/// <summary>Pure startup projection; parsing validates every referenced base before any guest is rebuilt.</summary>
public static class Puae6MediaIdentity
{
	public static byte[]? CanonicalSaveRam(byte[]? data, IReadOnlyList<byte[]> floppies, IReadOnlyList<byte[]> hardFiles, bool floppyWrites)
	{
		if (data is null) return null;
		var parsed = SaveRam.Parse(data, floppies, hardFiles);
		if (!floppyWrites && parsed.Floppy.Count != 0) throw new SaveRamRejectedException(SaveRamRejection.WritesDisabled, "the movie carries floppy writes but they are disabled");
		return data.Take(parsed.Length).ToArray();
	}
}
