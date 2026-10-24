// Production function bodies, real blob/zfile/diagnostics; explicit observed
// boundaries below are test fixtures, not production device implementations.
#include "sysconfig.h"
#include "sysdeps.h"
#include "options.h"
#include "uae.h"
#include "memory.h"
#include "rommgr.h"
#include "zfile.h"
#include "gui.h"
#include "debugmem.h"
#include "cpuboard.h"
#include "devices.h"
#include "ar.h"
#include "rom_status.h"
#include "blob_io.h"
#include "diagnostics.h"
#include <cassert>
#include <cstdlib>
#include <initializer_list>

uae_prefs currprefs{}, changed_prefs{};
bool cloanto_rom, kickstart_rom;
uae_u16 kickstart_version;
addrbank kickmem_bank{}, extendedkickmem_bank{}, extendedkickmem2a_bank{}, extendedkickmem2b_bank{};
addrbank chipmem_bank{}, bogomem_bank{}, mem25bit_bank{}, a3000lmem_bank{}, a3000hmem_bank{};
addrbank kickram_bank{}, custmem1_bank{}, custmem2_bank{}, fakeuaebootrom_bank{};
static bool roms_modified, need_hardreset, fail_allocation;
static uae_u8 *a1000_bootrom;
static int a1000_kickstart_mode, extendedkickmem_type;
static uaecptr a1000_kickstart_size;
static const uae_char *kickstring = "exec.library";
static unsigned patches, protections, opens, keys, notices, errors, lifecycle;
static unsigned crc_notices;
enum CartridgeCall { CART_UNLOAD, CART_LOAD, CART_INIT, HRTMON_LOAD, CART_CLEANUP };
static CartridgeCall cartridge_calls[32];
static unsigned cartridge_count;
static void note_cartridge(CartridgeCall call)
{
    assert(cartridge_count < 32);
    cartridge_calls[cartridge_count++] = call;
}
static void expect_cartridge(std::initializer_list<CartridgeCall> expected)
{
    assert(cartridge_count == expected.size());
    unsigned i = 0;
    for (auto call : expected) assert(cartridge_calls[i++] == call);
    cartridge_count = 0;
}
// Lifecycle observations only. No cartridge is loaded or reported successful.
int action_replay_unload(int reset)
{ assert(reset == 0); note_cartridge(CART_UNLOAD); return -1; }
int action_replay_load()
{ assert(!currprefs.cartfile[0]); note_cartridge(CART_LOAD); return -1; }
void action_replay_init(int enabled)
{ assert(enabled == 1); note_cartridge(CART_INIT); }
int hrtmon_load() { note_cartridge(HRTMON_LOAD); return -1; }
void action_replay_cleanup() { note_cartridge(CART_CLEANUP); }

// Isolated reader probe; integration includes the real ROM manager below.
#ifndef P6_REAL_ROM_INPUT
zfile *read_rom_name(const TCHAR *name, bool rw)
{
    assert(!rw); ++opens;
    return zfile_fopen(name, "rb", ZFD_NORMAL);
}
#endif
void addkeydir(const TCHAR *) { std::abort(); } // Implicit key discovery is excluded.
zfile *read_executable_rom(zfile *, int, int) { std::abort(); }
int decode_rom(uae_u8 *, int, int, int) { std::abort(); }
bool is_board_enabled(uae_prefs *, int, int) { std::abort(); }
void kickstart_fix_checksum(uae_u8 *, int)
{ fprintf(stderr, "Unexpected repair of ROM payload\n"); std::abort(); }
#include "rom_checksum_under_test.h"
#ifdef P6_REAL_ROM_INPUT
static romdata roms[1] = {}; // No known SHA fixtures; metadata is observed below.
#include "rom_input_under_test.h"
#endif
static romdata known_rom{};
static bool use_known_rom;
romdata *getromdatabyzfile(zfile *) { std::abort(); }
romdata *getromdatabydata(uae_u8 *data, int size)
{
    assert(data == kickmem_bank.baseaddr && (size == ROM_SIZE_512 || size == ROM_SIZE_256));
    return use_known_rom ? &known_rom : nullptr; // Explicit metadata boundary.
}
void uae_restart(uae_prefs *, int, const TCHAR *) { std::abort(); }
static void a1000_handle_kickstart(int mode) { assert(mode == 0 && !a1000_bootrom); }
static bool load_extendedkickstart(const TCHAR *name, int) { assert(!name[0]); return false; }
static void patch_kick() { ++patches; }
// Host ROM page protection only serves the JIT (patch 0051): loading must never call it.
void protect_roms(bool) { ++protections; }
void notify_user(int message)
{
    if (message == NUMSG_KSROMCRCERROR) ++crc_notices;
    else { assert(message == NUMSG_NOROM); ++notices; }
}
void error_log(const TCHAR *format, ...) { assert(!strcmp(format, "Failed to open '%s'\n")); ++errors; }
static void init_mem_banks() { ++lifecycle; }
void virtualdevice_init() { ++lifecycle; }
bool mapped_malloc(addrbank *bank)
{
    assert(bank == &kickmem_bank && bank->reserved_size == ROM_SIZE_512);
    bank->baseaddr = fail_allocation ? nullptr : static_cast<uae_u8 *>(malloc(bank->reserved_size));
    bank->allocated_size = bank->baseaddr ? bank->reserved_size : 0;
    return bank->baseaddr != nullptr;
}
void mapped_free(addrbank *bank)
{
    free(bank->baseaddr); bank->baseaddr = nullptr; bank->allocated_size = 0;
}

