// Checks of the .uae importer (adapters/libretro/config_import.cpp, T31 closure) on synthetic files, with no engine and no frontend: the keys it translates, the
// ones it refuses and the message of each, the bounds of the parser, the line ends and comments, the paths, the precedence over the frontend's options, and a
// seeded mutation of valid and hostile files under the sanitizers. Exit code 0 and "uae probe ok" when every check holds.
#include "config_import.h"

#include "p6.h"

#include <stdio.h>
#include <string.h>

using namespace p6_libretro;
using namespace p6_options;

static int failures = 0;
#define CHECK(condition)                                                          \
    do {                                                                          \
        if (!(condition)) {                                                       \
            printf("FAILED line %d: %s\n", __LINE__, #condition);                 \
            ++failures;                                                           \
        }                                                                         \
    } while (0)

static std::vector<uint8_t> bytes_of(const std::string &text) { return std::vector<uint8_t>(text.begin(), text.end()); }

struct Result {
    bool ok;
    UaeImport imported;
};

static Result import(const std::string &text, const Settings &base = Settings(), const std::string &directory = "/games/a/", size_t max_disks = 16)
{
    Result r;
    r.ok = import_uae(bytes_of(text), directory, base, max_disks, &r.imported);
    return r;
}

static bool mentions(const Result &r, const char *key, const char *words)
{
    for (const UaeReport &report : r.imported.errors)
        if (report.key == key && report.message.find(words) != std::string::npos) return true;
    return false;
}

// The p6_config that the settings make: what config_hash is a function of.
static p6_config config_of(const Settings &settings)
{
    p6_config config = {};
    to_config(settings, &config);
    return config;
}

static bool same_config(const Settings &a, const Settings &b)
{
    const p6_config x = config_of(a), y = config_of(b);
    return memcmp(&x, &y, sizeof x) == 0;
}

static void valid_keys_make_the_config_of_the_equivalent_options()
{
    struct Row {
        const char *file;
        uint32_t machine, standard, chip, slow, fast, fpu, rate, writes;
    };
    const Row rows[] = {
        {"chipset_compatible=A1200\n", 3, 1, 0, 0, 0, 0, 48000, 0},
        {"chipset_compatible=a600\nchipmem_size=4\nfastmem_size=8\n", 2, 1, 2048, 0, 8192, 0, 48000, 0},
        {"chipset=aga\ncpu_model=68020\nfpu_model=68882\n", 3, 1, 0, 0, 0, 68882, 48000, 0},
        {"chipset=ocs\nbogomem_size=2\nntsc=true\nsound_frequency=44100\n", 1, 2, 0, 512, 0, 0, 44100, 0},
        {"chipset_compatible=A500\nbogomem_size=0\nfastmem_size_k=1024\nfpu_model=0\n", 1, 1, 0, 0xFFFFFFFFu, 1024, 0, 48000, 0},
        {"chipset_compatible=A500\nbogomem_size=7\nfloppy_write_protect=false\n", 1, 1, 0, 1792, 0, 0, 48000, 1},
        {"chipset_compatible=A500\nbogomem_size=6\nchipmem_size=2\nfloppy_write_protect=true\n", 1, 1, 1024, 1536, 0, 0, 48000, 0},
        {"puae6_machine=A600\npuae6_video_standard=NTSC\npuae6_chip_size=MB1\npuae6_slow_size=None\npuae6_fast_size=MB4\npuae6_fpu_model=M68881\n"
         "puae6_audio_rate=44100\npuae6_floppy_writes=enabled\n", 2, 2, 1024, 0xFFFFFFFFu, 4096, 68881, 44100, 1},
    };
    for (const Row &row : rows) {
        Settings expected;
        expected.machine = row.machine;
        expected.video_standard = row.standard;
        expected.chip_size = row.chip;
        expected.slow_size = row.slow;
        expected.fast_size = row.fast;
        expected.fpu_model = row.fpu;
        expected.audio_rate_hz = row.rate;
        expected.floppy_writes = row.writes;
        const Result r = import(row.file);
        if (!r.ok) printf("unexpected: %s\n", describe_report(r.imported.errors[0]).c_str());
        CHECK(r.ok);
        CHECK(same_config(r.imported.settings, expected));
        CHECK(!r.imported.applied.empty());
    }
    // The preset a file names is the preset of the frontend: a600_puae5 = A600 + 2 MiB Chip + 8 MiB Fast, then the keys refine it.
    Settings preset;
    preset.preset = 2;
    apply_preset(&preset);
    CHECK(same_config(import("puae6_preset=a600_puae5\n").imported.settings, preset));
    Settings refined = preset;
    refined.fast_size = 4096;
    CHECK(same_config(import("puae6_preset=a600_puae5\nfastmem_size=4\n").imported.settings, refined));
    // T73: WinUAE's spellings of Paula's output become the options of the frontend (separation in tenths: 7 is the profile's 70 %); the profile's own values are the defaults.
    Settings audio;
    audio.sound_separation = 1;           // 0 %: abi 1 + percent / 10
    audio.sound_interpolation = 4;        // rh: abi 1 + upstream's 3
    audio.sound_filter = 1;               // off
    audio.sound_filter_type = 2;          // enhanced = the A1200 model
    CHECK(same_config(import("sound_stereo_separation=0\nsound_interpol=rh\nsound_filter=off\nsound_filter_type=enhanced\n").imported.settings, audio));
    Settings loud;
    loud.sound_separation = 11;           // 100 %
    loud.sound_interpolation = 5;         // crux
    loud.sound_filter = 3;                // always on
    loud.sound_filter_type = 1;           // standard = the A500 model
    CHECK(same_config(import("sound_stereo_separation=10\nsound_interpol=crux\nsound_filter=on\nsound_filter_type=standard\n").imported.settings, loud));
    CHECK(same_config(import("sound_stereo_separation=7\nsound_interpol=anti\nsound_filter=emulated\n").imported.settings, Settings()));
    CHECK(same_config(import("puae6_sound_stereo_separation=0\npuae6_sound_interpolation=rh\npuae6_sound_filter=off\npuae6_sound_filter_type=A1200\n").imported.settings, audio));
    // An empty file, or one with comments only, is the frontend's options.
    CHECK(import("").ok && same_config(import("").imported.settings, Settings()));
    CHECK(import("; nothing\n# nothing\n\n   \n").ok);
}

static void precedence()
{
    // The file's keys beat the frontend's options; an option the file does not name stays.
    Settings frontend;
    frontend.machine = 3;
    frontend.fast_size = 8192;
    frontend.video_standard = 2;
    Result r = import("chipset_compatible=A500\n", frontend);
    CHECK(r.ok && r.imported.settings.machine == 1 && r.imported.settings.fast_size == 8192 && r.imported.settings.video_standard == 2);
    // The frontend's own preset arrives expanded (read_options does it) and is refined by the file.
    Settings with_preset;
    with_preset.preset = 1;
    apply_preset(&with_preset);
    r = import("chipset_compatible=A600\n", with_preset);
    CHECK(r.ok && r.imported.settings.machine == 2 && r.imported.settings.fast_size == 8192);
    // The order of the lines does not matter.
    CHECK(same_config(import("fastmem_size=2\nchipset_compatible=A1200\n").imported.settings, import("chipset_compatible=A1200\nfastmem_size=2\n").imported.settings));
    // A file that names no machine leaves the frontend's machine, and the keys that describe a machine are checked against it.
    frontend = Settings();
    frontend.machine = 3;
    CHECK(import("cpu_model=68020\nchipset=aga\n", frontend).ok);
    CHECK(!import("cpu_model=68000\n", frontend).ok);
    // The presentation, input and OSD options are never touched.
    Settings busy;
    for (const Option &option : kOptions)
        if (strcmp(option.key, "puae6_machine") && !strncmp(option.key, "puae6_video_", 12)) busy.*option.field = option.values[option.count - 1].number;
    busy.statusbar = 3;
    busy.mapper_b = 5;
    busy.vkbd_theme = 4;
    busy.turbo_fire = 1;
    r = import("chipset_compatible=A1200\nntsc=true\ngfx_width=640\njoyport0=mouse\nmouse_speed=300\n", busy);
    CHECK(r.ok);
    Settings after = r.imported.settings;
    CHECK(after.statusbar == 3 && after.mapper_b == 5 && after.vkbd_theme == 4 && after.turbo_fire == 1);
    for (const Option &option : kOptions) {
        const bool semantic = !strcmp(option.key, "puae6_machine") || !strcmp(option.key, "puae6_video_standard") || !strcmp(option.key, "puae6_preset");
        if (!semantic) CHECK(after.*option.field == busy.*option.field);
    }
    CHECK(r.imported.warnings.size() == 3);         // gfx_width, joyport0, mouse_speed
}

static void dangerous_keys_are_refused_with_their_key_and_line()
{
    struct Row {
        const char *line, *key, *words;
    };
    const Row rows[] = {
        {"filesystem2=rw,DH0:work:/home/me/work,0", "filesystem2", "host directories"},
        {"filesystem=rw,DH0:/x", "filesystem", "host directories"},
        {"hardfile2=rw,DH0:/disks/a.hdf,32,1,2,512,0,,uae0", "hardfile2", "hard disk images"},
        {"hardfile=rw,32,1,2,512,/x.hdf", "hardfile", "hard disk images"},
        {"uaehf0=hdf,rw,DH0:/disks/a.hdf,32,1,2,512,0,,uae0", "uaehf0", "hard disk images"},
        {"cdimage0=/games/cd.iso", "cdimage0", "no CD drive"},
        {"kickstart_rom_file=/roms/kick.rom", "kickstart_rom_file", "system directory"},
        {"kickstart_rom_file=kick.rom", "kickstart_rom_file", "system directory"},
        {"kickstart_ext_rom_file=ext.rom", "kickstart_ext_rom_file", "system directory"},
        {"cart_file=ar.rom", "cart_file", "system directory"},
        {"flash_file=nvram.bin", "flash_file", "system directory"},
        {"scsi=true", "scsi", "no SCSI"},
        {"scsi_a3000=true", "scsi_a3000", "no SCSI"},
        {"gfxcard_type=ZorroIII", "gfxcard_type", "no RTG"},
        {"gfxcard_size=16", "gfxcard_size", "no RTG"},
        {"rtg_nocustom=true", "rtg_nocustom", "no RTG"},
        {"picasso96_nocustom=true", "picasso96_nocustom", "no RTG"},
        {"cachesize=8192", "cachesize", "JIT"},
        {"compfpu=true", "compfpu", "JIT"},
        {"comp_constjump=true", "comp_constjump", "JIT"},
        {"cpu_speed=max", "cpu_speed", "real clock"},
        {"cpu_throttle=50", "cpu_throttle", "real clock"},
        {"cpu_multiplier=8", "cpu_multiplier", "fixed by the machine profile"},
        {"cpu_frequency=14", "cpu_frequency", "fixed by the machine profile"},
        {"cpu_cycle_exact=false", "cpu_cycle_exact", "cycle exact"},
        {"cpu_compatible=false", "cpu_compatible", "cycle exact"},
        {"cycle_exact=false", "cycle_exact", "cycle exact"},
        {"mmu_model=68030", "mmu_model", "no MMU"},
        {"z3mem_size=64", "z3mem_size", "24-bit bus"},
        {"mbresmem_size=8", "mbresmem_size", "24-bit bus"},
        {"a3000mem_size=4", "a3000mem_size", "24-bit bus"},
        {"fastmem2_size=4", "fastmem2_size", "one Zorro II"},
        {"immediate_blits=true", "immediate_blits", "compatibility shortcut"},
        {"waiting_blits=automatic", "waiting_blits", "compatibility shortcut"},
        {"floppy_speed=0", "floppy_speed", "turbo floppy"},
        {"synchronize_clock=true", "synchronize_clock", "never follows the host"},
        {"bsdsocket_emu=true", "bsdsocket_emu", "network"},
        {"cpuboard_type=Blizzard1260", "cpuboard_type", "expansion boards"},
        {"serial_port=COM1", "serial_port", "host devices"},
        {"parallel_port=LPT1", "parallel_port", "host devices"},
        {"pcmcia=true", "pcmcia", "PCMCIA"},
        {"include=other.uae", "include", "include another file"},
        {"plugin=x.dll", "plugin", "plugin"},
        {"collision_level=full", "collision_level", "fixed by the machine profile"},
        {"rtc=MSM6242B", "rtc", "fixed by the machine profile"},
        {"sound_mixed_stereo_delay=2", "sound_mixed_stereo_delay", "fixed by the machine profile"},
        {"sound_interpol=sinc", "sound_interpol", "sinc interpolation is not offered"},
        {"sound_interpol=fast", "sound_interpol", "expected none, anti, rh or crux"},
        {"sound_filter=fixedonly", "sound_filter", "fixedonly is not offered"},
        {"sound_filter_type=odd", "sound_filter_type", "expected standard"},
        {"sound_stereo_separation=11", "sound_stereo_separation", "expected 0 to 10"},
        {"sound_stereo_separation=-1", "sound_stereo_separation", "expected 0 to 10"},
        {"cs_ide=true", "cs_ide", "fixed by the machine profile"},
        {"nr_floppies=4", "nr_floppies", "DF0 and DF1"},
        {"floppy0type=1", "floppy0type", "3.5"},
        {"floppy0wp=true", "floppy0wp", "floppy_write_protect"},
        {"floppy1=disk.adf", "floppy1", "DF0 alone"},
        {"cpu_model=68040", "cpu_model", "not the one of the machine"},
        {"cpu_model=68000\nchipset_compatible=A1200", "cpu_model", "A1200: 68020"},
        {"chipset=aga\nchipset_compatible=A500", "chipset", "A500: ocs"},
        {"chipset=ecs_agnus", "chipset", "not one of this core's"},
        {"chipset_compatible=A4000", "chipset_compatible", "A500, A600, A1200"},
        {"chipset_compatible=CD32", "chipset_compatible", "A500, A600, A1200"},
        {"fpu_model=68040", "fpu_model", "0, 68881 or 68882"},
        {"fpu_model=abc", "fpu_model", "expected a number"},
        {"chipmem_size=-1", "chipmem_size", "128 KiB"},
        {"chipmem_size=0", "chipmem_size", "256 KiB"},
        {"chipmem_size=3", "chipmem_size", "1536 KiB of Chip RAM is not offered"},
        {"bogomem_size=1", "bogomem_size", "256 KiB of Slow RAM is not offered"},
        {"fastmem_size=16", "fastmem_size", "16 MiB of Fast RAM is not offered"},
        {"sound_frequency=22050", "sound_frequency", "22050 Hz is not offered"},
        {"ntsc=maybe", "ntsc", "true or false"},
        {"totally_unknown=1", "totally_unknown", "unknown key"},
        {"kbd_layout=us", "kbd_layout", "unknown key"},
        {"puae6_video_crop=maximum", "puae6_video_crop", "presentation"},
        {"puae6_nothing=1", "puae6_nothing", "unknown key"},
        {"puae6_machine=A4000", "puae6_machine", "allowed: A500, A600, A1200"},
        {"puae6_preset=none2", "puae6_preset", "allowed:"},
        {"floppy0=/abs/disk.adf", "floppy0", "absolute"},
        {"floppy0=C:\\disks\\a.adf", "floppy0", "absolute"},
        {"floppy0=\\\\server\\share\\a.adf", "floppy0", "absolute"},
        {"floppy0=file://x/a.adf", "floppy0", "absolute"},
        {"floppy0=../a.adf", "floppy0", "leaves the folder"},
        {"floppy0=sub/../../a.adf", "floppy0", "leaves the folder"},
        {"floppy0=sub\\..\\a.adf", "floppy0", "leaves the folder"},
        {"floppy0=~/a.adf", "floppy0", "'~'"},
        {"floppy0=$(FILE_PATH)/a.adf", "floppy0", "variable"},
        {"floppy0=${HOME}/a.adf", "floppy0", "variable"},
        {"diskimage16=a.adf", "diskimage16", "diskimage0 .. diskimage15"},
        {"diskimagex=a.adf", "diskimagex", "diskimage0 .. diskimage15"},
        {"diskimage01=a.adf", "diskimage01", "diskimage0 .. diskimage15"},
        {"diskimage0=a.adf\nDISKIMAGE0=b.adf", "diskimage0", "twice"},
    };
    for (const Row &row : rows) {
        const Result r = import(std::string("; header\n") + row.line + "\n");
        if (r.ok || !mentions(r, row.key, row.words)) {
            printf("not refused as expected: %s\n", row.line);
            for (const UaeReport &report : r.imported.errors) printf("  %s\n", describe_report(report).c_str());
        }
        CHECK(!r.ok && mentions(r, row.key, row.words));
        CHECK(!r.imported.errors.empty());
    }
    // The line number names the line (comments and blank lines count).
    const Result r = import("; one\n\nfilesystem2=rw,x:/y\n");
    CHECK(!r.ok && r.imported.errors.size() == 1 && r.imported.errors[0].line == 3);
    CHECK(describe_report(r.imported.errors[0]).rfind("line 3 (filesystem2): not supported: ", 0) == 0);
    // Every problem of the file is reported, not only the first.
    const Result many = import("filesystem2=a\nscsi=true\nhardfile2=b\ncdimage0=c\n");
    CHECK(!many.ok && many.imported.errors.size() == 4);
}

static void harmless_keys_are_accepted()
{
    // A path key with no value mounts nothing; the keys that describe the machine as it is, a metadata line and a zero of an absent board are fine.
    const char *file =
        "config_description=my A1200\nconfig_version=5.0.0\nconfig_hardware=true\nconfig_host=true\n"
        "chipset_compatible=A1200\nchipset=aga\ncpu_model=68020\ncpu_cycle_exact=true\ncpu_memory_cycle_exact=true\nblitter_cycle_exact=true\ncpu_compatible=true\n"
        "cpu_24bit_addressing=true\ncpu_speed=real\ncachesize=0\ncompfpu=false\nmmu_model=0\nimmediate_blits=false\nwaiting_blits=disabled\nfloppy_speed=100\n"
        "nr_floppies=2\nfloppy0type=0\nfloppy1type=0\nfloppy2type=-1\nfloppy3type=-1\nfloppy0wp=false\nscsi=false\n"
        "z3mem_size=0\nz3mem_start=0x10000000\nmbresmem_size=0\nfastmem2_size=0\nfilesystem2=\ncdimage0=\nkickstart_rom_file=\nbsdsocket_emu=false\n"
        "synchronize_clock=no\nfloppy1=\nfloppy2=\nfloppy3=\n";
    const Result r = import(file);
    if (!r.ok) printf("unexpected: %s\n", describe_report(r.imported.errors[0]).c_str());
    CHECK(r.ok);
    CHECK(r.imported.settings.machine == 3);
    CHECK(r.imported.warnings.empty());
    // The keys of the host's window, the input and the LEDs are ignored with a warning that names the key.
    const Result noisy = import("gfx_width=800\ngfx_fullscreen_amiga=true\nwin32.logfile=true\njoyport1=joy1\ninput.1.joystick.0.axis.0=0\nmouse_speed=200\nshow_leds=true\n"
                                "leds_on_screen=1\nuse_gui=no\nmagic_mouse=true\nkbd_lang=us\nrom_path=/roms\nfloppy_path=/disks\n");
    CHECK(noisy.ok && noisy.imported.warnings.size() == 13);
    CHECK(noisy.imported.warnings[0].key == "gfx_width" && noisy.imported.warnings[0].line == 1);
    CHECK(same_config(noisy.imported.settings, Settings()));
}

static void the_text_is_read_like_a_configuration_file()
{
    const char *keys = "chipset_compatible=A600\nchipmem_size=2\nntsc=yes\n";
    Settings expected;
    expected.machine = 2;
    expected.chip_size = 1024;
    expected.video_standard = 2;
    CHECK(same_config(import(keys).imported.settings, expected));
    // CRLF, CR, a final line without end, a BOM, whitespace, tabs, a quoted value, comments with ; and #.
    const std::string variants[] = {
        "chipset_compatible=A600\r\nchipmem_size=2\r\nntsc=yes\r\n",
        "chipset_compatible=A600\rchipmem_size=2\rntsc=yes\r",
        "chipset_compatible=A600\nchipmem_size=2\nntsc=yes",
        std::string("\xEF\xBB\xBF") + keys,
        "  chipset_compatible = A600\n\tchipmem_size\t=\t2  \n ntsc = yes \n",
        "chipset_compatible=\"A600\"\nchipmem_size=\"2\"\nntsc=\"yes\"\n",
        "; a comment\n# another = 1\nchipset_compatible=A600\n   ; indented comment\nchipmem_size=2\n\n\nntsc=yes\n",
        "CHIPSET_COMPATIBLE=a600\nChipMem_Size=2\nNTSC=YES\n",
    };
    for (const std::string &variant : variants) {
        const Result r = import(variant);
        if (!r.ok) printf("variant refused: %s\n", describe_report(r.imported.errors[0]).c_str());
        CHECK(r.ok && same_config(r.imported.settings, expected));
    }
    // Line numbers count every kind of line end once (CRLF is one end).
    Result r = import("a=1\r\nb=2\rfilesystem2=x\n");
    CHECK(!r.ok && mentions(r, "filesystem2", "host directories") && r.imported.errors.back().line == 3);
    // Not text.
    CHECK(!import(std::string("chipmem_size=2\0\n", 16)).ok);
    CHECK(!import("chipmem_size=2\x01\n").ok);
    CHECK(!import("chipmem_size=2\x7F\n").ok);
    CHECK(!import("no equals sign\n").ok);
    CHECK(!import("=value\n").ok);
    CHECK(!import("bad key=1\n").ok);
    CHECK(!import("key with $ = 1\n").ok);
    CHECK(import("empty_value=\n").ok == false);        // unknown key, whatever the value
    // Duplicate keys that name one option are refused (the order of lines must not matter); repeating a harmless key is fine.
    CHECK(!import("chipmem_size=2\nchipmem_size=4\n").ok);
    CHECK(mentions(import("chipmem_size=2\nchipmem_size=2\n"), "chipmem_size", "again"));
    CHECK(mentions(import("chipset_compatible=A500\npuae6_machine=A500\n"), "puae6_machine", "again"));
    CHECK(import("config_description=a\nconfig_description=b\ngfx_width=1\ngfx_width=2\n").ok);
}

static void the_bounds_hold()
{
    // Size of the file, length of a line, number of lines.
    std::string big(kMaxUaeBytes + 1, ';');
    CHECK(!import(big).ok && mentions(import(big), "", "larger than"));
    std::string full;
    for (size_t i = 0; i < kMaxUaeLines; ++i) full += std::string(kMaxUaeBytes / kMaxUaeLines - 1, ';') + "\n";
    CHECK(full.size() == kMaxUaeBytes && import(full).ok);
    std::string long_line = "config_description=" + std::string(kMaxUaeLineBytes, 'x') + "\n";
    CHECK(!import(long_line).ok && import(long_line).imported.errors[0].line == 1);
    std::string exact = "config_description=" + std::string(kMaxUaeLineBytes - 19, 'x') + "\n";
    CHECK(import(exact).ok);
    std::string lines;
    for (size_t i = 0; i < kMaxUaeLines; ++i) lines += "\n";
    CHECK(import(lines).ok);
    CHECK(!import(lines + "\n").ok && mentions(import(lines + "\n"), "", "more than"));
    // The reports are bounded and counted.
    std::string bad;
    for (int i = 0; i < 500; ++i) bad += "nonsense" + std::to_string(i) + "=1\n";
    const Result r = import(bad);
    CHECK(!r.ok && r.imported.errors.size() == kMaxUaeReports && r.imported.errors_omitted == 500 - kMaxUaeReports);
    std::string noisy;
    for (int i = 0; i < 100; ++i) noisy += "gfx_x" + std::to_string(i) + "=1\n";
    const Result w = import(noisy);
    CHECK(w.ok && w.imported.warnings.size() == kMaxUaeReports && w.imported.warnings_omitted == 100 - kMaxUaeReports);
    // The number of disks.
    std::string disks;
    for (int i = 0; i < 16; ++i) disks += "diskimage" + std::to_string(i) + "=d" + std::to_string(i) + ".adf\n";
    CHECK(import(disks).ok && import(disks).imported.disks.size() == 16);
    CHECK(!import(disks + "floppy0=extra.adf\n").ok && mentions(import(disks + "floppy0=extra.adf\n"), "", "at most 16"));
    CHECK(!import(disks, Settings(), "", 8).ok);
    // A huge number in a size key is refused, not wrapped.
    CHECK(!import("chipmem_size=99999999999999999999\n").ok);
    CHECK(!import("fastmem_size=4294967297\n").ok);
    CHECK(!import("bogomem_size=-2\n").ok);
}

static void the_disks_are_paths_relative_to_the_file()
{
    Result r = import("floppy0=game.adf\n", Settings(), "/games/a/");
    CHECK(r.ok && r.imported.disks.size() == 1 && r.imported.disks[0].path == "/games/a/game.adf" && r.imported.disks[0].label == "game");
    r = import("floppy0=disks/game.adf\n", Settings(), "");
    CHECK(r.ok && r.imported.disks[0].path == "disks/game.adf");
    r = import("floppy0=sub\\game one.adf\n", Settings(), "vfs://base/");
    CHECK(r.ok && r.imported.disks[0].path == "vfs://base/sub\\game one.adf" && r.imported.disks[0].label == "game one");
    // DF0 first, then the swap list in number order whatever the order of the file, without repeating DF0's file; an empty entry is no disk.
    r = import("diskimage3=d.adf\ndiskimage1=b.adf\nfloppy0=a.adf\ndiskimage0=a.adf\ndiskimage2=\ndiskimage10=e.adf\n");
    CHECK(r.ok && r.imported.disks.size() == 4);
    CHECK(r.imported.disks[0].path == "/games/a/a.adf" && r.imported.disks[1].path == "/games/a/b.adf" && r.imported.disks[2].path == "/games/a/d.adf" &&
          r.imported.disks[3].path == "/games/a/e.adf");
    // Without floppy0 the swap list starts the playlist; no disk at all is a machine that boots its Kickstart alone.
    r = import("diskimage1=b.adf\ndiskimage0=a.adf\n");
    CHECK(r.ok && r.imported.disks.size() == 2 && r.imported.disks[0].path == "/games/a/a.adf");
    CHECK(import("floppy0=\n").ok && import("floppy0=\n").imported.disks.empty());
    // A name with dots that is not "..".
    CHECK(import("floppy0=a..b.adf\n").ok && import("floppy0=.hidden/x.adf\n").ok && import("floppy0=...\n").ok);
}

// A tiny deterministic generator; the mutation of files does not depend on the host.
struct Random {
    uint64_t state;
    uint32_t next()
    {
        state = state * UINT64_C(6364136223846793005) + UINT64_C(1442695040888963407);
        return uint32_t(state >> 33);
    }
    uint32_t below(uint32_t n) { return n ? next() % n : 0; }
};

static void whatever_the_bytes_the_answer_is_consistent()
{
    const std::string seeds[] = {
        "chipset_compatible=A1200\nchipmem_size=4\nfastmem_size=8\nfloppy0=a.adf\ndiskimage1=b.adf\nntsc=true\n",
        "; x\r\nfilesystem2=rw,DH0:a:/b,0\r\nhardfile2=rw,DH1:/c.hdf\r\nscsi=true\r\n",
        "\xEF\xBB\xBF" "chipset=ocs\ncpu_model=68000\nbogomem_size=2\nfloppy_write_protect=false\ngfx_width=1\n",
        "puae6_preset=a600_puae5\nfpu_model=68881\nsound_frequency=44100\nfloppy0=\"x y.adf\"\n",
    };
    const char *words[] = {"floppy0=", "diskimage", "..", "/", "\\", "=", "\n", "\r\n", "$(", "~", "chipset_compatible=A", "fastmem_size=", "999999999999", "-", "\"", ";", "0", "A1200"};
    Random random{UINT64_C(0x5EED5EED)};
    unsigned accepted = 0, refused = 0;
    for (unsigned iteration = 0; iteration < 20000; ++iteration) {
        std::string text = seeds[random.below(4)];
        const unsigned edits = 1 + random.below(6);
        for (unsigned e = 0; e < edits && !text.empty(); ++e) {
            const size_t at = random.below(uint32_t(text.size()));
            switch (random.below(5)) {
            case 0: text[at] = char(random.below(256)); break;
            case 1: text.erase(at, 1 + random.below(8)); break;
            case 2: text.insert(at, words[random.below(sizeof words / sizeof words[0])]); break;
            case 3: text.insert(at, 1, char(random.below(256))); break;
            default: text += text.substr(at, 1 + random.below(40)); break;
            }
        }
        Settings base;
        base.machine = 1 + random.below(3);
        const Result r = import(text, base, "/d/");
        CHECK(r.ok == r.imported.errors.empty());
        CHECK(r.imported.errors.size() <= kMaxUaeReports && r.imported.warnings.size() <= kMaxUaeReports);
        if (r.ok) {
            ++accepted;
            CHECK(r.imported.disks.size() <= 16);
            for (const PlaylistEntry &disk : r.imported.disks) {
                CHECK(disk.path.compare(0, 3, "/d/") == 0 && disk.path.size() > 3);
                const std::string relative = disk.path.substr(3);
                CHECK(!is_absolute(relative));
                size_t start = 0;
                while (start <= relative.size()) {         // no component is ".."
                    size_t stop = start;
                    while (stop < relative.size() && !is_separator(relative[stop])) ++stop;
                    CHECK(relative.compare(start, stop - start, "..") != 0);
                    start = stop + 1;
                }
            }
            // What was read is a machine the schema has.
            const Settings &s = r.imported.settings;
            CHECK(s.machine >= 1 && s.machine <= 3);
            CHECK(s.video_standard == 1 || s.video_standard == 2);
            CHECK(s.audio_rate_hz == 44100 || s.audio_rate_hz == 48000);
        } else {
            ++refused;
        }
    }
    CHECK(accepted > 200 && refused > 200);        // both outcomes are exercised
    // Pure noise.
    for (unsigned iteration = 0; iteration < 3000; ++iteration) {
        std::vector<uint8_t> noise(random.below(600));
        for (uint8_t &b : noise) b = uint8_t(random.below(256));
        UaeImport out;
        const bool ok = import_uae(noise, "/d/", Settings(), 16, &out);
        CHECK(ok == out.errors.empty());
    }
}

int main()
{
    valid_keys_make_the_config_of_the_equivalent_options();
    precedence();
    dangerous_keys_are_refused_with_their_key_and_line();
    harmless_keys_are_accepted();
    the_text_is_read_like_a_configuration_file();
    the_bounds_hold();
    the_disks_are_paths_relative_to_the_file();
    whatever_the_bytes_the_answer_is_consistent();
    if (failures) {
        printf("%d check(s) failed\n", failures);
        return 1;
    }
    printf("uae probe ok\n");
    return 0;
}
