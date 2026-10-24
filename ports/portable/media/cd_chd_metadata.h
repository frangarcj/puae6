// Private CHDv5 CD TOC, without provider/LBA synthesis or a public format claim.
#pragma once
#include "chd_reader.h"
namespace p6_cd {
struct ChdTrack {
    uint64_t offset_frames;
    uint32_t number, frames, pregap, postgap, extra_frames, data_bytes, pg_data_bytes;
    char type[16], subtype[8], pgtype[16], pgsub[8]; // exact spelling, including V
    bool stored_pregap;
    uint8_t flags;
};
struct ChdMetadata {
    uint32_t tracks;
    uint64_t padded_frames, tail_frames; // physical CHD units, never disc LBAs
    ChdTrack track[99];
};
// CHTR or CHT2 exclusively; max256B per text record. No fallback after I/O errors.
// Errors preserve out. Unknown/non-CD formats and mixed families are Unsupported.
Result chd_metadata(const ChdReader &, ChdMetadata *out);
}
