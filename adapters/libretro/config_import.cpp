// The .uae importer (see config_import.h for the policy). Nothing here touches the engine, the ABI or the host: it reads bytes and writes a Settings.
// The keys and the values follow WinUAE's cfgfile.cpp (the units of chipmem_size, bogomem_size and fastmem_size, the spelling of chipset_compatible), which
// is what PUAE5 reads; the code is written for PUAE6 and reuses no line of either.
#include "config_import.h"

#include "options.h"

#include <ctype.h>
#include <string.h>
#include <algorithm>
#include <map>
#include <utility>

namespace p6_libretro {
namespace {

struct Entry {
    unsigned line;
    std::string key;       // lower case
    std::string value;     // trimmed, one pair of surrounding double quotes removed
};

std::string lower(std::string text)
{
    for (char &c : text) c = char(tolower(static_cast<unsigned char>(c)));
    return text;
}

bool starts_with(const std::string &text, const char *prefix) { return text.compare(0, strlen(prefix), prefix) == 0; }

// WinUAE's booleans: yes/true and no/false (any case); on/off are not WinUAE's and are not accepted.
bool parse_bool(const std::string &value, bool *out)
{
    const std::string v = lower(value);
    if (v == "true" || v == "yes") *out = true;
    else if (v == "false" || v == "no") *out = false;
    else return false;
    return true;
}

// Decimal digits, an optional leading minus when `negative_ok`, at most nine digits: no overflow and no surprises (WinUAE also reads 0x... and "true", which a
// size key has no use for).
bool parse_int(const std::string &value, bool negative_ok, int64_t *out)
{
    size_t at = 0;
    bool negative = false;
    if (negative_ok && !value.empty() && value[0] == '-') {
        negative = true;
        at = 1;
    }
    if (at >= value.size() || value.size() - at > 9) return false;
    int64_t number = 0;
    for (; at < value.size(); ++at) {
        if (value[at] < '0' || value[at] > '9') return false;
        number = number * 10 + (value[at] - '0');
    }
    *out = negative ? -number : number;
    return true;
}

// --- the rules for the keys that are not translated -------------------------------------------------------------------------------------------------

constexpr const char *kHostDisk =
    "host directories and hard disk images are not mounted from a configuration (the deterministic profile has no host filesystem; hard disks are media the core is given)";
constexpr const char *kCd = "there is no CD drive in the machines of this core";
constexpr const char *kRom = "the Kickstart is looked for in the system directory under the names of the chosen machine, never by a path in a configuration";
constexpr const char *kRtg = "there is no RTG graphics board";
constexpr const char *kJit = "the deterministic profile has no JIT";
constexpr const char *kBoards = "expansion boards, CPU boards and host devices are not part of the machine profiles";
constexpr const char *kProfile =
    "this value is fixed by the machine profile and is not a key of the option schema; the importer cannot tell whether the file agrees with it, so it refuses it (remove the line)";
constexpr const char *kExec = "a .uae cannot include another file, load a plugin or run a program";
constexpr const char *kZ3 = "Zorro III and motherboard RAM do not exist: every machine here has a 24-bit bus (only 0 is accepted)";
constexpr const char *kHostWindow = "a host window or input setting: the frontend and the core's own options decide it";

enum class Kind { Reject, Zero, Ignore, Metadata };

struct Rule {
    const char *key;
    bool prefix;
    Kind kind;
    bool empty_ok;            // a path key with no value mounts nothing, so it changes nothing
    const char *text;
};

// First match wins: exact keys go before the prefixes that would swallow them.
constexpr Rule kRules[] = {
    {"z3mem_start", false, Kind::Metadata, false, ""},
    {"z3mapping", false, Kind::Metadata, false, ""},
    {"rom_path", false, Kind::Ignore, false, "host paths are not used: the Kickstart comes from the system directory"},
    {"floppy_path", false, Kind::Ignore, false, "host paths are not used: the disks are paths relative to the .uae"},
    {"filesystem", true, Kind::Reject, true, kHostDisk},
    {"hardfile", true, Kind::Reject, true, kHostDisk},
    {"uaehf", true, Kind::Reject, true, kHostDisk},
    {"cdimage", true, Kind::Reject, true, kCd},
    {"kickstart", true, Kind::Reject, true, kRom},
    {"cart", true, Kind::Reject, true, kRom},
    {"flash_file", true, Kind::Reject, true, kRom},
    {"gfxcard", true, Kind::Reject, false, kRtg},
    {"rtg", true, Kind::Reject, false, kRtg},
    {"picasso96", true, Kind::Reject, false, kRtg},
    {"uaegfx", true, Kind::Reject, false, kRtg},
    {"comp_", true, Kind::Reject, false, kJit},
    {"scsi_", true, Kind::Reject, false, "there is no SCSI controller in the machines of this core"},
    {"cpuboard", true, Kind::Reject, false, kBoards},
    {"ppc", true, Kind::Reject, false, kBoards},
    {"a2065", true, Kind::Reject, false, kBoards},
    {"a2091", true, Kind::Reject, false, kBoards},
    {"a4091", true, Kind::Reject, false, kBoards},
    {"catweasel", true, Kind::Reject, false, kBoards},
    {"toccata", true, Kind::Reject, false, kBoards},
    {"x86", true, Kind::Reject, false, kBoards},
    {"bsdsocket", true, Kind::Reject, false, "there is no network in the deterministic profile"},
    {"uaenet", true, Kind::Reject, false, "there is no network in the deterministic profile"},
    {"uaeboard", true, Kind::Reject, false, kBoards},
    {"serial_", true, Kind::Reject, false, kBoards},
    {"parallel_", true, Kind::Reject, false, kBoards},
    {"midi", true, Kind::Reject, false, kBoards},
    {"sampler", true, Kind::Reject, false, kBoards},
    {"pcmcia", true, Kind::Reject, false, "there is no PCMCIA slot (the A600 and the A1200 of this core have no Gayle)"},
    {"genlock", true, Kind::Reject, false, kBoards},
    {"include", true, Kind::Reject, false, kExec},
    {"plugin", true, Kind::Reject, false, kExec},
    {"shell", true, Kind::Reject, false, kExec},
    {"exec", true, Kind::Reject, false, kExec},
    {"script", true, Kind::Reject, false, kExec},
    {"cmd", true, Kind::Reject, false, kExec},
    {"command", true, Kind::Reject, false, kExec},
    {"z3mem", true, Kind::Zero, false, kZ3},
    {"z3fastmem", true, Kind::Zero, false, kZ3},
    {"mem25bit", true, Kind::Zero, false, kZ3},
    {"a3000mem", true, Kind::Zero, false, kZ3},
    {"mbresmem", true, Kind::Zero, false, kZ3},
    {"megachipmem", true, Kind::Zero, false, kZ3},
    {"fastmem2", true, Kind::Zero, false, "only one Zorro II Fast RAM board is offered (only 0 is accepted for the others)"},
    {"fastmem3", true, Kind::Zero, false, "only one Zorro II Fast RAM board is offered (only 0 is accepted for the others)"},
    {"fastmem4", true, Kind::Zero, false, "only one Zorro II Fast RAM board is offered (only 0 is accepted for the others)"},
    {"cpu_", true, Kind::Reject, false, kProfile},
    {"fpu_", true, Kind::Reject, false, kProfile},
    {"cs_", true, Kind::Reject, false, kProfile},
    {"chipset_", true, Kind::Reject, false, kProfile},
    {"blitter_", true, Kind::Reject, false, kProfile},
    {"agnus_", true, Kind::Reject, false, kProfile},
    {"denise_", true, Kind::Reject, false, kProfile},
    {"collision_level", false, Kind::Reject, false, kProfile},
    {"rtc", false, Kind::Reject, false, kProfile},
    {"sound_", true, Kind::Reject, false, kProfile},
    {"keyboard_", true, Kind::Reject, false, kProfile},
    {"floppy", true, Kind::Reject, false, kProfile},
    {"gfx_", true, Kind::Ignore, false, kHostWindow},
    {"win32.", true, Kind::Ignore, false, kHostWindow},
    {"input.", true, Kind::Ignore, false, "input is mapped by the core's own options (RetroPad Mapper)"},
    {"joyport", true, Kind::Ignore, false, "the ports are the core's controller types, a choice of the frontend"},
    {"mouse_", true, Kind::Ignore, false, "input is mapped by the core's own options (Input)"},
    {"magic_mouse", true, Kind::Ignore, false, "input is mapped by the core's own options (Input)"},
    {"show_leds", true, Kind::Ignore, false, "the LEDs are drawn by the core's statusbar option"},
    {"leds_on_screen", true, Kind::Ignore, false, "the LEDs are drawn by the core's statusbar option"},
    {"use_gui", true, Kind::Ignore, false, kHostWindow},
    {"kbd_lang", true, Kind::Ignore, false, "the keyboard layout is the frontend's"},
    {"config_", true, Kind::Metadata, false, ""},
};

// Keys whose value must be one of a few spellings because the machine is that way (all lower case; a boolean is compared as "true" or "false").
struct Fixed {
    const char *key;
    const char *accepted;     // '|' separated
    const char *text;
};

constexpr const char *kCycleExact = "the engine is cycle exact only";
constexpr Fixed kFixed[] = {
    {"cpu_cycle_exact", "true", kCycleExact},
    {"cpu_memory_cycle_exact", "true", kCycleExact},
    {"blitter_cycle_exact", "true", kCycleExact},
    {"cycle_exact", "true", kCycleExact},
    {"cpu_compatible", "true", kCycleExact},
    {"cpu_24bit_addressing", "true", "every machine here has a 24-bit bus"},
    {"cpu_address_space_24", "true", "every machine here has a 24-bit bus"},
    {"cpu_speed", "real", "the machine runs at its real clock; the host speed never changes the emulation"},
    {"cpu_throttle", "0|0.0", "the machine runs at its real clock; the host speed never changes the emulation"},
    {"cachesize", "0", kJit},
    {"compfpu", "false", kJit},
    {"mmu_model", "0", "there is no MMU"},
    {"immediate_blits", "false", "a compatibility shortcut that is not the hardware"},
    {"waiting_blits", "disabled|false|no|0", "a compatibility shortcut that is not the hardware"},
    {"synchronize_clock", "false", "the clock never follows the host (see the schema's rtc_epoch_seconds)"},
    {"bsdsocket_emu", "false", "there is no network in the deterministic profile"},
    {"scsi", "false", "there is no SCSI controller in the machines of this core"},
    {"nr_floppies", "2", "the machine has DF0 and DF1 and no more (libretro drives DF0 alone)"},
    {"floppy0type", "0", "DF0 is a 3.5\" double density drive"},
    {"floppy1type", "0", "DF1 is a 3.5\" double density drive"},
    {"floppy2type", "-1", "DF2 does not exist"},
    {"floppy3type", "-1", "DF3 does not exist"},
    {"floppy0wp", "false", "write protection is one switch for every disk: use floppy_write_protect"},
    {"floppy1wp", "false", "write protection is one switch for every disk: use floppy_write_protect"},
    {"floppy2wp", "false", "write protection is one switch for every disk: use floppy_write_protect"},
    {"floppy3wp", "false", "write protection is one switch for every disk: use floppy_write_protect"},
};

// The native spelling of a reboot option: the libretro key, exactly as the frontend's menu has it. The presentation, input and OSD options are not here.
constexpr const char *kNativeKeys[] = {
    "puae6_machine",  "puae6_preset",     "puae6_video_standard", "puae6_chip_size",     "puae6_slow_size",
    "puae6_fast_size", "puae6_fpu_model", "puae6_audio_rate",     "puae6_floppy_writes", "puae6_sound_stereo_separation",
    "puae6_sound_interpolation", "puae6_sound_filter", "puae6_sound_filter_type", "puae6_floppy_speed", "puae6_video_resolution", "puae6_video_line_mode",
};

const p6_options::Option *find_option(const char *key)
{
    for (const p6_options::Option &option : p6_options::kOptions)
        if (strcmp(option.key, key) == 0) return &option;
    return nullptr;
}

// What a machine of the schema is, for the keys that describe it rather than choose it.
struct MachineFacts {
    const char *name;
    const char *chipset;
    const char *cpu;
};
constexpr MachineFacts kMachines[] = {{"A500", "ocs", "68000"}, {"A600", "ecs", "68000"}, {"A1200", "aga", "68020"},
                                      {"A500OG", "ocs", "68000"}, {"A500PLUS", "ecs", "68000"}, {"A1000", "ocs", "68000"}, {"A2000OG", "ocs", "68000"}, {"A2000", "ecs_agnus", "68000"}};
// The spellings of chipset_compatible (WinUAE's cscompa) that name a machine. "A2000" names two: the A2000 OG with the OCS chipset and the A2000 with the ECS Agnus (see finish).
struct Spelling {
    const char *spelling;
    const char *machine;
};
constexpr Spelling kSpellings[] = {{"a500", "A500"}, {"a500+", "A500PLUS"}, {"a600", "A600"}, {"a1000", "A1000"}, {"a1200", "A1200"}, {"a2000", "A2000"}};

class Importer {
public:
    Importer(const p6_options::Settings &base, const std::string &directory, size_t max_disks, UaeImport *out)
        : directory_(directory), max_disks_(max_disks), out_(out)
    {
        *out_ = UaeImport();
        out_->settings = base;
    }

