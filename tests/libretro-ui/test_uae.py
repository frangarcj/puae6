"""T31 closure (LR01, FUZZ01): a .uae as content is read through an allowlist and becomes the options of the common schema, never a machine of its own.

adapters/libretro/config_import.cpp reads `key=value` lines of a WinUAE/PUAE configuration: the keys that change the machine and that the schema has are translated
to its reboot options, the keys that only describe the machine are checked against it, the disks are paths relative to the file, and anything that could change the
machine unnoticed (a host directory, a hard disk, a Kickstart by path, SCSI, RTG, JIT, an absolute path, any key it does not know) refuses the load with the key and the
line. The tests ask three questions:
  * is the C++ right? tests/libretro-ui/uae_probe.cpp, under ASan and UBSan, on synthetic files: every key and every refusal with its message, the bounds of the parser,
    CRLF, BOM, comments, relative paths, the precedence over the frontend's options, and a seeded mutation of valid and hostile files;
  * does the core behave the same as with the equivalent options? The minimal frontend loads the .uae and, in another process, the equivalent options: the same pictures, the
    same audio and the same saved state (whose header carries config_hash), unit for unit; a .uae that sets nothing is the frontend's options; one the importer refuses
    fails the load with the reason, before any machine exists;
  * is the documentation true? The table of docs/options.md lists the keys the importer has, and the other way round.
Run: python3 -m unittest discover -s tests/libretro-ui -p "test_uae.py"
"""
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest

HOST = Path(__file__).resolve().parents[1] / "libretro-host"
sys.path.insert(0, str(HOST))
import test_libretro as lr  # noqa: E402

ROOT = lr.ROOT
SAVE = re.compile(r"^state save (\S+) ok=(\d) size=(\d+) used=(\d+) hash=(\w+) section=(\w+)$", re.M)
KICKSTART_A600 = lr.KICKSTART_NAMES_A600[0]


@unittest.skipUnless(lr.TOOLS, "toolchain unavailable")
class UaeProbeTests(unittest.TestCase):
    """The C++ of the importer on synthetic files, compiled with ASan and UBSan next to no engine."""

    def test_the_importer_holds_its_own_checks(self):
        with tempfile.TemporaryDirectory(prefix="p6-uae-") as directory:
            exe = Path(directory) / "uae_probe"
            command = ["clang++", "-std=c++17", "-O1", "-g", "-Wall", "-Wextra", "-Werror", "-fsanitize=address,undefined", "-fno-sanitize-recover=all",
                       "-I" + str(ROOT / "adapters/libretro"), "-I" + str(ROOT / "bridge/include"), "-I" + str(ROOT / "references/libretro-api/include"),
                       "-I" + str(ROOT / "ports/portable/media"), str(ROOT / "tests/libretro-ui/uae_probe.cpp"), str(ROOT / "adapters/libretro/config_import.cpp"),
                       "-o", str(exe)]
            built = subprocess.run(command, capture_output=True, text=True, timeout=600)
            self.assertEqual(built.returncode, 0, built.stderr[-3000:])
            result = subprocess.run([str(exe)], capture_output=True, text=True, timeout=600, env={**os.environ, "ASAN_OPTIONS": "detect_leaks=0"})
            self.assertEqual(result.returncode, 0, result.stdout[-3000:] + result.stderr[-3000:])
            self.assertIn("uae probe ok", result.stdout)


