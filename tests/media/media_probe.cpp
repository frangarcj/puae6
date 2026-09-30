// Media of p6_load: identity, validation and registration against the real engine (tests/media/test_media.py).
//   usage: p6_media_probe boot.rom
// Later tasks (T12b, T12c) extend this probe with the event queue and the guest-visible disk scenario.
#include "p6.h"
#include "media.h"
#include "session.h"
#include "sha256.h"
#include "blob_io.h"
#include "sysconfig.h"
#include "sysdeps.h"
#include "options.h"
#include "uae.h"
#include "events.h"
#include "memory.h"
#include "newcpu.h"

#include <cassert>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>
#include <vector>

extern int in_m68k_go;  // defined in newcpu.cpp, not declared by any header

static p6_error make_error()
{
    p6_error e{};
    e.abi_version = P6_ABI_VERSION;
    e.struct_size = sizeof e;
    return e;
}

static p6_config make_config()
{
    p6_config c{};
    c.abi_version = P6_ABI_VERSION;
    c.struct_size = sizeof c;
    c.machine = P6_MACHINE_A500;
    c.video_standard = P6_VIDEO_PAL;
    c.audio_rate_hz = 48000;
    c.random_seed = 1;
    c.rtc_epoch_seconds = 946684800;
    return c;
}

static std::vector<uint8_t> read_file(const char *path)
{
    FILE *f = fopen(path, "rb");
    if (!f) { perror(path); exit(2); }
    std::vector<uint8_t> data;
    uint8_t buffer[65536];
    for (size_t n; (n = fread(buffer, 1, sizeof buffer, f)) > 0;) data.insert(data.end(), buffer, buffer + n);
    fclose(f);
    return data;
}

// A deterministic 901120-byte image that starts like an AmigaDOS boot block ("DOS", flags 0).
static std::vector<uint8_t> make_adf(uint32_t label)
{
    std::vector<uint8_t> adf(p6_bridge::kAdfBytes);
    uint32_t x = 0x9e3779b9u ^ (label * 2654435761u);
    for (uint8_t &b : adf) {
        x ^= x << 13; x ^= x >> 17; x ^= x << 5;
        b = uint8_t(x >> 11);
    }
    adf[0] = 'D'; adf[1] = 'O'; adf[2] = 'S'; adf[3] = 0;
    return adf;
}

static p6_media medium_of(const std::vector<uint8_t> &adf)
{
    p6_media m{};
    m.type = P6_MEDIA_ADF;
    p6_sha256_hash(adf.data(), adf.size(), m.content_id);
    m.image = p6_buffer{adf.data(), adf.size()};
    return m;
}

static std::string id_of(const p6_media &m)
{
    char id[p6_bridge::kMediaBlobIdBytes];
    p6_bridge::media_blob_id(m.content_id, id);
    return id;
}

static bool registered(const p6_media &m) { return p6_blob_exists(id_of(m).c_str()) == P6_BLOB_OK; }

static p6_result load(const std::vector<uint8_t> &rom, const p6_media *media, uint32_t count, p6_error *e)
{
    return p6_load(p6_buffer{rom.data(), rom.size()}, media, count, e);
}

// A rejected list leaves the instance initialized and untouched: nothing registered, the engine never entered, and the
// next load works.
static void rejects(const std::vector<uint8_t> &rom, const p6_media *media, uint32_t count, p6_result code, const char *what)
{
    p6_error e = make_error();
    currprefs.seed = 0x5eed1234;
    const p6_result got = load(rom, media, count, &e);
    if (got != code) fprintf(stderr, "%s: load returned %d (%s), expected %d\n", what, int(got), e.message, int(code));
    assert(got == code);
    assert(e.message[0] != 0 && strlen(e.message) < P6_ERROR_TEXT_CAPACITY);
    assert(currprefs.seed == 0x5eed1234 && in_m68k_go == 0 && quit_program == 0);
    for (uint32_t i = 0; i < count && media; ++i) assert(!registered(media[i]));
    assert(p6_blob_exists("p6-firmware.rom") == P6_BLOB_NOT_FOUND);
}