    bool run(const std::vector<uint8_t> &bytes)
    {
        std::vector<Entry> entries;
        parse(bytes, &entries);
        if (!out_->errors.empty()) return false;       // a file that is not even text is not read further
        apply_named_preset(entries);
        for (const Entry &entry : entries) dispatch(entry);
        finish();
        return out_->errors.empty();
    }

private:
    const std::string &directory_;
    size_t max_disks_;
    UaeImport *out_;
    std::map<std::string, unsigned> claimed_;          // option key -> the line that set it
    const Entry *chipset_ = nullptr, *cpu_ = nullptr, *a2000_ = nullptr;
    const Entry *floppy0_ = nullptr;
    std::vector<std::pair<unsigned, const Entry *>> images_;

    void error(unsigned line, const std::string &key, const std::string &message)
    {
        if (out_->errors.size() < kMaxUaeReports) out_->errors.push_back({line, key, message});
        else ++out_->errors_omitted;
    }

    void warn(unsigned line, const std::string &key, const std::string &message)
    {
        if (out_->warnings.size() < kMaxUaeReports) out_->warnings.push_back({line, key, message});
        else ++out_->warnings_omitted;
    }

    void error(const Entry &e, const std::string &message) { error(e.line, e.key, message); }

    // --- the text ------------------------------------------------------------------------------------------------------------------------------

