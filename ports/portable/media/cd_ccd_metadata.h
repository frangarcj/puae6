// Metadata-only export of pinned WinUAE parseccd; no filesystem/device API.
#pragma once
#include "cd_source.h"
namespace p6_cd {
enum CcdField { CcdSession, CcdPoint, CcdAdr, CcdControl, CcdTrackNo, CcdAMin, CcdASec, CcdAFrame,
                CcdALba, CcdZero, CcdPMin, CcdPSec, CcdPFrame, CcdPLba, CcdFields };
struct CcdEntry { int32_t value[CcdFields]; };
struct CcdTrack { int32_t mode, index0, index1; bool has_index0; };
struct CcdMetadata {
    uint32_t tracks, entries, pregap_mode, pregap_subc;
    CcdEntry entry[102]; // File ENTRY order, including A0/A1/A2 and their raw Q fields.
    CcdTrack track[99];
};
// Full line, bytes excluding NUL, 0=EOF, negative=I/O error. NUL-terminate.
// Limits:4095B/line,64KiB total. No IMG/SUB reads or borrowed file handles.
using CcdReadLine = int (*)(void *, char *, size_t);
Result ccd_metadata(void *context, CcdReadLine, CcdMetadata *out);
}
