// Metadata-only export of pinned WinUAE parsecue; no device or filesystem API.
#pragma once
#include "cd_disc.h"
#include "cd_wave.h"
namespace p6_cd {
struct CueSource { uint32_t ordinal; uint64_t bytes; };
struct CueTrack {
    uint32_t source, stride, index0, index1, pregap, postgap;
    Mode mode;
    uint8_t control;
    bool has_index0;
};
struct CueMetadata { uint32_t tracks; CueTrack track[kMaxTracks]; };
struct CueIo {
    void *context;
    // Return bytes excluding NUL, 0 at EOF, negative on error. NUL-terminate.
    // Maximum line4095B and total64KiB. EOF may follow a line without newline.
    int (*read_line)(void *, char *out, size_t capacity);
    // Borrowed metadata: caller owns/deduplicates CdFile providers, validates WAV
    // via e1 and retains them for the last Disc read. No CD payload is returned.
    Result (*resolve)(void *, const char *name, Encoding, CueSource *out);
};
Result cue_metadata(const CueIo &, CueMetadata *out);
}