    void parse(const std::vector<uint8_t> &bytes, std::vector<Entry> *entries)
    {
        if (bytes.size() > kMaxUaeBytes) {
            error(0, "", "the file is larger than " + std::to_string(kMaxUaeBytes) + " bytes");
            return;
        }
        size_t at = 0;
        if (bytes.size() >= 3 && bytes[0] == 0xEF && bytes[1] == 0xBB && bytes[2] == 0xBF) at = 3;
        unsigned line_number = 0;
        while (at < bytes.size()) {
            size_t end = at;
            while (end < bytes.size() && bytes[end] != '\n' && bytes[end] != '\r') ++end;
            const unsigned line = ++line_number;
            const size_t length = end - at;
            const size_t start = at;
            at = end;
            if (at < bytes.size() && bytes[at] == '\r' && at + 1 < bytes.size() && bytes[at + 1] == '\n') ++at;
            ++at;
            if (line > kMaxUaeLines) {
                error(0, "", "the file has more than " + std::to_string(kMaxUaeLines) + " lines");
                return;
            }
            if (length > kMaxUaeLineBytes) {
                error(line, "", "the line is longer than " + std::to_string(kMaxUaeLineBytes) + " bytes");
                continue;
            }
            std::string text(bytes.begin() + std::ptrdiff_t(start), bytes.begin() + std::ptrdiff_t(start + length));
            bool clean = true;
            for (unsigned char c : text)
                if ((c < 0x20 && c != '\t') || c == 0x7F) clean = false;
            if (!clean) {
                error(line, "", "the line has a control character (or a NUL byte): this is not a text configuration");
                continue;
            }
            size_t first = 0, last = text.size();
            while (first < last && (text[first] == ' ' || text[first] == '\t')) ++first;
            while (last > first && (text[last - 1] == ' ' || text[last - 1] == '\t')) --last;
            text = text.substr(first, last - first);
            if (text.empty() || text[0] == ';' || text[0] == '#') continue;
            const size_t equals = text.find('=');
            if (equals == std::string::npos) {
                error(line, "", "expected key=value");
                continue;
            }
            std::string key = text.substr(0, equals), value = text.substr(equals + 1);
            while (!key.empty() && (key.back() == ' ' || key.back() == '\t')) key.pop_back();
            size_t value_first = 0;
            while (value_first < value.size() && (value[value_first] == ' ' || value[value_first] == '\t')) ++value_first;
            value.erase(0, value_first);
            if (key.empty()) {
                error(line, "", "the line has no key before the '='");
                continue;
            }
            bool key_ok = true;
            for (unsigned char c : key)
                if (!(isalnum(c) || c == '_' || c == '.' || c == '-')) key_ok = false;
            if (!key_ok) {
                error(line, key, "not a valid key (letters, digits, '_', '.' and '-')");
                continue;
            }
            if (value.size() >= 2 && value.front() == '"' && value.back() == '"') value = value.substr(1, value.size() - 2);
            entries->push_back({line, lower(key), value});
        }
    }