static void identity_names()
{
    uint8_t id[P6_CONTENT_ID_SIZE];
    for (size_t i = 0; i < sizeof id; ++i) id[i] = uint8_t(0xf0 + i * 7);
    char name[p6_bridge::kMediaBlobIdBytes];
    p6_bridge::media_blob_id(id, name);
    assert(strlen(name) == 70 && !strncmp(name, "media-", 6) && !strchr(name, '.'));
    assert(!strncmp(name + 6, "f0f7fe050c13", 12));
    for (size_t i = 6; i < 70; ++i) assert((name[i] >= '0' && name[i] <= '9') || (name[i] >= 'a' && name[i] <= 'f'));
}

static void rejected_lists(const std::vector<uint8_t> &rom)
{
    const std::vector<uint8_t> a = make_adf(1), b = make_adf(2);
    const p6_media ma = medium_of(a), mb = medium_of(b);

    rejects(rom, nullptr, 1, P6_ERROR_BAD_ARGUMENT, "null array");
    std::vector<p6_media> many(P6_MAX_MEDIA + 1, ma);
    rejects(rom, many.data(), uint32_t(many.size()), P6_ERROR_CAPACITY, "too many");

    p6_media m = ma; m.type = 0;                       rejects(rom, &m, 1, P6_ERROR_UNSUPPORTED, "type 0");
    m = ma; m.type = 2;                                rejects(rom, &m, 1, P6_ERROR_UNSUPPORTED, "type 2");
    m = ma; m.flags = 1;                               rejects(rom, &m, 1, P6_ERROR_BAD_ARGUMENT, "flags");
    m = ma; m.image.data = nullptr;                    rejects(rom, &m, 1, P6_ERROR_BAD_ARGUMENT, "no data");
    for (uint64_t bytes : {uint64_t(0), uint64_t(512), p6_bridge::kAdfBytes - 1, p6_bridge::kAdfBytes + 1,
                           uint64_t(2 * p6_bridge::kAdfBytes), uint64_t(4u << 20)}) {
        m = ma; m.image.bytes = bytes;
        rejects(rom, &m, 1, P6_ERROR_WRONG_MEDIA, "size");
    }

    // Formats the engine would take for something else: extended ADF (both magics) and an Amiga executable.
    const uint8_t headers[3][8] = {{'U', 'A', 'E', '-', '1', 'A', 'D', 'F'}, {'U', 'A', 'E', '-', '-', 'A', 'D', 'F'},
                                   {0x00, 0x00, 0x03, 0xF3, 0x00, 0x00, 0x00, 0x00}};
    for (const auto &header : headers) {
        std::vector<uint8_t> special = a;
        memcpy(special.data(), header, 8);
        const p6_media ms = medium_of(special);        // even with an honest content id
        rejects(rom, &ms, 1, P6_ERROR_WRONG_MEDIA, "special header");
    }

    const p6_media twice[2] = {ma, ma};                rejects(rom, twice, 2, P6_ERROR_BAD_ARGUMENT, "duplicate");
    // Wrong disk: the id of one image on the bytes of another, and an id with a single flipped bit.
    p6_media swapped[2] = {ma, mb};
    memcpy(swapped[0].content_id, mb.content_id, P6_CONTENT_ID_SIZE);
    memcpy(swapped[1].content_id, ma.content_id, P6_CONTENT_ID_SIZE);
    rejects(rom, swapped, 2, P6_ERROR_WRONG_MEDIA, "swapped ids");
    m = ma; m.content_id[31] ^= 1;                     rejects(rom, &m, 1, P6_ERROR_WRONG_MEDIA, "flipped id bit");
    m = ma; memset(m.content_id, 0, sizeof m.content_id);   rejects(rom, &m, 1, P6_ERROR_WRONG_MEDIA, "zero id");
    // One bad entry after good ones registers nothing.
    p6_media mixed[3] = {ma, mb, ma};
    mixed[2].content_id[0] ^= 1;
    rejects(rom, mixed, 3, P6_ERROR_WRONG_MEDIA, "bad third entry");

    // A firmware the engine refuses after the media were registered: everything is released.
    std::vector<uint8_t> broken = rom;
    broken[0xD2] ^= 0x10;
    const p6_media pair[2] = {ma, mb};
    p6_error e = make_error();
    assert(load(broken, pair, 2, &e) == P6_ERROR_WRONG_MEDIA && e.message[0] != 0);
    assert(!registered(ma) && !registered(mb) && in_m68k_go == 0 && quit_program == 0);
    assert(p6_session::session.media_count == 0);
}

