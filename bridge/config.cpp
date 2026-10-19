// Configuration of the machine profiles: validation of p6_config and firmware, and the preset.
//
// The preset is not a copy of upstream defaults. It starts from what WinUAE itself builds for the machine
// (default_prefs, then built_in_prefs, see kMachines: model 0 = A500, OCS Agnus, 0.5M Chip + 0.5M Slow; model 2 = A600, ECS
// Agnus + Denise, 1M Chip, no Slow; both 68000 cycle exact; model 4 = A1200, 68EC020 cycle exact, AGA, 2M Chip, no Slow; T50: the A500 OG, A500+, A1000, A2000 OG and A2000), applies the
// overrides listed in the file of config/profiles/ that kMachines names and finishes with upstream fixup_prefs.
// tests/host/test_*_profile.py compare the profile files with the resulting preferences field by field, so a value that is
// neither inherited nor declared cannot appear silently.
#include "config.h"
#include "error_util.h"

#include "sysconfig.h"
#include "sysdeps.h"
#include "options.h"
#include "memory.h"
#include "disk.h"
#include "custom.h"
#include "audio.h"
#include "inputdevice.h"
#include "gensound.h"
#include "rommgr.h"
#include "rom_status.h"
#include "sounddep/sound.h"
#include "civil_time.h"
#include "video_present.h"
#include "video_geometry.h"
#include "ide_units.h"

#include <stdio.h>
#include <initializer_list>
#include <set>

