#include "cd_session.h"
#include "cd_image.h"
#include "sysconfig.h"
#include "sysdeps.h"
#include "cd_device.h"
#include "akiko_state.h"
#include "disk_control.h"
#include "content.h"
#include "firmware.h"
#include "romkey.h"
#include "config.h"
namespace p6_libretro {
struct CdSession::Data {
    struct Entry { CdImage image; p6_cd::Disc disc; };
    std::vector<std::unique_ptr<Entry>> entries;
    bool bound = false;
    uint32_t machine = P6_MACHINE_CD32;
    ~Data() { if (bound) p6_cd::device_unbind(); }
};
CdSession::CdSession() : data_(new Data) {}
CdSession::~CdSession() = default;
bool CdSession::content(const char *path)
{
    if (!path) return false;
    for (const char *ext : {"iso", "cue", "ccd", "nrg", "mds", "chd"}) if (has_extension(path, ext)) return true;
    return false;
}
bool CdSession::open(uint32_t machine, const std::vector<PlaylistEntry> &slots, unsigned selected, const retro_vfs_interface *vfs,
                     std::vector<Image> *images, std::string *why)
{
    using p6_cd::Result;
    std::unique_ptr<Data> next(new Data);
    std::vector<Image> mapped;
    const p6_cd::Disc *catalog[p6_cd::kDeviceMaxDiscs]{};
    if (data_->bound || slots.empty() || slots.size() > p6_cd::kDeviceMaxDiscs || selected >= slots.size()) {
        *why = "CD catalog requires 1..16 fixed images and a valid selection"; return false;
    }
    if (machine == P6_MACHINE_CDTV && (slots.size() != 1 || selected)) {
        *why = "CDTV requires one fixed initial CD"; return false;
    }
    next->machine = machine;
    for (const auto &slot : slots) {
        std::unique_ptr<Data::Entry> entry(new Data::Entry);
        if (slot.save_disk || !content(slot.path.c_str()) || entry->image.open(slot.path.c_str(), vfs) != Result::Ok ||
            entry->disc.bind(*entry->image.layout(), entry->image.bindings(), entry->image.sources()) != Result::Ok) {
            *why = "CD image cannot be opened (requires a supported layout and exact seekable reads)"; return false;
        }
        unsigned index = 0;
        for (; index < next->entries.size(); ++index)
            if (!memcmp(catalog[index]->layout().content_id, entry->disc.layout().content_id, 32)) break;
        if (index == next->entries.size()) {
            catalog[index] = &entry->disc;
            next->entries.push_back(std::move(entry));
        }
        mapped.push_back({slot.path, slot.label, index});
    }
    if (p6_cd::device_catalog_bind(catalog, unsigned(next->entries.size()), mapped[selected].medium) != Result::Ok) {
        *why = "CD catalog cannot be bound"; return false;
    }
    next->bound = true;
    data_.swap(next); images->swap(mapped);
    return true;
}
unsigned CdSession::count() const { return unsigned(data_->entries.size()); }
bool CdSession::apply(bool mount, uint32_t medium)
{
    if (data_->machine != P6_MACHINE_CD32) return false;
    if (!mount) return p6_akiko_media_set(false);
    if (medium >= count()) return false;
    // A settled insertion of the same owner is a no-op; a different one needs
    // the real absence action first, retaining the existing unit and open count.
    if (p6_cd::device_bound_disc() != &data_->entries[medium]->disc &&
        p6_cd::device_select(medium) != p6_cd::Result::Ok) return false;
    return p6_akiko_media_set(true);
}
bool CdSession::extend_firmware(uint32_t machine, const FileReader &files, const char *directory, std::vector<uint8_t> *rom, std::string *why)
{
    const bool cdtv = machine == P6_MACHINE_CDTV;
    const size_t bytes = (cdtv ? 256 : 512) * 1024;
    if (rom->size() != bytes) { *why = "CD main ROM has the wrong size"; return false; }
    const std::vector<const char *> names = cdtv
        ? std::vector<const char *>{"kick34005.CDTV", "amiga-os-130-cdtv-ext.rom", "CDTV-Extended-ROMv2.7.rom"}
        : std::vector<const char *>{"kick40060.CD32.ext", "amiga-os-310-cd32-ext.rom"};
    for (const char *name : names) {
        std::vector<uint8_t> ext;
        if (files.read(join_path(directory, name), bytes + kCloantoMagicBytes, &ext) != ReadResult::Ok) continue;
        if (is_cloanto_rom(ext.data(), ext.size())) {
            std::vector<uint8_t> key, plain;
            for (const char *key_name : {"rom.key", "ROM.KEY"})
                if (files.read(join_path(directory, key_name), kMaxRomKeyBytes, &key) == ReadResult::Ok && !key.empty()) break;
            if (decrypt_cloanto_rom(ext.data(), ext.size(), key.data(), key.size(), &plain) != CloantoResult::Decrypted) continue;
            ext.swap(plain);
        }
        if (ext.size() != bytes) continue;
        ext.insert(ext.end(), rom->begin(), rom->end());
        // Try aliases only when their complete bytes pass the bridge's checks.
        if (p6_bridge::validate_firmware({ext.data(), ext.size()}, machine, nullptr) != P6_OK) continue;
        rom->swap(ext); return true;
    }
    *why = cdtv ? "CDTV needs a valid 256 KiB v2.7 extended ROM (kick34005.CDTV or CDTV-Extended-ROMv2.7.rom)"
                : "CD32 needs a valid 512 KiB extended ROM (kick40060.CD32.ext or amiga-os-310-cd32-ext.rom)";
    return false;
}
}
