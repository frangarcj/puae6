// Media of p6_load and the media events against the real engine (tests/media/test_media.py).
//   usage: p6_media_probe boot.rom
// The guest-visible disk scenario (registers, MFM, replay) is T12c's; here the drives are looked at from the engine.
#include "p6.h"
#include "media.h"
#include "trackdisk.h"
#include "overlay.h"
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
    m = ma; m.type = 3;                                rejects(rom, &m, 1, P6_ERROR_UNSUPPORTED, "type 3");   // 2 is P6_MEDIA_HDF since T29a
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

// A reset started from the API keeps what is in the drives, also a swap that has not landed yet.
static void reset_keeps_the_media(const std::vector<uint8_t> &rom, const std::vector<p6_media> &media,
                                  const std::vector<std::vector<uint8_t>> &disks)
{
    const std::string a = id_of(media[0]), b = id_of(media[1]);
    p6_error e = make_error();
    assert(p6_reset(&e) == P6_ERROR_BAD_STATE && e.message[0]);          // nothing loaded
    assert(load(rom, media.data(), 2, &e) == P6_OK);
    assert(p6_reset(&e) == P6_OK && !p6_session::session.reset_queued);  // before the first frame the power-on reset is pending
    assert(enqueues(event_of(P6_MEDIA_EVENT_MOUNT, 0, 0, 1), P6_OK, "a in df0"));
    assert(enqueues(event_of(P6_MEDIA_EVENT_MOUNT, 1, 1, 2), P6_OK, "b in df1"));
    run_units(3);
    assert(!disk_empty(0) && !disk_empty(1) && names(0, a) && names(1, b));
    assert(p6_reset(&e) == P6_OK && p6_session::session.reset_queued);
    run_units(1);
    assert(!p6_session::session.reset_queued && !disk_empty(0) && !disk_empty(1) && names(0, a) && names(1, b));
    // A swap that has not landed when the reset comes: the reset head inserts the disk the caller asked for, at once.
    assert(enqueues(event_of(P6_MEDIA_EVENT_MOUNT, 0, 1, 3), P6_OK, "swap df0 to b"));
    run_units(3);
    assert(disk_empty(0) && names(0, b));
    assert(p6_reset(&e) == P6_OK);
    run_units(1);
    assert(!disk_empty(0) && names(0, b) && p6_session::session.drive_media[0] == 1);
    // Events queued with the reset run after its head, in order.
    assert(enqueues(event_of(P6_MEDIA_EVENT_EJECT, 1, P6_MEDIA_INDEX_EJECT, 4), P6_OK, "eject df1"));
    assert(p6_reset(&e) == P6_OK);
    run_units(1);
    assert(!disk_empty(0) && disk_empty(1) && names(1, ""));
    assert(blob_holds(media[0], disks[0]) && blob_holds(media[1], disks[1]));
    assert(p6_unload(&e) == P6_OK && p6_reset(&e) == P6_ERROR_BAD_STATE);
}

static p6_result appends(const p6_media *medium, uint32_t *index, p6_result code, const char *what)
{
    p6_error e = make_error();
    const uint32_t count = p6_session::session.media_count;
    uint32_t got_index = 0xfeedu;
    const p6_result got = p6_media_append(medium, index ? &got_index : nullptr, &e);
    if (got != code) fprintf(stderr, "%s: append returned %d (%s), expected %d\n", what, int(got), e.message, int(code));
    assert(got == code);
    assert((got == P6_OK) == (e.message[0] == 0) && strlen(e.message) < P6_ERROR_TEXT_CAPACITY);
    if (got != P6_OK) assert(got_index == 0xfeedu && p6_session::session.media_count == count);    // a refusal changes nothing, not even *index
    if (index) *index = got_index;
    return got;
}