namespace p6_bridge {

void release_input_device_names()
{
    std::set<TCHAR *> owned;
    for (uae_prefs *prefs : {&currprefs, &changed_prefs}) {
        for (int slot = 0; slot < MAX_INPUT_SETTINGS; ++slot) {
            for (int device = 0; device < MAX_INPUT_DEVICES; ++device) {
                for (uae_input_device *entry : {&prefs->joystick_settings[slot][device], &prefs->mouse_settings[slot][device],
                                                &prefs->keyboard_settings[slot][device]}) {
                    if (entry->name) owned.insert(entry->name);
                    if (entry->configname) owned.insert(entry->configname);
                    entry->name = entry->configname = nullptr;
                }
            }
        }
    }
    for (TCHAR *text : owned)
        xfree(text);
}

const char *const kFirmwareId = "p6-firmware.rom";

namespace {

// The config of the last build_preferences: check_preset (called by the lifecycle right after it) judges the result by its machine and memory.
p6_config preset_config = {};
bool preset_floppy_writes = false;     // P6_CONFIG_FLOPPY_WRITES of that build: the master switch floppy_read_only is its opposite
bool preset_ide = false;               // T56: that build has the IDE port of Gayle

// A Kickstart is 256 or 512 KiB; the 880 KiB "KICK" disk image is the largest accepted container.
constexpr uint64_t kFirmwareMaxBytes = 1760u * 512u;

uint32_t fold_seed(uint64_t seed)
{
    return uint32_t(seed) ^ uint32_t(seed >> 32);
}

// Ports of the fixed A500: port 0 takes the logical mouse and joystick 0, port 1 joystick 1; the bridge decides at
// each unit which of them the host is using (p6_input port type) and leaves the other neutral. Static on purpose:
// changing the port assignment at run time (inputdevice_updateconfig) drops held buttons and the engine key table.
// Joysticks are gamepads so that their second and third buttons are visible on POTGOR; without it they are no-ops.
void pin_input()
{
    for (int p = 0; p < MAX_JPORTS; ++p) {
        currprefs.jports[p].nokeyboardoverride = false;
        currprefs.jports[p].changed = false;
        for (int d = 0; d < MAX_JPORT_DEVS; ++d) {
            jport_dev &jd = currprefs.jports[p].jd[d];
            jd.id = JPORT_NONE;
            jd.mode = JSEM_MODE_DEFAULT;
            jd.submode = 0;
            jd.autofire = 0;
            jd.idc.name[0] = jd.idc.configname[0] = jd.idc.shortid[0] = 0;
        }
    }
    currprefs.jports[0].jd[0].id = JSEM_MICE + 0;
    currprefs.jports[0].jd[1].id = JSEM_JOYS + 0;
    currprefs.jports[0].jd[1].mode = JSEM_MODE_GAMEPAD;
    currprefs.jports[1].jd[0].id = JSEM_JOYS + 1;
    currprefs.jports[1].jd[0].mode = JSEM_MODE_GAMEPAD;
    currprefs.input_mouse_speed = 100;        // exact: no fractional carry between units
    currprefs.input_contact_bounce = 0;       // no random contact bounce
    currprefs.input_tablet = TABLET_OFF;
    currprefs.input_autoswitchleftright = false;
    currprefs.input_keyboard_type = KBTYPE_AMIGA;
    currprefs.input_advancedmultiinput = false;
    currprefs.keyboard_mode = 0;              // the CIA serial keyboard, with the guest's handshake
    currprefs.cs_resetwarning = false;        // Ctrl-Amiga-Amiga resets at once, without the warning handshake
    currprefs.dongle = 0;
}

}  // namespace

namespace {

bool one_of(uint32_t value, std::initializer_list<uint32_t> allowed)
{
    for (uint32_t candidate : allowed)
        if (value == candidate) return true;
    return false;
}

}  // namespace

namespace {

// What each machine is: the upstream preset it comes from (built_in_prefs model and configuration), its own memory, CPU and chipset, and whether Gayle is declared absent.
// `compatible` is the cs_compatible value the finished preset must carry: the chipset table upstream applies for the machine, or 0 where the table is applied once and then cleared (A600 and A1200:
// no Gayle; A1000 and A2000: the reset warning, see build_preferences).
struct MachineInfo {
    uint32_t machine;
    const char *name;
    int model, config;
    Memory memory;
    int cpu;
    int chipset_mask;
    int compatible;
    bool no_gayle;
};

const MachineInfo kMachines[] = {
    {P6_MACHINE_A500, "A500", 0, 0, {512, 512, 0}, 68000, CSMASK_OCS, CP_A500, false},
    {P6_MACHINE_A600, "A600", 2, 0, {1024, 0, 0}, 68000, CSMASK_ECS_AGNUS | CSMASK_ECS_DENISE, 0, true},
    {P6_MACHINE_A1200, "A1200", 4, 0, {2048, 0, 0}, 68020, CSMASK_ECS_AGNUS | CSMASK_ECS_DENISE | CSMASK_AGA, 0, true},
    {P6_MACHINE_A500OG, "A500OG", 0, 3, {512, 0, 0}, 68000, CSMASK_OCS, CP_A500, false},
    {P6_MACHINE_A500PLUS, "A500PLUS", 1, 0, {1024, 0, 0}, 68000, CSMASK_ECS_AGNUS | CSMASK_ECS_DENISE, CP_A500P, false},
    {P6_MACHINE_A1000, "A1000", 3, 0, {512, 0, 0}, 68000, CSMASK_A1000, 0, false},
    {P6_MACHINE_A2000OG, "A2000OG", 0, 0, {512, 512, 0}, 68000, CSMASK_OCS, 0, false},
    {P6_MACHINE_A2000, "A2000", 1, 0, {1024, 0, 0}, 68000, CSMASK_ECS_AGNUS, 0, false},
};

const MachineInfo *find_machine(uint32_t machine)
{
    for (const MachineInfo &m : kMachines)
        if (m.machine == machine) return &m;
    return nullptr;
}

// The caller has validated the machine (validate_config), so an unknown value is the A500.
const MachineInfo &machine_info(uint32_t machine)
{
    const MachineInfo *m = find_machine(machine);
    return m ? *m : kMachines[0];
}

bool is_aga(uint32_t machine) { return (machine_info(machine).chipset_mask & CSMASK_AGA) != 0; }

// The machines that have Gayle (the ones whose preset leaves it out by default): the A600 and the A1200.
bool has_gayle(uint32_t machine) { return machine == P6_MACHINE_A600 || machine == P6_MACHINE_A1200; }

}  // namespace

bool ide_attached(const p6_config &c, uint32_t hard_files)
{
    return c.hdf_controller == P6_HDF_CONTROLLER_IDE && has_gayle(c.machine) && hard_files > 0;
}

bool machine_has_ecs_agnus(uint32_t machine)
{
    return (machine_info(machine).chipset_mask & (CSMASK_ECS_AGNUS | CSMASK_AGA)) != 0;
}

Memory preset_memory(uint32_t machine)
{
    return machine_info(machine).memory;
}

Memory resolve_memory(const p6_config &c)
{
    const Memory preset = preset_memory(c.machine);
    return Memory{c.chip_kib ? c.chip_kib : preset.chip_kib,
                  c.slow_kib == P6_MEMORY_NONE ? 0 : c.slow_kib ? c.slow_kib : preset.slow_kib,
                  c.fast_kib == P6_MEMORY_NONE ? 0 : c.fast_kib};
}

// Bits of the flags word of config_hash (the envelope has no field for the memory): 0 to 7 are p6_config.flags (P6_CONFIG_*, T27 uses bit 0; validate_config refuses any bit that is not a
// defined flag, so the memory can never collide with them), 8 to 11 fast_kib / 1024, 12 to 16 slow_kib / 64, 17 to 22 chip_kib / 64, 31 marks that the memory is not the preset's.
constexpr uint32_t kMemoryIdentityBit = 0x80000000u;

// T54: 23 and 24 carry the FPU (1 = 68881, 2 = 68882), so a config without one hashes as it always did, and the memory bits above never meet them (chip_kib / 64 is at most 32 << 17 = bit 22).
constexpr unsigned kFpuIdentityShift = 23;

// T62: 25 to 27 carry the floppy speed as the step above the real drive's (0 = 100 %, 1 = 200 %, 2 = 400 %, 3 = 800 %, 4 = turbo): 100 % is the zero of every config that predates the option,
// and bit 31 (the memory marker) and bits 23 and 24 (the FPU) stay where they were.
constexpr unsigned kFloppyIdentityShift = 25;

// T72: 28 and 29 carry the resolution off hires (1 = lores, 2 = super-hires) and 30 the single line mode, so the profile's raster hashes as it always did (bit 31 is the memory marker and the
// floppy speed ends at bit 27).
constexpr unsigned kRasterIdentityShift = 28;

uint32_t raster_identity(const p6_config &c)
{
    const int res = engine_resolution(c.video_resolution), vres = engine_line_mode(c.video_line_mode);
    const uint32_t resolution = res == 0 ? 1u : res == 2 ? 2u : 0u;
    return (resolution | (vres == 0 ? 4u : 0u)) << kRasterIdentityShift;
}

int resolve_floppy_speed(const p6_config &c)
{
    switch (c.floppy_speed) {
    case 2: return 200;
    case 3: return 400;
    case 4: return 800;
    case 5: return 0;
    default: return 100;      // 0 (the profile's) and 1 (named)
    }
}

uint32_t resolve_floppy_drives(const p6_config &c)
{
    return c.floppy_drives ? c.floppy_drives : 2;
}

int resolve_cpu(const p6_config &c)
{
    // 0 is the machine's own CPU; any other value is 1 + (model - 68000) / 10 (p6.h).
    return c.cpu_model ? 68000 + 10 * (int(c.cpu_model) - 1) : machine_info(c.machine).cpu;
}

int resolve_cpu_multiplier(const p6_config &c)
{
    // upstream's built_in_prefs gives the 68000 machines 2 << 8 (7.09 MHz) and the 68EC020 of the A1200 4 << 8 (14.19 MHz); CPU_CLOCK_MULTIPLIER of the config replaces it
    return c.cpu_multiplier ? int(c.cpu_multiplier) : machine_info(c.machine).cpu == 68020 ? 4 : 2;
}

bool cpu_identity(const p6_config &c, uint8_t out[2])
{
    // The resolved model as 0 = 68000, 1 = 68010, 2 = 68020 and the resolved multiplier of the clock (0 while it is the machine's own): two spellings of the same machine (0 and the machine's
    // own number) hash alike.
    out[0] = uint8_t((resolve_cpu(c) - 68000) / 10);
    out[1] = resolve_cpu_multiplier(c) == (machine_info(c.machine).cpu == 68020 ? 4 : 2) ? 0 : uint8_t(resolve_cpu_multiplier(c));
    return resolve_cpu(c) != machine_info(c.machine).cpu || out[1] != 0;
}

Audio resolve_audio(const p6_config &c)
{
    // 0 is the profile's own setting; any other value is upstream's + 1 (p6.h). The type 'auto' is the model of the machine's Paula output stage (A1200: its AGA filter).
    const int own_type = is_aga(c.machine) ? FILTER_SOUND_TYPE_A1200 : FILTER_SOUND_TYPE_A500;
    return Audio{c.sound_separation ? int(c.sound_separation) - 1 : kProfileSeparation,
                 c.sound_interpolation ? int(c.sound_interpolation) - 1 : kProfileInterpolation,
                 c.sound_filter ? int(c.sound_filter) - 1 : kProfileFilter,
                 c.sound_filter_type == 1 ? FILTER_SOUND_TYPE_A500 : c.sound_filter_type == 2 ? FILTER_SOUND_TYPE_A1200 : own_type};
}

bool audio_is_profile(const p6_config &c)
{
    const Audio a = resolve_audio(c);
    const int own_type = is_aga(c.machine) ? FILTER_SOUND_TYPE_A1200 : FILTER_SOUND_TYPE_A500;
    // The model of the filter is only a difference while a filter filters.
    return a.separation == kProfileSeparation && a.interpolation == kProfileInterpolation && a.filter == kProfileFilter && (a.filter == FILTER_SOUND_OFF || a.filter_type == own_type);
}

bool audio_identity(const p6_config &c, uint8_t out[4])
{
    if (audio_is_profile(c)) return false;
    // The resolved settings, so that two spellings of one machine (0 and the profile's value, auto and the machine's model, any model with the filter off) hash alike.
    const Audio a = resolve_audio(c);
    out[0] = uint8_t(a.separation);
    out[1] = uint8_t(a.interpolation);
    out[2] = uint8_t(a.filter);
    out[3] = a.filter == FILTER_SOUND_OFF ? 0 : uint8_t(a.filter_type);
    return true;
}

uint32_t identity_flags(const p6_config &c)
{
    const uint32_t fpu = (c.fpu_model == 68881 ? 1u : c.fpu_model == 68882 ? 2u : 0u) << kFpuIdentityShift;
    const uint32_t floppy = (c.floppy_speed >= 2 ? uint32_t(c.floppy_speed) - 1u : 0u) << kFloppyIdentityShift;
    const uint32_t raster = raster_identity(c);
    if (memory_is_preset(c)) return c.flags | fpu | floppy | raster;
    const Memory m = resolve_memory(c);
    return c.flags | fpu | floppy | raster | kMemoryIdentityBit | (m.chip_kib / 64) << 17 | (m.slow_kib / 64) << 12 | (m.fast_kib / 1024) << 8;
}

bool memory_is_preset(const p6_config &c)
{
    const Memory preset = preset_memory(c.machine), now = resolve_memory(c);
    return now.chip_kib == preset.chip_kib && now.slow_kib == preset.slow_kib && now.fast_kib == preset.fast_kib;
}

p6_result validate_memory(const p6_config &c, p6_error *error)
{
    // What the engine allocates and maps without a quirk: Chip RAM sizes of 1.5 MiB (allocated as 2 MiB with the upper half out of reach) are not offered.
    // The OCS Agnus of the A500 and A2000 OG has 1 MiB as the 8371 with the jumper, anything above it needs ECS. AGA maps at most 1.5 MiB of Slow RAM (memory_reset).
    const MachineInfo &m = machine_info(c.machine);
    const bool ecs = (m.chipset_mask & (CSMASK_ECS_AGNUS | CSMASK_AGA)) != 0, aga = is_aga(c.machine);
    if (c.machine == P6_MACHINE_A1000) {
        // The 8361 Agnus of the A1000 addresses 512 KiB and the machine has no expansion the profile could name: the memory is the preset's.
        if ((c.chip_kib && c.chip_kib != 512) || (c.slow_kib && c.slow_kib != P6_MEMORY_NONE) || (c.fast_kib && c.fast_kib != P6_MEMORY_NONE))
            return p6_fail(error, P6_ERROR_UNSUPPORTED, "the A1000 has the memory of its preset: 512 KiB Chip, no Slow, no Fast");
        return P6_OK;
    }
    if (c.chip_kib && !one_of(c.chip_kib, {512, 1024}) && !(ecs && c.chip_kib == 2048))
        return p6_fail(error, P6_ERROR_UNSUPPORTED, "chip memory size is not supported for this machine");
    if (c.slow_kib && c.slow_kib != P6_MEMORY_NONE && !one_of(c.slow_kib, {512, 1024, 1536}) && !(!aga && c.slow_kib == 1792))
        return p6_fail(error, P6_ERROR_UNSUPPORTED, "slow memory size is not supported for this machine");
    if (c.fast_kib && c.fast_kib != P6_MEMORY_NONE && !one_of(c.fast_kib, {1024, 2048, 4096, 8192}))
        return p6_fail(error, P6_ERROR_UNSUPPORTED, "fast memory size is not supported");
    return P6_OK;
}

p6_result validate_config(const p6_config &c, p6_error *error)
{
    if (c.abi_version != P6_ABI_VERSION || c.struct_size != sizeof c)
        return p6_fail(error, P6_ERROR_BAD_ARGUMENT, "config has the wrong ABI version or size");
    if (!find_machine(c.machine))
        return p6_fail(error, P6_ERROR_UNSUPPORTED, "only the A500, A500 OG, A500+, A600, A1000, A1200, A2000 OG and A2000 are supported");
    if (c.video_standard != P6_VIDEO_PAL && c.video_standard != P6_VIDEO_NTSC)
        return p6_fail(error, P6_ERROR_BAD_ARGUMENT, "video standard must be PAL or NTSC");
    if (c.audio_rate_hz != 44100 && c.audio_rate_hz != 48000)
        return p6_fail(error, P6_ERROR_UNSUPPORTED, "audio rate must be 44100 or 48000 Hz");
    if ((c.flags & ~P6_CONFIG_FLOPPY_WRITES) != 0)
        return p6_fail(error, P6_ERROR_BAD_ARGUMENT, "unknown config flags");
    // The FPU (T54) is the coprocessor of a 68020: the 68000 machines have no coprocessor interface, and the internal FPU of the 68030/68040 comes with those CPUs (T55).
    if (c.fpu_model != 0 && c.fpu_model != 68881 && c.fpu_model != 68882)
        return p6_fail(error, P6_ERROR_UNSUPPORTED, "fpu model must be 0 (none), 68881 or 68882");
    if (c.fpu_model != 0 && c.machine != P6_MACHINE_A1200)
        return p6_fail(error, P6_ERROR_UNSUPPORTED, "an FPU needs a 68020: only the A1200 can have a 68881 or 68882");
    // The CPU (T52): the machines with a 68000 take a 68010 in its socket; the A1200's 68EC020 is soldered and it is the only CPU its AGA preset, its Kickstart 3.1 and its 24-bit map are made for.
    if (c.cpu_model > 3)
        return p6_fail(error, P6_ERROR_UNSUPPORTED, "cpu model must be 0 (the machine's own), 1 (68000), 2 (68010) or 3 (68020)");
    if (c.cpu_model != 0 && resolve_cpu(c) != machine_info(c.machine).cpu) {
        if (machine_info(c.machine).cpu == 68020)
            return p6_fail(error, P6_ERROR_UNSUPPORTED, "the A1200 has a soldered 68EC020: no other CPU is offered for it");
        if (resolve_cpu(c) != 68010)
            return p6_fail(error, P6_ERROR_UNSUPPORTED, "the machines with a 68000 take a 68000 or a 68010; a 68020 is the A1200's");
    }
    if (const p6_result memory = validate_memory(c, error))
        return memory;
    if (c.cpu_compatibility > P6_CPU_NORMAL)
        return p6_fail(error, P6_ERROR_UNSUPPORTED, "cpu compatibility must be exact, compatible or normal");
    for (uint8_t byte : c.reserved7)
        if (byte) return p6_fail(error, P6_ERROR_BAD_ARGUMENT, "reserved config fields are not zero");
    // The controller of the hard files (T56): the IDE port is Gayle's, and only the A600 and the A1200 have Gayle.
    if (c.hdf_controller > P6_HDF_CONTROLLER_IDE)
        return p6_fail(error, P6_ERROR_UNSUPPORTED, "the controller of the hard files must be 0 (UAE) or 1 (IDE)");
    if (c.hdf_controller == P6_HDF_CONTROLLER_IDE && !has_gayle(c.machine))
        return p6_fail(error, P6_ERROR_UNSUPPORTED, "an IDE port is Gayle's: only the A600 and the A1200 have one");
    if (!one_of(c.cpu_multiplier, {0, 1, 2, 4, 8, 10, 12, 16}))
        return p6_fail(error, P6_ERROR_UNSUPPORTED, "cpu multiplier must be 0 (the machine's own), 1, 2, 4, 8, 10, 12 or 16");
    if (!resolution_option_valid(c.video_resolution))
        return p6_fail(error, P6_ERROR_UNSUPPORTED, "video resolution must be 0 (the profile's), 1 (lores), 2 (hires) or 3 (super-hires)");
    if (!line_mode_option_valid(c.video_line_mode))
        return p6_fail(error, P6_ERROR_UNSUPPORTED, "video line mode must be 0 (the profile's), 1 (single) or 2 (double)");
    if (c.floppy_speed > 5)
        return p6_fail(error, P6_ERROR_UNSUPPORTED, "floppy speed must be 0 (the profile's), 1 (100 %), 2 (200 %), 3 (400 %), 4 (800 %) or 5 (turbo)");
    if (c.floppy_drives > 4)
        return p6_fail(error, P6_ERROR_UNSUPPORTED, "floppy drives must be 0 (the two-drive profile) or 1 to 4");
    if (c.sound_separation > 11)
        return p6_fail(error, P6_ERROR_UNSUPPORTED, "stereo separation must be 0 (the profile's) or 1 to 11 (0 to 100 %)");
    if (c.sound_interpolation == 3)
        return p6_fail(error, P6_ERROR_UNSUPPORTED, "sinc interpolation is not supported: its queue is not part of the native state (none, anti, rh and crux are)");
    if (c.sound_interpolation > 5)
        return p6_fail(error, P6_ERROR_UNSUPPORTED, "interpolation must be 0 (the profile's), 1 (none), 2 (anti), 4 (rh) or 5 (crux)");
    if (c.sound_filter > 3)
        return p6_fail(error, P6_ERROR_UNSUPPORTED, "audio filter must be 0 (the profile's), 1 (off), 2 (emulated) or 3 (on)");
    if (c.sound_filter_type > 2)
        return p6_fail(error, P6_ERROR_UNSUPPORTED, "audio filter type must be 0 (auto), 1 (A500) or 2 (A1200)");
    if (c.rtc_epoch_seconds < P6_CIVIL_MIN_SECONDS || c.rtc_epoch_seconds > P6_CIVIL_MAX_SECONDS)
        return p6_fail(error, P6_ERROR_BAD_ARGUMENT, "the clock epoch is outside years 1..9999");
    return P6_OK;
}

p6_result validate_firmware(const p6_buffer &firmware, p6_error *error)
{
    if (!firmware.data || firmware.bytes == 0)
        return p6_fail(error, P6_ERROR_NEED_FIRMWARE, "no firmware was supplied");
    if (firmware.bytes > kFirmwareMaxBytes)
        return p6_fail(error, P6_ERROR_WRONG_MEDIA, "firmware is larger than any Kickstart image");
    // Amiga Forever's encrypted ROMs (Cloanto AMIROMTYPE1) are decrypted by the adapter, with the rom.key of the person, before p6_load: the bridge has no key and the firmware identity is the SHA-256 of the
    // decrypted image. Saying so is clearer than "not a Kickstart".
    if (firmware.bytes > 11 && memcmp(firmware.data, "AMIROMTYPE1", 11) == 0)
        return p6_fail(error, P6_ERROR_WRONG_MEDIA, "firmware is an encrypted Amiga Forever ROM (AMIROMTYPE1): decrypt it with its rom.key before p6_load");
    return P6_OK;
}

void build_preferences(const p6_config &c, bool ide)
{
    default_prefs(&currprefs, true, 0);
    // The preset names its Kickstart by id through the ROM list, which has no entries here; the
    // firmware is the buffer given to p6_load, so the result of configure_rom() is not needed.
    preset_config = c;
    preset_floppy_writes = (c.flags & P6_CONFIG_FLOPPY_WRITES) != 0;
    preset_ide = ide;
    p6_gayle_units_clear();
    const MachineInfo &machine = machine_info(c.machine);
    // The chipset tables read the video standard (the mains-driven CIA TOD of the A1000 and the A2000 counts 50 or 60 Hz): WinUAE has it chosen before the model.
    currprefs.ntscmode = c.video_standard == P6_VIDEO_NTSC;
    built_in_prefs(&currprefs, machine.model, machine.config, 0, 0);
    if (c.machine == P6_MACHINE_A2000OG || c.machine == P6_MACHINE_A2000) {
        // PUAE5's A2000 is the A500 (OG: OCS, 0.5M Chip + 0.5M Slow) or the A500+ (ECS Agnus with the OCS Denise, 1M Chip) with `chipset_compatible = A2000`, which in WinUAE is
        // cs_compatible = CP_A2000 followed by built_in_chipset_prefs: clock, CIA TOD from the mains, CIA TOD bug, unmapped space.
        if (c.machine == P6_MACHINE_A2000) currprefs.chipset_mask = CSMASK_ECS_AGNUS;
        currprefs.cs_compatible = CP_A2000;
        built_in_chipset_prefs(&currprefs);
    }
    if (currprefs.floppyslots[1].dfxtype == DRV_NONE)
        currprefs.floppyslots[1].dfxtype = DRV_35_DD;     // upstream's A500 OG and A1000 have one drive; the ABI gives every machine DF0 and DF1 (the external drive port)
    if (resolve_floppy_drives(c) != 2) {
        currprefs.nr_floppies = int(resolve_floppy_drives(c));
        for (unsigned i = 0; i < 4; ++i) currprefs.floppyslots[i].dfxtype = i < resolve_floppy_drives(c) ? DRV_35_DD : DRV_NONE;
    }
    if (machine.no_gayle) {
        // The A600 or A1200 table (CP_A600, CP_A1200) has just been applied once. Gayle, its IDE and its PCMCIA slot are not compiled (P6_PORTABLE),
        // so they are switched off here instead of being declared and missing.
        currprefs.cs_ide = 0;
        currprefs.cs_pcmcia = 0;
        currprefs.cs_ciaoverlay = 1;     // without Gayle the CIA drives the ROM overlay (the state bridge only knows this mode)
        currprefs.cs_1mchipjumper = true;   // what fixup_prefs sets for 1 MiB Chip; upstream's trailing CP_A600 table would clear it again
        // T56: with a hard file on the IDE the machine has Gayle's IDE port (cs_ide, as the A600 and the A1200 tables have it) and still no PCMCIA slot; the CIA keeps the ROM overlay
        // (the real machines leave it to Gayle's reset, which the state bridge does not know). Nothing attached: nothing of Gayle, as before.
        if (ide)
            currprefs.cs_ide = IDE_A600A1200;
        else
            clear_device_rom(&currprefs, ROMTYPE_MB_IDE, 0, true);      // upstream's table lists Gayle's IDE among the boards of the motherboard (currprefs.expansionboard): not without a hard file
    }
    if (machine.compatible == 0) {
        // The chipset table has been applied once, by built_in_prefs (A600, A1200) or just above (A2000, whose base preset is the A500). With cs_compatible = 0 the fixup_prefs calls of the
        // lifecycle leave the rest of the table (ECS mirrors, CIA type, E clock phase, clock, mains TOD) as it is; with the table's value every one of them applies it again, and the tables
        // do what the profile does not have: Gayle, IDE and PCMCIA on the A600 and the A1200; the reset warning handshake on the A1000 and the A2000 (cs_resetwarning, which pin_input clears
        // and the input boundary of the state refuses).
        currprefs.cs_compatible = 0;
    }
    if (machine.cpu == 68020) {
        // built_in_prefs sets waiting_blits = 1 for every CPU from the 68020 up: the blitter wait then runs inside the write that starts
        // the blit, as an emulation shortcut that no hardware has. The CPU runs its own polling loops instead, as on the 68000 machines.
        currprefs.waiting_blits = 0;
    }
    // The FPU (T54). fpu_mode 1 is upstream's "softfloat" setting; the portable build has no other backend (patch 0140) and fpu_strict/fpu_no_unimplemented keep
    // the 68881/68882 behaviour that built_in_prefs leaves (the strict and no-unimplemented modes are 68040/68060 matters).
    currprefs.fpu_model = int(c.fpu_model);
    currprefs.fpu_mode = c.fpu_model ? 1 : 0;
    // The CPU (T52): the machine's own unless the config names a 68010 for a 68000 machine. Everything else of the preset (cycle exact, 24-bit space, no MMU) is the 68000's and the 68010 shares it;
    // build_cpufunctbl then takes op_smalltbl_13, the 68010 cycle-exact table, and m68k_run_1_ce runs it (patch 0310).
    currprefs.cpu_model = resolve_cpu(c);
    currprefs.cpu_cycle_exact = c.cpu_compatibility == P6_CPU_EXACT;
    currprefs.cpu_memory_cycle_exact = currprefs.cpu_cycle_exact;
    currprefs.cpu_compatible = c.cpu_compatibility != P6_CPU_NORMAL;
    // fixup_cpu requires memory CE for a CE blitter; the non-exact levels use its normal emulated event scheduling.
    currprefs.blitter_cycle_exact = currprefs.cpu_cycle_exact;
    currprefs.m68k_speed = 0;          // instruction cycles, never fastest-possible/host-time execution
    // The clock of the CPU (T52): upstream's cpu_clock_multiplier is the multiplier << 8, and the machine's own is what built_in_prefs has just put there. cpucycleunit is derived from it by the
    // reset head (prefs_changed_cpu), never from a host clock.
    currprefs.cpu_clock_multiplier = resolve_cpu_multiplier(c) << 8;
    const Memory memory = resolve_memory(c);
    currprefs.chipmem.size = memory.chip_kib * 1024;
    currprefs.bogomem.size = memory.slow_kib * 1024;
    currprefs.fastmem[0].size = memory.fast_kib * 1024;     // one Zorro II autoconfig board (patch 0110); fixup_prefs keeps board 0 only
    currprefs.ntscmode = c.video_standard == P6_VIDEO_NTSC;
    currprefs.seed = fold_seed(c.random_seed);
    currprefs.sound_freq = int(c.audio_rate_hz);
    currprefs.sound_stereo = SND_STEREO;
    currprefs.produce_sound = 3;
    currprefs.sound_auto = 0;
    // Paula's output (T73): the profile's own setting unless the config names one (resolve_audio); validate_config has refused what the engine cannot restore.
    const Audio audio = resolve_audio(c);
    currprefs.sound_stereo_separation = audio.separation;   // profile 7 (upstream default): each side keeps 26/32 of its channels and takes 6/32 of the other
    currprefs.sound_interpol = audio.interpolation;
    currprefs.sound_filter = audio.filter;
    currprefs.sound_filter_type = audio.filter_type;
    currprefs.sound_mixed_stereo_delay = 0;   // no delayed crossfeed
    currprefs.sound_volume_paula = 0;         // Paula volume at full scale
    currprefs.sound_volume_master = 0;
    currprefs.start_gui = false;
    // Video: the canonical raster (docs/adr/0006-video-output.md) is decided by these preferences, not by
    // what the guest programs. Hires, line doubled by default (T72: the options pick lores or super-hires and single lines), overscan mode 3, no centering: 756 x 574 PAL / 484 NTSC.
    currprefs.gfx_autoresolution = 0;        // the presentation provider serves one fixed raster
    currprefs.gfx_autoresolution_vga = false;
    currprefs.gfx_framerate = 1;             // every video unit is drawn
    currprefs.gfx_resolution = engine_resolution(c.video_resolution);      // T72: the profile's raster is hires and double lines; the options choose another
    currprefs.gfx_vresolution = engine_line_mode(c.video_line_mode);
    currprefs.gfx_iscanlines = 1;            // interlaced fields are woven into one buffer (single lines: a field per unit, nothing to weave)
    currprefs.gfx_pscanlines = 0;
    currprefs.gfx_scandoubler = false;
    currprefs.gfx_lores_mode = 0;
    currprefs.gfx_overscanmode = OVERSCANMODE_OVERSCAN;
    currprefs.gfx_xcenter = currprefs.gfx_ycenter = 0;
    currprefs.gfx_extrawidth = currprefs.gfx_extraheight = 0;
    currprefs.gfx_threebitcolors = 0;
    currprefs.gfx_blackerthanblack = false;
    currprefs.display_calibration = false;
    currprefs.gfx_monitorblankdelay = 0;
    currprefs.lightpen_crosshair = false;    // no drawn cursor: upstream default is on
    currprefs.leds_on_screen = 0;
    currprefs.refresh_indicator = false;
    // Fast-line caching is separate from CPU accuracy. Keep the full chipset path until its additional caches have native-state coverage.
    currprefs.cs_optimizations = c.cpu_compatibility == P6_CPU_EXACT ? DISPLAY_OPTIMIZATIONS_FULL : DISPLAY_OPTIMIZATIONS_NONE;
    currprefs.cs_hvcsync = 0;
    pin_input();
    // Media are identified by content id and arrive through p6_load: upstream's default names df0.adf..df3.adf must never
    // be looked up, and every disk is write protected (docs/adr/0008-media-identity.md). The per-slot forcedwriteprotect
    // flag is not pinned: the reset head re-inserts disks with it cleared, so the master switch carries the guarantee.
    for (int i = 0; i < 4; ++i)
        currprefs.floppyslots[i].df[0] = 0;
    currprefs.floppy_speed = resolve_floppy_speed(c);       // T62: 100 is the real drive, 0 the turbo mode; no wall clock is read either way (the DMA counts emulated colour clocks)
    currprefs.floppy_read_only = !preset_floppy_writes;      // every disk is write protected unless the session reserved a write overlay (T27)
    currprefs.reset_delay = 0;        // config-invariants.json: the delayed-reset wait cannot poll
    currprefs.crash_auto_reset = 0;   // config-invariants.json: a halted CPU stays in the polling wait
    _tcscpy(currprefs.romfile, kFirmwareId);
    fixup_prefs(&currprefs, true);
}

p6_result check_preset(p6_error *error)
{
    const uae_prefs &p = currprefs;
    const MachineInfo &machine = machine_info(preset_config.machine);
    const bool no_gayle = machine.no_gayle;
    const Memory memory = resolve_memory(preset_config);
    const char *drifted = nullptr;
    if (p.cpu_model != resolve_cpu(preset_config) || p.cpu_clock_multiplier != (resolve_cpu_multiplier(preset_config) << 8) || p.cpu_cycle_exact != (preset_config.cpu_compatibility == P6_CPU_EXACT) || p.cpu_memory_cycle_exact != p.cpu_cycle_exact ||
        p.cpu_compatible != (preset_config.cpu_compatibility != P6_CPU_NORMAL) || p.m68k_speed || p.fpu_model != int(preset_config.fpu_model) ||
        p.fpu_mode != (preset_config.fpu_model ? 1 : 0) || p.fpu_strict || p.fpu_no_unimplemented || p.mmu_model ||
        !p.address_space_24 || (machine.cpu == 68020 && p.waiting_blits)) drifted = "cpu";
    else if (p.chipset_mask != machine.chipset_mask || p.blitter_cycle_exact != p.cpu_cycle_exact) drifted = "chipset";
    else if (p.chipmem.size != memory.chip_kib * 1024 || p.bogomem.size != memory.slow_kib * 1024 || p.fastmem[0].size != memory.fast_kib * 1024 ||
             p.fastmem[1].size || p.fastmem[2].size || p.fastmem[3].size || p.z3fastmem[0].size || p.mbresmem_low.size || p.mbresmem_high.size) drifted = "memory";
    else if (p.cs_compatible != machine.compatible) drifted = "chipset table";
    else if (no_gayle && (p.cs_ide != (preset_ide ? IDE_A600A1200 : 0) || p.cs_pcmcia || p.cs_compatible || !p.cs_ciaoverlay)) drifted = "gayle";
    else if (!no_gayle && p.cs_ide) drifted = "gayle";
    else if (p.reset_delay || p.crash_auto_reset) drifted = "reset";
    else if (p.uaeboard || p.uaeboard_nodiag || p.boot_rom || p.uae_hide || p.uae_hide_autoconfig || p.scsi || p.uaescsidevmode || p.socket_emu ||
             p.uaeserial || p.sana2 || p.win32_automount_drives || p.win32_automount_removable || p.win32_automount_cddrives || p.win32_automount_netdrives ||
             p.harddrive_read_only) drifted = "uae controller";    // the hard files are the only thing the UAE controller is configured for, and it is the default one
    else if (p.jports[0].jd[0].id != JSEM_MICE || p.jports[0].jd[1].id != JSEM_JOYS || p.jports[1].jd[0].id != JSEM_JOYS + 1 ||
             p.input_mouse_speed != 100 || p.keyboard_mode || p.cs_resetwarning || p.input_contact_bounce) drifted = "input";
    else if (p.floppyslots[0].df[0] || p.floppyslots[1].df[0] || p.floppyslots[2].df[0] || p.floppyslots[3].df[0] ||
             p.floppy_read_only == preset_floppy_writes || p.floppy_speed != resolve_floppy_speed(preset_config)) drifted = "floppy";
    else if (p.gfx_resolution != engine_resolution(preset_config.video_resolution) || p.gfx_vresolution != engine_line_mode(preset_config.video_line_mode) || p.gfx_iscanlines != 1 ||
             p.gfx_pscanlines || p.gfx_scandoubler || p.gfx_lores_mode || p.gfx_overscanmode != OVERSCANMODE_OVERSCAN ||
             p.gfx_xcenter || p.gfx_ycenter || p.gfx_extrawidth || p.gfx_extraheight || p.gfx_threebitcolors ||
             p.gfx_blackerthanblack || p.display_calibration || p.gfx_monitorblankdelay || p.lightpen_crosshair ||
             p.leds_on_screen || p.cs_optimizations != (preset_config.cpu_compatibility == P6_CPU_EXACT ? DISPLAY_OPTIMIZATIONS_FULL : DISPLAY_OPTIMIZATIONS_NONE) || p.cs_hvcsync) drifted = "video";
    if (!drifted && resolve_floppy_drives(preset_config) != 2)
        for (unsigned i = 0; i < 4; ++i)
            if (p.floppyslots[i].dfxtype != (i < resolve_floppy_drives(preset_config) ? DRV_35_DD : DRV_NONE)) drifted = "floppy drives";
    if (drifted) {
        char text[64];
        snprintf(text, sizeof text, "the %s preset drifted from the profile (%s)", machine.name, drifted);
        return p6_fail(error, P6_ERROR_INTERNAL, text);
    }
    if (!p6_sound_validate_config(p))
        return p6_fail(error, P6_ERROR_UNSUPPORTED, "audio settings are not supported");
    if (const int video = p6_video_validate_config(&p)) {
        char text[64];
        snprintf(text, sizeof text, "video settings are not supported (code %d)", video);
        return p6_fail(error, P6_ERROR_UNSUPPORTED, text);
    }
    return P6_OK;
}

// A Kickstart the engine knows by its content (never by its name) that needs more than the preset's CPU (patch 0290): the message says which ROM it is, what it needs and what this machine
// has. P6_OK when the refusal was something else.
static p6_result refused_by_cpu(p6_error *error)
{
    const char *name = nullptr;
    const int needs = p6_memory_rom_refusal(&name);
    if (!needs || !name) return P6_OK;
    const char *requirement = (needs & 8) ? "a 68030 or later" : ((needs & 3) == 3) ? "a 68030" : (needs & 4) ? "the A4000 chipset (Ramsey, Gary, IDE)"
        : ((needs & 3) == 1) ? "a 68EC020 or later" : "a 68020 or later with a 32-bit bus";
    const MachineInfo &machine = machine_info(preset_config.machine);
    char text[P6_ERROR_TEXT_CAPACITY];
    snprintf(text, sizeof text, "the ROM \"%.100s\" needs %s but the %s has a %s: it is the firmware of another machine", name, requirement, machine.name,
             resolve_cpu(preset_config) == 68020 ? "68EC020" : resolve_cpu(preset_config) == 68010 ? "68010" : "68000");
    return p6_fail(error, P6_ERROR_WRONG_MEDIA, text);
}

p6_result check_loaded_firmware(p6_error *error)
{
    switch (p6_memory_rom_status()) {
    case P6_ROM_LOAD_OK:
        break;
    case P6_ROM_UNLOADED:
    case P6_ROM_OPEN_ERROR:
        return p6_fail(error, P6_ERROR_NEED_FIRMWARE, "firmware was not found");
    case P6_ROM_UNSUPPORTED:
        if (const p6_result refused = refused_by_cpu(error)) return refused;
        return p6_fail(error, P6_ERROR_WRONG_MEDIA, "firmware is not an accepted Kickstart image");
    case P6_ROM_READ_ERROR:
        return p6_fail(error, P6_ERROR_WRONG_MEDIA, "firmware is not an accepted Kickstart image");
    case P6_ROM_NO_MEMORY:
        return p6_fail(error, P6_ERROR_CAPACITY, "not enough memory for the firmware");
    default:
        return p6_fail(error, P6_ERROR_INTERNAL, "firmware status is inconsistent");
    }
    // The loader accepts a bad checksum with a notice only; the decoded image must be intact.
    if (!kickstart_checksum(kickmem_bank.baseaddr, kickmem_bank.reserved_size))
        return p6_fail(error, P6_ERROR_WRONG_MEDIA, "firmware checksum is invalid");
    return P6_OK;
}

}  // namespace p6_bridge
