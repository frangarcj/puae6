#include "chd_host.h"
#include "cd_chd_metadata.h"
#include <stddef.h>
#include <fcntl.h>
#include <sys/stat.h>
#ifdef _WIN32
#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <windows.h>
#include <io.h>
#include <limits.h>
#include <stdio.h>
#else
#include <unistd.h>
#endif
#include <errno.h>
#include <new>
#include <string.h>
using p6_cd::Result;
#ifndef _WIN32
static_assert(sizeof(off_t)>=8, "CHD host requires 64-bit file offsets");
#endif
static_assert(sizeof(p6_chd_host_info)==32 && offsetof(p6_chd_host_info,bytes)==24, "CHD info ABI");
static_assert(sizeof(p6_chd_host_track)==96 && offsetof(p6_chd_host_track,offset_frames)==40 && offsetof(p6_chd_host_track,type)==48, "CHD track ABI");
namespace {
struct File {
    int fd=-1;
    ~File() { if(fd>=0) {
#ifdef _WIN32
        _close(fd);
#else
        close(fd);
#endif
    } }
};
struct Owner { File file; p6_cd::ChdReader reader; p6_cd::ChdMetadata metadata{}; };
Result open_file(File &file, const char *path, uint64_t *bytes) {
#ifdef _WIN32
    const int units=MultiByteToWideChar(CP_UTF8,MB_ERR_INVALID_CHARS,path,-1,nullptr,0);
    if(units<=0) return Result::Invalid;
    std::unique_ptr<wchar_t[]> wide(new(std::nothrow) wchar_t[units]);
    if(!wide) return Result::ReadError;
    if(MultiByteToWideChar(CP_UTF8,MB_ERR_INVALID_CHARS,path,-1,wide.get(),units)!=units) return Result::Invalid;
    file.fd=_wopen(wide.get(),_O_RDONLY|_O_BINARY|_O_NOINHERIT);
    struct __stat64 st{};
    if(file.fd<0 || _fstat64(file.fd,&st) || (st.st_mode&_S_IFMT)!=_S_IFREG || st.st_size<=0) return Result::ReadError;
#else
    file.fd=open(path,O_RDONLY|O_CLOEXEC); struct stat st{};
    if(file.fd<0 || fstat(file.fd,&st) || !S_ISREG(st.st_mode) || st.st_size<=0) return Result::ReadError;
#endif
    *bytes=uint64_t(st.st_size); return Result::Ok;
}
bool read_file(const void *context, uint64_t offset, void *out, size_t count, size_t *got) {
    *got=0; if(offset>INT64_MAX) return false;
#ifdef _WIN32
    // This owner is never reentered/shared by threads; seek+read is one ABI call.
    if(count>INT_MAX || uint64_t(count)>uint64_t(INT64_MAX)-offset) return false;
    const int fd=static_cast<const Owner *>(context)->file.fd;
    if(_lseeki64(fd,static_cast<__int64>(offset),SEEK_SET)!=static_cast<__int64>(offset)) return false;
    int n;
    do { n=_read(fd,out,static_cast<unsigned>(count)); } while(n<0 && errno==EINTR);
#else
    ssize_t n;
    do { n=pread(static_cast<const Owner *>(context)->file.fd,out,count,off_t(offset)); } while(n<0 && errno==EINTR);
#endif
    if(n<0) return false;
    *got=size_t(n); return *got==count;
}
}
int P6_CHD_CALL p6_chd_host_open(const char *path, void **out, p6_chd_host_info *info) {
    if(!path || !out || !info || info->version!=1 || info->size!=sizeof(*info)) return int(Result::Invalid);
    std::unique_ptr<Owner> owner(new(std::nothrow) Owner);
    if(!owner) return int(Result::ReadError);
    uint64_t bytes=0; auto result=open_file(owner->file,path,&bytes);
    if(result!=Result::Ok) return int(result);
    result=owner->reader.open(read_file,owner.get(),bytes);
    if(result!=Result::Ok) return int(result);
    result=p6_cd::chd_metadata(owner->reader,&owner->metadata);
    if(result!=Result::Ok) return int(result);
    uint64_t end=0;
    for(unsigned i=0;i<owner->metadata.tracks;++i) {
        const auto &t=owner->metadata.track[i];
        if((!i && t.pregap && !t.stored_pregap) || !strcmp(t.type,"CDI/2352") || !strcmp(t.pgtype+t.stored_pregap,"CDI/2352")) return int(Result::Unsupported);
        end+=uint64_t(t.pregap)+t.frames-(t.stored_pregap?t.pregap:0)+t.postgap;
        if(end>100*60*75-150) return int(Result::Unsupported);
    }
    const p6_chd_host_info candidate{1,sizeof(*info),2448,owner->reader.hunk_bytes(),owner->metadata.tracks,0,owner->reader.bytes()};
    *info=candidate; *out=owner.release(); return int(Result::Ok);
}
int P6_CHD_CALL p6_chd_host_get_track(void *handle, uint32_t index, p6_chd_host_track *out) {
    if(!handle || !out || out->version!=1 || out->size!=sizeof(*out)) return int(Result::Invalid);
    const auto &m=static_cast<Owner *>(handle)->metadata;
    if(index>=m.tracks) return int(Result::Range);
    const auto &t=m.track[index];
    p6_chd_host_track candidate{1,sizeof(*out),t.number,t.frames,t.pregap,t.postgap,t.extra_frames,t.stored_pregap,t.flags,0,t.offset_frames,{},{},{},{}};
    memcpy(candidate.type,t.type,sizeof t.type); memcpy(candidate.subtype,t.subtype,sizeof t.subtype);
    memcpy(candidate.pgtype,t.pgtype,sizeof t.pgtype); memcpy(candidate.pgsub,t.pgsub,sizeof t.pgsub);
    *out=candidate; return int(Result::Ok);
}
int P6_CHD_CALL p6_chd_host_read(void *handle, uint64_t offset, void *out, uint32_t count) {
    return int(handle?static_cast<Owner *>(handle)->reader.read_at(offset,out,count):Result::Invalid);
}
void P6_CHD_CALL p6_chd_host_close(void *handle) { delete static_cast<Owner *>(handle); }
