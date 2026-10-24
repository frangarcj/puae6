#include "cd_mds.h"
#include "cd_file.h"
#include "sysconfig.h"
#include "tchar.h"
struct TrapContext;
#include "blkdev.h"
#include <algorithm>
#include <string>
namespace p6_libretro {
using namespace p6_cd;
struct CdMds::Data {
    CdFile descriptor, file;
    MdsMetadata metadata{};
    Layout layout{};
    Binding original{}, description{}, binding[99]{};
    struct View { Data *owner; unsigned number; } view[99]{};
    static bool whole_read(const void *ctx, uint64_t offset, void *out, size_t length, size_t *got)
    {
        const bool ok = static_cast<const Data *>(ctx)->descriptor.read_at(offset, out, length);
        *got = ok ? length : 0; return ok;
    }
    static bool read(const void *ctx, uint64_t offset, void *out, size_t length, size_t *got)
    {
        if (!got) return false;
        *got = 0;
        const auto &v = *static_cast<const View *>(ctx);
        const auto &d = *v.owner; const auto &t = d.metadata.track[v.number];
        const uint64_t bytes = uint64_t(t.sectors) * t.sector_size;
        if (length > 2448 || (length && !out) || offset > bytes || length > bytes - offset) return false;
        if (t.sector_size != 2448) {
            if (!d.file.read_at(t.start_offset + offset, out, length)) return false;
        } else {
            uint8_t staging[2448], sector[2448], sub[96];
            for (size_t done = 0; done < length;) {
                const auto at = offset + done, within = at % 2448;
                if (!d.file.read_at(t.start_offset + at - within, sector, sizeof sector)) return false;
                sub_to_deinterleaved(sector + 2352, sub); // Only SUB96; preserve main bytes.
                memcpy(sector + 2352, sub, sizeof sub);
                const size_t take = length - done < 2448 - within ? length - done : size_t(2448 - within);
                memcpy(staging + done, sector + within, take); done += take;
            }
            if (length) memcpy(out, staging, length);
        }
        *got = length; return true;
    }
    static int64_t plba(const MdsToc &q) { return int64_t(q.pmin) * 4500 + q.psec * 75 + q.pframe - 150; }
    Result prepare()
    {
        // libMirage a9d6a2044b3c7bdf63ca44517ddd5738fd68c69e image-mds/parser.c:
        // synthetic pregap (461..474), data offset (492), length (543..545), disc start (646..651).
        int64_t cursor = metadata.session_start;
        layout.tracks = metadata.tracks;
        for (unsigned n = 0; n < metadata.tracks; ++n) {
            const auto &r = metadata.track[n];
            const int64_t index1 = cursor + r.pregap, end = index1 + r.sectors;
            if ((!n && cursor > 0) || (n && cursor < 0) || index1 < 0 || end > INT_MAX - 150 ||
                index1 != r.start_sector || index1 != plba(r.toc)) return Result::Invalid;
            const uint64_t bytes = uint64_t(r.sectors) * r.sector_size;
            if (r.start_offset > file.size() || bytes > file.size() - r.start_offset) return Result::Range;
            for (unsigned p = 0; p < n; ++p) {
                const auto &prev = metadata.track[p];
                if (r.start_offset < prev.start_offset + uint64_t(prev.sectors) * prev.sector_size && prev.start_offset < r.start_offset + bytes) return Result::Invalid;
            }
            const uint32_t clipped = cursor < 0 ? 0 : uint32_t(cursor);
            const auto mode = r.mode == 0xa9 ? Mode::Audio : r.mode == 0xab ? Mode::Mode2Raw : r.sector_size == 2048 ? Mode::Mode1_2048 : Mode::Mode1Raw;
            layout.track[n] = {clipped, uint32_t(index1), uint32_t(end), n, r.sector_size, 0, mode, uint8_t(r.toc.adr_control >> 4), uint8_t(r.toc.adr_control & 15), false, uint32_t(index1) - clipped};
            view[n] = {this, n}; binding[n] = {{{0}, bytes, 1}, read, &view[n]}; cursor = end;
        }
        if (cursor != metadata.session_end || cursor != plba(metadata.lead[2])) return Result::Invalid;
        layout.leadout = uint32_t(cursor);
        Disc validation; const auto valid = validation.bind(layout, binding, metadata.tracks);
        if (valid != Result::Ok) return valid;
        auto result = descriptor.bind(Encoding::Opaque, &description);
        if (result == Result::Ok) result = file.bind(Encoding::Opaque, &original);
        if (result != Result::Ok) return result;
        uint8_t buffer[2448]; p6_sha256 hash;
        for (unsigned n = 0; n < metadata.tracks; ++n) {
            p6_sha256_init(&hash);
            for (uint64_t at = 0; at < binding[n].identity.bytes;) {
                const size_t length = size_t(binding[n].identity.bytes - at < sizeof buffer ? binding[n].identity.bytes - at : sizeof buffer);
                size_t got = 0;
                if (!read(&view[n], at, buffer, length, &got) || got != length) return Result::ReadError;
                p6_sha256_update(&hash, buffer, length); at += length;
            }
            p6_sha256_final(&hash, binding[n].identity.content_id);
        }
        p6_sha256_init(&hash); p6_sha256_update(&hash, "P6MDS1", 6);
        auto put = [&](uint64_t value, unsigned bytes = 4) {
            uint8_t data[8]; for (unsigned n = 0; n < bytes; ++n) data[n] = uint8_t(value >> (8 * n));
            p6_sha256_update(&hash, data, bytes);
        };
        for (const auto *id : {&description.identity, &original.identity}) { put(id->bytes, 8); p6_sha256_update(&hash, id->content_id, 32); }
        put(layout.tracks); put(layout.leadout);
        for (unsigned n = 0; n < metadata.tracks; ++n) {
            const auto &t = layout.track[n];
            put(t.index0); put(t.index1); put(t.end); put(t.source); put(t.stride); put(t.offset, 8);
            put(unsigned(t.mode)); put(t.adr); put(t.control); put(t.stored_pregap); put(t.synthetic_pregap); put(t.postgap);
            put(binding[n].identity.bytes, 8); p6_sha256_update(&hash, binding[n].identity.content_id, 32);
        }
        p6_sha256_final(&hash, layout.content_id); return Result::Ok;
    }
};
CdMds::CdMds() = default;
CdMds::~CdMds() = default;
Result CdMds::open(const char *path, const retro_vfs_interface *vfs)
{
    if (!path || data_) return Result::Invalid;
    auto candidate = std::make_unique<Data>();
    if (!candidate->descriptor.open(path, vfs)) return Result::ReadError;
    auto result = mds_metadata(candidate.get(), Data::whole_read, candidate->descriptor.size(), &candidate->metadata);
    if (result != Result::Ok) return result;
    std::string filename = candidate->metadata.filename, descriptor = path;
    std::replace(descriptor.begin(), descriptor.end(), '\\', '/');
    const auto slash = descriptor.find_last_of('/'), dot = descriptor.find_last_of('.');
    if (candidate->metadata.wildcard) {
        if (dot == std::string::npos || (slash != std::string::npos && dot < slash)) return Result::Invalid;
        filename = descriptor.substr(0, dot) + filename.substr(1);
    } else {
        std::replace(filename.begin(), filename.end(), '\\', '/');
        if (filename[0] != '/' && !(filename.size() > 1 && filename[1] == ':')) filename = descriptor.substr(0, slash == std::string::npos ? 0 : slash + 1) + filename;
    }
    if (!candidate->file.open(filename.c_str(), vfs)) return Result::ReadError;
    result = candidate->prepare();
    if (result == Result::Ok) data_ = std::move(candidate);
    return result;
}
const Layout *CdMds::layout() const { return data_ ? &data_->layout : nullptr; }
const Binding *CdMds::bindings() const { return data_ ? data_->binding : nullptr; }
uint32_t CdMds::sources() const { return data_ ? data_->metadata.tracks : 0; }
const MdsMetadata *CdMds::metadata() const { return data_ ? &data_->metadata : nullptr; }
const Identity *CdMds::container_identity() const { return data_ ? &data_->original.identity : nullptr; }
const Identity *CdMds::descriptor_identity() const { return data_ ? &data_->description.identity : nullptr; }
}
