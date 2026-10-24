"""T51: the Kickstart a machine runs can be chosen (puae6_kickstart) and can come encrypted by Amiga Forever (rom.key), through the libretro core (LR01, MED01, FUZZ01 has its own target).

What is checked, with synthetic material only (a micro-ROM is a valid 256 KiB Kickstart for the bridge: header, size fields and checksum):
  * the names a version is looked for under are those PUAE5 publishes (libretro-uae's uae_kickstarts[], read from references/), every one of them is found, and a version that is chosen
    only looks for its own names (a directory with the 1.3 does not boot a machine that was asked for the 2.05, and Automatic never looks for the 2.05 on the A500);
  * the firmware is identified by its bytes: a state taken with another Kickstart is refused by the machine that runs this one and accepted where the bytes are the same;
  * an ROM encrypted with a key (AMIROMTYPE1 + XOR with rom.key, the format of Cloanto's Amiga Forever; the test cipher is written here from the description, not taken from the core) is
    decrypted in the adapter, boots exactly as the plain ROM does, has the plain ROM's identity (a state moves between them), and every way it can fail says what is wrong
    (no key, a wrong key, a key too long, an image that decrypts to something that is not a Kickstart, the same file given to a runner that has no adapter);
  * with the owner's Kickstarts (opt-in: P6_KICKSTART_ROM 1.3, P6_KICKSTART_A600_ZIP 2.05, P6_KICKSTART_A1200_ROM 3.1; read where they are, linked into a temporary system directory, never
    copied into the repository, never hashed) alternative Kickstarts boot on machines that do not have them by default, and the 3.1 of the A1200 is refused with the reason on every
    machine with a 68000. Without the variables those tests are skipped.
Run: python3 -m unittest discover -s tests/libretro-host -p "test_firmware.py"
"""
import array
from collections import Counter
import hashlib
import os
from pathlib import Path
import re
import struct
import subprocess
import sys
import tempfile
import unittest
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_libretro as lr  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
PUAE5_TABLE = ROOT / "references/machawk-uae/libretro/libretro-core.h"
SCHEMA = ROOT / "config/schema/options.json"

# option value -> (version the message names, the names the adapter must look for, in PUAE5's order first)
KS13 = os.environ.get("P6_KICKSTART_ROM")
KS205_ZIP = os.environ.get("P6_KICKSTART_A600_ZIP")
KS31_A1200 = os.environ.get("P6_KICKSTART_A1200_ROM")
LOAD = re.compile(r"^state load (\S+) (\S+) ok=(\d) size=(\d+)$", re.M)
SAVE = re.compile(r"^state save (\S+) ok=(\d) size=(\d+) used=(\d+) hash=(\w+) section=(\w+)$", re.M)


def puae5_names():
    """libretro-uae's uae_kickstarts[]: normal file name -> (normal, Amiga Forever, TOSEC-mod, TOSEC), the empty ones left out."""
    text = PUAE5_TABLE.read_text(encoding="utf-8")
    body = text[text.index("static retro_kickstarts uae_kickstarts"):]
    body = body[:body.index("};")]
    table = {}
    for entry in re.finditer(r"\{\w+,\s*((?:\"[^\"]*\",?\s*){4})\}", body):
        names = re.findall(r"\"([^\"]*)\"", entry[1])
        table[names[0]] = [name for name in names if name]
    return table


def cloanto(rom, key):
    """The AMIROMTYPE1 format as Cloanto's Amiga Forever writes it: the 11 bytes of the tag, then every byte of the ROM XORed with the key repeated from its first byte."""
    return b"AMIROMTYPE1" + bytes(byte ^ key[index % len(key)] for index, byte in enumerate(rom))


def key_of(length, seed="rom.key"):
    """A synthetic key (the real one is Cloanto's and is never here): `length` bytes from a hash chain of the seed."""
    out = b""
    block = seed.encode()
    while len(out) < length:
        block = hashlib.sha256(block).digest()
        out += block
    return out[:length]


def choices_of_the_schema():
    import json
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    (selector,) = [s for s in schema["selectors"] if s["id"] == "kickstart"]
    return [v["value"] for v in selector["values"] if v["value"] != "auto"]


