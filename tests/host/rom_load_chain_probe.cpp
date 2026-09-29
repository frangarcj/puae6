// End-to-end synthetic A500 ROM validation through real rommgr and memory bodies.
#include "sysconfig.h"
#include "sysdeps.h"
#include "crc32.h"
#define P6_REAL_ROM_INPUT
#define main isolated_memory_probe_main
#include "memory_rom_status_probe.cpp"
#undef main
#include <cstdint>
#include <utility>
#include <vector>

static uint32_t ones_sum(const uae_u8 *data, size_t size)
{
    uint64_t sum = 0;
    for (size_t i = 0; i < size; i += 4)
        sum += (uint64_t(data[i]) << 24) | (uint64_t(data[i + 1]) << 16) |
               (uint64_t(data[i + 2]) << 8) | data[i + 3];
    while (sum >> 32) sum = (sum & UINT64_C(0xffffffff)) + (sum >> 32);
    return uint32_t(sum);
}

static std::vector<uae_u8> payload(size_t size)
{
    std::vector<uae_u8> data(size);
    make_payload(data.data(), unsigned(size));
    return data;
}

static std::vector<uae_u8> byte_swapped(const std::vector<uae_u8> &source)
{
    auto result = source;
    for (size_t i = 0; i < result.size(); i += 2) std::swap(result[i], result[i + 1]);
    return result;
}

static std::vector<uae_u8> word_swapped(const std::vector<uae_u8> &source)
{
    auto result = source;
    for (size_t i = 0; i < result.size(); i += 4) {
        std::swap(result[i], result[i + 2]);
        std::swap(result[i + 1], result[i + 3]);
    }
    return result;
}

static void reseal(std::vector<uae_u8> &data)
{
    const size_t size = data.size();
    memset(data.data() + size - 24, 0, 4);
    const uint32_t check = ~ones_sum(data.data(), size);
    for (unsigned i = 0; i < 4; ++i) data[size - 24 + i] = check >> (24 - 8 * i);
}

static std::vector<uae_u8> cloanto_image(const std::vector<uae_u8> &plain,
                                         const std::vector<uae_u8> &key)
{
    std::vector<uae_u8> out{'A','M','I','R','O','M','T','Y','P','E','1'};
    for (size_t i = 0; i < plain.size(); ++i) out.push_back(plain[i] ^ key[i % key.size()]);
    return out;
}

static void expect_load(const char *id, int expected, const std::vector<uae_u8> *known = nullptr,
                        bool bad_checksum = false, bool metadata = false, unsigned cpu = 0)
{
    strcpy(changed_prefs.romfile, id);
    strcpy(currprefs.romfile, id);
    const unsigned old_patches = patches;
    protections = notices = errors = crc_notices = 0;
    use_known_rom = metadata;
    known_rom = {};
    known_rom.name = _T("Synthetic known ROM");
    known_rom.model = _T("A500");
    known_rom.cpu = cpu;
    known_rom.type = ROMTYPE_KICK;
    set_roms_modified();
    reload_roms();
    assert(p6_memory_rom_status() == expected && !roms_modified && need_hardreset);
    assert(protections == 2);
    const bool cpu_rejection = use_known_rom && expected == P6_ROM_UNSUPPORTED;
    assert(patches == old_patches + (expected == P6_ROM_LOAD_OK));
    assert(notices == (expected != P6_ROM_LOAD_OK && !cpu_rejection));
    assert(errors == notices && crc_notices == bad_checksum);
    if (expected == P6_ROM_LOAD_OK && known) {
        const size_t size = known->size();
        assert(kickmem_bank.reserved_size == int(size));
        assert(kickmem_bank.mask == int(size) - 1 && kickstart_version == 37);
        assert(!memcmp(kickmem_bank.baseaddr, known->data(), size));
    }
    use_known_rom = false;
}

static void set_rom(const char *id, const std::vector<uae_u8> &data)
{
    free_keyring();
    p6_blob_clear();
    assert(p6_blob_register(id, P6_BLOB_ROM, data.data(), data.size()) == 0);
}

static void reject_rom(const char *id, const std::vector<uae_u8> &data, int status)
{
    set_rom(id, data);
    expect_load(id, status);
}

static void accepted_inputs()
{
    for (size_t size : {size_t(ROM_SIZE_256), size_t(ROM_SIZE_512)}) {
        auto raw = payload(size);
        const char *id = size == ROM_SIZE_256 ? "raw256" : "raw512";
        set_rom(id, raw); expect_load(id, P6_ROM_LOAD_OK, &raw);

        auto swapped = byte_swapped(raw);
        id = size == ROM_SIZE_256 ? "swap256" : "swap512";
        set_rom(id, swapped); expect_load(id, P6_ROM_LOAD_OK, &raw);

        auto word = word_swapped(raw);
        id = size == ROM_SIZE_256 ? "word256" : "word512";
        set_rom(id, word); expect_load(id, P6_ROM_LOAD_OK, &raw);

        const std::vector<uae_u8> key(23, size == ROM_SIZE_256 ? 0x35 : 0xa7);
        const char *enc_id = size == ROM_SIZE_256 ? "cloak256" : "cloak512";
        const char *key_id = size == ROM_SIZE_256 ? "key256" : "key512";
        const auto encoded = cloanto_image(raw, key);
        p6_blob_clear();
        assert(p6_blob_register(enc_id, P6_BLOB_ROM, encoded.data(), encoded.size()) == 0);
        assert(p6_blob_register(key_id, P6_BLOB_KEY, key.data(), key.size()) == 0);
        assert(load_keyring(nullptr, key_id) == 1);
        expect_load(enc_id, P6_ROM_LOAD_OK, &raw);
    }
    auto half = payload(ROM_SIZE_256);
    std::vector<uae_u8> kick(1760 * 512);
    memcpy(kick.data(), "KICK", 4); memcpy(kick.data() + 512, half.data(), half.size());
    set_rom("kick880", kick); expect_load("kick880", P6_ROM_LOAD_OK, &half);
}

