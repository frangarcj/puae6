// Exercises the real P6 DISK_validate_filename body with synthetic immutable assets.
#include <cassert>
#include <cerrno>
#include <cstddef>
#include <cstdint>
#include <algorithm>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>
#include <vector>
#include "sysconfig.h"
#include "sysdeps.h"
#include "options.h"
#include "disk.h"
#include "zfile.h"
#include "crc32.h"
#include "blob_io.h"

static long fail_malloc_at = -1, malloc_calls;
static long fail_calloc_at = -1, calloc_calls;
extern "C" void *p6_zfile_test_malloc(size_t n)
{
    if (fail_malloc_at >= 0 && malloc_calls++ == fail_malloc_at) return nullptr;
    return std::malloc(n);
}
extern "C" void *p6_zfile_test_calloc(size_t n, size_t size)
{
    if (fail_calloc_at >= 0 && calloc_calls++ == fail_calloc_at) return nullptr;
    return std::calloc(n, size);
}

#include "disk_asset_under_test.h"

static std::vector<uint8_t> asset(size_t n, uint8_t seed)
{
    std::vector<uint8_t> v(n);
    for (size_t i = 0; i < n; ++i) v[i] = uint8_t(seed + i * 29u + (i >> 3));
    return v;
}
static uint32_t crc_reference(const std::vector<uint8_t> &v)
{
    uint32_t c = UINT32_C(0xffffffff);
    for (uint8_t b : v) {
        c ^= b;
        for (int i = 0; i < 8; ++i) c = (c >> 1) ^ (UINT32_C(0xedb88320) & (0u - (c & 1u)));
    }
    return c ^ UINT32_C(0xffffffff);
}
static void reg(const char *id, uint32_t kind, const std::vector<uint8_t> &v)
{ assert(p6_blob_register(id, kind, v.data(), v.size()) == P6_BLOB_OK); }
static void reset_outputs(TCHAR *name, bool *wp, uae_u32 *crc, zfile **f)
{
    std::fill(name, name + MAX_DPATH, _T('X')); *wp = false; *crc = 0xdecafbad; *f = (zfile *)1;
}
static void rejected(struct uae_prefs *p, const TCHAR *id, int error)
{
    TCHAR name[MAX_DPATH]; bool wp; uae_u32 crc; zfile *f;
    reset_outputs(name, &wp, &crc, &f); errno = 0;
    assert(DISK_validate_filename(p, id, 9, name, 1, &wp, &crc, &f) == 0);
    assert(errno == error && name[0] == 0 && wp && crc == 0 && f == nullptr);
}
static void success(struct uae_prefs *p, const char *id, const std::vector<uint8_t> &v,
                    int leave, bool supply_zf, bool want_handle, bool want_crc)
{
    TCHAR name[MAX_DPATH]; bool wp; uae_u32 crc; zfile *f;
    reset_outputs(name, &wp, &crc, &f);
    if (!supply_zf) f = nullptr;
    assert(DISK_validate_filename(p, id, -1, name, leave, &wp,
                                  want_crc ? &crc : nullptr, supply_zf ? &f : nullptr) == 1);
    assert(_tcscmp(name, id) == 0 && wp &&
           (want_crc ? crc == crc_reference(v) : crc == 0xdecafbad));
    assert((f != nullptr) == want_handle);
    if (f) {
        assert(zfile_gettype(f) == ZFILE_DISKIMAGE);
        assert(_tcscmp(zfile_getname(f), id) == 0 && zfile_ftell(f) == 0);
        if (want_crc) assert(zfile_crc32(f) == crc_reference(v) && zfile_ftell(f) == 0);
        std::vector<uint8_t> actual(v.size());
        assert(zfile_fread(actual.data(), 1, actual.size(), f) == actual.size() && actual == v);
        assert(zfile_fread(actual.data(), 1, 1, f) == 0 && zfile_ftell(f) == (long)v.size());
        uint8_t byte = 0; assert(zfile_fwrite(&byte, 1, 1, f) == 0);
        assert(zfile_ferror(f) == EROFS);
        zfile_fclose(f);
    }
}

