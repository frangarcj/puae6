// Native immutable NRG DAO owner; bindings borrow this object/VFS lifetime.
#pragma once
#include "cd_disc.h"
#include "cd_nrg_metadata.h"
#include <memory>
struct retro_vfs_interface;
namespace p6_libretro {
enum class NrgSessionPolicy { SingleOnly, ExplicitSessions, ExplicitPrivate = ExplicitSessions, EtnfGlobalPrivate };
class CdNrg {
public:
    CdNrg();
    ~CdNrg();
    CdNrg(const CdNrg &) = delete;
    CdNrg &operator=(const CdNrg &) = delete;
    // Direct opens retain SingleOnly; the factory explicitly preserves the session table.
    p6_cd::Result open(const char *, const retro_vfs_interface * = nullptr,
                      NrgSessionPolicy = NrgSessionPolicy::SingleOnly);
    const p6_cd::Layout *layout() const;
    const p6_cd::Binding *bindings() const;
    uint32_t sources() const;
    const p6_cd::NrgMetadata *metadata() const;
    const p6_cd::Identity *container_identity() const;
private:
    struct Data;
    std::unique_ptr<Data> data_;
};
}
