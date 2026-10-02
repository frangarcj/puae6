// The native state of a loaded image (docs/adr/0009-state-format.md): a registry of chunks, one visitor function each, over the pure
// container of ports/portable/state_io. The public p6_state_* functions of p6.h are defined in state.cpp; they refuse (UNSUPPORTED) until
// every chunk of the registry is implemented, because a state with chunks missing would restore only part of a machine and nothing
// may advertise that as rewind (docs/02-contracts.md). The tests reach the registry through the Options below.
#ifndef P6_BRIDGE_STATE_H
#define P6_BRIDGE_STATE_H
#include "p6.h"
#include "archive.h"
#include "envelope.h"

namespace p6_state {

// One chunk of the payload. `visit` describes the chunk's state once, for saving, measuring, validating and applying (archive.h);
// null means the chunk is not written yet. `rebuild` runs after every chunk has been applied, to derive what is not saved.
struct Chunk {
    const char *tag;
    const char *name;
    uint32_t version;
    void (*visit)(Archive &);
    void (*rebuild)();
};

// The payload order of the ADR: cpu, scheduler, ram, rtc, chipset, video, audio, cia, disk, input, bridge.
constexpr uint32_t kChunkCount = 11;
extern const Chunk kChunks[kChunkCount];

// The chunk visitors that live outside state.cpp (state_cpu.cpp, state_devices.cpp).
void visit_cpu(Archive &a);
void visit_scheduler(Archive &a);
void rebuild_scheduler();
void visit_rtc(Archive &a);
void visit_cia(Archive &a);
void rebuild_cia();
void visit_input(Archive &a);

// A value of the machine that travels as a fixed-width integer in [lo, hi]. Returns what was read (Save: the live value), which is the only thing a
// cross-check may use: Validate stores nothing, so the live variable it came from belongs to another machine.
template <class W> W get(Archive &a, long long live, long long lo, long long hi, const char *name)
{
    if (a.mode() == Mode::Save) a.require(live >= lo && live <= hi, "a live value of the machine is outside the range of its field");
    return a.val<W>(W(live), W(lo), W(hi), name);
}

// Test-only knobs; the public functions use the defaults.
struct Options {
    // Write and accept a chunk that is not implemented as an empty body of version 0, so that the container and the chunks that
    // exist can be tested before the rest. A state made this way restores part of a machine and is never a valid p6_state_save.
    bool allow_placeholders = false;
    // Make the apply pass of the first chunk fail after every body has been validated, as a defect of a visitor would: the image must end
    // Faulted (only p6_unload recovers it) and the error must say so.
    bool fail_apply = false;
    // The same for the reset head that a load into an image that has not started runs first: it reports a failure after it ran.
    bool fail_head = false;
    // Apply and rebuild only the chunks whose bit is set (bit i = kChunks[i]); 0 means all of them. Every chunk is still validated. A test
    // asks a chunk alone to put its subsystem back, to compare it with what it was when the state was taken.
    uint32_t only_chunks = 0;
    // Called by save() for every field written: the chunk index, the offset of the field in the whole state, its size and its name. For
    // the tests that change one field of a saved body.
    void (*on_field)(void *context, uint32_t chunk, uint64_t offset, uint64_t size, const char *name) = nullptr;
    void *on_field_context = nullptr;
};

// The build as the envelope binds it (build_identity.cpp).
struct BuildIdentity {
    uint32_t profile_id;
    uint8_t upstream_commit[20];
    uint8_t patch_series_hash[32];
    uint8_t codegen_hash[32];
    uint8_t sources_hash[32];
    uint8_t core_build_id[32];
};
const BuildIdentity &build_identity();

// What the loaded image is bound to: this build, the configuration, the media and the firmware.
Identity current_identity();

// True when every chunk has a visitor; otherwise `missing` (when not null) gets the names of those that have not.
bool complete(char *missing, size_t capacity);

p6_result bound(uint64_t *bytes, const Options &options, p6_error *error);
p6_result save(void *buffer, uint64_t capacity, uint64_t *written, const Options &options, p6_error *error);
p6_result load(const void *buffer, uint64_t bytes, const Options &options, p6_error *error);

}  // namespace p6_state
#endif
