#include "cd_image.h"
#include "cd_cue.h"
#include "cd_ccd.h"
#include "cd_nrg.h"
#include "cd_mds.h"
#include "cd_file.h"
namespace p6_libretro {
using namespace p6_cd;
namespace {
bool extension(const char *path, const char *wanted)
{
    const char *dot = nullptr;
    for (const char *p = path; *p; ++p) {
        if (*p == '/' || *p == '\\') dot = nullptr;
        else if (*p == '.') dot = p + 1;
    }
    if (!dot) return false;
    while (*dot && *wanted) {
        const char lower = *dot >= 'A' && *dot <= 'Z' ? char(*dot + ('a' - 'A')) : *dot;
        if (lower != *wanted) return false;
        ++dot; ++wanted;
    }
    return !*dot && !*wanted;
}
}
struct CdImage::Data {
    CdCue cue;
    CdCcd ccd;
    CdNrg nrg;
    CdMds mds;
    CdFile iso;
    Layout iso_layout{};
    Binding iso_binding{};
    const Layout *layout = nullptr;
    const Binding *bindings = nullptr;
    uint32_t count = 0;
    Result open_iso(const char *path, const retro_vfs_interface *vfs)
    {
        if (!iso.open(path, vfs)) return Result::ReadError;
        if (!iso.size() || iso.size() % 2048 || iso.size() / 2048 > INT_MAX - 150) return Result::Range;
        const auto result = iso.bind(Encoding::Iso2048, &iso_binding);
        if (result != Result::Ok) return result;
        iso_layout.tracks = 1; iso_layout.leadout = uint32_t(iso.size() / 2048);
        iso_layout.track[0] = {0, 0, iso_layout.leadout, 0, 2048, 0, Mode::Mode1_2048, 1, 4, true};
        p6_sha256 hash; p6_sha256_init(&hash); p6_sha256_update(&hash, "P6ISO1", 6);
        uint8_t bytes[8];
        for (unsigned n = 0; n < 8; ++n) bytes[n] = uint8_t(iso.size() >> (8 * n));
        p6_sha256_update(&hash, bytes, sizeof bytes); p6_sha256_update(&hash, iso_binding.identity.content_id, 32);
        p6_sha256_final(&hash, iso_layout.content_id);
        layout = &iso_layout; bindings = &iso_binding; count = 1;
        return Result::Ok;
    }
    template<class Owner> Result use(Owner &owner, const char *path, const retro_vfs_interface *vfs)
    {
        const auto result = owner.open(path, vfs);
        if (result == Result::Ok) { layout = owner.layout(); bindings = owner.bindings(); count = owner.sources(); }
        return result;
    }
};
CdImage::CdImage() = default;
CdImage::~CdImage() = default;
Result CdImage::open(const char *path, const retro_vfs_interface *vfs)
{
    if (!path || !*path || data_) return Result::Invalid;
    auto candidate = std::make_unique<Data>();
    const auto result = extension(path, "iso") ? candidate->open_iso(path, vfs) :
        extension(path, "cue") ? candidate->use(candidate->cue, path, vfs) :
        extension(path, "ccd") ? candidate->use(candidate->ccd, path, vfs) :
        extension(path, "nrg") ? candidate->use(candidate->nrg, path, vfs) :
        extension(path, "mds") ? candidate->use(candidate->mds, path, vfs) : Result::Unsupported;
    if (result == Result::Ok) data_ = std::move(candidate);
    return result;
}
const Layout *CdImage::layout() const { return data_ ? data_->layout : nullptr; }
const Binding *CdImage::bindings() const { return data_ ? data_->bindings : nullptr; }
uint32_t CdImage::sources() const { return data_ ? data_->count : 0; }
}
