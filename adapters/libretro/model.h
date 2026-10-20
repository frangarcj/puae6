// The automatic model and the tags in the names of the files (T76): how puae6_machine=auto and puae6_video_standard=PAL auto / NTSC auto become a machine and a video
// standard before the machine exists. PUAE5 does the same with puae_model=auto and the path of the content (libretro-core.c retro_create_config, retro_config_force_region);
// MacHawk takes the model from its sync setting and has no tags, so the idea is PUAE5's and the code is new.
//
// The resolution is a PURE function of the options as read (Settings), the content type (hard disk or direct CD), and the list of names to look at. It reads no file,
// no clock, no environment and no locale; the same inputs give the same machine on every host, and the result is a p6_config like any other (so it enters config_hash and the
// state identity: a state of a content whose tag picked another machine is refused by p6_state_load, like a state of another machine).
//
// Which names, and in which order, is the caller's rule (libretro_core.cpp): the path of the content, then each disk the content names in playlist order (an archive's members
// after their archive). Precedence, fixed and documented in docs/options.md ("Modelo automatico y etiquetas del nombre"):
//   1. An explicit option wins: a model or a preset other than Automatic is never changed by a tag (PUAE5 reads the tags only on Automatic); a video standard other than the
//      automatic ones likewise. Tags are still looked at, so that the log can say that one was not used.
//   2. Model, strong tags: names are tried in order and the FIRST name that holds one decides; inside a name the tag listed first in the table below wins (PUAE5's order, a name
//      that holds (A1200) and (A500) is an A1200). A tag not supported for automatic selection (68030, 68040) is a warning and leaves the model to the options: it does not let a
//      weaker tag of the same or another name choose another machine in its place.
//   3. Model, weak words (AGA, AmigaCD, ECS, OCS): only if no name holds a strong tag, again the first name that holds one; they move the model only when the model the content
//      type would have is not already of that chipset (PUAE5).
//   4. Else the model of the content type: puae6_model_hd when the content has a hard disk (an .hdf, an .hdz, a volume), puae6_model_fd otherwise.
//   5. Video standard: the first name with NTSC / (USA) or with PAL / (Europe) / ...; NTSC wins inside one name (PUAE5). Else the standard the option names (PAL auto, NTSC auto).
// A bare word (PAL, NTSC, AGA, ECS, OCS, AmigaCD, CD32, CDTV) must not touch a letter or a digit on either side ("PALADIN" is not PAL; PUAE5's strstr finds it); a tag with
// parentheses or an underscore marks itself. Matching is case sensitive and byte by byte: nothing depends on the locale.
//
// Unsupported automatic tags are warnings and never an error or another machine: (A4030) (030) (A4040) (040) CDTV _Arcadia.
// (CE) forces cycle-exact CPU timing even when a faster level was selected in the menu.
#ifndef P6_LIBRETRO_MODEL_H
#define P6_LIBRETRO_MODEL_H

#include "options.h"
#include "p6.h"

#include <stddef.h>
#include <stdint.h>
#include <string.h>
#include <string>
#include <vector>