#include "memory_rom_status_under_test.h"

static void restore(const char *name, int expected, bool bad_checksum = false)
{
    strcpy(changed_prefs.romfile, name);
    const unsigned before = patches;
    const unsigned before_cartridge = cartridge_count;
    protections = notices = errors = opens = keys = crc_notices = 0;
    set_roms_modified(); reload_roms();
    assert(p6_memory_rom_status() == expected && !roms_modified && need_hardreset);
    assert(protections == 0 && patches == before + (expected == P6_ROM_LOAD_OK));
    assert(notices == (expected != P6_ROM_LOAD_OK) && errors == notices);
    assert(crc_notices == bad_checksum);
    assert(keys == 0 && opens == (expected != P6_ROM_UNSUPPORTED && expected != P6_ROM_NO_MEMORY));
    reload_roms(); // No requested reload must retain the same causal error.
    assert(p6_memory_rom_status() == expected && protections == 0);
    p6_diag_reset(); // Diagnostics are not the owner of load outcome.
    assert(p6_memory_rom_status() == expected);
    assert(cartridge_count == before_cartridge);
}

// Independent 64-bit carry folding builds legal test bytes; these are not a
// bootable Kickstart. No production checksum repair is used by the harness.
static void make_payload(uae_u8 *data, unsigned size)
{
    for (unsigned i = 0; i < size; ++i) data[i] = (i * 37 + i / 251) & 255;
    data[0] = 0x11; data[1] = size == ROM_SIZE_256 ? 0x11 : 0x14;
    data[12] = 0; data[13] = 37;
    for (unsigned i = 0; i < 4; ++i) data[size - 20 + i] = size >> (24 - 8*i);
    memset(data + size - 24, 0, 4);
    uint64_t sum = 0;
    for (unsigned i = 0; i < size; i += 4)
        sum += (uint64_t(data[i]) << 24) | (uint64_t(data[i+1]) << 16) |
               (uint64_t(data[i+2]) << 8) | data[i+3];
    while (sum >> 32) sum = (sum & 0xffffffff) + (sum >> 32);
    const uint32_t check = ~uint32_t(sum);
    for (unsigned i = 0; i < 4; ++i) data[size - 24 + i] = check >> (24 - 8*i);
}

