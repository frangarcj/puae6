// Bounded metadata export of pinned WinUAE parsenrg; no handles or sector copies.
#pragma once
#include "cd_source.h"
namespace p6_cd {
struct NrgTrack {
    uint64_t offset0, offset1, end;
    int32_t index0, index1;
    uint16_t stride;
    uint8_t mode, adr, control;
    bool has_index0;
    char isrc[12];
};
struct NrgMetadata {
    uint64_t directory;
    uint32_t tracks, version;
    int32_t leadin, leadout;
    uint8_t disk_type, session_hint;
    char catalog[13];
    NrgTrack track[99];
};
// Exact offset reads, at most256B/request; no payload reads. Input size is the
// whole container. Errors leave out intact. Only single-session DAO v1/v2 here.
Result nrg_metadata(const void *context, ReadFn, uint64_t bytes, NrgMetadata *out);
}
