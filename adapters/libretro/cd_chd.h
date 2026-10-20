// Bindings and layout borrow this owner; the VFS table must outlive it.
// Private CHD owner: initial raw/audio and Mode1/2048-NONE domain, not a public loader.
#pragma once
#include "cd_disc.h"
#include "cd_chd_metadata.h"
#include <memory>
struct retro_vfs_interface;
namespace p6_libretro {
class CdChd {
public:
    CdChd();
    ~CdChd();
    CdChd(const CdChd &) = delete;
    CdChd &operator=(const CdChd &) = delete;
    p6_cd::Result open(const char *, const retro_vfs_interface * = nullptr);
    const p6_cd::Layout *layout() const;
    const p6_cd::Binding *bindings() const;
    uint32_t sources() const;
    const p6_cd::ChdMetadata *metadata() const;
    const p6_cd::Identity *container_identity() const;
private:
    struct Data;
    std::unique_ptr<Data> data_;
};
}
