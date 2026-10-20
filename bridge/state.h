// The native state of a loaded image (docs/adr/0009-state-format.md): a registry of chunks, one visitor function each, over the pure
// container of ports/portable/state_io. The public p6_state_* functions of p6.h are defined in state.cpp and, since the closure of T15, work:
// every chunk validates and applies, and DET02 (the future of a loaded state, bit for bit), STATE01 (the bound), DET03 (through the public ABI,
// across instances) and FUZZ01 (hostile states) hold. A build that links this library announces P6_CAP_STATE (state_linked, below); one that does
// not (the headless runner, the frontend adapters and the Waterbox guest take p6_bridge only) neither defines the functions nor claims the capability. The tests reach the
// registry through the Options below.
#ifndef P6_BRIDGE_STATE_H
#define P6_BRIDGE_STATE_H
#include "p6.h"
#include "state_io/archive.h"
#include "state_io/envelope.h"

namespace p6_state {

// What the state being loaded says about the media of the drives. `drive_media` is the manifest index each drive holds (P6BR; -1: none), read while P6BR validates;
// `held` is the medium the engine's drive has to hold once the disk chunk is applied, which differs only for a drive that counts down a delayed change (its bodies say
// it is empty while P6BR names the medium it waits for). Both come from the state alone: the media are the ones of the manifest, which the identity has already compared.
struct MediaPlan {
    int32_t drive_media[4];
    int32_t held[4];
};

// One chunk of the payload. `visit` describes the chunk's state once, for saving, measuring, validating and applying (archive.h);
// null means the chunk is not written yet. `rebuild` runs after every chunk has been applied, to derive what is not saved. `manifest` checks the body
// against the media manifest of the state (the registered media, which every loaded image has, not the drives of the live machine) once every chunk has been
// validated and before the machine is touched: a reason refuses the state as INCOMPATIBLE, and it fills in the plan the chunk's apply follows.
struct Chunk {
    const char *tag;
    const char *name;
    uint32_t version;
    void (*visit)(Archive &);
    void (*rebuild)();
    const char *(*manifest)(const uint8_t *body, uint64_t length, MediaPlan &plan) = nullptr;
};

// The kill switch of the release. While it is false the public p6_state_* functions say UNSUPPORTED (the tests still reach the registry through
// Options::allow_placeholders) and p6_query_caps leaves P6_CAP_STATE clear, which is what a state that restores part of a machine, or one whose
// future has not been proven bit for bit, must do (docs/02-contracts.md). T15i2 gave every chunk a visitor that validates and applies, T15d-T15g
// found and closed what the bodies of WinUAE lose (the beam, the Denise ring, the Copper wait, the sprite DMA state, the audio filter, the drives),
// and the closure of T15 turned it on with STATE01, DET02, DET03 and FUZZ01.
constexpr bool kReleased = true;

// Defined in bridge/caps.cpp (p6_bridge, which every build has) and set by the static initialisation of state.cpp, which only an image that links
// this library runs: p6_query_caps announces P6_CAP_STATE exactly in the images that carry the functions.
}  // namespace p6_state
namespace p6_bridge { extern bool state_linked; }
namespace p6_state {

// The payload order of the ADR: cpu, scheduler, ram, rtc, chipset, video, audio, cia, disk, input, bridge.
constexpr uint32_t kChunkCount = 11;
constexpr uint32_t kMaxChunkCount = kChunkCount + 1; // CD32 appends P6AK; legacy stays eleven.
extern const Chunk kChunks[kMaxChunkCount];
uint32_t chunk_count();
// P6SC publishes its validated cycle for later chunks, independent of live time.
void publish_scheduler_cycle(int64_t);
int64_t scheduler_cycle(Mode);
// P6CP publishes the incoming flags; P6RM checks cartridge control against that candidate.
void publish_cpu_spc(uint32_t);
uint32_t cpu_spc(Mode);
void visit_akiko(Archive &);

// The chunk visitors that live outside state.cpp (state_cpu.cpp, state_devices.cpp).
void visit_cpu(Archive &a);
void visit_scheduler(Archive &a);
void rebuild_scheduler();
void visit_rtc(Archive &a);
void visit_cia(Archive &a);
void rebuild_cia();
void visit_input(Archive &a);
// The bodies of WinUAE's own save_* functions, as sub-bodies (state_upstream.cpp): P6DK, P6CH, P6AU and P6VD.
void visit_disk(Archive &a);
void rebuild_disk();
void visit_chipset(Archive &a);
void visit_audio(Archive &a);
void visit_video(Archive &a);
// The disk body against the manifest (state_upstream.cpp): nullptr, or why the media the drives of the state hold are not the ones P6BR names.
const char *disk_matches_manifest(const uint8_t *body, uint64_t length, MediaPlan &plan);
// The plan of the load in progress (state.cpp), or null outside a load: the apply of P6DK puts the media the state names into the drives.
const MediaPlan *loading_media();
// The size of the manifest the state in hand was taken with (state.cpp): every medium index a body carries is below it.
uint32_t manifest_count();
// The autoconfig chain of the Zorro II boards (state.cpp; the Fast RAM board has `fast` bytes, 0 for a machine without one) and the hard-file section of P6DK (state_hdf.cpp):
// the controller of the UAE hard files and the overlay of their writes, present only while a hard file is mounted. hdf_matches_manifest is what the section says about the
// media (the content id of every hard file against the registered one, before the machine is touched): nullptr, or why the state's hard files are not this image's.
void visit_expansion(Archive &a, uint32_t fast);
void visit_hdf(Archive &a);
const char *hdf_matches_manifest(const uint8_t *section, uint64_t length);

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
