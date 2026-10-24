// MDS v1 metadata from pinned WinUAE parsemds; no file handles or payload reads.
#pragma once
#include "cd_source.h"
namespace p6_cd {
struct MdsToc {
    uint8_t adr_control, track_number, point, amin, asec, aframe, zero, pmin, psec, pframe;
};
struct MdsTrack {
    MdsToc toc;
    int32_t start_sector; // Raw signed PLBA at track+36; NOT an inferred INDEX00/01.
    uint64_t start_offset; // Raw MDF byte offset at track+40; no pregap/150 adjustment.
    uint32_t pregap, sectors; // Raw extra+0/+4, not a choice of stored/synthetic pregap.
    uint16_t sector_size;
    uint8_t mode, subchannel, wide_name;
};
struct MdsMetadata {
    uint8_t version[2];
    uint16_t medium, session_number, first_track, last_track;
    int32_t session_start, session_end;
    uint32_t tracks;
    bool wildcard; // Literal *.mdf (or absent filename); expansion belongs to the owner.
    char filename[1024]; // Well-formed UTF-8, decoded from ASCII or UTF16LE; no path resolution.
    MdsToc lead[3]; // A0/A1/A2 in that order; raw binary fields, not BCD conversion.
    MdsTrack track[99];
};
// v1.x CD, session1, one shared MDF name, compatible2048/2352/2448 geometry.
// At most256B/request and64KiB total reads, independent of descriptor/MDF size.
// Errors preserve out. Unsupported versions/sessions/modes/multipart are explicit.
Result mds_metadata(const void *context, ReadFn, uint64_t bytes, MdsMetadata *out);
}
