// Exercises actual rommgr input, normalization, and keyring bodies on P6 blobs.
#include <cassert>
#include <cstddef>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>
#include <vector>
#include "sysconfig.h"
#include "sysdeps.h"
#include "options.h"
#include "uae.h"
#include "memory.h"
#include "rommgr.h"
#include "zfile.h"
#include "crc32.h"
#include "blob_io.h"

static long fail_malloc_at = -1, fail_calloc_at = -1;
static long malloc_calls, calloc_calls;
static long fail_zcalloc_at = -1, zfile_calloc_calls;
extern "C" void *p6_zfile_test_malloc(size_t size) { return std::malloc(size); }
extern "C" void *p6_zfile_test_calloc(size_t count, size_t size)
{
    if (fail_zcalloc_at >= 0 && zfile_calloc_calls++ == fail_zcalloc_at) return nullptr;
    return std::calloc(count, size);
}
static void *probe_malloc(size_t size, size_t count)
{
    if (fail_malloc_at >= 0 && malloc_calls++ == fail_malloc_at) return nullptr;
    return count && size > SIZE_MAX / count ? nullptr : std::malloc(size * count);
}
static void *probe_calloc(size_t size, size_t count)
{
    if (fail_calloc_at >= 0 && calloc_calls++ == fail_calloc_at) return nullptr;
    return count && size > SIZE_MAX / count ? nullptr : std::calloc(count, size);
}
#undef xmalloc
#undef xcalloc
#undef xfree
#define xmalloc(T, N) static_cast<T *>(probe_malloc(sizeof(T), (N)))
#define xcalloc(T, N) static_cast<T *>(probe_calloc(sizeof(T), (N)))
#define xfree(P) std::free(P)

static struct romlist *rl;
static int romlist_cnt;
static struct romdata roms[1] = {}; // Empty SHA metadata: no synthetic ROM match.
bool cloanto_rom;
static struct romdata alias_data{};
static struct romlist alias_entry{};
static TCHAR alias_path[MAX_DPATH]{};

#include "rom_input_under_test.h"

struct zfile *read_rom(struct romdata *, bool) { std::abort(); }
bool my_issamepath(const TCHAR *, const TCHAR *) { std::abort(); }
void addkeydir(const TCHAR *) { std::abort(); }

static uint32_t sum32(const uint8_t *data, size_t size)
{
    uint64_t sum = 0;
    for (size_t i = 0; i < size; i += 4)
        sum += (uint64_t(data[i]) << 24) | (uint64_t(data[i + 1]) << 16) |
               (uint64_t(data[i + 2]) << 8) | data[i + 3];
    while (sum >> 32) sum = (sum & UINT64_C(0xffffffff)) + (sum >> 32);
    return uint32_t(sum);
}

static std::vector<uint8_t> make_rom(size_t size)
{
    std::vector<uint8_t> data(size);
    for (size_t i = 0; i < size; ++i) data[i] = uint8_t(0x80u + i * 37u + (i >> 5));
    data[0] = 0x11; data[1] = size == ROM_SIZE_256 ? 0x11 : 0x14;
    data[size - 20] = uint8_t(size >> 24); data[size - 19] = uint8_t(size >> 16);
    data[size - 18] = uint8_t(size >> 8); data[size - 17] = uint8_t(size);
    const size_t check_at = 0x100;
    std::memset(data.data() + check_at, 0, 4);
    const uint32_t check = ~sum32(data.data(), size);
    data[check_at] = uint8_t(check >> 24); data[check_at + 1] = uint8_t(check >> 16);
    data[check_at + 2] = uint8_t(check >> 8); data[check_at + 3] = uint8_t(check);
    assert(sum32(data.data(), size) == UINT32_MAX);
    return data;
}

static void register_blob(const char *id, const std::vector<uint8_t> &bytes,
                          uint32_t kind = P6_BLOB_ROM)
{ assert(p6_blob_register(id, kind, bytes.data(), bytes.size()) == 0); }

static std::vector<uint8_t> read_stream(zfile *file)
{
    assert(file);
    const int size = zfile_size32(file);
    assert(size >= 0);
    std::vector<uint8_t> bytes(size);
    assert(zfile_fread(bytes.data(), 1, bytes.size(), file) == bytes.size());
    zfile_fclose(file);
    return bytes;
}

