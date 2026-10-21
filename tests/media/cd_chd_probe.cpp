#include "../../adapters/libretro/cd_chd.h"
#include "cd_read.h"
#include "cd_akiko.h"
#include "sha256.h"
#include "libretro.h"
#include "uae/cdrom.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <memory>
using namespace p6_cd;
using p6_libretro::CdChd;
#define CHECK(x) do { if (!(x)) { fprintf(stderr,"line%d: %s\n",__LINE__,#x); return 70; } } while (0)
struct retro_vfs_file_handle { FILE *file; };
static int fault;
static bool whole, views;
static retro_vfs_file_handle *RETRO_CALLCONV vopen(const char *p, unsigned, unsigned) { FILE *f = fopen(p,"rb"); return f ? new retro_vfs_file_handle{f} : nullptr; }
static int RETRO_CALLCONV vclose(retro_vfs_file_handle *f) { int s = fclose(f->file); delete f; return s; }
static int64_t RETRO_CALLCONV vtell(retro_vfs_file_handle *f) { return ftello(f->file); }
static int64_t RETRO_CALLCONV vsize(retro_vfs_file_handle *f) { auto p = ftello(f->file); fseeko(f->file,0,SEEK_END); auto n = ftello(f->file); fseeko(f->file,p,SEEK_SET); return n; }
static int64_t RETRO_CALLCONV vseek(retro_vfs_file_handle *f, int64_t at, int origin) { if (whole && at == 124) views = true; return fseeko(f->file,at,origin); }
static int64_t RETRO_CALLCONV vread(retro_vfs_file_handle *f, void *out, uint64_t length) {
    if (fault == 1 || (fault == 2 && length > 2448) || (fault == 3 && views)) return -1;
    if (fault == 4) return 0;
    if (length > 2448) whole = true;
    return fread(out,1,size_t(length < 3 ? length : 3),f->file);
}
static void print_id(const uint8_t *hash) { for (unsigned n = 0; n < 32; ++n) printf("%02x",hash[n]); }
static void digest(const void *data, size_t n) { uint8_t hash[32]; p6_sha256_hash(data,n,hash); print_id(hash); }
static bool untouched(const uint8_t *data, size_t n) { for (size_t i = 0; i < n; ++i) if (data[i] != 0xa5) return false; return true; }
int main(int argc, char **argv) {
    if (argc == 2 && !strcmp(argv[1],"--form2-edc")) {
        uint8_t sector[2368], original[2352]; memset(sector,0xa5,sizeof sector);
        for (unsigned n = 0; n < 2352; ++n) sector[8+n] = uint8_t(n*37+11);
        memcpy(original,sector+8,sizeof original); encode_l2_mode2_form2(sector+8);
        CHECK(untouched(sector,8) && untouched(sector+2360,8));
        CHECK(!memcmp(original,sector+8,2348)); // Arbitrary header/subheader/payload stay exact.
        printf("form2-edc "); digest(sector+8,2352); puts(""); return 0;
    }
    CHECK(argc == 2 || argc == 3); retro_vfs_interface api{};
    api.open=vopen; api.close=vclose; api.tell=vtell; api.size=vsize; api.seek=vseek; api.read=vread;
    const auto *vfs = getenv("P6_CHD_VFS") ? &api : nullptr;
    if (const auto *value = getenv("P6_CHD_FAULT")) fault = atoi(value);
    auto owner = std::make_unique<CdChd>(); const auto status = owner->open(argv[1],vfs);
    if (status != Result::Ok) { CHECK(!owner->layout() && !owner->metadata() && !owner->bindings() && !owner->sources() && !owner->container_identity()); printf("error %u\n",unsigned(status)); return 2; }
    CHECK(owner->open(argv[1],vfs) == Result::Invalid); Disc disc;
    CHECK(disc.bind(*owner->layout(),owner->bindings(),owner->sources()) == Result::Ok);
    const auto &id = *owner->container_identity(); printf("container %llu ",(unsigned long long)id.bytes); print_id(id.content_id); puts("");
    const auto &layout = disc.layout(); printf("disc %u %u ",layout.tracks,layout.leadout); print_id(layout.content_id); puts("");
    cd_toc_head toc{}; CHECK(akiko_toc(disc,&toc) == Result::Ok);
    for (int n=0; n<toc.points; ++n) {
        const auto &t=toc.toc[n];
        printf("toc %u %u %u %u %d\n",unsigned(t.point),unsigned(t.adr),unsigned(t.control),unsigned(t.track),t.paddress);
    }
    uint8_t out[2464]; size_t got = 123;
    for (unsigned n = 0; n < owner->sources(); ++n) {
        const auto &b = owner->bindings()[n]; const auto &t = layout.track[n];
        printf("track %u %u %u %u %u %llu %u %u %u %u %u %u\n",t.index0,t.index1,t.end,t.source,t.stride,(unsigned long long)t.offset,unsigned(t.mode),t.adr,t.control,t.stored_pregap,t.synthetic_pregap,t.postgap);
        printf("regions %u %u %u %u\n",n,t.regions.flags,unsigned(t.regions.pregap_mode),t.regions.pregap_control);
        printf("source %u %llu ",n,(unsigned long long)b.identity.bytes); print_id(b.identity.content_id); puts("");
        const uint64_t at = t.stride-8; const size_t length = size_t(b.identity.bytes-at < 2448 ? b.identity.bytes-at : 2448);
        CHECK(b.read(b.context,at,out,length,&got) && got == length);
        printf("bytes %u %llu %zu ",n,(unsigned long long)at,length); digest(out,length); puts("");
        const uint64_t last = b.identity.bytes > t.stride+8 ? b.identity.bytes-t.stride-8 : 0;
        const size_t tail = size_t(b.identity.bytes-last < 2448 ? b.identity.bytes-last : 2448);
        CHECK(b.read(b.context,last,out,tail,&got) && got == tail);
        printf("bytes %u %llu %zu ",n,(unsigned long long)last,tail); digest(out,tail); puts("");
        memset(out,0xa5,sizeof out); CHECK(b.read(b.context,b.identity.bytes,nullptr,0,&got) && !got);
        CHECK(!b.read(b.context,0,nullptr,1,&got) && !got && untouched(out,sizeof out));
        CHECK(!b.read(b.context,0,out,1,nullptr) && untouched(out,sizeof out));
        for (const auto &bad : {std::pair{b.identity.bytes,size_t(1)}, {UINT64_MAX,size_t(1)}, {uint64_t(0),size_t(2449)}})
            CHECK(!b.read(b.context,bad.first,out,bad.second,&got) && !got && untouched(out,sizeof out));
    }
    for (uint32_t lba = 0; lba < layout.leadout; ++lba) {
        SectorInfo info; CHECK(describe_sector(disc,lba,&info) == Result::Ok);
        memset(out,0xa5,sizeof out);
        CHECK(disc.read_sector(lba,2448,out,2448)==(info.stored_subs ? Result::Ok : Result::Unsupported));
        if (!info.stored_subs) CHECK(untouched(out,sizeof out));
        printf("info %u %u %u %u %u\n",lba,info.effective.control,info.stored_subs,info.pause,info.audio_gap);
        for (unsigned view : {2048u,2352u,2448u}) {
            memset(out,0xa5,sizeof out); const auto r = canonical_read(disc,lba,view,out+8,view);
            CHECK(untouched(out,8) && untouched(out+8+view,sizeof out-8-view)); printf("sector %u %u %u ",lba,view,unsigned(r));
            if (r == Result::Ok) digest(out+8,view); else CHECK(untouched(out,sizeof out)); puts("");
        }
    }
    if (vfs) for (unsigned n = 0; n < layout.tracks; ++n) if (layout.track[n].stride == 2448) {
        uint8_t saved[2448]; const auto &t = layout.track[n]; const auto &b = owner->bindings()[n];
        // Choose a different CHD hunk even for the final source before inducing I/O failure.
        const auto &other = layout.track[n+1 < layout.tracks ? layout.tracks-1 : 0];
        const auto evict = n+1 < layout.tracks ? other.end-other.postgap-1 : other.index1;
        for (uint32_t lba : {t.stored_pregap ? t.index0 : t.index1,t.index1}) {
            SectorInfo info; CHECK(disc.describe(lba,&info)==Result::Ok);
            CHECK(canonical_read(disc,lba,2448,saved,sizeof saved) == Result::Ok);
            CHECK(canonical_read(disc,evict,2448,out,sizeof out) == Result::Ok);
            for (int error : {1,4}) {
                fault=error; memset(out,0xa5,sizeof out);
                CHECK(!b.read(b.context,t.stride-8,out,2448,&got) && !got && untouched(out,sizeof out));
                CHECK(canonical_read(disc,lba,2448,out,2448) == Result::ReadError && untouched(out,sizeof out));
                if (info.stored_subs) CHECK(read_subchannel(disc,lba,out,96) == Result::ReadError && untouched(out,sizeof out));
                else CHECK(read_subchannel(disc,lba,out,96)==Result::Ok&&!memcmp(out,saved+2352,96)&&untouched(out+96,sizeof out-96));
                fault=0; CHECK(canonical_read(disc,lba,2448,out,2448) == Result::Ok && !memcmp(out,saved,2448));
                CHECK(canonical_read(disc,evict,2448,out,sizeof out) == Result::Ok);
            }
        }
        if (t.postgap) {
            CHECK(canonical_read(disc,t.end-1,2448,saved,sizeof saved)==Result::Ok);
            fault=1; CHECK(canonical_read(disc,t.end-1,2448,out,2448)==Result::Ok&&!memcmp(out,saved,2448)); fault=0;
        }
    }
    if (argc == 3) {
        auto fresh = std::make_unique<CdChd>(); CHECK(fresh->open(argv[2],vfs) == Result::Ok);
        const auto result = disc.rebind(*fresh->layout(),fresh->bindings(),fresh->sources());
        if (result == Result::Ok) owner.reset(); else fresh.reset();
        p6_sha256 hash; p6_sha256_init(&hash);
        for (uint32_t lba = 0; lba < layout.leadout; ++lba) { CHECK(canonical_read(disc,lba,2448,out,2448) == Result::Ok); p6_sha256_update(&hash,out,2448); }
        uint8_t id[32]; p6_sha256_final(&hash,id); printf("rebind %u ",unsigned(result)); print_id(id); puts("");
    }
}