@unittest.skipUnless(lr.TOOLS, "toolchain unavailable")
class KickstartChoiceTests(lr.LibretroHarness):
    def system_with(self, files):
        """A system directory with these files (name -> bytes)."""
        path = self.work()
        for name, data in files.items():
            (path / name).write_bytes(data)
        return path

    def rom_bytes(self, rom="boot"):
        return self.roms[rom].read_bytes()

    @staticmethod
    def messages(text):
        return re.findall(r"(?m)^message (.*)$", text)

    def hashes(self, out):
        return [(r["hash"], r["audio"]) for r in self.runs(out)]

    # --- the names -----------------------------------------------------------------------------------------------------------------------

    def test_the_choices_are_the_kickstarts_puae5_offers_and_every_name_it_publishes_for_them_is_looked_for(self):
        table = puae5_names()
        values = choices_of_the_schema()
        self.assertEqual(len(values), 7)
        header = (ROOT / "adapters/libretro/firmware.h").read_text(encoding="utf-8")
        for value in values:
            with self.subTest(value=value):
                self.assertIn(value, table, "PUAE5 does not publish this value")
                for name in table[value]:
                    self.assertIn(f'"{name}"', header, f"{name} is a name PUAE5 reads for {value}")
        for value in table:
            if ".A4000" in value or ".CD" in value:
                continue
            self.assertIn(value, values, f"PUAE5's {value} has no choice (and is not a machine this core lacks)")

    def test_every_name_of_every_choice_is_found_with_the_vfs_and_with_stdio_and_boots_what_automatic_boots(self):
        control = self.hashes(self.frontend("boot", "--runs", 5).stdout)
        table = puae5_names()
        for value in choices_of_the_schema():
            for name in table[value]:
                for no_vfs in ((), ("--no-vfs",)):
                    with self.subTest(value=value, name=name, no_vfs=no_vfs):
                        out = self.frontend("boot", "--runs", 5, "--set", f"puae6_kickstart={value}", *no_vfs, name=name).stdout
                        self.assertIn(f"Kickstart {name}, read " + ("with stdio" if no_vfs else "through the frontend VFS"), out)
                        self.assertEqual(self.hashes(out), control)

    def test_a_chosen_version_looks_only_for_its_names_and_automatic_never_looks_for_the_others(self):
        wrong = self.frontend("boot", "--set", "puae6_kickstart=kick37350.A600", name=lr.KICKSTART_NAMES[0], expect=3).stdout
        text = " ".join(self.messages(wrong))
        self.assertIn("no Kickstart 2.05 ROM in the system directory ", text)
        self.assertIn("(looked for kick37350.A600, ...); puae6_kickstart asks for it on the A500", text)
        self.assertNotIn("run 1 ", wrong)
        auto = self.frontend("boot", name=lr.KICKSTART_NAMES_A600[0], expect=3).stdout
        self.assertIn("no Kickstart 1.3 ROM in the system directory ", " ".join(self.messages(auto)))
        # Each machine with the version of its own is the same as Automatic.
        for machine, version in (("A600", "kick37350.A600"), ("A500PLUS", "kick37175.A500"), ("A2000", "kick40063.A600"), ("A1200", "kick40068.A1200")):
            with self.subTest(machine=machine):
                names = lr.MACHINES.get(machine)
                chosen = self.frontend("boot", "--runs", 3, "--set", f"puae6_machine={machine}", "--set", f"puae6_kickstart={version}", name=version).stdout
                self.assertIn(f"Kickstart {version},", chosen)

    def test_a_directory_with_every_version_gives_each_choice_its_own_rom(self):
        files = {"kick33180.A500": self.rom_bytes("boot"), "kick34005.A500": self.rom_bytes("input_all"), "kick37175.A500": self.rom_bytes("identify")}
        directory = self.system_with(files)
        seen = {}
        for value, rom in (("kick33180.A500", "boot"), ("kick34005.A500", "input_all"), ("kick37175.A500", "identify")):
            out = self.frontend(rom, "--runs", 6, "--set", f"puae6_kickstart={value}", system=directory).stdout
            self.assertIn(f"Kickstart {value},", out)
            seen[value] = self.hashes(out)
            plain = self.hashes(self.frontend(rom, "--runs", 6, name=lr.KICKSTART_NAMES[0]).stdout)
            self.assertEqual(seen[value], plain, value)

    # --- the identity of the firmware ------------------------------------------------------------------------------------------------------

    def test_a_state_taken_with_another_kickstart_is_refused_and_one_taken_with_the_same_bytes_is_not(self):
        path = self.work() / "a.state"
        directory = self.system_with({"kick33180.A500": self.rom_bytes("boot"), "kick34005.A500": self.rom_bytes("input_all")})
        saved = self.frontend("boot", "--runs", 8, "--set", "puae6_kickstart=kick33180.A500", "--do", f"5:savefile:{path}", system=directory).stdout
        self.assertTrue(SAVE.search(saved)[2] == "1")
        control = self.frontend("input_all", "--runs", 9, "--set", "puae6_kickstart=kick34005.A500", system=directory).stdout
        refused = self.frontend("input_all", "--runs", 9, "--set", "puae6_kickstart=kick34005.A500", "--do", f"5:loadfile:{path}", system=directory).stdout
        self.assertEqual(LOAD.search(refused)[3], "0", "a state of the 1.2 in a machine that runs the 1.3")
        self.assertEqual([r["hash"] for r in self.runs(refused)], [r["hash"] for r in self.runs(control)])
        # The same bytes under the other version's name are the same firmware: the identity is the content, not the name or the option.
        same = self.system_with({"kick34005.A500": self.rom_bytes("boot")})
        accepted = self.frontend("boot", "--runs", 9, "--set", "puae6_kickstart=kick34005.A500", "--do", f"5:loadfile:{path}:pos=5", system=same).stdout
        self.assertEqual(LOAD.search(accepted)[3], "1")

    # --- Amiga Forever's rom.key -----------------------------------------------------------------------------------------------------------

    def test_an_encrypted_rom_is_decrypted_with_the_rom_key_and_boots_exactly_as_the_plain_one(self):
        plain = self.rom_bytes("boot")
        control = self.hashes(self.frontend("boot", "--runs", 8).stdout)
        for length in (1, 2, 7, 255, 256, 1584, 4096, 65536):          # the key is a whole file of any length, repeated from its first byte; 64 KiB is the most the adapter reads
            with self.subTest(key_bytes=length):
                directory = self.system_with({"amiga-os-130.rom": cloanto(plain, key_of(length)), "rom.key": key_of(length)})
                out = self.frontend("boot", "--runs", 8, system=directory).stdout
                self.assertIn("Kickstart amiga-os-130.rom (decrypted with rom.key), ", out)
                self.assertEqual(self.hashes(out), control)
                if length >= 8:
                    self.assertNotIn(key_of(length)[:8].hex(), out)           # the key is never logged

    def test_the_key_file_may_be_called_rom_key_in_capitals_and_the_same_key_serves_every_machine(self):
        for machine, (option, names, _, _) in lr.MACHINES.items():
            with self.subTest(machine=machine):
                key = key_of(1584, machine)
                directory = self.system_with({names[1]: cloanto(self.rom_bytes("boot"), key), "ROM.KEY": key})
                out = self.frontend("boot", "--runs", 3, *option, system=directory).stdout
                self.assertIn(f"Kickstart {names[1]} (decrypted with rom.key), ", out)

    def test_an_encrypted_rom_has_the_identity_of_the_plain_one_so_a_state_moves_between_them(self):
        key = key_of(1584)
        path = self.work() / "e.state"
        encrypted = self.system_with({"amiga-os-130.rom": cloanto(self.rom_bytes("boot"), key), "rom.key": key})
        saved = self.frontend("boot", "--runs", 8, "--do", f"5:savefile:{path}", system=encrypted).stdout
        self.assertEqual(SAVE.search(saved)[2], "1")
        plain = self.system("boot")
        accepted = self.frontend("boot", "--runs", 9, "--do", f"5:loadfile:{path}:pos=5", system=plain).stdout
        self.assertEqual(LOAD.search(accepted)[3], "1", "the state of the encrypted ROM in the plain one")
        other = self.frontend("input_all", "--runs", 9, "--do", f"5:loadfile:{path}", name=lr.KICKSTART_NAMES[0]).stdout
        self.assertEqual(LOAD.search(other)[3], "0", "and not in another Kickstart")

    def test_an_encrypted_rom_that_cannot_be_decrypted_says_why_and_never_hides_a_good_rom_under_the_next_name(self):
        plain = self.rom_bytes("boot")
        key = key_of(1584)
        encrypted = cloanto(plain, key)
        cases = {
            "no key": ({"amiga-os-130.rom": encrypted}, "amiga-os-130.rom is encrypted by Amiga Forever (AMIROMTYPE1) and the system directory has no usable rom.key"),
            "empty key": ({"amiga-os-130.rom": encrypted, "rom.key": b""}, "the system directory has no usable rom.key"),
            "key too long": ({"amiga-os-130.rom": encrypted, "rom.key": key_of(65537)}, "the system directory has no usable rom.key"),
            "wrong key": ({"amiga-os-130.rom": encrypted, "rom.key": key_of(1584, "another key")}, "the rom.key of the system directory does not decrypt it"),
            "a tag and nothing else": ({"amiga-os-130.rom": b"AMIROMTYPE1", "rom.key": key}, "does not decrypt it"),
            "a tag and three bytes": ({"amiga-os-130.rom": b"AMIROMTYPE1" + b"\x11\x11\x4e", "rom.key": key}, "does not decrypt it"),
        }
        for label, (files, text) in cases.items():
            with self.subTest(case=label):
                out = self.frontend("boot", system=self.system_with(files), expect=3).stdout
                self.assertIn("load refused", out)
                self.assertIn(text, " ".join(self.messages(out)))
                self.assertNotIn("run 1 ", out)
        # A good ROM under a later name of the same machine is used, and the encrypted one that could not be opened is only a warning.
        both = self.system_with({"amiga-os-130.rom": encrypted, lr.KICKSTART_NAMES[2]: plain})
        out = self.frontend("boot", "--runs", 3, system=both).stdout
        self.assertIn(f"Kickstart {lr.KICKSTART_NAMES[2]}, ", out)
        self.assertRegex(out, r"log 2 \[PUAE6\] amiga-os-130.rom is encrypted by Amiga Forever")

    def test_an_image_that_decrypts_to_something_that_is_not_a_kickstart_is_refused_by_the_bridge(self):
        key = key_of(1584)
        broken = bytearray(self.rom_bytes("boot"))
        broken[1000] ^= 0xFF                                                     # the header is still a Kickstart's, the checksum is not (the loader refuses it)
        out = self.frontend("boot", system=self.system_with({"amiga-os-130.rom": cloanto(bytes(broken), key), "rom.key": key}), expect=3).stdout
        self.assertIn("amiga-os-130.rom refused (firmware is not an accepted Kickstart image)", " ".join(self.messages(out)))
        short = self.frontend("boot", system=self.system_with({"amiga-os-130.rom": cloanto(self.rom_bytes("boot")[:1000], key), "rom.key": key}), expect=3).stdout
        self.assertIn("amiga-os-130.rom refused (firmware is not an accepted Kickstart image)", " ".join(self.messages(short)))

    def test_the_bridge_names_an_encrypted_image_when_an_adapter_gives_it_as_it_is(self):
        encrypted = self.work() / "encrypted.rom"
        encrypted.write_bytes(cloanto(self.rom_bytes("boot"), key_of(1584)))
        result = subprocess.run([str(self.headless), "--run", str(encrypted), "--units", "2"], capture_output=True, text=True, timeout=120)
        self.assertEqual(result.returncode, 1)
        self.assertRegex(result.stderr, r"p6_load failed \(6\): firmware is an encrypted Amiga Forever ROM \(AMIROMTYPE1\): decrypt it with its rom\.key before p6_load")


