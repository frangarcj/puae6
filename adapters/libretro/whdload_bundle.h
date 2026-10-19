// Pure three-role T65a bundle. No host paths, engine calls or third-party assets.
#pragma once
#include "volume.h"
#include "whdload_data.generated.h"
#include <array>
#include <iterator>

namespace p6_libretro {
struct WhdLoadBundle { std::array<std::vector<uint8_t>, 3> images; };

namespace whdload_detail {
inline void little(std::vector<uint8_t> *out, uint32_t value, unsigned bytes)
{
    for (unsigned n = 0; n < bytes; ++n) out->push_back(uint8_t(value >> (n * 8)));
}
inline std::vector<uint8_t> text(const char *value) { return {value, value + strlen(value)}; }

// Input names are AmigaDOS Latin-1. Python ZipInfo emits ASCII or UTF-8, with
// flag 0x800 only in the latter case; ordering stays that of the Latin-1 paths.
inline std::vector<uint8_t> stored_zip(const std::map<std::string, std::vector<uint8_t>> &files)
{
    using namespace whdload_data;
    std::vector<uint8_t> out, central;
    for (const auto &file : files) {
        std::string name;
        bool high = false;
        for (unsigned char c : file.first) {
            if (c < 128) name += char(c);
            else { high = true; name += char(0xc0 | (c >> 6)); name += char(0x80 | (c & 63)); }
        }
        const uint32_t offset = uint32_t(out.size()), size = uint32_t(file.second.size());
        const uint32_t crc = crc32_update(0, file.second.data(), file.second.size());
        auto fields = [&](std::vector<uint8_t> *to) {
            for (uint32_t v : {20u, high ? 0x800u : 0u, 0u, 0u, kZipDosDate}) little(to, v, 2);
            for (uint32_t v : {crc, size, size}) little(to, v, 4);
            little(to, uint32_t(name.size()), 2); little(to, 0, 2);
        };
        little(&out, 0x04034b50, 4); fields(&out);
        out.insert(out.end(), name.begin(), name.end());
        out.insert(out.end(), file.second.begin(), file.second.end());
        little(&central, 0x02014b50, 4); little(&central, kZipMadeBy, 2); fields(&central);
        for (unsigned n = 0; n < 3; ++n) little(&central, 0, 2); // comment, disk, internal attributes
        little(&central, kZipExternalAttributes, 4); little(&central, offset, 4);
        central.insert(central.end(), name.begin(), name.end());
    }
    const uint32_t offset = uint32_t(out.size());
    out.insert(out.end(), central.begin(), central.end());
    little(&out, 0x06054b50, 4); little(&out, 0, 4);
    little(&out, uint32_t(files.size()), 2); little(&out, uint32_t(files.size()), 2);
    little(&out, uint32_t(central.size()), 4); little(&out, offset, 4); little(&out, 0, 2);
    return out;
}

inline bool validate(const std::vector<VolumeFile> &files, std::string *why)
{
    if (files.size() > kMaxVolumeEntries) { *why = "too many WHDLoad files"; return false; }
    std::map<std::string, std::pair<std::string, bool>> nodes;
    std::map<std::string, bool> seen;
    size_t total = 0;
    for (const auto &file : files) {
        if (file.data.size() > kMaxVolumeBytes - total) { *why = "WHDLoad data exceeds volume limit"; return false; }
        total += file.data.size();
        size_t at = 0, depth = 0;
        for (;;) {
            const size_t slash = file.path.find('/', at);
            const bool last = slash == std::string::npos, directory = !last || file.is_dir;
            const std::string part = file.path.substr(at, last ? std::string::npos : slash - at);
            if (!ffs::valid_name(part) || part == "." || part == ".." || part.find('\\') != std::string::npos ||
                    volume_detail::control_character(part) || ++depth > ffs::kMaxDepth) { *why = "unsafe WHDLoad member path"; return false; }
            const std::string path = file.path.substr(0, slash), folded = ffs::folded(path);
            const auto old = nodes.find(folded);
            if (old != nodes.end() && (old->second.first != path || old->second.second != directory)) {
                *why = "WHDLoad path collision"; return false;
            }
            nodes[folded] = {path, directory};
            if (last) {
                if (seen.count(folded)) { *why = "duplicate WHDLoad member"; return false; }
                seen[folded] = true;
                break;
            }
            at = slash + 1;
        }
    }
    return true;
}
} // namespace whdload_detail

// Low-level data constructor, like bundle.build. The frontend's provider loader
// must validate ProgramSha256/VfsSha256 before calling it with user binaries.
// No partial bundle escapes on error. Empty directories in game archives are
// ignored, as T65a's read_volume().files does before making Game.zip.
inline bool build_whdload_bundle(const std::vector<VolumeFile> &files, const std::vector<uint8_t> &program,
        const std::vector<uint8_t> &vfs, const std::map<std::string, std::vector<uint8_t>> &kickstarts,
        bool button_wait, WhdLoadBundle *result, std::string *why)
{
    using namespace whdload_data;
    using namespace whdload_detail;
    *result = {}; why->clear();
    if (!validate(files, why)) return false;
    size_t extra = 0;
    if (kickstarts.size() > kMaxVolumeEntries) { *why = "too many Kickstart files"; return false; }
    for (const auto *data : {&program, &vfs}) {
        if (data->size() > kMaxVolumeBytes - extra) { *why = "WHDLoad program/plugin exceeds volume limit"; return false; }
        extra += data->size();
    }
    for (const auto &rom : kickstarts) {
        if (rom.second.size() > kMaxVolumeBytes - extra) { *why = "Kickstart data exceeds volume limit"; return false; }
        extra += rom.second.size();
    }
    std::vector<const VolumeFile *> slaves;
    for (const auto &file : files)
        if (!file.is_dir && container_detail::lower_extension(file.path) == "slave") slaves.push_back(&file);
    if (slaves.size() != 1) { *why = "the bundle needs exactly one .slave"; return false; }
    const auto &slave = *slaves.front();
    const size_t slash = slave.path.rfind('/');
    const std::string prefix = slash == std::string::npos ? "" : slave.path.substr(0, slash + 1);
    std::map<std::string, std::vector<uint8_t>> game, system;
    for (const auto &file : files) {
        if (file.is_dir) continue;
        if (file.path.compare(0, prefix.size(), prefix) == 0 && file.path != "C/WHDLoad" && file.path != "S/Startup-Sequence" && file.path != "S/WHDLoad.prefs")
            game[file.path.substr(prefix.size())] = file.data;
        if (file.path.compare(0, 16, "Devs/Kickstarts/") == 0) system[file.path] = file.data;
    }
    for (const auto &rom : kickstarts) {
        if (rom.first.compare(0, 4, "kick") || !ffs::valid_name(rom.first) || volume_detail::control_character(rom.first)) {
            *why = "invalid Kickstart member name"; return false;
        }
        system["Devs/Kickstarts/" + rom.first] = rom.second;
    }
    system["Game.zip"] = stored_zip(game);
    system["S/WHDLoad.prefs"] = text(kPreferences);
    if (button_wait) {
        const auto line = text(kButtonWaitLine);
        system["S/WHDLoad.prefs"].insert(system["S/WHDLoad.prefs"].end(), line.begin(), line.end());
    }
    std::array<std::vector<VolumeFile>, 3> roles;
    roles[0] = {{"C/WHDLoad", false, program}, {"C/WHDLoad.VFS", false, vfs},
        {"C/P6Assign", false, {std::begin(kAssignProgram), std::end(kAssignProgram)}},
        {"Game.slave", false, slave.data}, {"S/Startup-Sequence", false, text(kStartupSequence)}};
    roles[1].push_back({"Devs/Kickstarts", true, {}});
    for (auto &file : system) roles[1].push_back({file.first, false, std::move(file.second)});
    WhdLoadBundle built;
    for (size_t n = 0; n < roles.size(); ++n) {
        VolumeOptions options;
        options.label = kLabels[n]; options.free_blocks = kFreeBlocks[n];
        if (!build_ffs_volume(&roles[n], options, &built.images[n], why)) return false;
    }
    *result = std::move(built);
    return true;
}
} // namespace p6_libretro