    // --- the options ---------------------------------------------------------------------------------------------------------------------------

    // Claims an option for a key of the file: two keys (or the same key twice) naming one option are refused, because which one wins would depend on the order.
    bool claim(const Entry &e, const char *option)
    {
        const auto found = claimed_.find(option);
        if (found != claimed_.end()) {
            error(e, std::string("sets ") + option + " again (it was set by line " + std::to_string(found->second) + ")");
            return false;
        }
        claimed_[option] = e.line;
        return true;
    }

    void note(const Entry &e, const std::string &target) { out_->applied.push_back(e.key + "=" + e.value + " -> " + target); }

    bool set_name(const Entry &e, const char *option_key, const std::string &name, bool record = true)
    {
        const p6_options::Option *option = find_option(option_key);
        if (!option) {
            error(e, std::string("the schema has no option ") + option_key + " (a defect of the importer)");
            return false;
        }
        for (unsigned i = 0; i < option->count; ++i) {
            if (name != option->values[i].name) continue;
            const uint32_t number = option->values[i].number;
            if ((!strcmp(option_key, "puae6_machine") && number == p6_options::kMachineAuto) ||
                (!strcmp(option_key, "puae6_video_standard") && (number == p6_options::kVideoStandardPalAuto || number == p6_options::kVideoStandardNtscAuto))) {
                // T76: the automatic values are resolved by the core from the name of the .uae before its keys are read; a file that cares names the machine and the standard.
                error(e, std::string("\"") + name + "\" is the frontend's automatic choice, not a value for a file: name the machine or the standard (" + option_key + " takes the others)");
                return false;
            }
            if (!claim(e, option_key)) return false;
            out_->settings.*option->field = number;
            if (record) note(e, std::string(option_key) + "=" + name);
            return true;
        }
        error(e, std::string("\"") + name + "\" is not a value of " + option_key + " (allowed: " + allowed_values(*option) + ")");
        return false;
    }

