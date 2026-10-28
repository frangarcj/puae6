// FMV-D2: complete P6FM body, exact native future and hostile candidate.
#include "p6.h"
#include "state.h"
#include "session.h"
#include "fmv_state.h"
#include "sysconfig.h"
#include "sysdeps.h"
#include "options.h"
#include "memory.h"
#include "events.h"

#include <cassert>
#include <cstdio>
#include <cstring>
#include <memory>
#include <vector>

static std::vector<uint8_t> read_file(const char *path)
{
    FILE *file = fopen(path, "rb"); assert(file && fseek(file, 0, SEEK_END) == 0);
    const long size = ftell(file); assert(size > 0 && fseek(file, 0, SEEK_SET) == 0);
    std::vector<uint8_t> bytes(static_cast<size_t>(size));
    assert(fread(bytes.data(), 1, bytes.size(), file) == bytes.size());
    fclose(file); return bytes;
}

static p6_error error() { return {P6_ABI_VERSION, sizeof(p6_error), {}}; }
static void ok(p6_result result, const p6_error &e)
{
    if (result != P6_OK) { fprintf(stderr, "result %d: %s\n", int(result), e.message); abort(); }
}
static p6_config config()
{
    p6_config c{}; c.abi_version = P6_ABI_VERSION; c.struct_size = sizeof c;
    c.machine = P6_MACHINE_CD32; c.video_standard = P6_VIDEO_PAL;
    c.audio_rate_hz = 48000; c.random_seed = 1; c.rtc_epoch_seconds = 946684800;
    return c;
}
static bool fmv_mapped()
{
    const char *rom = get_mem_bank(0x200000).name;
    const char *io = get_mem_bank(0x240000).name;
    const char *ram = get_mem_bank(0x280000).name;
    return rom && io && ram && !strcmp(rom, "CD32 FMV ROM") &&
           !strcmp(io, "CD32 FMV IO") && !strcmp(ram, "CD32 FMV RAM");
}
static void run_frames(unsigned count, uint32_t flags = P6_RENDER_AUDIO | P6_RENDER_VIDEO)
{
    p6_input input{P6_ABI_VERSION, sizeof input};
    p6_frame frame{P6_ABI_VERSION, sizeof frame}; auto e = error();
    for (unsigned n = 0; n < count; ++n) ok(p6_run_frame(&input, flags, &frame, &e), e);
}
static void run_until_mapped()
{
    for (unsigned n = 0; n < 300 && !fmv_mapped(); ++n) run_frames(1);
    assert(fmv_mapped());
}
static void load_image(const std::vector<uint8_t> &firmware, const std::vector<uint8_t> &fmv)
{
    auto c = config(); auto e = error(); ok(p6_init(&c, &e), e);
    ok(p6_cartridge_bind(P6_CARTRIDGE_CD32_FMV, {fmv.data(), fmv.size()}, &e), e);
    ok(p6_load({firmware.data(), firmware.size()}, nullptr, 0, &e), e);
}
static void shutdown_image()
{
    auto e = error(); ok(p6_shutdown(&e), e);
}
static std::vector<uint8_t> save_public(uint64_t *bound_out = nullptr)
{
    auto e = error(); uint64_t bound = 0, written = 0;
    ok(p6_state_bound(&bound, &e), e); std::vector<uint8_t> state(static_cast<size_t>(bound));
    ok(p6_state_save(state.data(), state.size(), &written, &e), e);
    assert(written <= bound); state.resize(size_t(written));
    if (bound_out) *bound_out = bound;
    return state;
}
static uint64_t get64(const uint8_t *p)
{
    uint64_t v = 0; for (unsigned n = 0; n < 8; ++n) v |= uint64_t(p[n]) << (8 * n); return v;
}
static void put32(uint8_t *p, uint32_t v)
{
    for (unsigned n = 0; n < 4; ++n) p[n] = uint8_t(v >> (8 * n));
}
static void assert_fmv_chunk(const std::vector<uint8_t> &state)
{
    assert(p6_state::chunk_count() == p6_state::kMaxChunkCount);
    uint64_t at = p6_state::kHeaderSize;
    for (uint32_t n = 0; n < p6_state::chunk_count(); ++n) {
        assert(at + p6_state::kChunkHeaderSize <= state.size());
        const uint64_t length = get64(state.data() + at + 8);
        if (n + 1 == p6_state::chunk_count()) {
            assert(!memcmp(state.data() + at, "P6FM", 4));
            assert(length > P6_FMV_RAM_BYTES + P6_FMV_AUDIO_RAM_BYTES);
        }
        at += p6_state::kChunkHeaderSize + length;
    }
    assert(at == state.size());
}
static void assert_equal_state(const std::vector<uint8_t> &expected, const std::vector<uint8_t> &actual)
{
    if (expected == actual) return;
    const size_t common = expected.size() < actual.size() ? expected.size() : actual.size();
    size_t at = p6_state::kHeaderSize; while (at < common && expected[at] == actual[at]) ++at;
    uint64_t chunk_at = p6_state::kHeaderSize; char tag[5] = {};
    for (uint32_t n = 0; n < p6_state::chunk_count() && chunk_at + p6_state::kChunkHeaderSize <= expected.size(); ++n) {
        const uint64_t length = get64(expected.data() + chunk_at + 8);
        if (at < chunk_at + p6_state::kChunkHeaderSize + length) { memcpy(tag, expected.data() + chunk_at, 4); break; }
        chunk_at += p6_state::kChunkHeaderSize + length;
    }
    fprintf(stderr, "state differs at %zu chunk=%s body=%llu: expected=%02x actual=%02x sizes=%zu/%zu\n", at, tag,
            static_cast<unsigned long long>(at - chunk_at - p6_state::kChunkHeaderSize),
            at < expected.size() ? expected[at] : 0, at < actual.size() ? actual[at] : 0,
            expected.size(), actual.size());
    abort();
}
static std::unique_ptr<p6_fmv_state> capture_fmv()
{
    std::unique_ptr<p6_fmv_state> result(new p6_fmv_state{});
    assert(p6_fmv_state_get(result.get())); return result;
}
static std::unique_ptr<p6_fmv_state> seed_pending_fmv()
{
    auto state = capture_fmv();
    state->ram[0x201] = 0xa5; state->audio_ram[37] = 0x5a;
    state->cl450_interruptmask = state->cl450_pending_interrupts = 1u << 6;
    state->packets[0] = {6, 1, 90000}; state->newpacket_mode = 1;
    state->frame_rate = 25; state->frame_pixbytes = 4; state->frame_width = state->frame_height = 2;
    state->video_hsync_wait = 3; state->videoram_read = 0; state->videoram_write = 1; state->videoram_count = 1;
    state->video[0].width = state->video[0].height = 2; state->video[0].depth = 4;
    for (uint32_t n = 0; n < 4; ++n) {
        const uint32_t pixel = 0x00405060 + n;
        memcpy(state->video[0].data + n * 4, &pixel, sizeof pixel);
    }
    state->l64111_fifo_count = 4; memcpy(state->l64111_fifo, "P6FM", 4);
    state->audio_frame_size = 64; state->audio_frame_count = 17; state->audio_data_remaining = 47;
    state->audio_frame_detect = state->audio_head_detect = 1; state->l64111_cb_mask = 63;
    state->mp2_id = 1; state->mp2_voffs = 7; state->mp2_v[0][7] = 0x12345678;
    state->pcm[0].ready = 1; state->pcm[0].pcm[0] = 1234; state->pcm[0].pcm[1] = -2345;
    state->genlock = {}; state->genlock.allocated = state->genlock.active = 1;
    state->genlock.width = state->genlock.height = 2; state->genlock.depth = 4;
    state->genlock.border32 = 0x00112233; state->genlock.border16 = 0x1234;
    for (uint32_t n = 0; n < 4; ++n) {
        const uint32_t pixel = 0x00102030 + n;
        memcpy(state->genlock.pixels + n * 4, &pixel, sizeof pixel);
    }
    state->aux.pixels[0] = 0x00abcdef; state->aux.pixels[1] = 0x00123456;
    assert(p6_fmv_state_validate(state.get(), {int64_t(get_cycles()), 41179}));
    assert(p6_fmv_state_apply(state.get(), {int64_t(get_cycles()), 41179}));
    return capture_fmv();
}
static void assert_same(const p6_fmv_state &a, const p6_fmv_state &b)
{
    assert(!memcmp(&a, &b, sizeof a));
}
struct Field { uint64_t offset = UINT64_MAX; };
static void field(void *opaque, uint32_t chunk, uint64_t offset, uint64_t size, const char *name)
{
    auto &found = *static_cast<Field *>(opaque);
    if (chunk == p6_state::kMaxChunkCount - 1 && size == 4 && !strcmp(name, "fmv.cl450_blank"))
        found.offset = offset;
}