static void loaded_lists(const std::vector<uint8_t> &rom)
{
    std::vector<std::vector<uint8_t>> disks;
    for (uint32_t i = 0; i < P6_MAX_MEDIA; ++i) disks.push_back(make_adf(100 + i));
    const std::vector<std::vector<uint8_t>> before = disks;
    std::vector<p6_media> media;
    for (const auto &d : disks) media.push_back(medium_of(d));

    for (uint32_t count : {0u, 1u, 2u, uint32_t(P6_MAX_MEDIA)}) {
        p6_error e = make_error();
        assert(load(rom, media.data(), count, &e) == P6_OK && e.message[0] == 0);
        // Nothing of the host filesystem or of upstream's default names is reachable.
        for (int i = 0; i < 4; ++i) assert(currprefs.floppyslots[i].df[0] == 0 && currprefs.floppyslots[i].forcedwriteprotect);
        assert(p6_blob_exists("df0.adf") != P6_BLOB_OK && p6_blob_exists("df1.adf") != P6_BLOB_OK);
        for (uint32_t i = 0; i < P6_MAX_MEDIA; ++i) assert(registered(media[i]) == (i < count));
        // The registered copy is the caller's image, byte for byte, and its own cursor works.
        for (uint32_t i = 0; i < count; ++i) {
            p6_blob_handle h = 0;
            p6_blob_info info{};
            assert(p6_blob_open(id_of(media[i]).c_str(), "rb", &h) == P6_BLOB_OK && p6_blob_stat(h, &info) == P6_BLOB_OK);
            assert(info.bytes == p6_bridge::kAdfBytes && info.kind == P6_BLOB_ADF);
            std::vector<uint8_t> copy(info.bytes);
            size_t items = 0;
            assert(p6_blob_read(h, copy.data(), copy.size(), 1, copy.size(), &items) == P6_BLOB_OK && items == copy.size());
            assert(copy == disks[i]);
            assert(p6_blob_write(h, copy.data(), 512) == P6_BLOB_READ_ONLY);
            assert(p6_blob_close(h) == P6_BLOB_OK);
        }
        // Identity kept for the state header.
        const p6_session::Session &s = p6_session::session;
        uint8_t digest[32];
        p6_sha256_hash(rom.data(), rom.size(), digest);
        assert(!memcmp(digest, s.firmware_sha256, 32) && s.media_count == count);
        for (uint32_t i = 0; i < count; ++i) assert(!memcmp(s.media_content_id[i], media[i].content_id, 32));
        assert(p6_unload(&e) == P6_OK);
        for (uint32_t i = 0; i < P6_MAX_MEDIA; ++i) assert(!registered(media[i]));
        assert(s.media_count == 0 && !s.firmware_sha256[0] && !s.media_content_id[0][0]);
        assert(disks == before);                       // the caller's buffers were never written
    }
}

int main(int argc, char **argv)
{
    if (argc != 2) { fprintf(stderr, "usage: %s boot.rom\n", argv[0]); return 64; }
    const std::vector<uint8_t> rom = read_file(argv[1]);
    assert(rom.size() == 256 * 1024);
    identity_names();

    p6_error e = make_error();
    p6_config c = make_config();
    assert(p6_init(&c, &e) == P6_OK);
    rejected_lists(rom);
    loaded_lists(rom);
    assert(p6_shutdown(&e) == P6_OK);
    puts("media load ok");
    return 0;
}
