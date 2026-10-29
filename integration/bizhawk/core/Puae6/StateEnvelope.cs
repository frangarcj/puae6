#nullable enable

using System.Collections.Generic;
using System.IO;
using System.Buffers.Binary;
using System.Linq;
using System.Security.Cryptography;
using System.Text;

namespace Puae6;

/// <summary>Why a state was refused. Every one of them is found before the sandbox is touched.</summary>
public enum StateRejection
{
	/// <summary>Not a PUAE6 state (another core's, another kind of file, garbage).</summary>
	NotAState,
	/// <summary>A PUAE6 state of a format this wrapper does not read.</summary>
	WrongVersion,
	/// <summary>The length it declares is more than a state can be.</summary>
	TooLarge,
	/// <summary>The stream ends before the state does.</summary>
	Truncated,
	/// <summary>Written by another build of the guest.</summary>
	OtherGuest,
	/// <summary>Written with other heap sizes.</summary>
	OtherHeaps,
	/// <summary>Written with other sync settings (machine, standard, seed, clock, ports, mouse speed, drives, memory, floppy writes, stored SaveRam).</summary>
	OtherConfig,
	/// <summary>Written with another firmware (the Kickstart is in the sandbox, not in the snapshot).</summary>
	OtherFirmware,
	/// <summary>Written with other disks.</summary>
	OtherMedia,
	/// <summary>The checksum of the payload does not match.</summary>
	Corrupt,
	/// <summary>The payload has the right checksum and a field that cannot be (a drive that does not exist, a size past the buffers).</summary>
	BadFields,
}

/// <summary>A state that was not loaded. Nothing of the machine, the wrapper or the sandbox changed.</summary>
public sealed class StateRejectedException : Exception
{
	public StateRejection Reason { get; }
	public StateRejectedException(StateRejection reason, string message) : base(message) => Reason = reason;
}

/// <summary>What a state is only valid for: the guest build, the heaps, the sync settings, the firmware and the disks (docs/02-contracts.md, "Estados").</summary>
public sealed class StateIdentity
{
	public byte[] GuestSha256 { get; }
	public uint[] HeapsKB { get; }
	public byte[] ConfigHash { get; }
	public byte[] FirmwareSha256 { get; }
	public byte[] MediaHash { get; }
	/// <summary>The machine these settings are for (a name of Puae6Machine: "A500", "A600", "A1200", "A500OG", "A500PLUS", "A1000", "A2000OG", "A2000"); empty when the identity does not name one.</summary>
	public string Machine { get; }
	/// <summary>The config hash the same settings would have on each of the other machines, by machine name: it lets a refusal say "made on the A500" instead of "other settings".</summary>
	public IReadOnlyDictionary<string, byte[]> ConfigHashOfOtherMachines { get; }

	/// <summary>The message of a refusal because the config differs: when <paramref name="have"/> is the config hash of the same settings on another machine, it names both.</summary>
	public string ConfigMismatch(byte[] have, string verb)
	{
		foreach (var other in ConfigHashOfOtherMachines)
			if (other.Value.SequenceEqual(have)) return $"the {verb} was made for the {other.Key}, this core is the {Machine}: the machine is a sync setting and a {verb} of one Amiga does not run on another";
		return $"the {verb} was made with other sync settings (machine, standard, seed, clock, ports, mouse speed, drives, memory, floppy writes, stored SaveRam); this core is the {Machine}";
	}

	public StateIdentity(string guestSha256, uint[] heapsKB, byte[] configHash, byte[] firmwareSha256, byte[] mediaHash, string machine = "",
		IReadOnlyDictionary<string, byte[]>? otherMachines = null)
	{
		Machine = machine;
		ConfigHashOfOtherMachines = otherMachines ?? new Dictionary<string, byte[]>();
		GuestSha256 = Enumerable.Range(0, 32).Select(i => Convert.ToByte(guestSha256.Substring(2 * i, 2), 16)).ToArray();
		HeapsKB = heapsKB;
		ConfigHash = configHash;
		FirmwareSha256 = firmwareSha256;
		MediaHash = mediaHash;
	}
}