namespace p6_libretro {

struct ModelResolution {
    p6_options::Settings settings;       // the options with the model, the video standard and the memory a tag implies made concrete
    bool tags_read = false;              // the tags were looked at (File Name Tags on)
    bool multidrive = false;
    bool force_cycle_exact = false;
    std::vector<std::string> notes;      // what decided, for the log (one line each)
    std::vector<std::string> warnings;   // what the person should know: a tag this core cannot honour
};

namespace model_detail {

inline bool alnum(char c) { return (c >= '0' && c <= '9') || (c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z'); }

// Is `tag` in `name`? A bare word must not touch a letter or a digit; a parenthesized one (and "_Arcadia") is found anywhere.
inline bool has_tag(const std::string &name, const char *tag, bool bare)
{
    const size_t length = strlen(tag);
    for (size_t at = name.find(tag); at != std::string::npos; at = name.find(tag, at + 1)) {
        if (!bare) return true;
        const bool before = at == 0 || !alnum(name[at - 1]);
        const bool after = at + length >= name.size() || !alnum(name[at + length]);
        if (before && after) return true;
    }
    return false;
}

enum class Effect { Machine, Preset, Unsupported };

struct ModelTag {
    const char *text;
    bool bare;
    Effect effect;
    uint32_t value;              // the machine (Machine), the preset (Preset: PUAE5's A1200 and A600 carry their memory) or unused
    const char *meaning;         // for the log and the warning
};

// PUAE5's order (retro_create_config); the machines this core has that PUAE5 has no tag for come last (each is unambiguous: they have parentheses).
constexpr ModelTag kModelTags[] = {
    {"(A4030)", false, Effect::Unsupported, 0, "the A4000/030"},
    {"(030)", false, Effect::Unsupported, 0, "the A4000/030"},
    {"(A4040)", false, Effect::Unsupported, 0, "the A4000/040"},
    {"(040)", false, Effect::Unsupported, 0, "the A4000/040"},
    {"(A1200OG)", false, Effect::Machine, P6_MACHINE_A1200, "the A1200"},
    {"(A1200NF)", false, Effect::Machine, P6_MACHINE_A1200, "the A1200"},
    {"(A1200)", false, Effect::Preset, p6_options::kPresetA1200Fast8, "the A1200 with 8 MiB of Fast RAM"},
    {"CD32", true, Effect::Preset, p6_options::kPresetA1200Fast8, "the A1200 with 8 MiB of Fast RAM"},
    {"(CD32NF)", false, Effect::Preset, p6_options::kPresetA1200Fast8, "the A1200 with 8 MiB of Fast RAM"},        // PUAE5's strstr finds CD32 inside these two
    {"(CD32FR)", false, Effect::Preset, p6_options::kPresetA1200Fast8, "the A1200 with 8 MiB of Fast RAM"},
    {"(A600)", false, Effect::Preset, p6_options::kPresetA600Puae5, "the A600 with 2 MiB of Chip RAM and 8 MiB of Fast RAM"},
    {"(A500+)", false, Effect::Machine, P6_MACHINE_A500PLUS, "the A500+"},
    {"(A500PLUS)", false, Effect::Machine, P6_MACHINE_A500PLUS, "the A500+"},
    {"(A500OG)", false, Effect::Machine, P6_MACHINE_A500OG, "the A500 OG"},
    {"(512K)", false, Effect::Machine, P6_MACHINE_A500OG, "the A500 OG"},
    {"(512KB)", false, Effect::Machine, P6_MACHINE_A500OG, "the A500 OG"},
    {"(A500)", false, Effect::Machine, P6_MACHINE_A500, "the A500"},
    {"(A2000OG)", false, Effect::Machine, P6_MACHINE_A2000OG, "the A2000 OG"},
    {"(A2000)", false, Effect::Machine, P6_MACHINE_A2000, "the A2000"},
    {"(A1000)", false, Effect::Machine, P6_MACHINE_A1000, "the A1000"},
};

// A direct CD has its own PUAE5 selection path; floppy CD32 tags keep the A1200 fallback above.
constexpr ModelTag kCdModelTags[] = {
    {"(CD32FR)", false, Effect::Preset, p6_options::kPresetCd32Fast8, "the CD32 with 8 MiB of Fast RAM"},
    {"FastRAM", true, Effect::Preset, p6_options::kPresetCd32Fast8, "the CD32 with 8 MiB of Fast RAM"},
    {"(CD32)", false, Effect::Machine, P6_MACHINE_CD32, "the stock CD32"},
    {"(CD32NF)", false, Effect::Machine, P6_MACHINE_CD32, "the stock CD32"},
    {"CD32", true, Effect::Machine, P6_MACHINE_CD32, "the stock CD32"},
};

// The words that name a chipset (PUAE5): they move the model only when the model the content type would have is not already of that chipset.
enum class Chipset { Aga, Ecs, Ocs };
struct WeakWord {
    const char *text;
    Chipset chipset;
};
constexpr WeakWord kWeakWords[] = {{"AGA", Chipset::Aga}, {"AmigaCD", Chipset::Aga}, {"ECS", Chipset::Ecs}, {"OCS", Chipset::Ocs}};

inline bool is_chipset(uint32_t machine, Chipset chipset)
{
    switch (chipset) {
    case Chipset::Aga: return machine == P6_MACHINE_A1200 || machine == P6_MACHINE_A4030 || machine == P6_MACHINE_A4040;
    case Chipset::Ecs: return machine == P6_MACHINE_A500PLUS || machine == P6_MACHINE_A600 || machine == P6_MACHINE_A2000;        // PUAE5 only knows the A500+
    case Chipset::Ocs:        // PUAE5: any A500*, the A500+ included; the OCS machines it has no preset for are OCS as well
        return machine == P6_MACHINE_A500 || machine == P6_MACHINE_A500OG || machine == P6_MACHINE_A500PLUS || machine == P6_MACHINE_A1000 || machine == P6_MACHINE_A2000OG;
    }
    return false;
}

struct RegionTag {
    const char *text;
    bool bare;
};
constexpr RegionTag kNtscTags[] = {{"NTSC", true}, {"(USA)", false}};
constexpr RegionTag kPalTags[] = {{"PAL", true}, {"(Europe)", false}, {"(France)", false}, {"(Germany)", false}, {"(Italy)", false}, {"(Spain)", false}, {"(Finland)", false},
                                  {"(Denmark)", false}, {"(Sweden)", false}};

inline const char *machine_name(uint32_t machine)
{
    for (const p6_options::Value &value : p6_options::kMachineValues)
        if (value.number == machine) return value.name;
    return "?";
}

inline std::string shown(const std::string &name)
{
    if (name.size() <= 100) return name;      // a path is up to 4096 bytes: a line of the log is not (the end of a path is the file)
    size_t from = name.size() - 97;
    while (from < name.size() && (static_cast<unsigned char>(name[from]) & 0xC0) == 0x80) ++from;      // not in the middle of a UTF-8 sequence
    return "..." + name.substr(from);
}

}  // namespace model_detail

// Resolves `chosen` (what read_options gave, with a .uae laid over it if the content is one) for the content `names` (see above); `hard_disk` is true when the content has a
// hard disk; `direct_cd` selects the separate CD32 tag table instead of the floppy/hard-disk fallback. The result is `settings` with machine and video_standard made concrete (never Automatic) and, for tags that carry memory, the memory options still at
// their defaults replaced by the tag's.
inline ModelResolution resolve_model(const p6_options::Settings &chosen, bool hard_disk, const std::vector<std::string> &names, bool direct_cd = false)
{
    using namespace model_detail;
    ModelResolution out;
    out.settings = chosen;
    p6_options::Settings &settings = out.settings;
    out.tags_read = chosen.filename_tags != 0;
    out.multidrive = chosen.floppy_multidrive != 0;
    const bool auto_machine = chosen.machine == p6_options::kMachineAuto;
    const bool auto_video = chosen.video_standard == p6_options::kVideoStandardPalAuto || chosen.video_standard == p6_options::kVideoStandardNtscAuto;
    const uint32_t content_machine = direct_cd ? uint32_t(P6_MACHINE_CD32) : hard_disk ? chosen.model_hd : chosen.model_fd;

    // What the names hold. `strong` is the tag that decides the model (null: none), `weak` the chipset word; `decided_by` is the name that holds it.
    const ModelTag *strong = nullptr;
    const WeakWord *weak = nullptr;
    const std::string *strong_name = nullptr, *weak_name = nullptr;
    int region = 0;                       // 1 NTSC, 2 PAL
    const std::string *region_name = nullptr;
    const char *region_tag = nullptr;
    bool said_cdtv = false, said_md = false, said_arcadia = false, said_ce = false;
    if (out.tags_read) {
        for (const std::string &name : names) {
            if (!strong) {
                const ModelTag *tags = direct_cd ? kCdModelTags : kModelTags;
                const size_t count = direct_cd ? sizeof kCdModelTags / sizeof *tags : sizeof kModelTags / sizeof *tags;
                for (size_t i = 0; i < count; ++i) {
                    const ModelTag &tag = tags[i];
                    if (has_tag(name, tag.text, tag.bare)) {
                        strong = &tag;
                        strong_name = &name;
                        break;
                    }
                }
            }
            if (!weak && !direct_cd)
                for (const WeakWord &word : kWeakWords)
                    if (has_tag(name, word.text, true)) {
                        weak = &word;
                        weak_name = &name;
                        break;
                    }
            if (!region) {
                for (const RegionTag &tag : kNtscTags)
                    if (has_tag(name, tag.text, tag.bare)) {
                        region = 1, region_name = &name, region_tag = tag.text;
                        break;
                    }
                if (!region)
                    for (const RegionTag &tag : kPalTags)
                        if (has_tag(name, tag.text, tag.bare)) {
                            region = 2, region_name = &name, region_tag = tag.text;
                            break;
                        }
            }
            // What this core cannot honour is said once per tag, for the first name that holds it.
            if (!said_cdtv && has_tag(name, "CDTV", true)) {
                said_cdtv = true;
                out.warnings.push_back("the tag CDTV in " + shown(name) + " is ignored: this core has no CDTV");
            }
            if (!direct_cd && !said_md && has_tag(name, "(MD)", false)) {
                said_md = true;
                out.multidrive = true;
                out.notes.push_back("the tag (MD) in " + shown(name) + " enables MultiDrive");
            }
            if (!said_arcadia && has_tag(name, "_Arcadia", false)) {
                said_arcadia = true;
                out.warnings.push_back("the tag _Arcadia in " + shown(name) + " is ignored: this core has no Arcadia pad");
            }
            if (!direct_cd && !said_ce && has_tag(name, "(CE)", false)) {
                said_ce = true;
                out.force_cycle_exact = true;
                settings.cpu_compatibility = P6_CPU_EXACT;
                out.notes.push_back("the tag (CE) in " + shown(name) + " forces cycle-exact CPU timing");
            }
        }
    }

    // Carry filename-triggered MultiDrive into .uae validation as well as boot placement.
    settings.floppy_multidrive = out.multidrive ? 1u : 0u;

    // The model.
    if (auto_machine) {
        uint32_t machine = content_machine;
        std::string why = direct_cd ? "the content is a direct CD: CD32" : hard_disk ? "the content has a hard disk: puae6_model_hd" : "the content has no hard disk: puae6_model_fd";
        if (!out.tags_read) {
            why += " (File Name Tags is off)";
        } else if (strong) {
            if (strong->effect == Effect::Unsupported) {
                out.warnings.push_back(std::string("the tag ") + strong->text + " in " + shown(*strong_name) + " names " + strong->meaning +
                                       ", which this core does not have as an automatic tag: the tag is ignored and the model comes from the options");
            } else {
                p6_options::Settings picked = chosen;
                if (strong->effect == Effect::Preset) {
                    picked.preset = strong->value;
                    p6_options::apply_preset(&picked);
                    machine = picked.machine;
                    if (chosen.chip_size == 0u) settings.chip_size = picked.chip_size;           // the memory options the person left at their defaults
                    if (chosen.fast_size == 0u) settings.fast_size = picked.fast_size;
                } else {
                    machine = strong->value;
                }
                why = std::string("the tag ") + strong->text + " in " + shown(*strong_name) + " names " + strong->meaning;
            }
        } else if (weak) {
            if (!is_chipset(content_machine, weak->chipset)) {
                machine = weak->chipset == Chipset::Aga ? P6_MACHINE_A1200 : weak->chipset == Chipset::Ecs ? P6_MACHINE_A600 : P6_MACHINE_A500;
                why = std::string("the word ") + weak->text + " in " + shown(*weak_name) + " names a chipset the model of the content type does not have";
            } else {
                why += std::string(" (the word ") + weak->text + " in " + shown(*weak_name) + " agrees with it)";
            }
        }
        settings.machine = machine;
        out.notes.push_back(std::string("model ") + machine_name(machine) + " (puae6_machine=auto): " + why);
    } else if (strong || weak) {
        out.notes.push_back(std::string("the tag ") + (strong ? strong->text : weak->text) + " in " + shown(strong ? *strong_name : *weak_name) +
                            " is not used: the model is fixed by puae6_machine or puae6_preset");
    }

    // The video standard.
    if (auto_video) {
        uint32_t standard = chosen.video_standard == p6_options::kVideoStandardNtscAuto ? uint32_t(P6_VIDEO_NTSC) : uint32_t(P6_VIDEO_PAL);
        std::string why = "no tag: the standard of the option";
        if (region) {
            standard = region == 1 ? uint32_t(P6_VIDEO_NTSC) : uint32_t(P6_VIDEO_PAL);
            why = std::string("the tag ") + region_tag + " in " + shown(*region_name);
        } else if (!out.tags_read) {
            why += " (File Name Tags is off)";
        }
        settings.video_standard = standard;
        out.notes.push_back(std::string(standard == P6_VIDEO_NTSC ? "NTSC" : "PAL") + " (puae6_video_standard auto): " + why);
    } else if (region) {
        out.notes.push_back(std::string("the tag ") + region_tag + " in " + shown(*region_name) + " is not used: puae6_video_standard is not automatic");
    }
    return out;
}

}  // namespace p6_libretro

#endif