static std::vector<uint8_t> cloanto(const std::vector<uint8_t> &plain,
                                    const std::vector<uint8_t> &key)
{
    std::vector<uint8_t> result{'A','M','I','R','O','M','T','Y','P','E','1'};
    for (size_t i = 0; i < plain.size(); ++i) result.push_back(plain[i] ^ key[i % key.size()]);
    return result;
}

static uae_u8 *key_copy(const std::vector<uint8_t> &key)
{
    auto *copy = static_cast<uae_u8 *>(std::malloc(key.size()));
    assert(copy); std::memcpy(copy, key.data(), key.size()); return copy;
}

static void input_paths()
{
    for (size_t size : {size_t(ROM_SIZE_256), size_t(ROM_SIZE_512)}) {
        auto plain = make_rom(size);
        const std::string suffix = size == ROM_SIZE_256 ? "256" : "512";
        std::string raw_id = "rom_raw" + suffix;
        register_blob(raw_id.c_str(), plain);
        std::strncpy(alias_path, raw_id.c_str(), sizeof alias_path - 1);
        alias_entry.path = alias_path; alias_entry.rd = &alias_data;
        rl = &alias_entry; romlist_cnt = 1; // Matching entry must not redirect P6 loading.
        assert(read_stream(read_rom_name(raw_id.c_str(), false)) == plain);

        auto swapped = plain; byteswap(swapped.data(), int(size));
        std::string swap_id = "rom_swap" + suffix;
        register_blob(swap_id.c_str(), swapped);
        assert(read_stream(read_rom_name(swap_id.c_str(), false)) == plain);

        auto word_swapped = plain; wordbyteswap(word_swapped.data(), int(size));
        std::string word_id = "rom_word" + suffix;
        register_blob(word_id.c_str(), word_swapped);
        assert(read_stream(read_rom_name(word_id.c_str(), false)) == plain);

        auto key = std::vector<uint8_t>(31);
        for (size_t i = 0; i < key.size(); ++i) key[i] = uint8_t(i * 11 + 3);
        auto encoded = cloanto(plain, key);
        std::string enc_id = "encrypted_rom" + suffix;
        register_blob(enc_id.c_str(), encoded);
        std::vector<uint8_t> hidden_key = key;
        std::string key_id = "keys_key" + suffix;
        register_blob(key_id.c_str(), hidden_key, P6_BLOB_KEY);
        free_keyring();
        assert(!read_rom_name(enc_id.c_str(), false)); // No implicit sibling key.
        assert(load_keyring(nullptr, key_id.c_str()) == 1 && get_keyring() == 1);
        struct zfile *decoded = read_rom_name(enc_id.c_str(), false);
        assert(decoded && _tcscmp(zfile_getname(decoded), enc_id.c_str()) == 0);
        assert(read_stream(decoded) == plain);
        assert(read_stream(zfile_fopen(enc_id.c_str(), _T("rb"))) == encoded);

        auto bad_key = key;
        for (size_t i = 2; i < bad_key.size(); ++i) bad_key[i] ^= uint8_t(i + 1);
        free_keyring();
        addkey(key_copy(bad_key), int(bad_key.size()), _T("plausible-wrong"));
        assert(!read_rom_name(enc_id.c_str(), false)); // A plausible wrong key alone must fail.
        free_keyring();
        addkey(key_copy(bad_key), int(bad_key.size()), _T("plausible-wrong"));
        addkey(key_copy(key), int(key.size()), _T("good"));
        assert(get_keyring() == 2);
        assert(read_stream(read_rom_name(enc_id.c_str(), false)) == plain);
        free_keyring();

        auto corrupt_plain = plain; corrupt_plain[2000] ^= 1;
        auto corrupt = cloanto(corrupt_plain, key);
        std::string corrupt_id = "encrypted_corrupt" + suffix;
        register_blob(corrupt_id.c_str(), corrupt);
        assert(load_keyring(nullptr, key_id.c_str()) == 1);
        assert(!read_rom_name(corrupt_id.c_str(), false));

        for (size_t payload_size : {size_t(0), size_t(ROM_SIZE_256 - 1),
                                    size_t(ROM_SIZE_256 + 1), size_t(ROM_SIZE_512 - 1)}) {
            std::vector<uint8_t> malformed{'A','M','I','R','O','M','T','Y','P','E','1'};
            malformed.resize(11 + payload_size, 0);
            std::string bad_id = "encrypted_bad" + suffix + "_" + std::to_string(payload_size);
            register_blob(bad_id.c_str(), malformed);
            assert(!read_rom_name(bad_id.c_str(), false));
        }
    }

    std::vector<uint8_t> kickdisk(1760 * 512);
    std::memcpy(kickdisk.data(), "KICK", 4);
    auto half = make_rom(ROM_SIZE_256);
    std::memcpy(kickdisk.data() + 512, half.data(), half.size());
    register_blob("rom_kickdisk", kickdisk);
    assert(read_stream(read_rom_name("rom_kickdisk", false)) == kickdisk);
}