    bool set_number(const Entry &e, const char *option_key, uint64_t number, const std::string &what)
    {
        const p6_options::Option *option = find_option(option_key);
        if (!option) {
            error(e, std::string("the schema has no option ") + option_key + " (a defect of the importer)");
            return false;
        }
        for (unsigned i = 0; i < option->count; ++i) {
            if (option->values[i].number != number) continue;
            if (!claim(e, option_key)) return false;
            out_->settings.*option->field = option->values[i].number;
            note(e, std::string(option_key) + "=" + option->values[i].name);
            return true;
        }
        error(e, what + " is not offered by this core (" + option_key + " allows: " + allowed_values(*option) + ")");
        return false;
    }

    // The preset a file names goes first, as a bundle the individual keys may then refine: the same rule the frontend's own preset obeys over the model
    // and memory options, one level up.
    void apply_named_preset(const std::vector<Entry> &entries)
    {
        for (const Entry &e : entries) {
            if (e.key != "puae6_preset") continue;
            const p6_options::Option *option = find_option("puae6_preset");
            for (unsigned i = 0; option && i < option->count; ++i) {
                if (e.value != option->values[i].name) continue;
                if (!claim(e, "puae6_preset")) return;
                out_->settings.preset = option->values[i].number;
                p6_options::apply_preset(&out_->settings);
                note(e, std::string("puae6_preset=") + e.value);
                return;
            }
            error(e, "\"" + e.value + "\" is not a value of puae6_preset (allowed: " + (option ? allowed_values(*option) : std::string("?")) + ")");
            return;
        }
    }

    bool check_path(const Entry &e, std::string *why) const
    {
        const std::string &v = e.value;
        if (is_absolute(v)) {
            *why = "an absolute path; give the disk relative to the folder of the .uae";
            return false;
        }
        if (v[0] == '~') {
            *why = "a path starting with '~' (the importer expands nothing); give the disk relative to the folder of the .uae";
            return false;
        }
        if (v.find("$(") != std::string::npos || v.find("${") != std::string::npos) {
            *why = "a variable (the importer expands nothing); give the disk relative to the folder of the .uae";
            return false;
        }
        size_t start = 0;
        while (start <= v.size()) {
            size_t stop = start;
            while (stop < v.size() && !is_separator(v[stop])) ++stop;
            if (v.compare(start, stop - start, "..") == 0) {
                *why = "a path that leaves the folder of the .uae (\"..\")";
                return false;
            }
            start = stop + 1;
        }
        return true;
    }

