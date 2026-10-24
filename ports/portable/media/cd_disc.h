// Private normalized CD layout, not a format parser or CD32 device.
#ifndef P6_MEDIA_CD_DISC_H
#define P6_MEDIA_CD_DISC_H
#include "cd_source.h"
namespace p6_cd {
constexpr uint32_t kMaxTracks = 99, kMaxSources = 99;
enum class Mode { Mode1_2048, Mode1Raw, Mode2Raw, Audio };
struct Track {
    uint32_t index0, index1, end; // disc LBAs, end exclusive; contiguous within each session
    uint32_t source, stride;     // source ordinal; 2048, 2352 or 2448 bytes
    uint64_t offset;             // first stored sector: INDEX00 if stored_pregap, else INDEX01
    Mode mode;
    uint8_t adr, control;        // ADR 1; control bit 2 distinguishes data from audio
    bool stored_pregap;          // false: INDEX00..01 is synthesized zero data/silence
    uint32_t synthetic_pregap = 0, postgap = 0; // zero preserves existing descriptors/wire
    struct Regions {
        enum : uint32_t { Explicit=1, ProgramSub96=2, PregapSub96=4, PostgapStored=8, PostgapSub96=16, PostgapIndex2=32 };
        uint32_t flags = 0; // zero selects the complete legacy policy; no other fields may be set
        Mode pregap_mode = Mode::Mode1_2048;
        uint32_t pregap_control = 0;
    } regions{};
};
struct Session {
    uint8_t first_track, last_track, format; // global 1..99; format 00/10/20
    int32_t cue0;                           // signed normalized boundary: explicit CUE0 or declared canonical policy
    uint32_t a2;                            // explicit program leadout LBA
};
struct Layout {
    uint8_t content_id[32];      // host verifies disc identity, including normalized TOC and sources
    uint32_t tracks, leadout;
    Track track[kMaxTracks];    // numbers 1..tracks; unused slots are ignored
    uint32_t sessions = 0;      // zero retains the entire legacy single-session policy
    Session session[kMaxTracks]{}; // in order; inactive fields must be zero
};
// Effective metadata never comes from stored Q, which may be intentionally invalid.
struct SectorInfo {
    Track effective;
    uint32_t number;
    bool stored_subs, pause, audio_gap, synthetic;
    uint64_t source_offset; // only meaningful for a stored sector
};
struct Binding { Identity identity; ReadFn read; const void *context; };
class Disc {
public:
    Result bind(const Layout &, const Binding *, uint32_t count);
    Result rebind(const Layout &, const Binding *, uint32_t count);
    Result describe(uint32_t lba, SectorInfo *) const;
    const Layout &layout() const { return layout_; }
    // One sector, exact 2048/2352/2448-byte view. Failure leaves output intact.
    // 2048 is Mode1 only; normalized 2448 = 2352 data + 96 P-W deinterleaved.
    // Disc preserves supplied audio bytes; CDDA interpretation belongs to its consumer.
    Result read_sector(uint32_t lba, uint32_t view, void *out, size_t capacity) const;
private:
    Result prepare(const Layout &, const Binding *, uint32_t);
    Layout layout_{};
    Source sources_[kMaxSources];
    uint32_t count_ = 0;
};
} // namespace p6_cd
#endif
