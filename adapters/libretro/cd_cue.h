// Native CUE owner. Files/VFS stay alive through the last bound Disc read.
#pragma once
#include "cd_disc.h"
#include <memory>
struct retro_vfs_interface;
namespace p6_libretro {
class CdCue {
public:
    CdCue();
    ~CdCue();
    CdCue(const CdCue &) = delete;
    CdCue &operator=(const CdCue &) = delete;
    p6_cd::Result open(const char *, const retro_vfs_interface * = nullptr);
    const p6_cd::Layout *layout() const;
    const p6_cd::Binding *bindings() const;
    uint32_t sources() const;
private:
    struct Data;
    std::unique_ptr<Data> data_;
};
}
