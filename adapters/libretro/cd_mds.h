// Native immutable MDS/MDF owner; bindings borrow this object/VFS lifetime.
#pragma once
#include "cd_disc.h"
#include "cd_mds_metadata.h"
#include <memory>
struct retro_vfs_interface;
namespace p6_libretro {
class CdMds {
public:
    CdMds();
    ~CdMds();
    CdMds(const CdMds &) = delete;
    CdMds &operator=(const CdMds &) = delete;
    p6_cd::Result open(const char *, const retro_vfs_interface * = nullptr);
    const p6_cd::Layout *layout() const;
    const p6_cd::Binding *bindings() const;
    uint32_t sources() const;
    const p6_cd::MdsMetadata *metadata() const;
    const p6_cd::Identity *container_identity() const;
    const p6_cd::Identity *descriptor_identity() const;
private:
    struct Data;
    std::unique_ptr<Data> data_;
};
}