static void keyring_and_oom()
{
    auto key = std::vector<uint8_t>(17, 0x4d);
    register_blob("keys_lifecycle", key, P6_BLOB_KEY);
    assert(load_keyring(nullptr, "keys_lifecycle") == 1);
    addkey(key_copy(key), int(key.size()), _T("duplicate"));
    assert(get_keyring() == 1);
    for (int i = 0; i < 4; ++i) {
        std::vector<uint8_t> distinct(18, uint8_t(i + 1));
        addkey(key_copy(distinct), int(distinct.size()), _T("slot"));
    }
    assert(get_keyring() == ROM_KEY_NUM);
    std::vector<uint8_t> overflow(18, 0x77);
    addkey(key_copy(overflow), int(overflow.size()), _T("overflow"));
    assert(get_keyring() == ROM_KEY_NUM);
    assert(load_keyring(nullptr, nullptr) == 0 && get_keyring() == 0);
    assert(load_keyring(nullptr, "") == 0 && get_keyring() == 0);
    addkey(nullptr, 0, _T("empty"));
    addkey(key_copy(key), -1, _T("negative"));
    assert(get_keyring() == 0);

    auto plain = make_rom(ROM_SIZE_256);
    auto swapped = plain; byteswap(swapped.data(), int(swapped.size()));
    register_blob("rom_oom", swapped);
    for (long fail : {0, 1}) {
        calloc_calls = 0; fail_calloc_at = fail;
        assert(!read_rom_name("rom_oom", false));
    }
    fail_calloc_at = -1;

    std::vector<uint8_t> cloanto_key(19, 0xa5);
    register_blob("keys_oom", cloanto_key, P6_BLOB_KEY);
    auto encoded = cloanto(plain, cloanto_key);
    register_blob("encrypted_oom", encoded);
    assert(load_keyring(nullptr, "keys_oom") == 1);
    malloc_calls = 0; fail_malloc_at = 0;
    assert(!read_rom_name("encrypted_oom", false));
    fail_malloc_at = -1;
    malloc_calls = 0; fail_malloc_at = 0;
    assert(load_keyring(nullptr, "keys_oom") == 0);
    fail_malloc_at = -1;

    for (bool encrypted : {false, true}) {
        auto publish_plain = make_rom(ROM_SIZE_256);
        const char *id;
        if (encrypted) {
            std::vector<uint8_t> key(19, 0x6a);
            register_blob("keys_publish", key, P6_BLOB_KEY);
            register_blob("encrypted_publish", cloanto(publish_plain, key));
            assert(load_keyring(nullptr, "keys_publish") == 1);
            id = "encrypted_publish";
        } else {
            byteswap(publish_plain.data(), int(publish_plain.size()));
            register_blob("rom_publish", publish_plain);
            id = "rom_publish";
        }
        for (long fail_at : {1L, 2L}) {
            zfile_calloc_calls = 0;
            fail_zcalloc_at = fail_at; // 0 opens input, 1 allocates result owner, 2 result data.
            assert(!read_rom_name(id, false));
            fail_zcalloc_at = -1;
            zfile *recovered = read_rom_name(id, false);
            assert(recovered);
            zfile_fclose(recovered);
        }
        p6_blob_handle handles[P6_BLOB_STREAMS];
        for (int i = 0; i < P6_BLOB_STREAMS; ++i)
            assert(p6_blob_open(id, "rb", &handles[i]) == 0);
        for (auto handle : handles) assert(p6_blob_close(handle) == 0);
    }
    free_keyring();
}

int main()
{
    input_paths();
    keyring_and_oom();
    free_keyring();
    p6_blob_clear();
}