@unittest.skipUnless(lr.TOOLS, "toolchain unavailable")
class UaeCoreTests(lr.LibretroHarness):

    def uae(self, text, name="game.uae", files=("a.adf",), encoding="utf-8", prefix=b""):
        """A directory with the disks and a .uae; returns the path of the .uae."""
        directory = self.work()
        for file in files:
            path = directory / file
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(lr.adf(file))
        (directory / name).write_bytes(prefix + text.encode(encoding))
        return directory / name

    def a600(self):
        return self.system("boot", KICKSTART_A600)

    def final_state(self, out):
        saves = SAVE.findall(out)
        self.assertTrue(saves, out[-800:])
        return saves[-1][2:]

    def messages(self, result):
        return re.findall(r"(?m)^message (.*)$", result.stdout)

    # --- the same machine as the equivalent options ----------------------------------------------------------------------------------------

    def test_a_uae_runs_exactly_as_the_equivalent_options(self):
        system = self.a600()
        configuration = self.uae("; the A600 of PUAE5\nchipset_compatible=A600\nchipmem_size=4\nfastmem_size=4\nfloppy0=a.adf\ngfx_width=640\n")
        via_file = self.frontend("boot", "--runs", 10, "--do", "10:save:s", system=system, content=configuration)
        adf = configuration.parent / "a.adf"
        via_options = self.frontend("boot", "--runs", 10, "--do", "10:save:s", "--set", "puae6_machine=A600", "--set", "puae6_chip_size=MB2",
                                    "--set", "puae6_fast_size=MB4", system=system, content=adf)
        runs = self.runs(via_file.stdout)
        self.assertEqual(len(runs), 10)
        self.assertEqual([(r["hash"], r["audio"], r["ahash"], r["ticks"]) for r in runs],
                         [(r["hash"], r["audio"], r["ahash"], r["ticks"]) for r in self.runs(via_options.stdout)])
        # The saved state carries config_hash: equal bytes (size, used, hash of the whole blob) mean the same configuration and the same machine.
        self.assertEqual(self.final_state(via_file.stdout)[:3], self.final_state(via_options.stdout)[:3])
        # And it is not the default machine's: the A500 with the same disk saves another state.
        default = self.frontend("boot", "--runs", 10, "--do", "10:save:s", content=adf)
        self.assertNotEqual(self.final_state(default.stdout)[:3], self.final_state(via_file.stdout)[:3])
        self.assertRegex(via_file.stdout, r"log 1 \[PUAE6\] .*game\.uae: chipset_compatible=A600 -> puae6_machine=A600")
        self.assertRegex(via_file.stdout, r"log 1 \[PUAE6\] .*game\.uae: chipmem_size=4 -> puae6_chip_size=MB2")
        self.assertRegex(via_file.stdout, r"log 2 \[PUAE6\] .*game\.uae line 6 \(gfx_width\): ignored: ")
        self.assertIn("1 key(s) of ", " ".join(self.messages(via_file)))

    def test_the_machine_of_the_file_beats_the_option_and_the_options_the_file_does_not_name_stay(self):
        system = self.a600()          # only the A600's Kickstart is in the directory: the load works only if the file's machine is the one that runs
        configuration = self.uae("chipset=ecs\ncpu_model=68000\nfloppy0=a.adf\n")
        result = self.frontend("boot", "--runs", 3, "--set", "puae6_machine=A1200", "--set", "puae6_video_standard=NTSC", system=system, content=configuration)
        self.assertIn("machine A600 (puae6_machine)", result.stdout)
        self.assertRegex(result.stdout, r"av base=\d+x484 ")      # NTSC: the frontend's option the file does not name
        # The same file with the preset of the frontend over the model (the preset is expanded first, then the file's keys refine it).
        preset = self.frontend("boot", "--runs", 3, "--set", "puae6_preset=a600_puae5", "--set", "puae6_machine=A500", system=system, content=configuration)
        self.assertIn("machine A600 (puae6_machine)", preset.stdout)

    def test_the_options_changed_later_are_reported_against_the_frontend_and_the_presentation_still_applies(self):
        system = self.a600()
        configuration = self.uae("chipset_compatible=A600\nfloppy0=a.adf\n")
        result = self.frontend("boot", "--runs", 6, "--set-at", "2:puae6_video_crop=medium", system=system, content=configuration)
        self.assertEqual([m for m in self.messages(result) if "reload the content" in m], [])
        changed = self.frontend("boot", "--runs", 6, "--set-at", "2:puae6_machine=A1200", system=system, content=configuration)
        self.assertEqual([m for m in self.messages(changed) if "reload the content" in m],
                         ["PUAE6: puae6_machine is now A1200 but the machine runs with A600: reload the content to apply it"])

    def test_the_disks_are_read_relative_to_the_file_through_the_vfs(self):
        system = self.a600()
        configuration = self.uae("chipset_compatible=A600\nfloppy0=disks/one.adf\ndiskimage0=disks/one.adf\ndiskimage1=disks/two one.adf\n",
                                 files=("disks/one.adf", "disks/two one.adf"))
        out = self.frontend("boot", "--runs", 2, "--do", "1:images", system=system, content=configuration).stdout
        directory = configuration.parent
        self.assertIn(f"vfs open {directory}/disks/one.adf", out)
        self.assertIn(f"vfs open {directory}/disks/two one.adf", out)
        self.assertIn("images count=2 index=0 eject=0", out)
        self.assertIn(f"image 0 path={directory}/disks/one.adf label=one", out)
        self.assertIn(f"image 1 path={directory}/disks/two one.adf label=two one", out)
        # Without a VFS (stdio) it is the same.
        plain = self.frontend("boot", "--runs", 2, "--no-vfs", "--do", "1:images", system=system, content=configuration).stdout
        self.assertIn("images count=2 index=0 eject=0", plain)

    def test_text_variants_load_the_same_machine(self):
        system = self.a600()
        text = "chipset_compatible=A600\nfloppy0=a.adf\nfastmem_size=2\n"
        reference = self.final_state(self.frontend("boot", "--runs", 6, "--do", "6:save:s", system=system, content=self.uae(text)).stdout)
        for name, kwargs in (("crlf", dict(text=text.replace("\n", "\r\n"))), ("bom", dict(text=text, prefix=b"\xef\xbb\xbf")),
                             ("comments", dict(text="; c\n# d\n" + text + "\n; end")), ("uppercase", dict(text=text.upper().replace("A.ADF", "a.adf")))):
            with self.subTest(variant=name):
                configuration = self.uae(**kwargs)
                result = self.frontend("boot", "--runs", 6, "--do", "6:save:s", system=system, content=configuration)
                self.assertEqual(self.final_state(result.stdout)[:3], reference[:3])

    def test_the_extension_is_offered(self):
        out = self.frontend("boot", "--runs", 1).stdout
        self.assertRegex(out, r"extensions=adf\|adz\|dms\|fdi\|zip\|7z\|rp9\|lha\|lzh\|hdf\|hdz\|m3u\|uae\|/ need_fullpath=1")

    # --- what is refused, and how -----------------------------------------------------------------------------------------------------------

    def refused(self, text, *words, **kwargs):
        configuration = self.uae(text, **kwargs)
        result = self.frontend("boot", system=self.a600(), content=configuration, expect=3)
        self.assertIn("load refused", result.stdout)
        shown = " ".join(self.messages(result))
        for word in words:
            self.assertIn(word, shown)
        return result

    def test_a_dangerous_key_fails_the_load_with_its_key_and_line(self):
        for line, key in (("filesystem2=rw,DH0:work:/home/me/work,0", "filesystem2"), ("hardfile2=rw,DH0:/x.hdf,32,1,2,512", "hardfile2"),
                          ("cdimage0=/x.iso", "cdimage0"), ("kickstart_rom_file=/roms/k.rom", "kickstart_rom_file"), ("scsi=true", "scsi"),
                          ("gfxcard_type=ZorroIII", "gfxcard_type"), ("cachesize=8192", "cachesize"), ("floppy0=/abs/a.adf", "floppy0")):
            with self.subTest(key=key):
                result = self.refused(f"chipset_compatible=A600\n{line}\n", f"line 2 ({key})", "game.uae")
                self.assertRegex(result.stdout, rf"log 3 \[PUAE6\] .*game\.uae line 2 \({key}\): ")
                self.assertNotIn("machine A600", result.stdout)         # no machine was created

    def test_an_unknown_key_is_refused_and_every_problem_is_in_the_log(self):
        result = self.refused("chipset_compatible=A600\nmystery_key=1\nfilesystem2=x\nfloppy0=../a.adf\n", "3 problem(s)", "line 2 (mystery_key)")
        errors = re.findall(r"log 3 \[PUAE6\] .*game\.uae (line \d+ \(\w+\)): ", result.stdout)
        self.assertEqual(errors, ["line 2 (mystery_key)", "line 3 (filesystem2)", "line 4 (floppy0)"])

    def test_a_disk_that_is_not_there_or_not_a_disk_fails_like_a_playlist_entry(self):
        self.refused("chipset_compatible=A600\nfloppy0=missing.adf\n", "disk 1", "missing.adf")
        self.refused("chipset_compatible=A600\nfloppy0=list.m3u\n", "disk 1", "is not an .adf")

    def test_a_file_over_the_bound_fails_without_being_read_whole(self):
        self.refused("; " + "x" * (130 * 1024) + "\n", "too large")
        self.refused("config_description=" + "x" * 5000 + "\n", "longer than 4096")

    def test_a_missing_or_unreadable_uae_fails_cleanly(self):
        result = self.frontend("boot", system=self.a600(), content=self.work() / "none.uae", expect=3)
        self.assertIn("none.uae cannot be loaded: it cannot be opened", " ".join(self.messages(result)))

    def test_a_uae_with_no_keys_is_the_frontends_options(self):
        system = self.system("boot")
        configuration = self.uae("; nothing here\n", files=())
        empty = self.frontend("boot", "--runs", 6, "--do", "6:save:s", system=system, content=configuration)
        none = self.frontend("boot", "--runs", 6, "--do", "6:save:s", system=system)
        self.assertEqual(self.final_state(empty.stdout)[:3], self.final_state(none.stdout)[:3])

    def test_load_run_unload_cycles_with_a_uae_repeat_exactly(self):
        configuration = self.uae("chipset_compatible=A600\nfloppy0=a.adf\n")
        out = self.frontend("boot", "--runs", 3, "--cycles", 3, system=self.a600(), content=configuration).stdout
        cycles = [m.groups() for m in lr.CYCLE.finditer(out)]
        self.assertEqual(len(cycles), 3)
        self.assertEqual(len({c[1:] for c in cycles}), 1, cycles)


