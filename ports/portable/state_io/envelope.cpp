#include "envelope.h"

#include "p6.h"
#include "sha256.h"

#include <string.h>

namespace p6_state {

namespace {

const uint8_t kMagic[8] = {'P', '6', 'N', 'A', 'T', 'I', 'V', 'E'};
const uint8_t kWaterboxMagic[8] = {'P', '6', 'W', 'A', 'T', 'E', 'R', 'B'};

// Header offsets (the table of the ADR).
enum : uint32_t {
    kOffFormat = 8, kOffHeaderSize = 12, kOffProfile = 16, kOffFlags = 20, kOffUnit = 24, kOffTicks = 32, kOffPayload = 40,
    kOffChunks = 48, kOffReserved0 = 52, kOffBuild = 56, kOffCommit = 88, kOffReserved1 = 108, kOffSeries = 112, kOffCodegen = 144,
    kOffConfig = 176, kOffMedia = 208, kOffFirmware = 240, kOffPayloadHash = 272, kOffHeaderHash = 304
};

void put32(uint8_t *p, uint32_t v) { for (int i = 0; i < 4; ++i) p[i] = uint8_t(v >> (8 * i)); }
void put64(uint8_t *p, uint64_t v) { for (int i = 0; i < 8; ++i) p[i] = uint8_t(v >> (8 * i)); }
uint32_t get32(const uint8_t *p) { uint32_t v = 0; for (int i = 3; i >= 0; --i) v = (v << 8) | p[i]; return v; }
uint64_t get64(const uint8_t *p) { uint64_t v = 0; for (int i = 7; i >= 0; --i) v = (v << 8) | p[i]; return v; }

void hash_with_domain(const char *domain, const uint8_t *data, uint64_t length, uint8_t out[32])
{
    const uint8_t *parts[1] = {data};
    domain_hash(domain, parts, &length, 1, out);
}

bool refuse(Refusal *refusal, int32_t code, const char *field, const char *message)
{
    if (refusal) *refusal = Refusal{code, field, message};
    return false;
}

}  // namespace

void domain_hash(const char *domain, const uint8_t *const *parts, const uint64_t *lengths, size_t count, uint8_t out[32])
{
    p6_sha256 context;
    p6_sha256_init(&context);
    p6_sha256_update(&context, domain, strlen(domain));
    const uint8_t zero = 0;
    p6_sha256_update(&context, &zero, 1);
    for (size_t i = 0; i < count; ++i)
        if (lengths[i]) p6_sha256_update(&context, parts[i], lengths[i]);
    p6_sha256_final(&context, out);
}

void config_hash(uint32_t machine, uint32_t video_standard, uint32_t audio_rate_hz, uint32_t flags, uint64_t random_seed,
                 int64_t rtc_epoch_seconds, uint8_t out[32])
{
    uint8_t fields[32];
    put32(fields + 0, machine);
    put32(fields + 4, video_standard);
    put32(fields + 8, audio_rate_hz);
    put32(fields + 12, flags);
    put64(fields + 16, random_seed);
    put64(fields + 24, uint64_t(rtc_epoch_seconds));
    hash_with_domain("p6/config/v1", fields, sizeof fields, out);
}

void media_manifest_hash(uint32_t count, const uint32_t *types, const uint8_t *const *content_ids, uint8_t out[32])
{
    p6_sha256 context;
    p6_sha256_init(&context);
    static const char domain[] = "p6/media-manifest/v1";
    p6_sha256_update(&context, domain, sizeof domain);      // includes the zero byte
    uint8_t word[4];
    put32(word, count);
    p6_sha256_update(&context, word, 4);
    for (uint32_t i = 0; i < count; ++i) {
        put32(word, types[i]);
        p6_sha256_update(&context, word, 4);
        p6_sha256_update(&context, content_ids[i], P6_CONTENT_ID_SIZE);
    }
    p6_sha256_final(&context, out);
}

void core_build_id(const uint8_t upstream_commit[20], const uint8_t patch_series_hash[32], const uint8_t codegen_hash[32],
                   const uint8_t sources_hash[32], uint32_t profile_id, uint8_t out[32])
{
    uint8_t format[4], profile[4];
    put32(format, kFormatVersion);
    put32(profile, profile_id);
    const uint8_t *parts[6] = {format, upstream_commit, patch_series_hash, codegen_hash, sources_hash, profile};
    const uint64_t lengths[6] = {4, 20, 32, 32, 32, 4};
    domain_hash("p6/core-build/v1", parts, lengths, 6, out);
}

void seal(uint8_t *state, uint64_t payload_length, uint32_t chunk_count, const Identity &identity, uint64_t unit_index,
          uint64_t master_ticks)
{
    memset(state, 0, kHeaderSize);
    memcpy(state, kMagic, 8);
    put32(state + kOffFormat, kFormatVersion);
    put32(state + kOffHeaderSize, kHeaderSize);
    put32(state + kOffProfile, identity.profile_id);
    put64(state + kOffUnit, unit_index);
    put64(state + kOffTicks, master_ticks);
    put64(state + kOffPayload, payload_length);
    put32(state + kOffChunks, chunk_count);
    memcpy(state + kOffBuild, identity.core_build_id, 32);
    memcpy(state + kOffCommit, identity.upstream_commit, 20);
    memcpy(state + kOffSeries, identity.patch_series_hash, 32);
    memcpy(state + kOffCodegen, identity.codegen_hash, 32);
    memcpy(state + kOffConfig, identity.config_hash, 32);
    memcpy(state + kOffMedia, identity.media_manifest_hash, 32);
    memcpy(state + kOffFirmware, identity.firmware_sha256, 32);
    hash_with_domain("p6/native-payload/v1", state + kHeaderSize, payload_length, state + kOffPayloadHash);
    hash_with_domain("p6/native-header/v1", state, kOffHeaderHash, state + kOffHeaderHash);
}

bool validate(const uint8_t *data, uint64_t length, const Identity &expected, uint64_t bound, const ChunkSpec *specs, uint32_t spec_count,
              const uint64_t *chunk_bounds, ChunkView *chunks, uint64_t *unit_index, uint64_t *master_ticks, Refusal *refusal)
{
    if (length < 8)
        return refuse(refusal, P6_ERROR_CORRUPT_STATE, "length", "shorter than a magic");
    if (!memcmp(data, kWaterboxMagic, 8))
        return refuse(refusal, P6_ERROR_INCOMPATIBLE, "magic", "a Waterbox snapshot is not a native state");
    if (length < kHeaderSize)
        return refuse(refusal, P6_ERROR_CORRUPT_STATE, "length", "shorter than the header");
    if (memcmp(data, kMagic, 8))
        return refuse(refusal, P6_ERROR_CORRUPT_STATE, "magic", "not a native state");
    if (get32(data + kOffFormat) != kFormatVersion)
        return refuse(refusal, P6_ERROR_INCOMPATIBLE, "format_version", "a version this build does not read");
    if (get32(data + kOffHeaderSize) != kHeaderSize)
        return refuse(refusal, P6_ERROR_CORRUPT_STATE, "header_size", "not the header size of this format");
    if (get32(data + kOffFlags))
        return refuse(refusal, P6_ERROR_CORRUPT_STATE, "flags", "reserved bits are set");
    if (get32(data + kOffReserved0) || get32(data + kOffReserved1))
        return refuse(refusal, P6_ERROR_CORRUPT_STATE, "reserved", "reserved fields are not zero");
    uint8_t digest[32];
    hash_with_domain("p6/native-header/v1", data, kOffHeaderHash, digest);
    if (memcmp(digest, data + kOffHeaderHash, 32))
        return refuse(refusal, P6_ERROR_CORRUPT_STATE, "header_sha256", "the header does not match its checksum");

    const uint64_t payload_length = get64(data + kOffPayload);
    if (payload_length > bound || length - kHeaderSize != payload_length)
        return refuse(refusal, P6_ERROR_CORRUPT_STATE, "payload_length", "does not fit the bytes there are or the bound");

    if (get32(data + kOffProfile) != expected.profile_id)
        return refuse(refusal, P6_ERROR_INCOMPATIBLE, "profile_id", "differs from the loaded machine");
    struct Field { const char *name; uint32_t offset; const uint8_t *want; uint32_t size; };
    const Field fields[] = {
        {"core_build_id", kOffBuild, expected.core_build_id, 32},
        {"upstream_commit", kOffCommit, expected.upstream_commit, 20},
        {"patch_series_hash", kOffSeries, expected.patch_series_hash, 32},
        {"codegen_hash", kOffCodegen, expected.codegen_hash, 32},
        {"config_hash", kOffConfig, expected.config_hash, 32},
        {"media_manifest_hash", kOffMedia, expected.media_manifest_hash, 32},
        {"firmware_sha256", kOffFirmware, expected.firmware_sha256, 32},
    };
    for (const Field &field : fields)
        if (memcmp(data + field.offset, field.want, field.size))
            return refuse(refusal, P6_ERROR_INCOMPATIBLE, field.name, "differs from the loaded machine");

    const uint8_t *payload = data + kHeaderSize;
    hash_with_domain("p6/native-payload/v1", payload, payload_length, digest);
    if (memcmp(digest, data + kOffPayloadHash, 32))
        return refuse(refusal, P6_ERROR_CORRUPT_STATE, "payload_sha256", "the payload does not match its checksum");
    if (get32(data + kOffChunks) != spec_count)
        return refuse(refusal, P6_ERROR_CORRUPT_STATE, "chunk_count", "not the chunks of this build");

    uint64_t at = 0;
    for (uint32_t i = 0; i < spec_count; ++i) {
        if (payload_length - at < kChunkHeaderSize)
            return refuse(refusal, P6_ERROR_CORRUPT_STATE, "chunk", "a chunk has no header");
        if (memcmp(payload + at, specs[i].tag, 4))
            return refuse(refusal, P6_ERROR_CORRUPT_STATE, "chunk", "unknown, repeated or out of order");
        const uint32_t version = get32(payload + at + 4);
        const uint64_t body_length = get64(payload + at + 8);
        if (version != specs[i].version)
            return refuse(refusal, P6_ERROR_INCOMPATIBLE, "chunk_version", "a chunk version this build does not read");
        at += kChunkHeaderSize;
        if (body_length > payload_length - at || body_length > (chunk_bounds ? chunk_bounds[i] : bound))
            return refuse(refusal, P6_ERROR_CORRUPT_STATE, "chunk_length", "a chunk claims more bytes than there are or its bound");
        chunks[i] = ChunkView{payload + at, body_length, version};
        at += body_length;
    }
    if (at != payload_length)
        return refuse(refusal, P6_ERROR_CORRUPT_STATE, "payload", "bytes after the last chunk");
    if (unit_index) *unit_index = get64(data + kOffUnit);
    if (master_ticks) *master_ticks = get64(data + kOffTicks);
    return true;
}

}  // namespace p6_state
