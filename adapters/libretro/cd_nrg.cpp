#include "cd_nrg.h"
#include "cd_file.h"
#include "sysconfig.h"
#include "tchar.h"
struct TrapContext;
#include "blkdev.h"
#include "uae/cdrom.h"
namespace p6_libretro {
using namespace p6_cd;
struct CdNrg::Data {
    CdFile file;
    NrgMetadata metadata{};
    Layout layout{};
    Binding original{}, binding[99]{};
    struct View { Data *owner; unsigned number; int64_t start; } view[99]{};
    static bool whole_read(const void *ctx, uint64_t offset, void *out, size_t length, size_t *got)
    {
        const bool ok = static_cast<const Data *>(ctx)->file.read_at(offset, out, length);
        *got = ok ? length : 0; return ok;
    }
    static bool read(const void *ctx, uint64_t offset, void *out, size_t length, size_t *got)
    {
        if (!got) return false;
        *got = 0;
        const auto &v = *static_cast<const View *>(ctx);
        const auto &d = *v.owner; const auto &t = d.metadata.track[v.number];
        const uint64_t bytes = d.binding[v.number].identity.bytes;
        if (length > 2448 || (length && !out) || offset > bytes || length > bytes - offset) return false;
        if (t.stride != 2448 && t.mode != 2 && t.mode != 3) {
            if (!d.file.read_at(t.offset0 + offset, out, length)) return false;
        } else {
            uint8_t staging[2448], sector[2448], sub[96];
            for (size_t done = 0; done < length;) {
                const uint64_t width = t.mode == 2 || t.mode == 3 ? 2352 : 2448;
                const auto at = offset + done, within = at % width;
                if (t.mode == 2 || t.mode == 3) {
                    // DiscSystem9b627578: SS_Mode2_2336/SS_Mode2_Form1_2048.
                    // Stored2336 bodies stay intact; absent Form1 subheaders are zero.
                    const unsigned prefix = t.mode == 2 ? 24 : 16;
                    if (!d.file.read_at(t.offset0 + (at / width) * t.stride, sector + prefix, t.stride)) return false;
                    memset(sector, 0, prefix); memset(sector + 1, 0xff, 10);
                    const auto absolute = uint32_t(v.start + int64_t(at / width) + 150);
                    auto bcd = [](uint32_t value) { return uint8_t((value / 10) * 16 + value % 10); };
                    sector[12] = bcd(absolute / 4500); sector[13] = bcd(absolute / 75 % 60);
                    sector[14] = bcd(absolute % 75); sector[15] = 2;
                    if (t.mode == 2) encode_l2_mode2_form1(sector);
                } else {
                    if (!d.file.read_at(t.offset0 + at - within, sector, sizeof sector)) return false;
                    sub_to_deinterleaved(sector + 2352, sub); // Only SUB96, once; audio/data stay byte exact.
                    memcpy(sector + 2352, sub, sizeof sub);
                }
                const size_t take = length - done < width - within ? length - done : size_t(width - within);
                memcpy(staging + done, sector + within, take); done += take;
            }
            if (length) memcpy(out, staging, length);
        }
        *got = length; return true;
    }
    Result prepare(NrgSessionPolicy policy)
    {
        const bool multi = metadata.sessions > 1;
        if (multi && policy != NrgSessionPolicy::ExplicitPrivate) return Result::Unsupported; // Before payload.
        const bool tao = metadata.layout != NrgLayout::Dao;
        layout.tracks = metadata.tracks; layout.leadout = uint32_t(metadata.leadout);
        if (multi) {
            layout.sessions = metadata.sessions;
            for (unsigned n = 0; n < layout.sessions; ++n) layout.session[n] = metadata.session[n].info;
        }
        for (unsigned n = 0; n < metadata.tracks; ++n) {
            const auto &r = metadata.track[n];
            const unsigned expected = r.mode == 0 || r.mode == 2 ? 2048 : r.mode == 3 ? 2336 : r.mode == 5 || r.mode == 6 || r.mode == 7 ? 2352 : 2448;
            if (r.stride != expected) return Result::Unsupported; // Padded sectors have no inferred decoder.
            const auto pregap = tao ? 150 : (r.offset1 - r.offset0) / r.stride;
            const int64_t start = r.has_index0 ? r.index0 : int64_t(r.index1) - int64_t(pregap);
            const int64_t end = int64_t(r.index1) + int64_t((r.end - r.offset1) / r.stride);
            if (int64_t(r.index1) - start != int64_t(pregap) || (n && start < 0) || (!n && start > 0) || end > metadata.leadout) return Result::Invalid;
            // Header MSF has two BCD digits, including the stored prefix hashed below.
            if ((r.mode == 2 || r.mode == 3) && (start < -150 || end > 100 * 60 * 75 - 150)) return Result::Unsupported;
            const unsigned stride = r.mode == 2 || r.mode == 3 ? 2352 : r.stride;
            const uint32_t clipped = start < 0 ? 0 : uint32_t(start);
            const auto mode = r.mode == 0 ? Mode::Mode1_2048 : r.mode == 7 || r.mode == 16 ? Mode::Audio : r.mode == 2 || r.mode == 3 || r.mode == 6 || r.mode == 17 ? Mode::Mode2Raw : Mode::Mode1Raw;
            layout.track[n] = {clipped, uint32_t(r.index1), uint32_t(end), n, stride, tao ? 0 : uint64_t(int64_t(clipped) - start) * stride, mode, r.adr, r.control, !tao, tao ? uint32_t(r.index1) - clipped : 0};
            view[n] = {this, n, tao ? r.index1 : start}; binding[n] = {{{0}, ((r.end - r.offset0) / r.stride) * stride, 1}, read, &view[n]};
        }
        Disc validation;
        const auto valid = validation.bind(layout, binding, metadata.tracks);
        if (valid != Result::Ok) return valid;
        auto result = file.bind(Encoding::Opaque, &original);
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
        // Separate the canonical gap policy from DAO without changing existing DAO identities.
        const char *domain = multi ? "P6NRGM" : metadata.layout == NrgLayout::Etn2Canonical150 ? "P6ETN2" : tao ? "P6ETN1" : "P6NRG1";
        p6_sha256_init(&hash); p6_sha256_update(&hash, domain, 6);
        auto put = [&](uint64_t value, unsigned bytes = 4) {
            uint8_t data[8];
            for (unsigned n = 0; n < bytes; ++n) data[n] = uint8_t(value >> (8 * n));
            p6_sha256_update(&hash, data, bytes);
        };
        put(original.identity.bytes, 8); p6_sha256_update(&hash, original.identity.content_id, 32);
        put(metadata.directory, 8); put(metadata.version); put(metadata.tracks); put(uint32_t(metadata.leadin)); put(uint32_t(metadata.leadout));
        put(metadata.disk_type); put(metadata.session_hint); p6_sha256_update(&hash, metadata.catalog, 13);
        for (unsigned n = 0; n < metadata.tracks; ++n) {
            const auto &r = metadata.track[n];
            put(r.offset0, 8); put(r.offset1, 8); put(r.end, 8); put(uint32_t(r.index0)); put(uint32_t(r.index1));
            put(r.stride); put(r.mode); put(r.adr); put(r.control); put(r.has_index0); p6_sha256_update(&hash, r.isrc, 12);
            p6_sha256_update(&hash, binding[n].identity.content_id, 32);
        }
        if (multi) {
            put(1); put(metadata.sessions); // Private session policy version, then ordered complete table.
            for (unsigned n = 0; n < metadata.sessions; ++n) {
                const auto &entry = metadata.session[n]; const auto &s = entry.info;
                put(s.first_track); put(s.last_track); put(s.format); put(uint32_t(s.cue0)); put(s.a2);
                put(entry.session_hint); p6_sha256_update(&hash, entry.catalog, 13);
            }
        }
        p6_sha256_final(&hash, layout.content_id); return Result::Ok;
    }
};
CdNrg::CdNrg() = default;
CdNrg::~CdNrg() = default;
Result CdNrg::open(const char *path, const retro_vfs_interface *vfs, NrgSessionPolicy policy)
{
    if (!path || data_ || (policy != NrgSessionPolicy::SingleOnly && policy != NrgSessionPolicy::ExplicitPrivate)) return Result::Invalid;
    auto candidate = std::make_unique<Data>();
    if (!candidate->file.open(path, vfs)) return Result::ReadError;
    auto result = nrg_metadata(candidate.get(), Data::whole_read, candidate->file.size(), &candidate->metadata);
    if (result == Result::Ok) result = candidate->prepare(policy);
    if (result == Result::Ok) data_ = std::move(candidate);
    return result;
}
const Layout *CdNrg::layout() const { return data_ ? &data_->layout : nullptr; }
const Binding *CdNrg::bindings() const { return data_ ? data_->binding : nullptr; }
uint32_t CdNrg::sources() const { return data_ ? data_->metadata.tracks : 0; }
const NrgMetadata *CdNrg::metadata() const { return data_ ? &data_->metadata : nullptr; }
const Identity *CdNrg::container_identity() const { return data_ ? &data_->original.identity : nullptr; }
}
