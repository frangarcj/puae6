// Whole memory.cpp, with only its source-selection entry exercised. The
// read_rom_name boundary is observed here, not a production ROM manager.
#include "memory.cpp"
#include "blob_io.h"
#include <cassert>
#include <cerrno>
#include <initializer_list>

static uae_prefs prefs;
static unsigned calls;
static bool forced_failure;
static const char *expected_name;
static zfile *returned;
zfile *read_rom_name(const TCHAR *name, bool rw)
{
    assert(!rw && name == prefs.romfile && !strcmp(name, expected_name));
    ++calls;
    if (forced_failure) { errno = EIO; return returned = nullptr; }
    returned = zfile_fopen(name, "rb", ZFD_NORMAL);
    if (returned) assert(zfile_fseek(returned, 3, SEEK_SET) == 0);
    return returned;
}

static zfile *open_source(const char *name)
{
    strcpy(prefs.romfile, name);
    expected_name = name; calls = 0;
    zfile *f = get_kickstart_filehandle(&prefs);
    assert(calls == 1 && f == returned && !strcmp(prefs.romfile, name));
    return f;
}

int main()
{
    const unsigned char data[] = {0, 1, 2, 3, 4, 5, 6, 7};
    // These are arbitrary legal test bytes, not a bootable/valid Kickstart ROM.
    assert(p6_blob_register("chosen.rom", P6_BLOB_ROM, data, sizeof data) == 0);
    assert(p6_blob_register("kick.rom", P6_BLOB_ROM, data, sizeof data) == 0);
    for (int i = 0; i < 100; ++i) {
        zfile *f = open_source("chosen.rom");
        assert(f && !strcmp(zfile_getname(f), "chosen.rom"));
        assert(zfile_size(f) == sizeof data && zfile_ftell(f) == 3);
        unsigned char read[2];
        assert(zfile_fread(read, 1, 2, f) == 2 && read[0] == 3 && read[1] == 4);
        zfile_fclose(f);
    }
    assert(!open_source("absent.rom") && errno == ENOENT);
    for (const char *name : {"", "../kick.rom", "/kick.rom", "roms/kick.rom"})
        assert(!open_source(name));
    char too_long[MAX_DPATH]; memset(too_long, 'x', sizeof too_long - 1);
    too_long[sizeof too_long - 1] = 0;
    assert(!open_source(too_long));
    forced_failure = true;
    assert(!open_source("chosen.rom") && errno == EIO);
    forced_failure = false;
    // Capacity error is not retried with an alternate asset either.
    zfile *held[P6_BLOB_STREAMS];
    for (auto &f : held) { f = open_source("chosen.rom"); assert(f); }
    assert(!open_source("chosen.rom") && errno == EMFILE);
    for (auto f : held) zfile_fclose(f);
    p6_blob_clear();
}
