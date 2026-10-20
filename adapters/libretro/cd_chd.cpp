#include "cd_chd.h"
#include "cd_file.h"
#include "cd_read.h"
#include "sysconfig.h"
#include "tchar.h"
struct TrapContext;
#include "blkdev.h"
#include "uae/cdrom.h"
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
    enum class Packing { Invalid, Mode1Cooked, Mode1Raw, Mode2Body, Mode2Form1, Mode2Form2, Mode2Raw, Audio };
    struct View { Data *owner; unsigned number; Packing packing, pregap; } view[99]{};
    static Packing packing(const char *name) {
        if (!strcmp(name,"MODE1") || !strcmp(name,"MODE1/2048")) return Packing::Mode1Cooked;
        if (!strcmp(name,"MODE1_RAW") || !strcmp(name,"MODE1/2352")) return Packing::Mode1Raw;
        if (!strcmp(name,"MODE2") || !strcmp(name,"MODE2/2336") || !strcmp(name,"MODE2_FORM_MIX")) return Packing::Mode2Body;
        if (!strcmp(name,"MODE2_FORM1") || !strcmp(name,"MODE2/2048")) return Packing::Mode2Form1;
        if (!strcmp(name,"MODE2_FORM2") || !strcmp(name,"MODE2/2324")) return Packing::Mode2Form2;
        if (!strcmp(name,"MODE2_RAW") || !strcmp(name,"MODE2/2352")) return Packing::Mode2Raw;
        return !strcmp(name,"AUDIO") ? Packing::Audio : Packing::Invalid;
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
            const bool pregap = r.stored_pregap && at/t.stride < r.pregap;
            const auto kind = pregap ? v.pregap : v.packing;
            const auto data_bytes = pregap ? r.pg_data_bytes : r.data_bytes;
            const auto subtype = pregap ? r.pgsub : r.subtype;
            // CHD SUB96 follows the stored data size, before any header/ECC expansion.
            if (t.stride == 2448) {
                memset(sub,0,sizeof sub); // Shared stride does not imply stored SUB in this region.
                if (!strcmp(subtype,"RW_RAW")) sub_to_deinterleaved(sector+data_bytes,sub);
                else if (strcmp(subtype,"NONE")) memcpy(sub,sector+data_bytes,sizeof sub);
            }
            const uint32_t lba = (t.stored_pregap ? t.index0 : t.index1) + uint32_t(at/t.stride);
            if (kind == Packing::Mode1Cooked && t.stride != 2048) {
                memmove(sector+16,sector,2048); encode_l2(sector,int(lba)+150);
            } else if (kind == Packing::Mode2Body || kind == Packing::Mode2Form1 || kind == Packing::Mode2Form2) {
                // DiscSystem9b627578 SS_Mode2_2336/SS_Mode2_Form1_2048: preserve
                // stored2336 bodies; absent cooked subheaders are synthesized.
                const unsigned prefix = kind == Packing::Mode2Body ? 16 : 24;
                memmove(sector+prefix,sector,data_bytes); memset(sector,0,prefix);
                memset(sector+1,0xff,10);
                const uint32_t absolute = lba+150;
                auto bcd = [](uint32_t value) { return uint8_t(value/10*16+value%10); };
                sector[12]=bcd(absolute/4500); sector[13]=bcd(absolute/75%60);
                sector[14]=bcd(absolute%75); sector[15]=2;
                if (kind == Packing::Mode2Form1) encode_l2_mode2_form1(sector);
                if (kind == Packing::Mode2Form2) {
                    // XA duplicates four bytes (UNECM v1.0): submode at2 and6.
                    // Correct the old MacHawk/libMirage synth's second position5.
                    sector[18]=sector[22]=0x20; encode_l2_mode2_form2(sector);
                }
            } else if (kind == Packing::Audio) {
                for (unsigned n = 0; n < 2352; n += 2) std::swap(sector[n],sector[n+1]);
            }
            if (t.stride == 2448) memcpy(sector+2352,sub,sizeof sub);
            const size_t take = std::min(length-done,size_t(t.stride-within));
            memcpy(staging+done,sector+within,take); done += take;
        }
        if (length) memcpy(out,staging,length);
        *got = length; return true;
    }
    Result prepare() {
        uint64_t cursor = 0; layout.tracks = metadata.tracks;
        for (unsigned n = 0; n < metadata.tracks; ++n) {
            const auto &r = metadata.track[n]; const auto kind = packing(r.type);
            const auto pgkind = packing(r.pgtype+r.stored_pregap);
            // First-track pregaps/HTOA and CDI need their own public address/session contract.
            if (kind == Packing::Invalid || (!n && r.pregap) || (r.pregap && pgkind == Packing::Invalid)) return Result::Unsupported;
            const bool regional = r.postgap || r.pregap > 150 || (r.pregap && (pgkind != kind || r.pg_data_bytes != r.data_bytes ||
                strcmp(r.pgsub,r.stored_pregap ? r.subtype : "NONE")));
            const uint64_t index1 = cursor+r.pregap, end = index1+r.frames-(r.stored_pregap ? r.pregap : 0)+r.postgap;
            if (end > 100*60*75-150) return Result::Unsupported; // common two-digit absolute MSF
            const bool main_sub = strcmp(r.subtype,"NONE"), pg_sub = r.stored_pregap && strcmp(r.pgsub,"NONE");
            const uint32_t stride = !regional && kind == Packing::Mode1Cooked && !main_sub ? 2048 : main_sub || (regional && pg_sub) ? 2448 : 2352;
            auto normalized = [](Packing value) { return value == Packing::Audio ? Mode::Audio :
                value == Packing::Mode1Cooked || value == Packing::Mode1Raw ? Mode::Mode1Raw : Mode::Mode2Raw; };
            const auto mode = stride == 2048 ? Mode::Mode1_2048 : normalized(kind);
            layout.track[n] = {uint32_t(cursor),uint32_t(index1),uint32_t(end),n,stride,0,mode,1,uint8_t(kind == Packing::Audio ? 0 : 4),r.stored_pregap,r.stored_pregap ? 0 : r.pregap,r.postgap};
            if (regional) {
                using R = Track::Regions;
                layout.track[n].regions = {R::Explicit|(main_sub ? R::ProgramSub96 : 0)|(pg_sub ? R::PregapSub96 : 0)|
                    (r.postgap ? R::PostgapIndex2 : 0),r.pregap ? normalized(pgkind) : Mode::Mode1_2048,
                    r.pregap && pgkind != Packing::Audio ? 4u : 0u};
            }
            view[n] = {this,n,kind,pgkind}; binding[n] = {{{0},uint64_t(r.frames)*stride,1},read,&view[n]}; cursor = end;
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
        bool regional = false;
        for (unsigned n = 0; n < metadata.tracks; ++n) regional |= layout.track[n].regions.flags != 0;
        p6_sha256_init(&hash); p6_sha256_update(&hash,regional ? "P6CHR1" : "P6CHD1",6);
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
            if (regional) { put(t.regions.flags); put(unsigned(t.regions.pregap_mode)); put(t.regions.pregap_control); }
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