// T17e: the manifest grows at its end after p6_load, validated as p6_load validates, and a refused or repeated medium changes nothing.
static void media_append(const std::vector<uint8_t> &rom)
{
    const std::vector<uint8_t> a = make_adf(40), b = make_adf(41), c = make_adf(42);
    const p6_media ma = medium_of(a), mb = medium_of(b), mc = medium_of(c);
    p6_error e = make_error();
    uint32_t index = 0;
    p6_caps caps{P6_ABI_VERSION, sizeof caps, 0, 0, 0, 0, 0};
    assert(p6_query_caps(&caps, &e) == P6_OK && (caps.flags & P6_CAP_MEDIA_APPEND));
    appends(&mb, &index, P6_ERROR_BAD_STATE, "nothing loaded");
    assert(load(rom, &ma, 1, &e) == P6_OK);
    appends(nullptr, &index, P6_ERROR_BAD_ARGUMENT, "no medium");
    appends(&mb, nullptr, P6_ERROR_BAD_ARGUMENT, "no index");
    p6_media m = mb; m.type = 2;                               appends(&m, &index, P6_ERROR_UNSUPPORTED, "type");
    m = mb; m.flags = 1;                                       appends(&m, &index, P6_ERROR_BAD_ARGUMENT, "flags");
    m = mb; m.image.data = nullptr;                            appends(&m, &index, P6_ERROR_BAD_ARGUMENT, "no data");
    for (uint64_t bytes : {uint64_t(0), p6_bridge::kAdfBytes - 1, p6_bridge::kAdfBytes + 1}) {
        m = mb; m.image.bytes = bytes;                         appends(&m, &index, P6_ERROR_WRONG_MEDIA, "size");
    }
    std::vector<uint8_t> special = b;
    memcpy(special.data(), "UAE-1ADF", 8);
    m = medium_of(special);                                    appends(&m, &index, P6_ERROR_WRONG_MEDIA, "special header");
    m = mb; m.content_id[0] ^= 1;                              appends(&m, &index, P6_ERROR_WRONG_MEDIA, "id is not the SHA-256 of the image");
    assert(!registered(mb) && p6_session::session.media_count == 1);

    // A medium goes to the end of the manifest; the one that was there keeps its index; the image is the caller's, byte for byte.
    assert(appends(&mb, &index, P6_OK, "append b") == P6_OK && index == 1);
    const p6_session::Session &s = p6_session::session;
    assert(s.media_count == 2 && !memcmp(s.media_content_id[0], ma.content_id, 32) && !memcmp(s.media_content_id[1], mb.content_id, 32));
    assert(registered(mb) && blob_holds(mb, b) && blob_holds(ma, a));
    // The same content again, or one that p6_load registered, is the index it already has: a disk is its content.
    assert(appends(&mb, &index, P6_OK, "b again") == P6_OK && index == 1 && s.media_count == 2);
    assert(appends(&ma, &index, P6_OK, "a, which p6_load registered") == P6_OK && index == 0 && s.media_count == 2);
    // The new index is a medium like the others: an event can mount it (an index past the manifest was refused before), and the drive then holds it.
    enqueues(event_of(P6_MEDIA_EVENT_MOUNT, 0, 2, 1), P6_ERROR_BAD_ARGUMENT, "index 2 does not exist yet");
    assert(appends(&mc, &index, P6_OK, "append c") == P6_OK && index == 2);
    assert(enqueues(event_of(P6_MEDIA_EVENT_MOUNT, 0, 2, 1), P6_OK, "mount c, appended"));
    run_units(1);
    assert(!disk_empty(0) && names(0, id_of(mc)) && s.drive_media[0] == 2);
    // A medium is never registered in the middle of a call (p6_run_frame holds the guard).
    {
        p6_session::Guard busy;
        assert(busy.ok);
        const std::vector<uint8_t> d = make_adf(43);
        const p6_media md = medium_of(d);
        appends(&md, &index, P6_ERROR_BUSY, "inside another call");
        assert(!registered(md));
    }
    // The manifest fills up at P6_MAX_MEDIA and then refuses a new medium, still answering for one it holds.
    std::vector<std::vector<uint8_t>> more;
    for (uint32_t i = 3; i < P6_MAX_MEDIA; ++i) {
        more.push_back(make_adf(100 + i));
        const p6_media mi = medium_of(more.back());
        assert(appends(&mi, &index, P6_OK, "fill") == P6_OK && index == i);
    }
    assert(s.media_count == P6_MAX_MEDIA);
    const std::vector<uint8_t> last = make_adf(999);
    const p6_media ml = medium_of(last);
    appends(&ml, &index, P6_ERROR_CAPACITY, "full");
    assert(!registered(ml));
    assert(appends(&mc, &index, P6_OK, "a medium it holds, when full") == P6_OK && index == 2);
    assert(appends(&ma, &index, P6_OK, "the first one, when full") == P6_OK && index == 0);
    // The state bound does not depend on the manifest size: unload leaves nothing registered and the next load starts from its own list.
    assert(p6_unload(&e) == P6_OK);
    assert(!registered(mb) && !registered(mc) && s.media_count == 0 && !s.media_content_id[1][0]);
    appends(&mb, &index, P6_ERROR_BAD_STATE, "after unload");
    assert(load(rom, &mb, 1, &e) == P6_OK && s.media_count == 1 && registered(mb) && !registered(mc));
    assert(appends(&mb, &index, P6_OK, "b, which is now index 0") == P6_OK && index == 0);
    assert(p6_unload(&e) == P6_OK);
}


