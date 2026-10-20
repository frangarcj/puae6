#include "cd_chd.h"
#include "cd_file.h"
#include "cd_read.h"
#include "sysconfig.h"
#include "tchar.h"
struct TrapContext;
#include "blkdev.h"
#include <algorithm>
#include <new>
namespace p6_libretro {
using namespace p6_cd;
struct CdChd::Data {
    CdFile file;
    ChdReader reader;
    ChdMetadata metadata{};
    Layout layout{};
    Binding original{}, binding[99]{};
    struct View { Data *owner; unsigned number; } view[99]{};
    static int mode(const char *name) {
        if (!strcmp(name,"MODE1") || !strcmp(name,"MODE1/2048")) return 0;
        if (!strcmp(name,"MODE1_RAW") || !strcmp(name,"MODE1/2352")) return 1;
        if (!strcmp(name,"MODE2_RAW") || !strcmp(name,"MODE2/2352")) return 2;
        return !strcmp(name,"AUDIO") ? 3 : -1;
    }
    static bool whole_read(const void *ctx, uint64_t at, void *out, size_t length, size_t *got) {
        const bool ok = static_cast<const Data *>(ctx)->file.read_at(at,out,length);
        *got = ok ? length : 0; return ok;
    }
    static bool read(const void *ctx, uint64_t offset, void *out, size_t length, size_t *got) {
        if (!got) return false;
        *got = 0;
        const auto &v = *static_cast<const View *>(ctx); auto &d = *v.owner;
        const auto &r = d.metadata.track[v.number]; const auto &t = d.layout.track[v.number];
        const auto bytes = d.binding[v.number].identity.bytes;
        if (length > 2448 || (length && !out) || offset > bytes || length > bytes - offset) return false;
        uint8_t staging[2448], sector[2448], sub[96];
        for (size_t done = 0; done < length;) {
            const uint64_t at = offset + done, within = at % t.stride;
            if (d.reader.read_at((r.offset_frames + at / t.stride) * 2448,sector,sizeof sector) != Result::Ok) return false;
            if (t.mode == Mode::Audio) for (unsigned n = 0; n < 2352; n += 2) std::swap(sector[n],sector[n+1]);
            if (t.stride == 2448 && !strcmp(r.subtype,"RW_RAW")) {
                sub_to_deinterleaved(sector+2352,sub); memcpy(sector+2352,sub,sizeof sub);
            }
            const size_t take = std::min(length-done,size_t(t.stride-within));
            memcpy(staging+done,sector+within,take); done += take;
        }
        if (length) memcpy(out,staging,length);
        *got = length; return true;
    }
    Result prepare() {
        uint64_t cursor = 0; layout.tracks = metadata.tracks;
        for (unsigned n = 0; n < metadata.tracks; ++n) {
            const auto &r = metadata.track[n]; const int kind = mode(r.type);
            // Other packing, initial gaps, postgaps and mixed pregap semantics remain explicit tasks.
            if (kind < 0 || (kind == 0 && strcmp(r.subtype,"NONE")) || (!n && r.pregap) || r.postgap || r.pregap > 150) return Result::Unsupported;
            if (r.pregap && (mode(r.pgtype+r.stored_pregap) != kind || r.pg_data_bytes != r.data_bytes ||
                strcmp(r.pgsub,r.stored_pregap ? r.subtype : "NONE"))) return Result::Unsupported;
            const uint64_t index1 = cursor+r.pregap, end = index1+r.frames-(r.stored_pregap ? r.pregap : 0);
            if (end > 100*60*75-150) return Result::Unsupported; // common two-digit absolute MSF
            const uint32_t stride = kind == 0 ? 2048 : !strcmp(r.subtype,"NONE") ? 2352 : 2448;
            layout.track[n] = {uint32_t(cursor),uint32_t(index1),uint32_t(end),n,stride,0,Mode(kind),1,uint8_t(kind == 3 ? 0 : 4),r.stored_pregap,r.stored_pregap ? 0 : r.pregap,0};
            view[n] = {this,n}; binding[n] = {{{0},uint64_t(r.frames)*stride,1},read,&view[n]}; cursor = end;
        }
        layout.leadout = uint32_t(cursor);
        Disc validation; const auto valid = validation.bind(layout,binding,metadata.tracks);
        if (valid != Result::Ok) return valid;
        auto status = file.bind(Encoding::Opaque,&original);
        if (status != Result::Ok) return status;
        uint8_t bytes[2448]; p6_sha256 hash;
        for (unsigned n = 0; n < metadata.tracks; ++n) {
            p6_sha256_init(&hash);
            for (uint64_t at = 0; at < binding[n].identity.bytes;) {
                const size_t length = size_t(std::min(uint64_t(sizeof bytes),binding[n].identity.bytes-at)); size_t got = 0;
                if (!read(&view[n],at,bytes,length,&got) || got != length) return Result::ReadError;
                p6_sha256_update(&hash,bytes,length); at += length;
            }
            p6_sha256_final(&hash,binding[n].identity.content_id);
        }
        p6_sha256_init(&hash); p6_sha256_update(&hash,"P6CHD1",6);
        auto put = [&](uint64_t value, unsigned width = 4) {
            uint8_t encoded[8]; for (unsigned n = 0; n < width; ++n) encoded[n] = uint8_t(value >> (n*8));
            p6_sha256_update(&hash,encoded,width);
        };
        put(original.identity.bytes,8); p6_sha256_update(&hash,original.identity.content_id,32);
        put(reader.bytes(),8); put(reader.hunk_bytes()); put(metadata.padded_frames,8); put(metadata.tail_frames,8);
        put(layout.tracks); put(layout.leadout);
        for (unsigned n = 0; n < metadata.tracks; ++n) {
            const auto &t = layout.track[n];
            for (auto value : {t.index0,t.index1,t.end,t.source,t.stride}) put(value);
            put(t.offset,8); put(unsigned(t.mode)); put(t.adr); put(t.control); put(t.stored_pregap); put(t.synthetic_pregap); put(t.postgap);
            put(binding[n].identity.bytes,8); p6_sha256_update(&hash,binding[n].identity.content_id,32);
        }
        p6_sha256_final(&hash,layout.content_id); return Result::Ok;
    }
};
CdChd::CdChd() = default;
CdChd::~CdChd() = default;
Result CdChd::open(const char *path, const retro_vfs_interface *vfs) {
    if (!path || !*path || data_) return Result::Invalid;
    std::unique_ptr<Data> candidate(new(std::nothrow) Data);
    if (!candidate) return Result::ReadError;
    if (!candidate->file.open(path,vfs)) return Result::ReadError;
    auto status = candidate->reader.open(Data::whole_read,candidate.get(),candidate->file.size());
    if (status == Result::Ok) status = chd_metadata(candidate->reader,&candidate->metadata);
    if (status == Result::Ok) status = candidate->prepare();
    if (status == Result::Ok) data_ = std::move(candidate);
    return status;
}
const Layout *CdChd::layout() const { return data_ ? &data_->layout : nullptr; }
const Binding *CdChd::bindings() const { return data_ ? data_->binding : nullptr; }
uint32_t CdChd::sources() const { return data_ ? data_->metadata.tracks : 0; }
const ChdMetadata *CdChd::metadata() const { return data_ ? &data_->metadata : nullptr; }
const Identity *CdChd::container_identity() const { return data_ ? &data_->original.identity : nullptr; }
}
