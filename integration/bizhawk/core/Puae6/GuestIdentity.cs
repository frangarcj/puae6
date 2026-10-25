#nullable enable

using System.IO;
using System.Linq;
using System.Runtime.InteropServices;
using System.Security.Cryptography;

namespace Puae6;

/// <summary>The two guest instruction sets supported by the enabled host profiles.</summary>
public enum GuestIsa : ushort { X64 = 62, AArch64 = 183 }

/// <summary>Why a guest file was refused.</summary>
public enum GuestRejection
{
	/// <summary>No file at the path.</summary>
	Missing,
	/// <summary>A compressed sibling exists, so the host would load that file and not the verified one.</summary>
	AmbiguousCompressedSibling,
	/// <summary>Too short to hold an ELF header, or not an ELF file.</summary>
	NotElf,
	/// <summary>An ELF, but not 64-bit little-endian.</summary>
	WrongClassOrByteOrder,
	/// <summary>An ELF for another machine than the explicitly selected guest ISA.</summary>
	WrongMachine,
	/// <summary>An ELF whose SHA-256 is not the pinned one.</summary>
	WrongHash,
}

/// <summary>A guest that was refused before the Waterbox host was created. Nothing was loaded and nothing was tried instead.</summary>
public sealed class GuestRejectedException : Exception
{
	public GuestRejection Reason { get; }
	public GuestRejectedException(GuestRejection reason, string message) : base(message) => Reason = reason;
}

/// <summary>
/// The identity gate of puae6.wbx (T20b). The Waterbox host of MacHawk accepts any ELF: measured in T09c, it loads an x86-64 guest without a word
/// and does not look at the layout, and it picks <c>name.zst</c> over <c>name</c> when both exist. So the wrapper has to say what it is willing to
/// run, before <c>wbx_create_host</c>: an ELF64 little-endian for the enabled host's ISA whose SHA-256 is the one this wrapper was built together with. A refused
/// guest is an error, never a fallback to another file (the UAE core's uae.wbx included).
/// </summary>
public static class GuestIdentity
{
	public const ushort EM_AARCH64 = 183;
	public const ushort EM_X86_64 = 62;
	const int ElfHeaderBytes = 64;

	public readonly record struct VerifiedGuest(string Path, string Sha256, long Bytes);

	/// <summary>Reject unsupported hosts before accessing firmware, media or a sandbox.</summary>
	public static GuestIsa ForHost(OSPlatform os, Architecture architecture)
	{
		if (os == OSPlatform.OSX && architecture == Architecture.Arm64) return GuestIsa.AArch64;
		if (os == OSPlatform.Windows && architecture == Architecture.X64) return GuestIsa.X64;
		throw new PlatformNotSupportedException($"PUAE6 Waterbox supports macOS ARM64 and Windows x64 only (host {os}/{architecture}); select UAE on this host.");
	}

	public static GuestIsa CurrentHostIsa => ForHost(
		RuntimeInformation.IsOSPlatform(OSPlatform.OSX) ? OSPlatform.OSX
			: RuntimeInformation.IsOSPlatform(OSPlatform.Windows) ? OSPlatform.Windows
			: OSPlatform.Create(RuntimeInformation.OSDescription), RuntimeInformation.ProcessArchitecture);

	static void CheckIsa(GuestIsa isa)
	{
		if (isa is not (GuestIsa.AArch64 or GuestIsa.X64)) throw new ArgumentOutOfRangeException(nameof(isa), isa, "Unsupported guest ISA");
	}

	/// <summary>Checks the file the host is going to load and returns what was verified, or throws <see cref="GuestRejectedException"/>.</summary>
	/// <param name="path">The guest, uncompressed. Its <c>.zst</c> sibling must not exist: WaterboxHost would load that one instead.</param>
	/// <param name="pinnedSha256">Lowercase or uppercase hex of the SHA-256 that the wrapper was built with.</param>
	public static VerifiedGuest Verify(string path, string pinnedSha256)
		=> Verify(path, pinnedSha256, GuestIsa.AArch64);

	public static VerifiedGuest Verify(string path, string pinnedSha256, GuestIsa isa)
	{
		CheckIsa(isa);
		if (pinnedSha256 is null || pinnedSha256.Length != 64 || !pinnedSha256.All(Uri.IsHexDigit))
			throw new ArgumentException("the pinned guest hash must be 64 hex digits", nameof(pinnedSha256));
		if (File.Exists(path + ".zst"))
			throw new GuestRejectedException(GuestRejection.AmbiguousCompressedSibling,
				$"{path}.zst exists and the Waterbox host would load it instead of the verified file; remove it or ship the guest uncompressed");
		byte[] bytes;
		try { bytes = File.ReadAllBytes(path); }
		catch (Exception e) when (e is FileNotFoundException or DirectoryNotFoundException)
		{
			throw new GuestRejectedException(GuestRejection.Missing, $"the PUAE6 guest {path} does not exist");
		}
		CheckElf(bytes, path, isa);
		string actual = Sha256Hex(bytes);
		if (!string.Equals(actual, pinnedSha256, StringComparison.OrdinalIgnoreCase))
			throw new GuestRejectedException(GuestRejection.WrongHash,
				$"{path} is a {isa} ELF but not the guest this wrapper was built with (sha256 {actual}, expected {pinnedSha256.ToLowerInvariant()})");
		return new VerifiedGuest(path, actual, bytes.LongLength);
	}

	/// <summary>Lowercase hex SHA-256 (written for net48 as well: Convert.ToHexString and SHA256.HashData are newer than that).</summary>
	public static string Sha256Hex(byte[] bytes)
	{
		using var sha = SHA256.Create();
		return BitConverter.ToString(sha.ComputeHash(bytes)).Replace("-", "").ToLowerInvariant();
	}

	/// <summary>The header checks alone: ELF magic, 64-bit, little endian, e_machine AArch64.</summary>
	public static void CheckElf(ReadOnlySpan<byte> bytes, string what)
		=> CheckElf(bytes, what, GuestIsa.AArch64);

	public static void CheckElf(ReadOnlySpan<byte> bytes, string what, GuestIsa isa)
	{
		CheckIsa(isa);
		if (bytes.Length < ElfHeaderBytes || bytes[0] != 0x7F || bytes[1] != (byte)'E' || bytes[2] != (byte)'L' || bytes[3] != (byte)'F')
			throw new GuestRejectedException(GuestRejection.NotElf, $"{what} is not an ELF file");
		if (bytes[4] != 2 || bytes[5] != 1)
			throw new GuestRejectedException(GuestRejection.WrongClassOrByteOrder, $"{what} is not a 64-bit little-endian ELF");
		int machine = bytes[18] | (bytes[19] << 8);       // e_machine, little endian
		if (machine != (ushort)isa)
			throw new GuestRejectedException(GuestRejection.WrongMachine, $"{what} is an ELF for machine {machine}, not {isa} ({(ushort)isa})");
	}
}
