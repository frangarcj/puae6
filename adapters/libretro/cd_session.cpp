#include "cd_session.h"
#include "cd_image.h"
#include "sysconfig.h"
#include "sysdeps.h"
#include "cd_device.h"
#include "content.h"
#include "firmware.h"
#include "romkey.h"
#include "config.h"
namespace p6_libretro {
struct CdSession::Data {
    CdImage image;
    p6_cd::Disc disc;
    bool bound = false;
    ~Data() { if (bound) p6_cd::device_unbind(); }
};
CdSession::CdSession() : data_(new Data) {}
CdSession::~CdSession() = default;
bool CdSession::content(const char *path)
{
    if (!path) return false;
    for (const char *ext : {"iso", "cue", "ccd", "nrg"}) if (has_extension(path, ext)) return true;
    return false;
}
const FirmwareNames &CdSession::firmware_names()
{
    return *firmware_for(P6_MACHINE_CD32);
}
bool CdSession::open(const char *path, const retro_vfs_interface *vfs, std::string *why)
{
    using p6_cd::Result;
    if (data_->bound || data_->image.open(path, vfs) != Result::Ok ||
        data_->disc.bind(*data_->image.layout(), data_->image.bindings(), data_->image.sources()) != Result::Ok ||
        p6_cd::device_bind(&data_->disc) != Result::Ok) {
        *why = "CD image cannot be opened (requires a supported layout and exact seekable reads)";
        return false;
    }
    data_->bound = true;
    return true;
}
bool CdSession::extend_firmware(const FileReader &files, const char *directory, std::vector<uint8_t> *rom, std::string *why)
{
    if (rom->size() != 512 * 1024) { *why = "CD32 main ROM must contain 512 KiB"; return false; }
    for (const char *name : {"kick40060.CD32.ext", "amiga-os-310-cd32-ext.rom"}) {
        std::vector<uint8_t> ext;
        if (files.read(join_path(directory, name), 512 * 1024 + kCloantoMagicBytes, &ext) != ReadResult::Ok) continue;
        if (is_cloanto_rom(ext.data(), ext.size())) {
            std::vector<uint8_t> key, plain;
            for (const char *key_name : {"rom.key", "ROM.KEY"})
                if (files.read(join_path(directory, key_name), kMaxRomKeyBytes, &key) == ReadResult::Ok && !key.empty()) break;
            if (decrypt_cloanto_rom(ext.data(), ext.size(), key.data(), key.size(), &plain) != CloantoResult::Decrypted) continue;
            ext.swap(plain);
        }
        if (ext.size() != 512 * 1024) continue;
        ext.insert(ext.end(), rom->begin(), rom->end());
        // Try aliases only when their complete bytes pass the bridge's checks.
        if (p6_bridge::validate_firmware({ext.data(), ext.size()}, p6_bridge::kCd32Machine, nullptr) != P6_OK) continue;
        rom->swap(ext); return true;
    }
    *why = "CD32 needs a valid 512 KiB extended ROM (kick40060.CD32.ext or amiga-os-310-cd32-ext.rom)";
    return false;
}
}
