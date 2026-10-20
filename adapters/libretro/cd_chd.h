// Bindings and layout borrow this owner; the VFS table must outlive it.
// Private CHD owner: raw/audio, Mode1 cooked and Mode2 body/Form1/Form2, not a public loader.
// Per-region packing/SUB and synthesized postgaps are explicit; HTOA/CDI remain unsupported.
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
