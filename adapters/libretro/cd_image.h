// Native host owner only. Layout/Binding borrow this object and its VFS lifetime.
#pragma once
#include "cd_disc.h"
#include <memory>
struct retro_vfs_interface;
namespace p6_libretro {
class CdImage {
public:
    CdImage();
    ~CdImage();
    CdImage(const CdImage &) = delete;
    CdImage &operator=(const CdImage &) = delete;
    p6_cd::Result open(const char *, const retro_vfs_interface * = nullptr);
    const p6_cd::Layout *layout() const;
    const p6_cd::Binding *bindings() const;
    uint32_t sources() const;
private:
    struct Data;
    std::unique_ptr<Data> data_;
};
}