int main()
{
    static uae_u8 payload[ROM_SIZE_512], half[ROM_SIZE_256], corrupt[ROM_SIZE_512];
    make_payload(payload, sizeof payload); make_payload(half, sizeof half);
    memcpy(corrupt, payload, sizeof payload); corrupt[2000] ^= 1;
    assert(kickstart_checksum(payload, sizeof payload));
    assert(kickstart_checksum(half, sizeof half));
    assert(p6_blob_register("fixture.rom", P6_BLOB_ROM, payload, sizeof payload) == 0);
    assert(p6_blob_register("short.rom", P6_BLOB_ROM, payload, 8) == 0);
    assert(p6_blob_register("half.rom", P6_BLOB_ROM, half, sizeof half) == 0);
    assert(p6_blob_register("corrupt.rom", P6_BLOB_ROM, corrupt, sizeof corrupt) == 0);
    assert(p6_memory_rom_status() == P6_ROM_UNLOADED);
    memory_init();
    expect_cartridge({CART_UNLOAD, CART_LOAD, CART_INIT, HRTMON_LOAD});
    assert(p6_memory_rom_status() == P6_ROM_UNLOADED && lifecycle == 2 && kickmem_bank.baseaddr);
    restore("fixture.rom", P6_ROM_LOAD_OK);
    assert(!memcmp(kickmem_bank.baseaddr, payload, sizeof payload));
    for (unsigned i = 0; i < 80; ++i) { // Also detects leaked streams on error.
        restore("absent.rom", P6_ROM_OPEN_ERROR);
        restore(":AROS", P6_ROM_UNSUPPORTED);
        restore("short.rom", P6_ROM_READ_ERROR);
    }
    assert(!memcmp(kickmem_bank.baseaddr, payload, sizeof payload));
    strcpy(currprefs.romfile, "fixture.rom");
    assert(read_kickstart_version(&currprefs) && p6_memory_rom_status() == P6_ROM_READ_ERROR);
    strcpy(currprefs.romfile, "absent.rom");
    assert(!read_kickstart_version(&currprefs) && p6_memory_rom_status() == P6_ROM_READ_ERROR);
    for (unsigned size : {0u, 10u, 11u, 20u, 31u, 32u, 127u,
                          ROM_SIZE_256 - 1u, ROM_SIZE_256 + 1u, ROM_SIZE_512 - 1u}) {
        char name[40]; snprintf(name, sizeof name, "cut-%u.rom", size);
        assert(p6_blob_register(name, P6_BLOB_ROM, payload, size) == 0);
        restore(name, P6_ROM_READ_ERROR);
        strcpy(currprefs.romfile, name);
        for (unsigned i = 0; i < 80; ++i) {
            assert(read_kickstart_version(&currprefs) == (size >= 32));
            assert(p6_memory_rom_status() == P6_ROM_READ_ERROR);
        }
    }
    restore("corrupt.rom", P6_ROM_READ_ERROR, true);
    // Without exec.library, a bad checksum must still fail (legacy bypass).
    memset(corrupt, 0, sizeof corrupt);
    assert(p6_blob_register("zeros.rom", P6_BLOB_ROM, corrupt, sizeof corrupt) == 0);
    restore("zeros.rom", P6_ROM_READ_ERROR, true);
    restore("half.rom", P6_ROM_LOAD_OK);
    assert(kickmem_bank.reserved_size == ROM_SIZE_256 && kickmem_bank.mask == ROM_SIZE_256 - 1);
    assert(!memcmp(kickmem_bank.baseaddr, half, sizeof half));
    assert(!memcmp(kickmem_bank.baseaddr + ROM_SIZE_256, half, sizeof half));
    zfile *f = zfile_fopen("half.rom", "rb", ZFD_NORMAL);
    assert(f && !read_kickstart(f, corrupt, sizeof corrupt, 1, 1));
    zfile_fclose(f); // noalias requires the whole segment, not a half ROM.
    static uae_u8 kickdisk[1760 * 512];
    memcpy(kickdisk, "KICK", 4); memcpy(kickdisk + 512, half, sizeof half);
    assert(p6_blob_register("kickdisk.rom", P6_BLOB_ROM, kickdisk, sizeof kickdisk) == 0);
    restore("kickdisk.rom", P6_ROM_LOAD_OK);
    assert(kickmem_bank.reserved_size == ROM_SIZE_256);
    assert(!memcmp(kickmem_bank.baseaddr, half, sizeof half));
    restore("fixture.rom", P6_ROM_LOAD_OK);
    memory_cleanup();
    expect_cartridge({CART_CLEANUP});
    assert(p6_memory_rom_status() == P6_ROM_UNLOADED && !kickmem_bank.baseaddr && lifecycle == 2);
    fail_allocation = true;
    memory_init();
    expect_cartridge({CART_UNLOAD, CART_LOAD, CART_INIT, HRTMON_LOAD});
    assert(!kickmem_bank.baseaddr && p6_memory_rom_status() == P6_ROM_UNLOADED);
    restore("fixture.rom", P6_ROM_NO_MEMORY);
    fail_allocation = false;
    memory_init();
    expect_cartridge({CART_UNLOAD, CART_LOAD, CART_INIT, HRTMON_LOAD});
    assert(p6_memory_rom_status() == P6_ROM_UNLOADED);
    restore("fixture.rom", P6_ROM_LOAD_OK);
    memory_cleanup(); p6_blob_clear();
    expect_cartridge({CART_CLEANUP});
    assert(p6_memory_rom_status() == P6_ROM_UNLOADED && !kickmem_bank.baseaddr);
    return 0;
}