    void dispatch(const Entry &e)
    {
        const std::string &key = e.key;
        bool flag = false;
        int64_t number = 0;

        // The native spelling of the reboot options.
        if (starts_with(key, "puae6_")) {
            if (key == "puae6_preset") return;       // applied before everything else
            for (const char *native : kNativeKeys) {
                if (key != native) continue;
                set_name(e, native, e.value);
                return;
            }
            if (find_option(key.c_str())) error(e, "is an option of the frontend's menu for the presentation, the input or the OSD; a .uae sets the machine only");
            else error(e, "unknown key");
            return;
        }

        if (key == "chipset_compatible") {
            const std::string v = lower(e.value);
            if (v == "-" || v == "generic") return;         // names no machine
            for (const Spelling &spelling : kSpellings) {
                if (v != spelling.spelling) continue;
                if (v == "a2000") a2000_ = &e;                 // the chipset line tells the A2000 OG from the A2000: decided in finish()
                else set_name(e, "puae6_machine", spelling.machine);
                return;
            }
            error(e, "the machine \"" + e.value + "\" is not one of this core's (A500, A500+, A600, A1000, A1200, A2000)");
            return;
        }
        if (key == "chipset") {
            chipset_ = &e;
            return;
        }
        if (key == "cpu_model") {
            cpu_ = &e;
            return;
        }
        if (key == "fpu_model") {
            if (!parse_int(e.value, false, &number)) error(e, "expected a number (0, 68881 or 68882)");
            else if (number == 0) set_name(e, "puae6_fpu_model", "None");
            else if (number == 68881) set_name(e, "puae6_fpu_model", "M68881");
            else if (number == 68882) set_name(e, "puae6_fpu_model", "M68882");
            else error(e, "FPU model " + e.value + " is not offered (0, 68881 or 68882)");
            return;
        }
        if (key == "chipmem_size") {
            if (!parse_int(e.value, true, &number)) error(e, "expected a number of 512 KiB units");
            else if (number < 0) error(e, "Chip RAM of 128 KiB is not offered by this core");
            else if (number == 0) error(e, "Chip RAM of 256 KiB is not offered by this core");
            else if (number > 1024) error(e, "Chip RAM beyond 512 MiB is not offered by this core");
            else set_number(e, "puae6_chip_size", uint64_t(number) * 512, std::to_string(number * 512) + " KiB of Chip RAM");
            return;
        }
        if (key == "bogomem_size") {
            if (!parse_int(e.value, false, &number) || number > 1024) error(e, "expected a number of 256 KiB units");
            else if (number == 0) set_name(e, "puae6_slow_size", "None");
            else set_number(e, "puae6_slow_size", uint64_t(number) * 256, std::to_string(number * 256) + " KiB of Slow RAM");
            return;
        }
        if (key == "fastmem_size" || key == "fastmem_size_k") {
            const bool kib = key == "fastmem_size_k";
            if (!parse_int(e.value, false, &number) || number > (kib ? 1048576 : 1024)) error(e, kib ? "expected a number of KiB" : "expected a number of MiB");
            else if (number == 0) set_name(e, "puae6_fast_size", "None");
            else set_number(e, "puae6_fast_size", uint64_t(number) * (kib ? 1 : 1024), std::to_string(number) + (kib ? " KiB" : " MiB") + " of Fast RAM");
            return;
        }
        if (key == "sound_frequency") {
            if (!parse_int(e.value, false, &number)) error(e, "expected a number of Hz");
            else set_number(e, "puae6_audio_rate", uint64_t(number), std::to_string(number) + " Hz");
            return;
        }
        // Paula's output (T73): WinUAE's own spellings of the four settings the schema offers. The mixed stereo delay, the volumes and the rest of sound_* stay refused below.
        if (key == "sound_stereo_separation") {
            if (!parse_int(e.value, false, &number) || number > 10) error(e, "expected 0 to 10 (tenths of 100 %)");
            else set_name(e, "puae6_sound_stereo_separation", std::to_string(number * 10));
            return;
        }
        if (key == "sound_interpol") {
            const std::string v = lower(e.value);
            if (v == "sinc") error(e, "sinc interpolation is not offered: its queue is not part of the native state (none, anti, rh and crux are)");
            else if (v == "none" || v == "anti" || v == "rh" || v == "crux") set_name(e, "puae6_sound_interpolation", v);
            else error(e, "expected none, anti, rh or crux (sinc is not offered)");
            return;
        }
        if (key == "sound_filter") {
            const std::string v = lower(e.value);
            if (v == "off" || v == "emulated" || v == "on") set_name(e, "puae6_sound_filter", v);
            else error(e, "expected off, emulated or on (fixedonly is not offered)");
            return;
        }
        // The floppy drive speed (T62): upstream's floppy_speed as a percentage of the real drive, 0 being the turbo mode. WinUAE's slider offers 100, 200, 400 and 800.
        if (key == "floppy_speed") {
            if (!parse_int(e.value, false, &number)) error(e, "expected 0 (turbo), 100, 200, 400 or 800");
            else if (number == 0) set_name(e, "puae6_floppy_speed", "turbo");
            else if (number == 100 || number == 200 || number == 400 || number == 800) set_name(e, "puae6_floppy_speed", std::to_string(number));
            else error(e, "floppy speed " + e.value + " is not offered (0 for turbo, 100, 200, 400 or 800)");
            return;
        }
        // The raster (T72): upstream's gfx_resolution and gfx_linemode, which are the engine's own gfx_resolution and gfx_vresolution. Scanlines are an effect of the host's display and are not offered.
        if (key == "gfx_resolution") {
            const std::string v = lower(e.value);
            if (v == "lores" || v == "hires" || v == "superhires") set_name(e, "puae6_video_resolution", v);
            else error(e, "expected lores, hires or superhires");
            return;
        }
        if (key == "gfx_linemode") {
            const std::string v = lower(e.value);
            if (v == "none") set_name(e, "puae6_video_line_mode", "single");
            else if (v == "double") set_name(e, "puae6_video_line_mode", "double");
            else error(e, "expected none (single lines) or double: scanlines and the other double modes are effects of the host's display, which the core does not draw");
            return;
        }
        if (key == "sound_filter_type") {
            const std::string v = lower(e.value);
            if (v == "standard") set_name(e, "puae6_sound_filter_type", "A500");
            else if (v == "enhanced") set_name(e, "puae6_sound_filter_type", "A1200");
            else error(e, "expected standard (the A500 model) or enhanced (the A1200 model)");
            return;
        }
        if (key == "ntsc") {
            if (!parse_bool(e.value, &flag)) error(e, "expected true or false");
            else set_name(e, "puae6_video_standard", flag ? "NTSC" : "PAL");
            return;
        }
        if (key == "floppy_write_protect") {
            if (!parse_bool(e.value, &flag)) error(e, "expected true or false");
            else set_name(e, "puae6_floppy_writes", flag ? "disabled" : "enabled");
            return;
        }
        if (key == "floppy0") {
            floppy0_ = &e;
            return;
        }
        if (key == "floppy1" || key == "floppy2" || key == "floppy3") {
            if (!e.value.empty()) error(e, "libretro drives DF0 alone (the machine's other drives are not given disks); name more disks with diskimage0 .. diskimage15 or in an .m3u");
            return;
        }
        if (starts_with(key, "diskimage")) {
            const std::string digits = key.substr(9);
            if (!parse_int(digits, false, &number) || number >= int64_t(kMaxUaeDiskImages) || (digits.size() > 1 && digits[0] == '0')) {
                error(e, "only diskimage0 .. diskimage" + std::to_string(kMaxUaeDiskImages - 1) + " exist");
                return;
            }
            for (const auto &image : images_) {
                if (image.first == unsigned(number)) {
                    error(e, "is named twice (line " + std::to_string(image.second->line) + ")");
                    return;
                }
            }
            images_.push_back({unsigned(number), &e});
            return;
        }

        for (const Fixed &fixed : kFixed) {
            if (key != fixed.key) continue;
            std::string value = lower(e.value);
            if (parse_bool(value, &flag)) value = flag ? "true" : "false";
            const char *accepted = fixed.accepted;
            while (*accepted) {
                const char *bar = strchr(accepted, '|');
                const size_t length = bar ? size_t(bar - accepted) : strlen(accepted);
                if (value.size() == length && value.compare(0, length, accepted, length) == 0) return;
                accepted += length + (bar ? 1 : 0);
            }
            error(e, std::string("\"") + e.value + "\" is not accepted: " + fixed.text + " (accepted: " + fixed.accepted + ")");
            return;
        }

        for (const Rule &rule : kRules) {
            if (rule.prefix ? !starts_with(key, rule.key) : key != rule.key) continue;
            switch (rule.kind) {
            case Kind::Reject:
                if (rule.empty_ok && e.value.empty()) return;
                error(e, std::string("not supported: ") + rule.text);
                return;
            case Kind::Zero: {
                bool zero = parse_int(e.value, false, &number) && number == 0;
                if (!zero && parse_bool(e.value, &flag) && !flag) zero = true;
                if (!zero) error(e, std::string("not supported: ") + rule.text);
                return;
            }
            case Kind::Ignore:
                warn(e.line, key, std::string("ignored: ") + rule.text);
                return;
            case Kind::Metadata:
                return;
            }
        }
        error(e, "unknown key: the importer reads only the keys listed in docs/options.md, because it cannot tell that any other leaves the machine alone");
    }

