// The native state envelope of docs/adr/0009-state-format.md: header, identity hashes and the validation that comes before a machine is
// touched. It knows nothing of the emulator; chunk bodies are opaque bytes here. tests/state/envelope.py is the reference encoder and
// validator this must agree with, byte for byte on the golden vector and verdict for verdict on every defect.
#ifndef P6_STATE_ENVELOPE_H
#define P6_STATE_ENVELOPE_H
#include <stddef.h>
#include <stdint.h>

namespace p6_state {

constexpr uint32_t kFormatVersion = 1;
constexpr uint32_t kHeaderSize = 336;
constexpr uint32_t kChunkHeaderSize = 16;       // tag (4), chunk_version u32, body_length u64
constexpr uint32_t kProfileA500 = 1;

// What a state is bound to: the build, the configuration, the media and the firmware.
struct Identity {
    uint32_t profile_id;
    uint8_t core_build_id[32];
    uint8_t upstream_commit[20];
    uint8_t patch_series_hash[32];
    uint8_t codegen_hash[32];
    uint8_t config_hash[32];
    uint8_t media_manifest_hash[32];
    uint8_t firmware_sha256[32];
};

// One chunk of the payload, in the order they must appear. `version` is the one this build writes and reads.
struct ChunkSpec {
    char tag[5];
    uint32_t version;
};

// A chunk found by validate(): a view into the caller's buffer.
struct ChunkView {
    const uint8_t *body;
    uint64_t length;
    uint32_t version;
};

// Why a state was refused. `code` is a p6_result (P6_ERROR_CORRUPT_STATE or P6_ERROR_INCOMPATIBLE), `field` names what was wrong.
struct Refusal {
    int32_t code;
    const char *field;
    const char *message;
};

// SHA-256 over "domain", a zero byte and the parts: two domains never give the same hash for the same bytes.
void domain_hash(const char *domain, const uint8_t *const *parts, const uint64_t *lengths, size_t count, uint8_t out[32]);

void config_hash(uint32_t machine, uint32_t video_standard, uint32_t audio_rate_hz, uint32_t flags, uint64_t random_seed,
                 int64_t rtc_epoch_seconds, uint8_t out[32]);
// `types[i]` and `content_ids[i]` in the order of the p6_load array.
void media_manifest_hash(uint32_t count, const uint32_t *types, const uint8_t *const *content_ids, uint8_t out[32]);
void core_build_id(const uint8_t upstream_commit[20], const uint8_t patch_series_hash[32], const uint8_t codegen_hash[32],
                   const uint8_t sources_hash[32], uint32_t profile_id, uint8_t out[32]);

// Writes the 336-byte header into state[0..kHeaderSize) for a payload already at state + kHeaderSize.
void seal(uint8_t *state, uint64_t payload_length, uint32_t chunk_count, const Identity &identity, uint64_t unit_index,
          uint64_t master_ticks);

// The validation order of the ADR: length, magic, version, header size, flags, header checksum, payload length (against `bound`),
// identity, payload checksum, chunk table (against `specs` and the per-chunk maxima `chunk_bounds`, which may be null). Nothing
// is allocated from a length before it has been checked. On success `chunks[i]` views the body of `specs[i]`.
bool validate(const uint8_t *data, uint64_t length, const Identity &expected, uint64_t bound, const ChunkSpec *specs, uint32_t spec_count,
              const uint64_t *chunk_bounds, ChunkView *chunks, uint64_t *unit_index, uint64_t *master_ticks, Refusal *refusal);

}  // namespace p6_state
#endif
