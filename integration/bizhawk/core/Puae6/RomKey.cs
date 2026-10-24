#nullable enable

using System;

namespace Puae6;

/// <summary>
/// The Kickstarts that Cloanto's Amiga Forever ships encrypted (T51), the same format and the same test as adapters/libretro/romkey.h: a file that starts with the 11 bytes "AMIROMTYPE1" is the ROM XORed, byte by
/// byte, with the bytes of a key file (rom.key) repeated from its first byte; the result is accepted if it starts like a Kickstart (WinUAE's test: a JMP at offset 2, or the $1111 / $1114 headers). The decrypted image is
/// the firmware: its SHA-256 is the identity of states and movies, and neither the key nor its hash is kept or reported. EmuHawk's firmware manager has no entry for a key (its database lists the plain Kickstarts by hash), so
/// the key arrives through <see cref="Puae6Parameters.RomKey"/> (a harness, or a front end that has one); an encrypted image without it fails the creation of the core with the reason.
/// </summary>
public static class Puae6RomKey
{
	const string Magic = "AMIROMTYPE1";
	public const int MagicBytes = 11;

	public static bool IsEncrypted(byte[] image)
	{
		if (image.Length < MagicBytes) return false;
		for (int i = 0; i < MagicBytes; i++)
			if (image[i] != (byte)Magic[i]) return false;
		return true;
	}

	/// <summary>WinUAE's test of a decrypted image (rommgr.cpp decode_cloanto_rom_do).</summary>
	public static bool StartsLikeAKickstart(byte[] rom)
		=> rom.Length >= 4 && ((rom[2] == 0x4e && rom[3] == 0xf9) || (rom[0] == 0x11 && (rom[1] == 0x11 || rom[1] == 0x14)));

	/// <summary>The firmware as the machine takes it: the image itself if it is not encrypted, else decrypted with <paramref name="key"/>. Throws ArgumentException saying what is missing or wrong.</summary>
	public static byte[] Resolve(byte[] image, byte[]? key)
	{
		if (!IsEncrypted(image)) return image;
		if (key == null || key.Length == 0)
			throw new ArgumentException("the Kickstart is encrypted by Amiga Forever (AMIROMTYPE1) and no rom.key was given: give the core the key (Puae6Parameters.RomKey) or a decrypted ROM", nameof(key));
		int payload = image.Length - MagicBytes;
		var rom = new byte[payload];
		int t = 0;
		for (int n = 0; n < payload; n++)
		{
			rom[n] = (byte)(image[MagicBytes + n] ^ key[t]);
			if (++t == key.Length) t = 0;
		}
		if (!StartsLikeAKickstart(rom))
			throw new ArgumentException("the Kickstart is encrypted by Amiga Forever (AMIROMTYPE1) and the rom.key does not decrypt it", nameof(key));
		return rom;
	}
}
