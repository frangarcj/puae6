#include "cd_mds_metadata.h"
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <vector>
using namespace p6_cd;
struct Input { std::vector<uint8_t> data; int fault; unsigned calls=0, bytes=0; };
static bool read(const void *context,uint64_t at,void *out,size_t length,size_t *got) {
    auto &s=*const_cast<Input *>(static_cast<const Input *>(context)); ++s.calls; s.bytes+=unsigned(length); *got=0;
    if(length>256 || s.bytes>65536) abort();
    if(s.fault==1 || (s.fault>3 && s.calls==unsigned(s.fault)) || at>s.data.size() || length>s.data.size()-at) return false;
    memcpy(out,s.data.data()+at,length);
    *got=s.fault==2 ? length-1:s.fault==3 ? length+1:length; return true;
}
static void toc(const MdsToc &t) {
    printf(" %u %u %u %u %u %u %u %u %u %u",t.adr_control,t.track_number,t.point,t.amin,t.asec,t.aframe,t.zero,t.pmin,t.psec,t.pframe);
}
int main(int argc,char **argv) {
    if(argc!=4) return 64;
    Input s{{},atoi(argv[3])}; FILE *f=fopen(argv[1],"rb"); if(!f) return 66;
    for(int c;(c=fgetc(f))!=EOF;) s.data.push_back(uint8_t(c)); fclose(f);
    struct { uint64_t before; MdsMetadata metadata; uint64_t after; } out;
    memset(&out,0xa5,sizeof out);
    const auto result=mds_metadata(&s,read,strtoull(argv[2],nullptr,10),&out.metadata);
    if(out.before!=UINT64_C(0xa5a5a5a5a5a5a5a5) || out.after!=out.before) return 70;
    if(result!=Result::Ok) {
        for(auto p=reinterpret_cast<const uint8_t *>(&out.metadata);p!=reinterpret_cast<const uint8_t *>(&out.metadata+1);++p) if(*p!=0xa5) return 71;
        printf("error=%u\n",unsigned(result)); return 2;
    }
    const auto &m=out.metadata;
    printf("mds %u %u %u %u %u %u %d %d %u\n",m.version[0],m.version[1],m.medium,m.session_number,m.first_track,m.last_track,m.session_start,m.session_end,m.tracks);
    printf("name %u %s\n",m.wildcard,m.filename);
    for(const auto &t:m.lead) { printf("lead"); toc(t); puts(""); }
    for(unsigned n=0;n<m.tracks;n++) {
        const auto &t=m.track[n]; printf("track %d %llu %u %u %u %u %u %u",t.start_sector,(unsigned long long)t.start_offset,t.pregap,t.sectors,t.sector_size,t.mode,t.subchannel,t.wide_name);
        toc(t.toc); puts("");
    }
}