// --- T57a: the extended ADF as a floppy image -----------------------------------------------------------------------------------------------------------------------
static void put16(std::vector<uint8_t> &v, size_t at, uint32_t x) { v[at] = uint8_t(x >> 8); v[at + 1] = uint8_t(x); }
static void put32(std::vector<uint8_t> &v, size_t at, uint32_t x) { for (int i = 0; i < 4; ++i) v[at + i] = uint8_t(x >> (24 - 8 * i)); }

// UAE-1ADF with `tracks` tracks: track 0 raw with `raw_bits` bits (6250 words of pattern), the others eleven AmigaDOS sectors of zeros and a pattern byte.
static std::vector<uint8_t> make_ext2(uint32_t tracks, uint32_t raw_bits, uint8_t pattern, uint32_t dos_bytes = 5632)
{
    const uint32_t raw_bytes = (raw_bits + 15) / 16 * 2;
    std::vector<uint8_t> v(12 + 12 * size_t(tracks) + raw_bytes + size_t(dos_bytes) * (tracks - 1), pattern);
    memcpy(v.data(), "UAE-1ADF", 8);
    put16(v, 8, 0);
    put16(v, 10, tracks);
    for (uint32_t t = 0; t < tracks; ++t) {
        uint8_t *row = &v[12 + 12 * size_t(t)];
        memset(row, 0, 12);
        row[3] = t == 0 ? 1 : 0;
        const uint32_t length = t == 0 ? raw_bytes : dos_bytes;
        put32(v, 12 + 12 * size_t(t) + 4, length);
        put32(v, 12 + 12 * size_t(t) + 8, t == 0 ? raw_bits : 0);
    }
    return v;
}

static std::vector<uint8_t> make_ext1(uint8_t pattern)
{
    std::vector<uint8_t> v(648 + 3000 + 5632u * 159, pattern);
    memcpy(v.data(), "UAE--ADF", 8);
    for (uint32_t t = 0; t < 160; ++t) {
        put16(v, 8 + 4 * size_t(t), t == 0 ? 0x4489 : 0);
        put16(v, 10 + 4 * size_t(t), t == 0 ? 3000 : 5632);
    }
    return v;
}