def last_picture(directory):
    """(width, height, colour counter) of the last picture a frontend dumped to `directory`."""
    files = sorted(Path(directory).glob("picture-*.bin"))
    data = files[-1].read_bytes()
    width, height = struct.unpack_from("<II", data)
    pixels = array.array("I")
    pixels.frombytes(data[16:16 + width * height * 4])
    return width, height, Counter(p & 0xFFFFFF for p in pixels)


NEEDS_ROMS = "BLOCKED: set P6_KICKSTART_ROM, P6_KICKSTART_A600_ZIP and P6_KICKSTART_A1200_ROM (the owner's Kickstarts 1.3, 2.05 and 3.1)"


@unittest.skipUnless(lr.TOOLS and KS13 and KS205_ZIP and KS31_A1200, NEEDS_ROMS)
class OwnersKickstartsTests(lr.LibretroHarness):
    """A Kickstart of another family boots, or is refused with the reason, on every machine. It checks that the machine runs (a picture on the screen that keeps moving), not compatibility."""
    UNITS = 1200
    NAMES = {"1.3": "kick34005.A500", "2.05": "kick37350.A600", "3.1": "kick40068.A1200"}

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.keep = {"1.3": Path(KS13), "3.1": Path(KS31_A1200)}
        with zipfile.ZipFile(KS205_ZIP) as archive:
            (member,) = [n for n in archive.namelist() if n.lower().endswith(".rom")]
            cls.keep["2.05"] = Path(cls.tmp.name) / "ks205.rom"      # a temporary file outside the repository, removed with the test's directory
            cls.keep["2.05"].write_bytes(archive.read(member))

    def boot(self, machine, version, runs=None):
        """One libretro session of `machine` with the owner's `version` chosen by puae6_kickstart; returns (process, dump directory)."""
        directory = self.work()
        os.symlink(self.keep[version], directory / self.NAMES[version])
        dump = self.work()
        runs = runs or self.UNITS
        result = subprocess.run([str(self.host), str(self.core), "--system", str(directory), "--runs", str(runs), "--set", f"puae6_machine={machine}", "--set",
                                 f"puae6_kickstart={self.NAMES[version]}", "--dump-pictures", str(dump), "--dump-from", str(runs)], capture_output=True, text=True, timeout=1800)
        return result, dump

    def assert_insert_disk_screen(self, machine, version):
        result, dump = self.boot(machine, version)
        self.assertEqual(result.returncode, 0, result.stdout[-600:])
        self.assertNotIn("VIOLATION", result.stdout)
        hashes = [m[1] for m in re.finditer(r"^run \d+ .* hash=(\w+) ", result.stdout, re.M)]
        self.assertEqual(len(hashes), self.UNITS)
        width, height, colours = last_picture(dump)
        self.assertGreater(len(set(hashes)), 4, f"{version} on the {machine}: the picture hardly changed")
        # The 2.0 and 3.1 insert-disk screens animate (a disk entering a drive); the 1.3 one is still, so a still last picture is only a hang when it is also empty.
        if version != "1.3":
            self.assertGreater(len(set(hashes[-200:])), 1, f"{version} on the {machine}: the last 200 units show one frozen picture")
        self.assertGreater(colours.most_common(1)[0][1], width * height // 2, f"{version} on the {machine}: no flat background")
        self.assertGreaterEqual(len(colours), 4, f"{version} on the {machine}: nothing on the screen besides the background")

    def test_an_alternative_kickstart_boots_on_machines_that_do_not_have_it_by_default(self):
        for machine, version in (("A500", "2.05"), ("A600", "1.3"), ("A500PLUS", "1.3"), ("A500PLUS", "2.05"), ("A500OG", "2.05"), ("A2000", "1.3"), ("A2000", "2.05"), ("A1000", "1.3"), ("A1000", "2.05"),
                                 ("A1200", "2.05"), ("A1200", "1.3")):
            with self.subTest(machine=machine, version=version):
                self.assert_insert_disk_screen(machine, version)

    def test_the_kickstart_3_1_of_the_a1200_is_refused_on_every_machine_with_a_68000_and_says_so(self):
        for machine in ("A500", "A600", "A500PLUS", "A500OG", "A1000", "A2000OG", "A2000"):
            with self.subTest(machine=machine):
                result, _ = self.boot(machine, "3.1", runs=2)
                self.assertEqual(result.returncode, 3, result.stdout[-600:])
                self.assertIn("load refused", result.stdout)
                text = " ".join(re.findall(r"(?m)^message (.*)$", result.stdout))
                self.assertIn(f'kick40068.A1200 refused (the ROM "KS ROM v3.1 (A1200)" needs a 68EC020 or later but the {machine} has a 68000: it is the firmware of another machine)', text)
                self.assertNotIn("run 1 ", result.stdout)

    def test_the_a1200_runs_its_own_kickstart_through_the_same_option(self):
        self.assert_insert_disk_screen("A1200", "3.1")


if __name__ == "__main__":
    unittest.main()
