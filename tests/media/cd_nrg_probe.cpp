#include "../../adapters/libretro/cd_nrg.h"
#include "cd_read.h"
#include "cd_akiko.h"
#include "uae/cdrom.h"
#include "sha256.h"
#include "libretro.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <memory>
using namespace p6_cd;
using p6_libretro::CdNrg;
using p6_libretro::NrgSessionPolicy;
#define CHECK(x) do { if (!(x)) { fprintf(stderr, "line%d: %s\n", __LINE__, #x); return 70; } } while (0)
struct retro_vfs_file_handle { FILE *file; };
static uint64_t fault_at;
static int fault;
static uint64_t metadata_directory;
static unsigned payload_reads;
static bool metadata_only;
static unsigned read_calls;
static retro_vfs_file_handle *RETRO_CALLCONV vopen(const char *path, unsigned, unsigned)
{
    FILE *file = fopen(path, "rb");
    if (file && metadata_only) {
        uint8_t footer[12];
        if (fseek(file,-12,SEEK_END) || fread(footer,1,12,file)!=12) { fclose(file); return nullptr; }
        const bool wide = !memcmp(footer,"NER5",4); metadata_directory=0;
        for(unsigned n=wide?4:8;n<12;++n) metadata_directory=metadata_directory*256+footer[n];
        rewind(file);
    }
    return file ? new retro_vfs_file_handle{file} : nullptr;
}
static int RETRO_CALLCONV vclose(retro_vfs_file_handle *f) { const int status = fclose(f->file); delete f; return status; }
static int64_t RETRO_CALLCONV vtell(retro_vfs_file_handle *f) { return ftell(f->file); }
static int64_t RETRO_CALLCONV vsize(retro_vfs_file_handle *f) { const auto pos = ftell(f->file); fseek(f->file, 0, SEEK_END); const auto size = ftell(f->file); fseek(f->file, pos, SEEK_SET); return size; }
static int64_t RETRO_CALLCONV vseek(retro_vfs_file_handle *f, int64_t at, int origin) { return fseek(f->file, long(at), origin); }
static int64_t RETRO_CALLCONV vread(retro_vfs_file_handle *f, void *out, uint64_t length)
{
    ++read_calls;
    if (metadata_only && uint64_t(ftell(f->file)) < metadata_directory) { ++payload_reads; return -1; }
    if (fault == 1 || fault == 2) return fault == 1 ? -1 : 0;
    if (fault == 3 && uint64_t(ftell(f->file)) >= fault_at) return -1;
    return fread(out, 1, size_t(length < 3 ? length : 3), f->file);
}
static void digest(const void *data, size_t length)
{
    uint8_t hash[32]; p6_sha256_hash(data, length, hash);
    for (auto value : hash) printf("%02x", value);
}
static bool untouched(const uint8_t *out, size_t length) { for (size_t n = 0; n < length; ++n) if (out[n] != 0xa5) return false; return true; }
int main(int argc, char **argv)
{
    if (argc < 2 || argc > 3) return 64;
    retro_vfs_interface api{};
    api.open = vopen; api.close = vclose; api.size = vsize; api.tell = vtell; api.seek = vseek; api.read = vread;
    const auto *vfs = getenv("P6_NRG_PROBE_VFS") ? &api : nullptr;
    metadata_only = getenv("P6_NRG_PROBE_METADATA_ONLY") != nullptr;
    CHECK(!metadata_only || vfs);
    const auto policy = getenv("P6_NRG_PRIVATE_SESSIONS") ? NrgSessionPolicy::ExplicitPrivate : NrgSessionPolicy::SingleOnly;
    CdNrg invalid; CHECK(invalid.open(argv[1],vfs,NrgSessionPolicy(99))==Result::Invalid&&!invalid.layout());
    auto owner = std::make_unique<CdNrg>(); const auto status = owner->open(argv[1], vfs, policy);
    if (status != Result::Ok) { CHECK(!payload_reads); CHECK(!owner->layout() && !owner->metadata() && !owner->bindings() && !owner->sources()); printf("error %u\n", unsigned(status)); return 2; }
    CHECK(owner->open(argv[1], vfs) == Result::Invalid);
    Disc disc; CHECK(disc.bind(*owner->layout(), owner->bindings(), owner->sources()) == Result::Ok);
    const auto &id = *owner->container_identity();
    printf("container %llu ", (unsigned long long)id.bytes); for (auto value : id.content_id) printf("%02x", value); puts("");
    const auto &layout = disc.layout(); printf("disc %u %u ", layout.tracks, layout.leadout); for (auto value : layout.content_id) printf("%02x", value); puts("");
    uint8_t out[2464]; size_t got = 123;
    if (layout.sessions) {
        const auto calls = read_calls;
        cd_toc_head global; memset(&global,0xa5,sizeof global);
        CHECK(akiko_toc(disc,&global)==Result::Ok);
        // Numeric oracle for the two-session DAO fixture; never a selected lead-in.
        CHECK(global.first_track==1&&global.first_track_offset==1&&global.last_track==3&&global.last_track_offset==3);
        CHECK(global.tracks==3&&global.points==6&&global.firstaddress==0&&global.lastaddress==39);
        const unsigned points[]={0xa0,1,2,3,0xa1,0xa2}, tracks[]={1,1,2,3,3,0};
        const int addresses[]={0,0,6,32,39,39}, controls[]={0,0,4,4,0,0};
        for(unsigned n=0;n<6;++n) CHECK(global.toc[n].point==points[n]&&global.toc[n].track==tracks[n]
            &&global.toc[n].paddress==addresses[n]&&global.toc[n].adr==1&&global.toc[n].control==controls[n]);
        for (unsigned n=0;n<layout.sessions;++n) {
            SessionToc result{}; CHECK(akiko_session_toc(disc,n+1,&result)==Result::Ok);
            const auto &s=result.session; const auto &t=result.toc;
            printf("session %u %u %u %u %d %u %d %d %d %d\n",n+1,s.first_track,s.last_track,s.format,s.cue0,s.a2,t.tracks,t.points,t.firstaddress,t.lastaddress);
            for(int i=0;i<t.points;++i) printf("toc %u %u %u %d %u\n",n+1,t.toc[i].point,t.toc[i].track,t.toc[i].paddress,t.toc[i].control);
            if (!n) continue;
            const auto gap_start=layout.session[n-1].a2, gap_end=layout.track[s.first_track-1].index0;
            for(uint32_t lba : {gap_start,uint32_t(s.cue0),gap_end-1}) if(lba<gap_end) {
                for(unsigned view : {2048u,2352u,2448u}) {
                    memset(out,0xa5,sizeof out);
                    CHECK(canonical_read(disc,lba,view,out,sizeof out)==Result::Range&&untouched(out,sizeof out));
                }
                CHECK(read_subchannel(disc,lba,out,sizeof out)==Result::Range&&untouched(out,sizeof out));
                printf("gap %u\n",lba);
            }
        }
        cd_toc_head again; CHECK(akiko_toc(disc,&again)==Result::Ok&&!memcmp(&global,&again,sizeof global));
        CHECK(read_calls==calls);
        if(vfs) {
            const auto &t=layout.track[layout.tracks-1]; fault_at=owner->metadata()->track[layout.tracks-1].offset1;
            for(fault=1;fault<=3;++fault) {
                memset(out,0xa5,sizeof out);
                CHECK(canonical_read(disc,t.index1,2352,out,sizeof out)==Result::ReadError&&untouched(out,sizeof out));
            }
            fault=0;
        }
    }
    for (unsigned n = 0; n < owner->sources(); ++n) {
        const auto &b = owner->bindings()[n];
        printf("source %u %llu ", n, (unsigned long long)b.identity.bytes); for (auto value : b.identity.content_id) printf("%02x", value); puts("");
        for (uint64_t at : {UINT64_C(0), UINT64_C(8), UINT64_C(2344), UINT64_C(2440), b.identity.bytes - 20}) {
        if (at > b.identity.bytes) continue;
        const size_t length = size_t(b.identity.bytes - at < 2448 ? b.identity.bytes - at : 2448);
        CHECK(b.read(b.context, at, out, length, &got) && got == length);
        printf("bytes %u %llu %zu ", n, (unsigned long long)at, length); digest(out, length); puts("");
        }
        memset(out, 0xa5, sizeof out);
        CHECK(b.read(b.context, b.identity.bytes, nullptr, 0, &got) && !got);
        CHECK(!b.read(b.context, b.identity.bytes, out, 1, &got) && !got && untouched(out, sizeof out));
        CHECK(!b.read(b.context, UINT64_MAX, out, 1, &got) && !got);
        CHECK(!b.read(b.context, 0, out, 2449, &got) && !got && untouched(out, sizeof out));
    }
    for (uint32_t n = 0; n < layout.tracks; ++n) {
        const auto &t = layout.track[n];
        if (owner->metadata()->layout != NrgLayout::Dao) {
            const auto &raw = owner->metadata()->track[n];
            CHECK(!t.stored_pregap && t.synthetic_pregap == t.index1 - t.index0 && !t.offset && raw.offset0 == raw.offset1);
            printf("etnf %u %u\n", n, raw.start_sector);
        }
        printf("track %u %u %u %u %u %llu %u\n", t.index0, t.index1, t.end, t.source, t.stride, (unsigned long long)t.offset, unsigned(t.mode));
        for (uint32_t lba : {t.index0, t.index1, t.end - 1}) for (unsigned view : {2048u, 2352u, 2448u}) {
            memset(out, 0xa5, sizeof out); const auto result = canonical_read(disc, lba, view, out + 8, view);
            CHECK(untouched(out, 8) && untouched(out + 8 + view, sizeof out - 8 - view));
            printf("sector %u %u %u ", lba, view, unsigned(result));
            if (result == Result::Ok) { if (t.mode == Mode::Mode1_2048 && view == 2352) digest(out + 24, 2048); else digest(out + 8, view); } else CHECK(untouched(out, sizeof out));
            puts("");
            if (view == 2352 && owner->metadata()->track[n].mode == 2) { CHECK(result == Result::Ok); printf("parity %u %u ", n, lba); digest(out + 8 + 2072, 280); puts(""); }
            if (view == 2448) { CHECK(read_subchannel(disc, lba, out, 96) == Result::Ok); printf("sub %u ", lba); digest(out, 96); puts(""); }
        }
        if (n && owner->metadata()->layout != NrgLayout::Dao) for (uint32_t lba : {t.index0 - 1, t.index1 - 1}) {
            CHECK(canonical_read(disc, lba, 2448, out, sizeof out) == Result::Ok);
            printf("sector %u 2448 0 ", lba); digest(out, 2448); puts("");
        }
    }
    if (vfs) for (unsigned n = 0; n < layout.tracks; ++n) if (layout.track[n].stride == 2448 || (owner->metadata()->track[n].mode == 2 || owner->metadata()->track[n].mode == 3)) {
        const auto &b = owner->bindings()[n]; fault_at = owner->metadata()->track[n].offset0 + 2448;
        for (fault = 1; fault <= 3; ++fault) {
            memset(out, 0xa5, sizeof out);
            CHECK(!b.read(b.context, 2440, out + 8, 2448, &got) && !got && untouched(out, sizeof out));
            CHECK(canonical_read(disc, layout.track[n].index1 + 2, 2448, out + 8, 2448) == Result::ReadError && untouched(out, sizeof out));
            if (layout.track[n].stride == 2448) CHECK(read_subchannel(disc, layout.track[n].index1 + 2, out, 96) == Result::ReadError && untouched(out, sizeof out));
            else CHECK(!b.read(b.context, 2 * 2352, out + 8, 16, &got) && !got && untouched(out, sizeof out));
        }
        break;
    }
    if (vfs && owner->metadata()->layout != NrgLayout::Dao) {
        fault = 1;
        for (unsigned n = 1; n < layout.tracks; ++n) {
            CHECK(canonical_read(disc, layout.track[n].index1 - 1, 2448, out, sizeof out) == Result::Ok);
            memset(out, 0xa5, sizeof out);
            CHECK(canonical_read(disc, layout.track[n].index1, 2448, out, sizeof out) == Result::ReadError && untouched(out, sizeof out));
        }
    }
    fault = 0;
    // Pure wrapper contract: preserve a nonzero duplicated XA subheader and payload.
    memset(out, 0xa5, sizeof out); auto *sector = out + 8;
    memset(sector, 0, 24); memset(sector + 1, 0xff, 10);
    sector[13] = 0x18; sector[14] = 0x34; sector[15] = 2; // LBA1234+150.
    for (unsigned n = 0; n < 8; ++n) sector[16 + n] = uint8_t(n % 4);
    for (unsigned n = 0; n < 2048; ++n) sector[24 + n] = uint8_t(n * 13 + 7);
    uint8_t original[2072]; memcpy(original, sector, sizeof original);
    encode_l2_mode2_form1(sector);
    CHECK(!memcmp(original, sector, sizeof original) && untouched(out, 8) && untouched(out + 2360, sizeof out - 2360));
    printf("encoder "); digest(sector, 2352); puts("");
    if (argc == 3) {
        uint8_t before[32], after[32];
        auto delivery = [&](uint8_t *id) {
            p6_sha256 hash; p6_sha256_init(&hash);
            for (unsigned n = 0; n < layout.tracks; ++n) {
                const auto &t = layout.track[n];
                for (uint32_t lba : {t.index0, t.index1, t.end - 1}) {
                    if (canonical_read(disc,lba,2352,out,sizeof out)!=Result::Ok) return false;
                    p6_sha256_update(&hash,out,2352);
                }
            }
            p6_sha256_final(&hash,id); return true;
        };
        if (layout.sessions) CHECK(delivery(before));
        auto fresh = std::make_unique<CdNrg>(); CHECK(fresh->open(argv[2], vfs, policy) == Result::Ok);
        const auto result = disc.rebind(*fresh->layout(), fresh->bindings(), fresh->sources());
        if (result == Result::Ok) owner.reset(); else fresh.reset();
        if (layout.sessions) CHECK(delivery(after)&&!memcmp(before,after,sizeof before));
        CHECK(canonical_read(disc, 0, 2352, out, sizeof out) == Result::Ok);
        printf("rebind %u ", unsigned(result)); digest(out, 2352); puts("");
    }
}