static void extended_adf(const std::vector<uint8_t> &rom)
{
    p6_error e = make_error();
    const std::vector<uint8_t> ext2 = make_ext2(160, 100000, 0x5a), ext1 = make_ext1(0x33), plain = make_adf(61);
    const p6_media m2 = medium_of(ext2), m1 = medium_of(ext1), mp = medium_of(plain);

    // Accepted by their bytes: the format is not in the type, and the manifest remembers it.
    const p6_media mixed[3] = {mp, m2, m1};
    assert(load(rom, mixed, 3, &e) == P6_OK && e.message[0] == 0);
    const p6_session::Session &s = p6_session::session;
    assert(s.media_count == 3 && s.media_format[0] == p6_floppy::kAdf && s.media_format[1] == p6_floppy::kExt2 && s.media_format[2] == p6_floppy::kExt1);
    assert(s.floppy_bytes == plain.size() + ext2.size() + ext1.size());
    assert(blob_holds(m2, ext2) && blob_holds(m1, ext1) && blob_holds(mp, plain));
    // The identity is the SHA-256 of the file as given: the same disk in the other format is another medium, and a flipped bit another one again.
    std::vector<uint8_t> other = ext2;
    other[other.size() - 1] ^= 1;
    const p6_media mo = medium_of(other);
    assert(memcmp(mo.content_id, m2.content_id, 32) != 0);
    // Mounted in a drive like any disk.
    p6_media_event event{};
    event.abi_version = P6_ABI_VERSION;
    event.struct_size = sizeof event;
    event.type = P6_MEDIA_EVENT_MOUNT;
    event.drive = 0;
    event.media_index = 1;
    event.sequence = 1;
    assert(p6_media_event_enqueue(&event, &e) == P6_OK);
    run_units(1);
    assert(!disk_empty(0) && names(0, id_of(m2)) && s.drive_media[0] == 1);
    // Appended after the load, with the same rules: another extended disk is registered, and the total of the floppies is bounded.
    uint32_t index = 0;
    assert(p6_media_append(&mo, &index, &e) == P6_OK && index == 3 && s.media_format[3] == p6_floppy::kExt2);
    assert(p6_unload(&e) == P6_OK);
    assert(!registered(m2) && s.media_count == 0);

    // What the engine could not read safely is refused before it is touched, with a message.
    for (uint32_t tracks : {0u, 167u, 0xFFFFu}) {
        std::vector<uint8_t> hostile = ext2;
        put16(hostile, 10, tracks);
        const p6_media mh = medium_of(hostile);
        rejects(rom, &mh, 1, P6_ERROR_WRONG_MEDIA, "number of tracks");
    }
    {
        std::vector<uint8_t> hostile = ext2;
        hostile.resize(hostile.size() - 1);                              // the last track lies beyond the end of the file
        const p6_media mh = medium_of(hostile);
        rejects(rom, &mh, 1, P6_ERROR_WRONG_MEDIA, "track beyond the file");
        hostile = ext2;
        put32(hostile, 12 + 8, 100000 + 16);                              // more bits than the bytes of the track hold
        const p6_media mb = medium_of(hostile);
        rejects(rom, &mb, 1, P6_ERROR_WRONG_MEDIA, "bits beyond the track");
        hostile = ext2;
        hostile[12 + 3] = 4;                                              // a track type the core does not read
        const p6_media mt = medium_of(hostile);
        rejects(rom, &mt, 1, P6_ERROR_WRONG_MEDIA, "track type");
        hostile = ext1;
        put16(hostile, 10, 60000);                                        // a track of 60000 bytes: beyond what a double-density track holds
        const p6_media ml = medium_of(hostile);
        rejects(rom, &ml, 1, P6_ERROR_WRONG_MEDIA, "ext1 long track");
        std::vector<uint8_t> big(4u << 20, 0);                            // beyond the 3 MiB the core reads
        memcpy(big.data(), "UAE-1ADF", 8);
        const p6_media mg = medium_of(big);
        rejects(rom, &mg, 1, P6_ERROR_WRONG_MEDIA, "larger than 3 MiB");
    }
    // The floppies together are bounded by 16 plain disks (14 417 920 bytes), whatever their formats: five extended disks of 2.9 MB fit, a sixth or a plain one with them does not.
    {
        std::vector<std::vector<uint8_t>> many;
        std::vector<p6_media> list;
        for (uint32_t i = 0; i < 6; ++i) {
            many.push_back(make_ext2(160, 100000, uint8_t(i), 18000));
            list.push_back(medium_of(many.back()));
        }
        assert(5 * many[0].size() <= p6_floppy::kMaxFloppyBytes && 6 * many[0].size() > p6_floppy::kMaxFloppyBytes && 5 * many[0].size() + plain.size() > p6_floppy::kMaxFloppyBytes);
        rejects(rom, list.data(), 6, P6_ERROR_CAPACITY, "six extended disks");
        std::vector<p6_media> withplain(list.begin(), list.begin() + 5);
        withplain.push_back(mp);
        rejects(rom, withplain.data(), 6, P6_ERROR_CAPACITY, "five extended disks and a plain one");
        assert(load(rom, list.data(), 5, &e) == P6_OK);
        index = 0;
        assert(p6_media_append(&list[5], &index, &e) == P6_ERROR_CAPACITY && e.message[0] != 0);        // nothing registered
        assert(!registered(list[5]) && s.media_count == 5);
        assert(p6_media_append(&list[2], &index, &e) == P6_OK && index == 2);                          // a medium it holds is still answered
        assert(p6_unload(&e) == P6_OK);
    }
    assert(p6_shutdown(&e) == P6_OK);

    // With writes on (P6_CONFIG_FLOPPY_WRITES) a plain ADF has the overlay and an extended one stays write protected, reported with no sectors and not exportable.
    p6_config c = make_config();
    c.flags = P6_CONFIG_FLOPPY_WRITES;
    assert(p6_init(&c, &e) == P6_OK);
    assert(load(rom, mixed, 3, &e) == P6_OK);
    assert(p6_bridge::media_forced_write_protect(id_of(m2).c_str()) && p6_bridge::media_forced_write_protect(id_of(m1).c_str()) && !p6_bridge::media_forced_write_protect(id_of(mp).c_str()));
    p6_media_write_status st{};
    st.abi_version = P6_ABI_VERSION;
    st.struct_size = sizeof st;
    for (uint32_t i = 0; i < 3; ++i) {
        st.media_index = i;
        assert(p6_media_write_status_get(&st, &e) == P6_OK && st.medium_sectors == 0 && st.raw_tracks == 0 && st.overlay_capacity == P6_OVL_SLOTS);
    }
    std::vector<uint8_t> out(p6_bridge::kAdfBytes);
    uint64_t written = 7;
    assert(p6_media_export(0, out.data(), out.size(), &written, &e) == P6_OK && written == p6_bridge::kAdfBytes && out == plain);
    written = 7;
    assert(p6_media_export(1, out.data(), out.size(), &written, &e) == P6_ERROR_UNSUPPORTED && written == 0 && e.message[0] != 0);
    assert(p6_media_export(2, out.data(), out.size(), &written, &e) == P6_ERROR_UNSUPPORTED && written == 0);
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
    reset_keeps_the_media(rom, media, disks);
    media_append(rom);
    extended_adf(rom);                                 // leaves the instance initialized with writes on
    assert(disks == before);                           // no swap, eject or frame wrote to the caller's images
    assert(p6_shutdown(&e) == P6_OK);
    puts("media ok");
    return 0;
}
