// T43a: immutable CD32 FMV firmware owner. The ROM is a private run-time input.
#include "p6.h"
#include "config.h"
#include "session.h"
#include "blob_io.h"
#include "sha256.h"

#include <cassert>
#include <cstdio>
#include <cstring>
#include <vector>

static std::vector<uint8_t> read_file(const char *path)
{
    FILE *file = fopen(path, "rb"); assert(file && fseek(file, 0, SEEK_END) == 0);
    const long size = ftell(file); assert(size > 0 && size <= 524288 && fseek(file, 0, SEEK_SET) == 0);
    std::vector<uint8_t> bytes((size_t(size)));
    assert(fread(bytes.data(), 1, bytes.size(), file) == bytes.size());
    fclose(file); return bytes;
}

static p6_error error() { return {P6_ABI_VERSION, sizeof(p6_error), {}}; }
static p6_config config(uint32_t machine = P6_MACHINE_CD32)
{
    p6_config c{}; c.abi_version = P6_ABI_VERSION; c.struct_size = sizeof c; c.machine = machine;
    c.video_standard = P6_VIDEO_PAL; c.audio_rate_hz = 48000; c.random_seed = 1; c.rtc_epoch_seconds = 946684800;
    return c;
}
static void ok(p6_result result, const p6_error &e)
{
    if (result != P6_OK) { fprintf(stderr, "result %d: %s\n", int(result), e.message); abort(); }
}
static void init(uint32_t machine = P6_MACHINE_CD32)
{
    auto c = config(machine); auto e = error(); ok(p6_init(&c, &e), e);
}
static void shutdown()
{
    auto e = error(); ok(p6_shutdown(&e), e);
    assert(!p6_bridge::cd32_fmv_bound() && p6_blob_exists(p6_bridge::kFmvCartridgeId) == P6_BLOB_NOT_FOUND);
}

static void expect_bind(p6_buffer image, p6_result expected, const char *message)
{
    auto e = error(); assert(p6_cartridge_bind(P6_CARTRIDGE_CD32_FMV, image, &e) == expected);
    assert(strstr(e.message, message));
}

int main(int argc, char **argv)
{
    assert(argc == 2); const auto input = read_file(argv[1]);
    assert(input.size() == 262144 || input.size() == 524288);
    std::vector<uint8_t> canonical(input.begin(), input.begin() + 262144);
    if (input.size() == 524288) assert(!memcmp(input.data(), input.data() + 262144, 262144));

    // Wrong machine, sizes, duplicate container and content never publish an owner.
    init(P6_MACHINE_A1200); expect_bind({input.data(), input.size()}, P6_ERROR_UNSUPPORTED, "requires CD32"); shutdown();
    init();
    expect_bind({nullptr, 262144}, P6_ERROR_WRONG_MEDIA, "expected a 256 KiB");
    expect_bind({input.data(), 262143}, P6_ERROR_WRONG_MEDIA, "expected a 256 KiB");
    auto different = input; different.resize(524288); memcpy(different.data() + 262144, canonical.data(), 262144); different.back() ^= 1;
    expect_bind({different.data(), different.size()}, P6_ERROR_WRONG_MEDIA, "halves differ");
    auto unknown = canonical; unknown[0] ^= 1;
    expect_bind({unknown.data(), unknown.size()}, P6_ERROR_WRONG_MEDIA, "not revision 40.22 or 40.30");
    assert(!p6_bridge::cd32_fmv_bound()); shutdown();

    // The 512 KiB duplicate is canonicalized and copied. Mutating the borrowed
    // host buffer after return cannot alter either its bytes or identity.
    init(); auto borrowed = input; auto e = error();
    ok(p6_cartridge_bind(P6_CARTRIDGE_CD32_FMV, {borrowed.data(), borrowed.size()}, &e), e);
    assert(p6_bridge::cd32_fmv_bound());
    uint64_t size = 0; uint32_t kind = 0;
    assert(p6_blob_size(p6_bridge::kFmvCartridgeId, &size, &kind) == P6_BLOB_OK && size == 262144 && kind == P6_BLOB_ROM);
    uint8_t before[32], after[32]; assert(p6_bridge::cd32_fmv_identity(before));
    borrowed.assign(borrowed.size(), 0);
    assert(p6_bridge::cd32_fmv_identity(after) && !memcmp(before, after, sizeof before));
    std::vector<uint8_t> stored(262144); size_t got = 0;
    assert(p6_blob_pread(p6_bridge::kFmvCartridgeId, 0, stored.data(), stored.size(), &got) == P6_BLOB_OK && got == stored.size());
    assert(stored == canonical);

    // A cartridge owner is exclusive. Decoder/device behavior belongs to the
    // FMV-B probes; this owner test intentionally does not require a CD32 ROM.
    expect_bind({input.data(), input.size()}, P6_ERROR_BAD_STATE, "bind one cartridge");
    std::vector<uint8_t> ar(65536); expect_bind({ar.data(), ar.size()}, P6_ERROR_BAD_STATE, "bind one cartridge");
    shutdown();

    // A fresh session can bind the canonical 256 KiB form and gets the same
    // content identity. No previous session cache is consulted.
    init(); e = error(); ok(p6_cartridge_bind(P6_CARTRIDGE_CD32_FMV, {canonical.data(), canonical.size()}, &e), e);
    assert(p6_bridge::cd32_fmv_identity(after) && !memcmp(before, after, sizeof before));
    shutdown();

    // Error ABI and busy calls stay transactional.
    init(); p6_error unusable{}; const auto untouched = unusable;
    assert(p6_cartridge_bind(P6_CARTRIDGE_CD32_FMV, {canonical.data(), canonical.size()}, &unusable) == P6_ERROR_BAD_ARGUMENT);
    assert(!memcmp(&unusable, &untouched, sizeof unusable));
    { p6_session::Guard busy; assert(busy.ok); e = error();
      assert(p6_cartridge_bind(P6_CARTRIDGE_CD32_FMV, {canonical.data(), canonical.size()}, &e) == P6_ERROR_BUSY); }
    assert(!p6_bridge::cd32_fmv_bound()); shutdown();
    puts("CD32 FMV firmware owner/identity/lifetime PASS");
}
