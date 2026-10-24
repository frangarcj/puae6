// Native immutable CCD owner. All bindings borrow this object/VFS lifetime.
#pragma once
#include "cd_disc.h"
#include "cd_ccd_metadata.h"
#include <memory>
struct retro_vfs_interface;
namespace p6_libretro {
class CdCcd {
public:
    CdCcd();
    ~CdCcd();
    CdCcd(const CdCcd &) = delete;
    CdCcd &operator=(const CdCcd &) = delete;
    p6_cd::Result open(const char *, const retro_vfs_interface * = nullptr);
    const p6_cd::Layout *layout() const;
    const p6_cd::Binding *bindings() const;
    uint32_t sources() const;
    const p6_cd::CcdMetadata *metadata() const;
private:
    struct Data;
    std::unique_ptr<Data> data_;
};
}