class UaeDocumentation(unittest.TestCase):
    """docs/options.md has one row for every key the importer knows, and none the importer lacks."""

    @classmethod
    def setUpClass(cls):
        cls.docs = (ROOT / "docs/options.md").read_text(encoding="utf-8")
        cls.source = (ROOT / "adapters/libretro/config_import.cpp").read_text(encoding="utf-8")

    def section(self):
        start = self.docs.index("## Importador `.uae`")
        end = self.docs.find("\n## ", start + 1)
        return self.docs[start:end if end > 0 else len(self.docs)]

    def test_every_exact_key_of_the_code_is_in_the_table(self):
        doc = self.section()
        exact = set(re.findall(r'^\s+\{"([a-z0-9_.]+)", (?:"|k[A-Za-z]+|[0-9a-z])', self.source, re.M))
        handled = set(re.findall(r'key == "([a-z0-9_]+)"', self.source))
        native = set(re.findall(r'"(puae6_[a-z_]+)"', self.source.split("kNativeKeys[]")[1].split("};")[0]))
        for key in sorted(exact | handled | native):
            self.assertTrue(key in doc, f"{key} is a key of the importer and docs/options.md does not say what it does")

    def test_every_prefix_rule_is_in_the_table(self):
        doc = self.section()
        for prefix in re.findall(r'^\s+\{"([a-z0-9_.]+)", true, Kind::', self.source, re.M):
            self.assertTrue(prefix in doc, f"the importer has a rule for {prefix}* and docs/options.md does not mention it")

    def test_the_section_states_the_policy_and_the_precedence(self):
        doc = self.section()
        for text in ("Precedencia", "allowlist", "se rechaza", "aviso", "128 KiB", "4096"):
            self.assertTrue(text in doc, f"the section lacks: {text}")


if __name__ == "__main__":
    unittest.main()