/// <summary>
/// The envelope of a Waterbox state of the PUAE6 core. The payload is the snapshot of the sandbox plus what the wrapper holds that the sandbox does not;
/// the header says what the payload is valid for and is checked, with the length and the checksum, before anything is restored: a state of another build,
/// another Kickstart, other settings or other disks is refused with an error and the instance goes on where it was. This is not the native state of
/// docs/adr/0009-state-format.md and the two are not interchangeable.
/// </summary>
public static class StateEnvelope
{
	public const uint Version = 3; // Wrapper layout adds the four causal mouse-scaling remainders.
	/// <summary>A state is the dirty pages of a 40 MiB image: nothing near this; it only keeps a crafted length from allocating gigabytes.</summary>
	public const long MaxPayloadBytes = 256L * 1024 * 1024;
	static readonly byte[] Magic = Encoding.ASCII.GetBytes("P6WS");
	static readonly byte[] ChecksumDomain = Encoding.ASCII.GetBytes("puae6-waterbox-state-v3\0");

	public static byte[] Sha256(params byte[][] parts)
	{
		using var sha = SHA256.Create();
		foreach (var part in parts) sha.TransformBlock(part, 0, part.Length, null, 0);
		sha.TransformFinalBlock(new byte[0], 0, 0);
		return sha.Hash!;
	}

	/// <summary>The checksum a state carries for its payload (also used by the tests to craft a state whose checksum is right and whose fields are not).</summary>
	public static byte[] Checksum(byte[] payload) => Sha256(ChecksumDomain, payload);

	public static void Write(BinaryWriter writer, StateIdentity identity, byte[] payload)
	{
		writer.Write(Magic);
		writer.Write(Version);
		writer.Write(identity.GuestSha256);
		foreach (uint heap in identity.HeapsKB) writer.Write(heap);
		writer.Write(identity.ConfigHash);
		writer.Write(identity.FirmwareSha256);
		writer.Write(identity.MediaHash);
		writer.Write((ulong)payload.Length);
		writer.Write(Checksum(payload));
		writer.Write(payload);
	}

	static byte[] Take(BinaryReader reader, int count, string what)
	{
		var bytes = reader.ReadBytes(count);
		if (bytes.Length != count) throw new StateRejectedException(StateRejection.Truncated, $"the state ends inside {what}");
		return bytes;
	}

	/// <summary>Reads and validates the envelope; returns the payload only when the state is a PUAE6 state of this version, made by this very machine setup, whole and unaltered.</summary>
	public static byte[] Read(BinaryReader reader, StateIdentity expected)
	{
		var magic = reader.ReadBytes(4);
		if (!magic.SequenceEqual(Magic)) throw new StateRejectedException(StateRejection.NotAState, "this is not a PUAE6 state");
		uint version = BinaryPrimitives.ReadUInt32LittleEndian(Take(reader, 4, "the header"));
		if (version != Version) throw new StateRejectedException(StateRejection.WrongVersion, $"the state has format {version}, this wrapper reads {Version}");
		void Same(byte[] have, byte[] want, StateRejection reason, string what)
		{
			if (!have.SequenceEqual(want)) throw new StateRejectedException(reason, $"the state was made with {what}");
		}
		Same(Take(reader, 32, "the header"), expected.GuestSha256, StateRejection.OtherGuest, "another build of the guest");
		var heaps = Take(reader, 4 * expected.HeapsKB.Length, "the header");
		for (int i = 0; i < expected.HeapsKB.Length; ++i)
			if (BinaryPrimitives.ReadUInt32LittleEndian(heaps.AsSpan(4 * i, 4)) != expected.HeapsKB[i]) throw new StateRejectedException(StateRejection.OtherHeaps, "the state was made with other heap sizes");
		var config = Take(reader, 32, "the header");
		if (!config.SequenceEqual(expected.ConfigHash)) throw new StateRejectedException(StateRejection.OtherConfig, expected.ConfigMismatch(config, "state"));
		Same(Take(reader, 32, "the header"), expected.FirmwareSha256, StateRejection.OtherFirmware, "another firmware");
		Same(Take(reader, 32, "the header"), expected.MediaHash, StateRejection.OtherMedia, "other disks");
		ulong length = BinaryPrimitives.ReadUInt64LittleEndian(Take(reader, 8, "the header"));
		if (length > (ulong)MaxPayloadBytes) throw new StateRejectedException(StateRejection.TooLarge, $"the state declares {length} bytes");
		var checksum = Take(reader, 32, "the header");
		// A length that the stream cannot satisfy is refused before a buffer of that size is allocated.
		if (reader.BaseStream.CanSeek && (long)length > reader.BaseStream.Length - reader.BaseStream.Position) throw new StateRejectedException(StateRejection.Truncated, "the state ends inside the payload");
		var payload = Take(reader, (int)length, "the payload");
		if (!Checksum(payload).SequenceEqual(checksum)) throw new StateRejectedException(StateRejection.Corrupt, "the state does not match its checksum");
		return payload;
	}
}
