// Native state of a loaded image: the chunk registry, the identity of the image and p6_state_bound / p6_state_save / p6_state_load
// (docs/adr/0009-state-format.md). The container is ports/portable/state_io; each chunk is one visitor function (archive.h) that
// describes the state once for saving, measuring, validating and applying.
//
// A load is validated in full before the machine is touched: the envelope (length, magic, version, checksums, bound, identity, chunk
// table), then every chunk body by running its visitor in Validate mode, which reads the same bytes with the same ranges as Apply
// and writes nothing. Only then do the chunks apply. Validate and Apply share the code, so a failure while applying is an internal
// error, and the image is left Faulted rather than half restored without saying so.
#include "state.h"

#include "error_util.h"
#include "session.h"
#include "frame_step.h"

#include <stdio.h>
#include <string.h>

#include "sysconfig.h"
#include "sysdeps.h"
#include "options.h"
#include "memory.h"

using p6_session::Guard;
using p6_session::State;
using p6_session::session;

namespace p6_state {

namespace {

// P6RM: Chip RAM and Slow RAM, byte for byte in the engine's own order. The sizes are written so that a state of another memory
// configuration is refused by the chunk even if the identity let it through; they are checked, not loaded, because the memory is
// allocated by p6_load from the configuration that is part of the identity. The block is never cleared on the way in
// (memory_clear belongs to a hard reset). Derived state (the overlay mapping, the bank map) is rebuilt by the chunks that own its
// inputs (docs: manifests/state-inventory.json, ram-overlay-state).
void visit_ram(Archive &a)
{
    const uint32_t chip = chipmem_bank.allocated_size, slow = bogomem_bank.allocated_size;
    a.val<uint32_t>(chip, chip, chip, "ram.chip_size");
    a.val<uint32_t>(slow, slow, slow, "ram.slow_size");
    a.bytes(chipmem_bank.baseaddr, chip, "ram.chip");
    a.bytes(bogomem_bank.baseaddr, slow, "ram.slow");
}

}  // namespace

const Chunk kChunks[kChunkCount] = {
    {"P6CP", "cpu", 1, visit_cpu, nullptr},
    {"P6SC", "scheduler", 1, visit_scheduler, rebuild_scheduler},
    {"P6RM", "ram", 1, visit_ram, nullptr},
    {"P6RT", "rtc", 1, nullptr, nullptr},
    {"P6CH", "chipset", 1, nullptr, nullptr},
    {"P6VD", "video", 1, nullptr, nullptr},
    {"P6AU", "audio", 1, nullptr, nullptr},
    {"P6CI", "cia", 1, nullptr, nullptr},
    {"P6DK", "disk", 1, nullptr, nullptr},
    {"P6IN", "input", 1, nullptr, nullptr},
    {"P6BR", "bridge", 1, nullptr, nullptr},
};

Identity current_identity()
{
    const BuildIdentity &build = build_identity();
    Identity identity = {};
    identity.profile_id = build.profile_id;
    memcpy(identity.core_build_id, build.core_build_id, 32);
    memcpy(identity.upstream_commit, build.upstream_commit, 20);
    memcpy(identity.patch_series_hash, build.patch_series_hash, 32);
    memcpy(identity.codegen_hash, build.codegen_hash, 32);
    const p6_config &c = session.config;
    config_hash(c.machine, c.video_standard, c.audio_rate_hz, c.flags, c.random_seed, c.rtc_epoch_seconds, identity.config_hash);
    // ABI v1 accepts only ADF images (validate_media), so every medium has the same type.
    uint32_t types[P6_MAX_MEDIA];
    const uint8_t *ids[P6_MAX_MEDIA];
    for (uint32_t i = 0; i < session.media_count && i < P6_MAX_MEDIA; ++i) {
        types[i] = P6_MEDIA_ADF;
        ids[i] = session.media_content_id[i];
    }
    media_manifest_hash(session.media_count, types, ids, identity.media_manifest_hash);
    memcpy(identity.firmware_sha256, session.firmware_sha256, 32);
    return identity;
}

bool complete(char *missing, size_t capacity)
{
    bool all = true;
    size_t used = 0;
    if (missing && capacity) missing[0] = 0;
    for (const Chunk &chunk : kChunks) {
        if (chunk.visit) continue;
        all = false;
        if (missing && capacity > used + 1) {
            const int n = snprintf(missing + used, capacity - used, "%s%s", used ? ", " : "", chunk.name);
            if (n > 0) used = size_t(n) < capacity - used ? used + size_t(n) : capacity - 1;
        }
    }
    return all;
}

namespace {

// What the registry says about this build: the chunk table the envelope is checked against and the largest body of each chunk.
struct Plan {
    ChunkSpec specs[kChunkCount];
    uint64_t bounds[kChunkCount];
    uint64_t total;         // header and every chunk at its largest
};

Plan make_plan()
{
    Plan plan = {};
    plan.total = kHeaderSize;
    for (uint32_t i = 0; i < kChunkCount; ++i) {
        const Chunk &chunk = kChunks[i];
        memcpy(plan.specs[i].tag, chunk.tag, 4);
        plan.specs[i].version = chunk.visit ? chunk.version : 0;    // version 0: the placeholder of a chunk that does not exist yet
        if (chunk.visit) {
            Archive measure = Archive::measurer();
            chunk.visit(measure);
            plan.bounds[i] = measure.position();
        }
        plan.total += kChunkHeaderSize + plan.bounds[i];
    }
    return plan;
}

// The checks every entry point makes: one call at a time, an image that is usable, and a registry that is complete (unless a test
// asked for placeholders).
p6_result gate(const Options &options, p6_error *error)
{
    if (session.state != State::Loaded)
        return p6_fail(error, P6_ERROR_BAD_STATE, session.state == State::Faulted ? "the image faulted; unload it" : "no image is loaded");
    char missing[160];
    if (!options.allow_placeholders && !complete(missing, sizeof missing)) {
        char text[P6_ERROR_TEXT_CAPACITY];
        snprintf(text, sizeof text, "native state is not implemented for: %s", missing);
        return p6_fail(error, P6_ERROR_UNSUPPORTED, text);
    }
    return P6_OK;
}

void put32(uint8_t *p, uint32_t v) { for (int i = 0; i < 4; ++i) p[i] = uint8_t(v >> (8 * i)); }
void put64(uint8_t *p, uint64_t v) { for (int i = 0; i < 8; ++i) p[i] = uint8_t(v >> (8 * i)); }

// Hands the offset of a field in the chunk body to Options::on_field as an offset in the whole state.
struct FieldRelay {
    const Options *options;
    uint32_t chunk;
    uint64_t body_at;
};

void relay_field(void *context, uint64_t offset, uint64_t size, const char *name)
{
    const FieldRelay *relay = static_cast<const FieldRelay *>(context);
    relay->options->on_field(relay->options->on_field_context, relay->chunk, relay->body_at + offset, size, name);
}

p6_result refused(p6_error *error, const char *chunk, const char *why)
{
    char text[P6_ERROR_TEXT_CAPACITY];
    snprintf(text, sizeof text, "state refused: chunk %s: %s", chunk, why);
    return p6_fail(error, P6_ERROR_CORRUPT_STATE, text);
}

}  // namespace

p6_result bound(uint64_t *bytes, const Options &options, p6_error *error)
{
    if (const p6_result gated = gate(options, error)) return gated;
    if (!bytes) return p6_fail(error, P6_ERROR_BAD_ARGUMENT, "bytes is null");
    *bytes = make_plan().total;
    return P6_OK;
}

p6_result save(void *buffer, uint64_t capacity, uint64_t *written, const Options &options, p6_error *error)
{
    if (written) *written = 0;
    if (const p6_result gated = gate(options, error)) return gated;
    if (!buffer || !written) return p6_fail(error, P6_ERROR_BAD_ARGUMENT, "buffer or written is null");
    // A state is a unit boundary of a machine that has run its reset head. Before the first unit the machine is a configuration, a
    // firmware and some media: the reset head has not built the CPU's tables or the scheduler's handlers, and the chipset, the audio and the
    // input are not those of a power-on yet (docs/adr/0009-state-format.md, point 6).
    if (!session.started)
        return p6_fail(error, P6_ERROR_BAD_STATE, "no state before the first unit: run p6_run_frame once");
    const Plan plan = make_plan();
    uint8_t *out = static_cast<uint8_t *>(buffer);
    if (capacity < kHeaderSize + kChunkHeaderSize)
        return p6_fail(error, P6_ERROR_CAPACITY, "the buffer is smaller than a state");

    // The body of each chunk is written after the header and its own 16 bytes, then the chunk header is filled in with the length it
    // came to. Nothing is written outside `capacity`: the archive refuses first.
    uint64_t at = kHeaderSize;
    for (uint32_t i = 0; i < kChunkCount; ++i) {
        const Chunk &chunk = kChunks[i];
        if (capacity - at < kChunkHeaderSize)
            return p6_fail(error, P6_ERROR_CAPACITY, "the buffer is smaller than the state");
        uint64_t body = 0;
        if (chunk.visit) {
            Archive archive = Archive::saver(out + at + kChunkHeaderSize, capacity - at - kChunkHeaderSize);
            FieldRelay relay = {&options, i, at + kChunkHeaderSize};
            if (options.on_field) archive.on_field(relay_field, &relay);
            chunk.visit(archive);
            if (!archive.ok()) {
                // A buffer that could have held the chunk is not the problem: the machine is in a state that cannot be saved (an exception in
                // flight, an event that a state cannot name).
                const bool fits = capacity - at - kChunkHeaderSize >= plan.bounds[i];
                char text[P6_ERROR_TEXT_CAPACITY];
                snprintf(text, sizeof text, "chunk %s could not be saved: %s", chunk.name, archive.why());
                return p6_fail(error, fits ? P6_ERROR_BAD_STATE : P6_ERROR_CAPACITY, text);
            }
            body = archive.position();
        }
        memcpy(out + at, chunk.tag, 4);
        put32(out + at + 4, chunk.visit ? chunk.version : 0);
        put64(out + at + 8, body);
        at += kChunkHeaderSize + body;
    }
    seal(out, at - kHeaderSize, kChunkCount, current_identity(), session.unit_index, session.master_ticks);
    *written = at;
    return P6_OK;
}

p6_result load(const void *buffer, uint64_t bytes, const Options &options, p6_error *error)
{
    if (const p6_result gated = gate(options, error)) return gated;
    if (!buffer && bytes) return p6_fail(error, P6_ERROR_BAD_ARGUMENT, "buffer is null");
    const uint8_t *data = static_cast<const uint8_t *>(buffer);

    // (1)-(6) and the chunk table: the envelope, against what this build and this image are.
    const Plan plan = make_plan();
    ChunkView views[kChunkCount];
    Refusal refusal = {};
    if (!validate(data, bytes, current_identity(), plan.total - kHeaderSize, plan.specs, kChunkCount, plan.bounds, views, nullptr, nullptr, &refusal)) {
        char text[P6_ERROR_TEXT_CAPACITY];
        snprintf(text, sizeof text, "state refused: %s: %s", refusal.field, refusal.message);
        return p6_fail(error, refusal.code, text);
    }

    // (7) Every body, read exactly as it will be applied and not stored anywhere.
    for (uint32_t i = 0; i < kChunkCount; ++i) {
        const Chunk &chunk = kChunks[i];
        if (!chunk.visit) continue;         // a placeholder has bound 0, so the envelope has already refused a body
        Archive archive = Archive::validator(views[i].body, views[i].length);
        chunk.visit(archive);
        archive.finish();
        if (!archive.ok()) return refused(error, chunk.name, archive.why());
    }

    // (8) Nothing can refuse any more. An image that has not started has not run its reset head, which is what builds the CPU's dispatch
    // tables and the scheduler's handlers (build_cpufunctbl, init_eventtab): the head runs first, exactly as p6_run_frame runs it, and every
    // chunk then overwrites what it left. A failure of the head, like a failure of a visitor below, is a defect and not of the state: say so and
    // stop the image.
    if (!session.started) {
        const bool head_ran = p6_bridge::run_reset_head();
        if (!head_ran || options.fail_head) {
            session.state = State::Faulted;
            return p6_fail(error, P6_ERROR_INTERNAL, "the reset head did not run before the state was applied; the image is faulted");
        }
    }
    const auto chosen = [&options](uint32_t i) { return !options.only_chunks || (options.only_chunks >> i & 1); };
    for (uint32_t i = 0; i < kChunkCount; ++i) {
        const Chunk &chunk = kChunks[i];
        if (!chunk.visit || !chosen(i)) continue;
        Archive archive = Archive::applier(views[i].body, views[i].length);
        chunk.visit(archive);
        archive.finish();
        if (options.fail_apply) archive.fail("a failure injected by a test");
        if (!archive.ok()) {
            session.state = State::Faulted;
            return p6_fail(error, P6_ERROR_INTERNAL, "a chunk failed to apply after it was validated; the image is faulted");
        }
    }
    for (uint32_t i = 0; i < kChunkCount; ++i)
        if (kChunks[i].visit && kChunks[i].rebuild && chosen(i)) kChunks[i].rebuild();
    return P6_OK;
}

}  // namespace p6_state