    void finish()
    {
        // The machine: named by the file (chipset_compatible, puae6_machine, a preset) or, when the file names none, by its chipset. A chipset and a CPU that
        // the file also gives only describe the machine and must agree with it.
        if (a2000_)         // chipset_compatible = A2000: the OCS chipset is the A2000 OG, the ECS Agnus (PUAE5's A2000) the A2000
            set_name(*a2000_, "puae6_machine", chipset_ && lower(chipset_->value) == "ocs" ? "A2000OG" : "A2000");
        const bool named = claimed_.count("puae6_machine") || claimed_.count("puae6_preset");
        std::string chipset;
        if (chipset_) {
            chipset = lower(chipset_->value);
            const char *machine = chipset == "ocs" ? "A500" : chipset == "ecs" ? "A600" : chipset == "aga" ? "A1200" : nullptr;
            if (chipset == "ecs_agnus") {
                // The A2000's chipset (T50): it checks a machine the file names (chipset_compatible = A2000) but does not choose one alone, since it says nothing of the Kickstart or the memory.
                if (!named) {
                    error(*chipset_, "the chipset ecs_agnus names no machine by itself: add chipset_compatible=A2000");
                    chipset.clear();
                }
            } else if (!machine) {
                error(*chipset_, "the chipset \"" + chipset_->value + "\" is not one of this core's (ocs, ecs_agnus, ecs, aga)");
                chipset.clear();
            } else if (!named) {
                set_name(*chipset_, "puae6_machine", machine);
            }
        }
        const p6_options::Option *machines = find_option("puae6_machine");
        const MachineFacts *facts = nullptr;
        for (const MachineFacts &machine : kMachines)
            if (machines && strcmp(option_value_name(*machines, out_->settings.machine), machine.name) == 0) facts = &machine;
        if (facts) {
            if (!chipset.empty() && chipset != facts->chipset)
                error(*chipset_, std::string("the chipset ") + chipset_->value + " is not the one of the machine (" + facts->name + ": " + facts->chipset + ")");
            if (cpu_ && lower(cpu_->value) != facts->cpu)
                error(*cpu_, "the CPU " + cpu_->value + " is not the one of the machine (" + facts->name + ": " + facts->cpu + ")");
        }

        // The disks: DF0 first, then the swap list in number order; a swap entry that is DF0's own file is not listed twice.
        std::vector<std::string> paths;
        auto add = [&](const Entry &e) {
            std::string why;
            if (!check_path(e, &why)) {
                error(e, "the path is " + why);
                return;
            }
            paths.push_back(e.value);
        };
        if (floppy0_ && !floppy0_->value.empty()) add(*floppy0_);
        std::sort(images_.begin(), images_.end(), [](const auto &a, const auto &b) { return a.first < b.first; });
        for (const auto &image : images_) {
            if (image.second->value.empty()) continue;
            if (floppy0_ && image.second->value == floppy0_->value) continue;
            add(*image.second);
        }
        if (paths.size() > max_disks_) {
            error(0, "", "the file names " + std::to_string(paths.size()) + " disks and a content holds at most " + std::to_string(max_disks_));
            return;
        }
        for (const std::string &path : paths) out_->disks.push_back({directory_ + path, without_extension(file_name(path))});
    }
};

}  // namespace

bool import_uae(const std::vector<uint8_t> &bytes, const std::string &directory, const p6_options::Settings &base, size_t max_disks, UaeImport *out)
{
    if (base.machine == p6_options::kMachineAuto || base.video_standard == p6_options::kVideoStandardPalAuto || base.video_standard == p6_options::kVideoStandardNtscAuto) {
        // The keys that describe a machine (chipset, CPU) are checked against the one the options chose, so it must exist: the core resolves the automatic model first (model.h).
        out->errors.push_back({0, "", "internal error: the importer needs a model and a video standard, not the automatic ones"});
        return false;
    }
    try {
        return Importer(base, directory, max_disks, out).run(bytes);
    } catch (const std::bad_alloc &) {
        out->errors.push_back({0, "", "not enough memory to read the file"});
        return false;
    }
}

std::string describe_report(const UaeReport &report)
{
    std::string text;
    if (report.line) text = "line " + std::to_string(report.line) + (report.key.empty() ? "" : " (" + report.key + ")") + ": ";
    return text + report.message;
}

}  // namespace p6_libretro