int main()
{
    auto bytes = asset(4099, 0x71), other = asset(32, 0x22);
    reg("disk.valid", P6_BLOB_ADF, bytes);
    reg("disk.rom", P6_BLOB_ROM, other);
    reg("disk.key", P6_BLOB_KEY, other);
    static struct uae_prefs prefs{};
    for (bool readonly : {false, true}) {
        prefs.floppy_read_only = readonly;
        success(&prefs, "disk.valid", bytes, 1, true, true, true);
        success(&prefs, "disk.valid", bytes, 0, true, false, true);
        success(&prefs, "disk.valid", bytes, 1, false, false, true);
        success(&prefs, "disk.valid", bytes, 1, true, true, false);
    }
    rejected(&prefs, nullptr, EINVAL);
    rejected(&prefs, "", EINVAL);
    rejected(&prefs, "disk.missing", ENOENT);
    rejected(&prefs, "disk.rom", ENOTSUP);
    rejected(&prefs, "disk.key", ENOTSUP);
    std::string maxid = "a" + std::string(P6_BLOB_ID_BYTES - 1, 'z');
    auto tiny = asset(8, 7); reg(maxid.c_str(), P6_BLOB_ADF, tiny);
    success(&prefs, maxid.c_str(), tiny, 1, true, true, true);
    std::string too_long = "a" + std::string(P6_BLOB_ID_BYTES, 'z');
    rejected(&prefs, too_long.c_str(), EINVAL);
    std::string path = "disk/valid";
    rejected(&prefs, path.c_str(), EINVAL);
    auto empty = std::vector<uint8_t>{}; reg("disk.empty", P6_BLOB_ADF, empty);
    success(&prefs, "disk.empty", empty, 0, false, false, true);
    assert(DISK_validate_filename(&prefs, _T("disk.valid"), 0, nullptr, 0,
                                  nullptr, nullptr, nullptr) == 1);

    // An Amiga executable is not a floppy image, even when registered as ADF.
    // Missing bytes read as zero, as in the legacy 8-byte probe of drive_insert.
    const std::vector<uint8_t> exe_header{0, 0, 3, 0xf3, 0, 0, 0, 0};
    auto exe = exe_header; exe.insert(exe.end(), {1, 2, 3, 4});
    auto exe_short = std::vector<uint8_t>{0, 0, 3, 0xf3};
    auto near_miss = exe_header; near_miss[7] = 1; near_miss.push_back(9);
    auto near_short = std::vector<uint8_t>{0, 0, 3};
    reg("disk.exe", P6_BLOB_ADF, exe);
    reg("disk.exe.short", P6_BLOB_ADF, exe_short);
    reg("disk.exe.near", P6_BLOB_ADF, near_miss);
    reg("disk.exe.short3", P6_BLOB_ADF, near_short);
    rejected(&prefs, _T("disk.exe"), ENOTSUP);
    rejected(&prefs, _T("disk.exe.short"), ENOTSUP);
    success(&prefs, "disk.exe.near", near_miss, 1, true, true, true);
    success(&prefs, "disk.exe.short3", near_short, 1, true, true, false);
    success(&prefs, "disk.exe.short3", near_short, 0, false, false, true);
    // Rejection releases its stream: the header probe must not leak one.
    for (int i = 0; i < P6_BLOB_STREAMS + 2; ++i) rejected(&prefs, _T("disk.exe"), ENOTSUP);
    success(&prefs, "disk.valid", bytes, 1, true, true, true);

    // Exhaust and recover every real blob stream through the validator.
    std::vector<zfile *> open;
    for (int i = 0; i < P6_BLOB_STREAMS; ++i) {
        TCHAR name[MAX_DPATH]; zfile *f = nullptr; bool wp = false;
        assert(DISK_validate_filename(&prefs, _T("disk.valid"), 0, name, 1, &wp, nullptr, &f) == 1);
        assert(f && wp); open.push_back(f);
    }
    rejected(&prefs, _T("disk.valid"), EMFILE);
    for (auto *f : open) zfile_fclose(f);
    success(&prefs, "disk.valid", bytes, 1, true, true, false);

    // Owner allocation fails before publication; CRC scratch failure preserves ENOMEM.
    fail_calloc_at = 0;
    for (int i = 0; i < P6_BLOB_STREAMS; ++i) { calloc_calls = 0; rejected(&prefs, _T("disk.valid"), ENOMEM); }
    fail_calloc_at = -1;
    fail_malloc_at = 0;
    for (int i = 0; i < P6_BLOB_STREAMS; ++i) { malloc_calls = 0; rejected(&prefs, _T("disk.valid"), ENOMEM); }
    fail_malloc_at = -1;
    success(&prefs, "disk.valid", bytes, 1, true, true, true);
    p6_blob_clear();
    return 0;
}