static void rejected_inputs()
{
    auto half = payload(ROM_SIZE_256);
    auto full = payload(ROM_SIZE_512);
    std::vector<uae_u8> bad = half; bad.pop_back();
    reject_rom("trunc256", bad, P6_ROM_READ_ERROR);
    bad = full; bad.pop_back(); reject_rom("trunc512", bad, P6_ROM_READ_ERROR);
    bad = half; bad.push_back(0x5a); reject_rom("suffix256", bad, P6_ROM_READ_ERROR);
    bad = full; bad.push_back(0x5a); reject_rom("suffix512", bad, P6_ROM_READ_ERROR);
    for (unsigned kind = 0; kind < 5; ++kind) {
        std::vector<uae_u8> bad;
        const char *id;
        if (kind == 0 || kind == 1) {
            bad.assign(ROM_SIZE_512, 0);
            if (kind == 0) { memcpy(bad.data(), "\0\0\3\363\0\0\0\0", 8); id = "hunkrom"; }
            else { memcpy(bad.data(), "\x7f" "ELF", 4); id = "elfrom"; }
        } else if (kind == 2) { bad.assign(ROM_SIZE_512 + 8, 0); id = "gvpplus8"; }
        else { bad.assign(kind == 3 ? ROM_SIZE_512 * 2 : ROM_SIZE_512 * 4, 0); id = kind == 3 ? "combined1m" : "combined2m"; }
        set_rom(id, bad); expect_load(id, P6_ROM_UNSUPPORTED);
    }
    std::vector<uae_u8> fake_kick(1760 * 512 + 1);
    memcpy(fake_kick.data(), "KICK", 4); memcpy(fake_kick.data() + 512, half.data(), half.size());
    set_rom("kick_suffix", fake_kick); expect_load("kick_suffix", P6_ROM_READ_ERROR);
    fake_kick.pop_back(); fake_kick[0] = 'N';
    set_rom("kick_nomagic", fake_kick); expect_load("kick_nomagic", P6_ROM_READ_ERROR);
    fake_kick = half; fake_kick.resize(ROM_SIZE_512, 0); memcpy(fake_kick.data(), "KICK", 4);
    reject_rom("kick_rawsize", fake_kick, P6_ROM_READ_ERROR);
    fake_kick.assign(1760 * 512 - 1, 0); memcpy(fake_kick.data(), "KICK", 4);
    reject_rom("kick_trunc", fake_kick, P6_ROM_READ_ERROR);

    auto bad_header = full; bad_header[1] = 0x11; reseal(bad_header);
    set_rom("badmagic", bad_header); expect_load("badmagic", P6_ROM_READ_ERROR);
    auto bad_length = full; bad_length[ROM_SIZE_512 - 20] ^= 1; reseal(bad_length);
    set_rom("badlength", bad_length); expect_load("badlength", P6_ROM_READ_ERROR);
    auto bad_sum = full; bad_sum[2000] ^= 1;
    set_rom("badsum", bad_sum); expect_load("badsum", P6_ROM_READ_ERROR, nullptr, true);
}

static void cpu_metadata_rejection()
{
    auto raw = payload(ROM_SIZE_512);
    set_rom("knownrom", raw);
    currprefs.cs_compatible = 1;
    currprefs.cs_ramseyrev = -1;
    currprefs.cs_fatgaryrev = -1;
    currprefs.cs_ide = 0;
    changed_prefs.cs_compatible = 1;
    changed_prefs.cs_ramseyrev = -1;
    changed_prefs.cs_fatgaryrev = -1;
    changed_prefs.cs_ide = 0;
    for (unsigned flag : {1u, 2u, 3u, 4u, 8u})
        expect_load("knownrom", P6_ROM_UNSUPPORTED, nullptr, false, true, flag);
    assert(currprefs.cs_compatible == 1 && currprefs.cs_ramseyrev == -1 &&
           currprefs.cs_fatgaryrev == -1 && currprefs.cs_ide == 0);
    assert(changed_prefs.cs_compatible == 1 && changed_prefs.cs_ramseyrev == -1 &&
           changed_prefs.cs_fatgaryrev == -1 && changed_prefs.cs_ide == 0);
    expect_load("knownrom", P6_ROM_LOAD_OK, &raw, false, true, 0);
}

int main()
{
    assert(p6_memory_rom_status() == P6_ROM_UNLOADED);
    memory_init();
    assert(p6_memory_rom_status() == P6_ROM_UNLOADED && kickmem_bank.baseaddr);
    accepted_inputs();
    rejected_inputs();
    cpu_metadata_rejection();
    p6_blob_handle handles[P6_BLOB_STREAMS];
    for (int i = 0; i < P6_BLOB_STREAMS; ++i)
        assert(p6_blob_open("knownrom", "rb", &handles[i]) == 0);
    for (auto handle : handles) assert(p6_blob_close(handle) == 0);
    memory_cleanup();
    p6_blob_clear();
    assert(p6_memory_rom_status() == P6_ROM_UNLOADED && !kickmem_bank.baseaddr);
}
