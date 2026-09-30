// Media of p6_load and the media events against the real engine (tests/media/test_media.py).
//   usage: p6_media_probe boot.rom
// The guest-visible disk scenario (registers, MFM, replay) is T12c's; here the drives are looked at from the engine.
#include "p6.h"
#include "media.h"
#include "session.h"
#include "sha256.h"
#include "blob_io.h"
#include "sysconfig.h"
#include "sysdeps.h"
#include "options.h"
#include "uae.h"
#include "disk.h"
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

static bool blob_holds(const p6_media &m, const std::vector<uint8_t> &bytes)
{
    p6_blob_handle h = 0;
    p6_blob_info info{};
    if (p6_blob_open(id_of(m).c_str(), "rb", &h) != P6_BLOB_OK || p6_blob_stat(h, &info) != P6_BLOB_OK) return false;
    std::vector<uint8_t> copy(info.bytes);
    size_t items = 0;
    const bool ok = p6_blob_read(h, copy.data(), copy.size(), 1, copy.size(), &items) == P6_BLOB_OK && items == copy.size() &&
                    copy == bytes;
    p6_blob_close(h);
    return ok;
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
        for (int i = 0; i < 4; ++i) assert(currprefs.floppyslots[i].df[0] == 0);
        assert(currprefs.floppy_read_only && changed_prefs.floppy_read_only);
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

static p6_media_event event_of(uint32_t type, uint32_t drive, uint32_t index, uint64_t sequence)
{
    p6_media_event e{};
    e.abi_version = P6_ABI_VERSION;
    e.struct_size = sizeof e;
    e.type = type;
    e.drive = drive;
    e.media_index = index;
    e.sequence = sequence;
    return e;
}

static void run_units(int count)
{
    p6_input in{};
    in.abi_version = P6_ABI_VERSION;
    in.struct_size = sizeof in;
    for (int i = 0; i < count; ++i) {
        p6_frame f{};
        f.abi_version = P6_ABI_VERSION;
        f.struct_size = sizeof f;
        p6_error e = make_error();
        const p6_result r = p6_run_frame(&in, 0, &f, &e);
        if (r != P6_OK) fprintf(stderr, "p6_run_frame: %d %s\n", int(r), e.message);
        assert(r == P6_OK);
    }
}

static bool enqueues(const p6_media_event &ev, p6_result code, const char *what)
{
    p6_error e = make_error();
    const uint32_t queued = p6_session::session.media_queued;
    const p6_result got = p6_media_event_enqueue(&ev, &e);
    if (got != code) fprintf(stderr, "%s: enqueue returned %d (%s), expected %d\n", what, int(got), e.message, int(code));
    assert(got == code);
    assert((got == P6_OK) == (e.message[0] == 0));
    assert(p6_session::session.media_queued == queued + (got == P6_OK ? 1u : 0u));    // a refusal queues nothing
    return got == P6_OK;
}

static bool names(int drive, const std::string &id)
{
    return id == currprefs.floppyslots[drive].df && id == changed_prefs.floppyslots[drive].df;
}

static void event_validation(const std::vector<uint8_t> &rom, const std::vector<p6_media> &media)
{
    p6_error e = make_error();
    const p6_media_event mount = event_of(P6_MEDIA_EVENT_MOUNT, 0, 0, 1);
    assert(p6_media_event_enqueue(&mount, &e) == P6_ERROR_BAD_STATE && e.message[0]);    // nothing loaded
    assert(load(rom, media.data(), 2, &e) == P6_OK);
    assert(p6_media_event_enqueue(nullptr, &e) == P6_ERROR_BAD_ARGUMENT);
    p6_media_event bad = mount; bad.abi_version = 2;         enqueues(bad, P6_ERROR_BAD_ARGUMENT, "abi");
    bad = mount; bad.struct_size = 8;                        enqueues(bad, P6_ERROR_BAD_ARGUMENT, "size");
    bad = mount; bad.reserved0 = 1;                          enqueues(bad, P6_ERROR_BAD_ARGUMENT, "reserved0");
    bad = mount; bad.reserved[0] = 1;                        enqueues(bad, P6_ERROR_BAD_ARGUMENT, "reserved[0]");
    bad = mount; bad.reserved[1] = 1;                        enqueues(bad, P6_ERROR_BAD_ARGUMENT, "reserved[1]");
    bad = mount; bad.type = 0;                               enqueues(bad, P6_ERROR_BAD_ARGUMENT, "type 0");
    bad = mount; bad.type = 3;                               enqueues(bad, P6_ERROR_BAD_ARGUMENT, "type 3");
    bad = mount; bad.drive = 4;                              enqueues(bad, P6_ERROR_BAD_ARGUMENT, "drive 4");
    bad = mount; bad.drive = 2;                              enqueues(bad, P6_ERROR_UNSUPPORTED, "drive 2");
    bad = mount; bad.drive = 3;                              enqueues(bad, P6_ERROR_UNSUPPORTED, "drive 3");
    bad = mount; bad.media_index = 2;                        enqueues(bad, P6_ERROR_BAD_ARGUMENT, "index past the list");
    bad = mount; bad.media_index = P6_MEDIA_INDEX_EJECT;     enqueues(bad, P6_ERROR_BAD_ARGUMENT, "mount of the eject marker");
    bad = event_of(P6_MEDIA_EVENT_EJECT, 0, 0, 1);           enqueues(bad, P6_ERROR_BAD_ARGUMENT, "eject with an index");
    // Sequences are session-wide and strictly increasing; a refused event consumes none.
    assert(enqueues(event_of(P6_MEDIA_EVENT_MOUNT, 0, 0, 5), P6_OK, "first"));
    enqueues(event_of(P6_MEDIA_EVENT_MOUNT, 0, 1, 5), P6_ERROR_BAD_ARGUMENT, "same sequence");
    enqueues(event_of(P6_MEDIA_EVENT_MOUNT, 0, 1, 4), P6_ERROR_BAD_ARGUMENT, "lower sequence");
    enqueues(event_of(P6_MEDIA_EVENT_MOUNT, 4, 1, 6), P6_ERROR_BAD_ARGUMENT, "refused with a good sequence");
    assert(enqueues(event_of(P6_MEDIA_EVENT_EJECT, 0, P6_MEDIA_INDEX_EJECT, 6), P6_OK, "6 after the refusals"));
    for (uint64_t n = 7; p6_session::session.media_queued < 16; ++n) assert(enqueues(event_of(P6_MEDIA_EVENT_MOUNT, 1, 1, n), P6_OK, "fill"));
    enqueues(event_of(P6_MEDIA_EVENT_MOUNT, 1, 1, 1000), P6_ERROR_CAPACITY, "queue full");
    // Nothing of it survives an unload: the next load starts with an empty queue and no sequence.
    assert(p6_unload(&e) == P6_OK && p6_session::session.media_queued == 0 && !p6_session::session.have_sequence);
    assert(p6_media_event_enqueue(&mount, &e) == P6_ERROR_BAD_STATE);
}

// Boot contents, the events after the first frame, and the drives seen from the engine.
static void drives_follow_the_events(const std::vector<uint8_t> &rom, const std::vector<p6_media> &media,
                                     const std::vector<std::vector<uint8_t>> &disks)
{
    const std::string a = id_of(media[0]), b = id_of(media[1]);
    p6_error e = make_error();
    assert(load(rom, media.data(), 2, &e) == P6_OK);
    // Before the first frame only the power-on contents are set: the last event of a drive wins.
    assert(enqueues(event_of(P6_MEDIA_EVENT_MOUNT, 0, 0, 1), P6_OK, "boot a"));
    assert(enqueues(event_of(P6_MEDIA_EVENT_EJECT, 0, P6_MEDIA_INDEX_EJECT, 2), P6_OK, "boot eject"));
    assert(enqueues(event_of(P6_MEDIA_EVENT_MOUNT, 0, 1, 3), P6_OK, "boot b"));
    assert(enqueues(event_of(P6_MEDIA_EVENT_MOUNT, 1, 0, 4), P6_OK, "boot a in df1"));
    assert(disk_empty(0) && disk_empty(1));
    run_units(1);
    assert(p6_session::session.media_queued == 0 && !disk_empty(0) && !disk_empty(1));   // inserted by the reset head
    assert(names(0, b) && names(1, a));
    assert(currprefs.floppy_read_only && changed_prefs.floppy_read_only);

    // The same disk again is a no-op; a different one takes the engine's delayed swap (eject at the next vsync, insert ~100
    // units later), and a later mount before it lands wins.
    assert(enqueues(event_of(P6_MEDIA_EVENT_MOUNT, 1, 0, 5), P6_OK, "again"));
    run_units(3);
    assert(!disk_empty(1) && names(1, a));
    assert(enqueues(event_of(P6_MEDIA_EVENT_MOUNT, 0, 0, 6), P6_OK, "swap df0 to a"));
    run_units(3);
    assert(disk_empty(0) && names(0, a));
    assert(enqueues(event_of(P6_MEDIA_EVENT_MOUNT, 0, 1, 7), P6_OK, "and back to b before it landed"));
    run_units(60);
    assert(disk_empty(0) && names(0, b));
    run_units(90);
    assert(!disk_empty(0) && names(0, b) && !disk_empty(1) && names(1, a));
    // Eject is immediate and a second one on an empty drive changes nothing.
    assert(enqueues(event_of(P6_MEDIA_EVENT_EJECT, 1, P6_MEDIA_INDEX_EJECT, 8), P6_OK, "eject df1"));
    run_units(1);
    assert(disk_empty(1) && names(1, "") && p6_session::session.drive_media[1] == -1);
    assert(enqueues(event_of(P6_MEDIA_EVENT_EJECT, 1, P6_MEDIA_INDEX_EJECT, 9), P6_OK, "eject an empty df1"));
    run_units(1);
    assert(disk_empty(1) && names(1, ""));
    // An eject cancels a swap that has not landed.
    assert(enqueues(event_of(P6_MEDIA_EVENT_MOUNT, 1, 1, 10), P6_OK, "mount b in df1"));
    run_units(5);
    assert(enqueues(event_of(P6_MEDIA_EVENT_EJECT, 1, P6_MEDIA_INDEX_EJECT, 11), P6_OK, "eject before it landed"));
    run_units(130);
    assert(disk_empty(1) && names(1, ""));
    // Mounting, swapping and ejecting never touched the registered images or the caller's.
    assert(blob_holds(media[0], disks[0]) && blob_holds(media[1], disks[1]));
    assert(p6_unload(&e) == P6_OK);
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
    std::vector<std::vector<uint8_t>> disks = {make_adf(7), make_adf(8)};
    const std::vector<std::vector<uint8_t>> before = disks;
    std::vector<p6_media> media = {medium_of(disks[0]), medium_of(disks[1])};
    event_validation(rom, media);
    drives_follow_the_events(rom, media, disks);
    assert(disks == before);                           // no swap, eject or frame wrote to the caller's images
    assert(p6_shutdown(&e) == P6_OK);
    puts("media ok");
    return 0;
}
