#include "../../adapters/libretro/cd_image.h"
#include "cd_read.h"
#include "sha256.h"
#include "libretro.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <memory>
using namespace p6_cd;
using p6_libretro::CdImage;
#define CHECK(x) do { if (!(x)) { fprintf(stderr,"line%d: %s\n",__LINE__,#x); return 70; } } while (0)
struct retro_vfs_file_handle { FILE *file; };
static unsigned opens, closes;
static bool fault;
static retro_vfs_file_handle *RETRO_CALLCONV vopen(const char *path, unsigned, unsigned) { FILE *f = fopen(path,"rb"); if (!f) return nullptr; ++opens; return new retro_vfs_file_handle{f}; }
static int RETRO_CALLCONV vclose(retro_vfs_file_handle *f) { ++closes; const int r = fclose(f->file); delete f; return r; }
static int64_t RETRO_CALLCONV vtell(retro_vfs_file_handle *f) { return ftell(f->file); }
static int64_t RETRO_CALLCONV vsize(retro_vfs_file_handle *f) { const auto pos = ftell(f->file); fseek(f->file,0,SEEK_END); const auto n = ftell(f->file); fseek(f->file,pos,SEEK_SET); return n; }
static int64_t RETRO_CALLCONV vseek(retro_vfs_file_handle *f, int64_t at, int origin) { return fseek(f->file,long(at),origin); }
static int64_t RETRO_CALLCONV vread(retro_vfs_file_handle *f, void *out, uint64_t n) { return fault ? -1 : int64_t(fread(out,1,size_t(n < 7 ? n : 7),f->file)); }
static bool untouched(const uint8_t *out, size_t n) { for (size_t i=0;i<n;++i) if(out[i]!=0xa5) return false; return true; }
static void digest(const void *data, size_t n) { uint8_t hash[32]; p6_sha256_hash(data,n,hash); for (auto b:hash) printf("%02x",b); }
int main(int argc, char **argv)
{
    if (argc < 2 || argc > 3) return 64;
    retro_vfs_interface api{}; api.open=vopen; api.close=vclose; api.tell=vtell; api.size=vsize; api.seek=vseek; api.read=vread;
    const auto *vfs = getenv("P6_CD_IMAGE_VFS") ? &api : nullptr;
    auto owner=std::make_unique<CdImage>(); CHECK(owner->open(nullptr,vfs)==Result::Invalid);
    const auto status=owner->open(argv[1],vfs);
    if (status!=Result::Ok) {
        CHECK(!owner->layout() && !owner->bindings() && !owner->sources() && opens==closes);
        printf("error %u opens %u\n",unsigned(status),opens); return 2;
    }
    CHECK(owner->open(argv[1],vfs)==Result::Invalid);
    Disc disc; CHECK(disc.bind(*owner->layout(),owner->bindings(),owner->sources())==Result::Ok);
    const auto &l=disc.layout(); printf("disc %u %u %u ",l.tracks,l.leadout,owner->sources()); for(auto b:l.content_id) printf("%02x",b); puts("");
    uint8_t out[2464];
    for(unsigned n=0;n<owner->sources();++n) {
        const auto &b=owner->bindings()[n]; printf("source %llu %u ",(unsigned long long)b.identity.bytes,b.identity.sector_bytes); for(auto c:b.identity.content_id) printf("%02x",c); puts("");
        Source source; CHECK(source.bind(b.identity,b.read,b.context)==Result::Ok);
        memset(out,0xa5,sizeof out); CHECK(source.read_at(b.identity.bytes,out+8,1)==Result::Range && untouched(out,sizeof out));
    }
    for(unsigned n=0;n<l.tracks;++n) for(uint32_t lba:{l.track[n].index0,l.track[n].index1,l.track[n].end-1}) {
        memset(out,0xa5,sizeof out); CHECK(canonical_read(disc,lba,2352,out+8,2352)==Result::Ok);
        CHECK(untouched(out,8) && untouched(out+2360,sizeof out-2360));
        printf("sector %u ",lba); digest(out+8,2352); puts("");
    }
    if(vfs) {
        CHECK(opens>closes); fault=true; memset(out,0xa5,sizeof out);
        CHECK(canonical_read(disc,l.track[0].index1,2352,out+8,2352)==Result::ReadError && untouched(out,sizeof out)); fault=false;
    }
    if(argc==3) {
        auto fresh=std::make_unique<CdImage>(); CHECK(fresh->open(argv[2],vfs)==Result::Ok);
        const auto result=disc.rebind(*fresh->layout(),fresh->bindings(),fresh->sources());
        if(result==Result::Ok) owner.reset(); else fresh.reset();
        CHECK(canonical_read(disc,0,2352,out,sizeof out)==Result::Ok);
        printf("rebind %u ",unsigned(result)); digest(out,2352); puts("");
    }
    disc=Disc{}; owner.reset(); CHECK(opens==closes); puts("closed");
}
