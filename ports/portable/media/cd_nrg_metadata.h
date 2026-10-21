// Bounded pinned WinUAE parsenrg metadata plus canonical ETNF/ETN2; no handles.
#pragma once
#include "cd_source.h"
namespace p6_cd {
enum class NrgLayout : uint8_t { Dao, EtnfCanonical150, Etn2Canonical150 };
struct NrgTrack {
    uint64_t offset0, offset1, end;
    int32_t index0, index1;
    uint16_t stride;
    uint8_t mode, adr, control;
    bool has_index0;
    uint32_t start_sector; // ETNF/ETN2 raw packed coordinate; absent in DAO.
    char isrc[12];
};
struct NrgMetadata {
    uint64_t directory;
    uint32_t tracks, version;
    int32_t leadin, leadout;
    uint8_t disk_type, session_hint;
    NrgLayout layout;
    char catalog[13];
    NrgTrack track[99];
};
// Exact offset reads, at most256B/request; no payload reads. Input size is the
// whole container. Errors leave out intact. Single-session DAO v1/v2 or ETNF/ETN2.
// ETNF/ETN2 offset0 == offset1 points to data, never a stored pregap. INDEX00/01 and
// leadout use canonical150, as GNUlibcdio68ea374f; original pregaps are not stored.
// VCDImager23f07386 image_nrg.c:_map/_write_tail omits them, even if nonstandard.
// ETN2 is NER5-only; byte extents are64-bit, its packed sector remains32-bit.
// libMirage a9d6a204 parser.c:373..383/835..883 shares the canonical150 mapping.
Result nrg_metadata(const void *context, ReadFn, uint64_t bytes, NrgMetadata *out);
}