extern "C" p6_result p6_state_bound(uint64_t *bytes, p6_error *error)
{
    if (!p6_error_usable(error)) return P6_ERROR_BAD_ARGUMENT;
    p6_error_clear(error);
    Guard guard;
    if (!guard.ok) return p6_fail(error, P6_ERROR_BUSY, "another call is in progress");
    return p6_state::bound(bytes, p6_state::Options{}, error);
}

extern "C" p6_result p6_state_save(void *buffer, uint64_t capacity, uint64_t *written, p6_error *error)
{
    if (!p6_error_usable(error)) return P6_ERROR_BAD_ARGUMENT;
    p6_error_clear(error);
    Guard guard;
    if (!guard.ok) return p6_fail(error, P6_ERROR_BUSY, "another call is in progress");
    return p6_state::save(buffer, capacity, written, p6_state::Options{}, error);
}

extern "C" p6_result p6_state_load(const void *buffer, uint64_t bytes, p6_error *error)
{
    if (!p6_error_usable(error)) return P6_ERROR_BAD_ARGUMENT;
    p6_error_clear(error);
    Guard guard;
    if (!guard.ok) return p6_fail(error, P6_ERROR_BUSY, "another call is in progress");
    return p6_state::load(buffer, bytes, p6_state::Options{}, error);
}