int main(int argc, char **argv)
{
    assert(argc == 4);
    const auto ext = read_file(argv[1]), main = read_file(argv[2]), fmv = read_file(argv[3]);
    assert(ext.size() == 524288 && main.size() == 524288);
    std::vector<uint8_t> firmware; firmware.reserve(ext.size() + main.size());
    firmware.insert(firmware.end(), ext.begin(), ext.end()); firmware.insert(firmware.end(), main.begin(), main.end());

    load_image(firmware, fmv); run_until_mapped();
    put_long(0x280100, 0x1234abcd); run_frames(1);
    const auto saved_fmv = seed_pending_fmv(); uint64_t bound = 0;
    const auto saved = save_public(&bound); assert_fmv_chunk(saved);
    assert(saved.size() <= bound && bound < 32u * 1024u * 1024u);

    // The complete image, including the FMV aggregate, has the same future
    // after an in-place restore.
    run_frames(7); const auto future = save_public();
    auto e = error();
    ok(p6_state_load(saved.data(), saved.size(), &e), e);
    assert(get_long(0x280100) == 0x1234abcd); const auto restored = capture_fmv();
    assert_same(*saved_fmv, *restored);
    run_frames(7); const auto repeated = save_public(); assert_equal_state(future, repeated);

    // Presentation flags only choose which completed outputs cross the ABI; the
    // FMV cartridge, compositor, scheduler and Paula continue identically.
    for (uint32_t flags : {uint32_t(P6_RENDER_AUDIO), uint32_t(P6_RENDER_VIDEO), 0u}) {
        e = error(); ok(p6_state_load(saved.data(), saved.size(), &e), e);
        run_frames(7, flags); assert_equal_state(future, save_public());
    }

    // A cold image runs only the reset head before Apply; no guest I/O or
    // autoconfig progress is needed to reconstruct the board.
    shutdown_image(); load_image(firmware, fmv);
    e = error(); ok(p6_state_load(saved.data(), saved.size(), &e), e);
    assert(fmv_mapped() && get_long(0x280100) == 0x1234abcd);
    const auto cold = capture_fmv(); assert_same(*saved_fmv, *cold);
    run_frames(7); const auto fresh = save_public(); assert_equal_state(future, fresh);

    // Reseal an invalid field so the refusal comes from P6FM, then prove that
    // Validate did not alter the destination or its serialized image.
    uint64_t hostile_bound = 0; e = error(); ok(p6_state::bound(&hostile_bound, {}, &e), e);
    std::vector<uint8_t> hostile(static_cast<size_t>(hostile_bound)); uint64_t hostile_written = 0; Field found;
    p6_state::Options options; options.on_field = field; options.on_field_context = &found;
    ok(p6_state::save(hostile.data(), hostile.size(), &hostile_written, options, &e), e);
    hostile.resize(size_t(hostile_written)); assert(found.offset != UINT64_MAX);
    const auto before = capture_fmv(); const auto before_state = hostile;
    put32(hostile.data() + found.offset, 2);
    p6_state::seal(hostile.data(), hostile.size() - p6_state::kHeaderSize, p6_state::chunk_count(),
                   p6_state::current_identity(), p6_session::session.unit_index, p6_session::session.master_ticks);
    e = error(); assert(p6_state_load(hostile.data(), hostile.size(), &e) == P6_ERROR_CORRUPT_STATE);
    assert(strstr(e.message, "fmv")); const auto after = capture_fmv(); assert_same(*before, *after);
    assert(save_public() == before_state);

    printf("FMV P6FM native state/fresh future/corruption PASS bound=%llu state=%zu\n",
           static_cast<unsigned long long>(bound), saved.size());
    shutdown_image();
}
