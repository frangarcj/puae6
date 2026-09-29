// Configuration of the A500 profile: validation of p6_config and firmware, and the preset.
//
// The preset is not a copy of upstream defaults. It starts from what WinUAE itself builds for an A500
// (default_prefs, then built_in_prefs model 0 / config 0: KS 1.3 era, OCS Agnus, 0.5M Chip + 0.5M Slow,
// 68000 cycle exact), applies the overrides listed in config/profiles/a500-ocs.json and finishes with
// upstream fixup_prefs. tests/host/test_a500_profile.py compares the profile file with the resulting
// preferences field by field, so a value that is neither inherited nor declared cannot appear silently.
#include "config.h"
#include "error_util.h"

#include "sysconfig.h"
#include "sysdeps.h"
#include "options.h"
#include "memory.h"
#include "custom.h"
#include "audio.h"
#include "gensound.h"
#include "rommgr.h"
#include "rom_status.h"
#include "sounddep/sound.h"
#include "civil_time.h"
#include "video_present.h"

#include <stdio.h>

namespace p6_bridge {

const char *const kFirmwareId = "p6-firmware.rom";

namespace {

// A Kickstart is 256 or 512 KiB; the 880 KiB "KICK" disk image is the largest accepted container.
constexpr uint64_t kFirmwareMaxBytes = 1760u * 512u;

uint32_t fold_seed(uint64_t seed)
{
    return uint32_t(seed) ^ uint32_t(seed >> 32);
}

}  // namespace

p6_result validate_config(const p6_config &c, p6_error *error)
{
    if (c.abi_version != P6_ABI_VERSION || c.struct_size != sizeof c)
        return p6_fail(error, P6_ERROR_BAD_ARGUMENT, "config has the wrong ABI version or size");
    if (c.machine != P6_MACHINE_A500)
        return p6_fail(error, P6_ERROR_UNSUPPORTED, "only the A500 is supported");
    if (c.video_standard != P6_VIDEO_PAL && c.video_standard != P6_VIDEO_NTSC)
        return p6_fail(error, P6_ERROR_BAD_ARGUMENT, "video standard must be PAL or NTSC");
    if (c.audio_rate_hz != 44100 && c.audio_rate_hz != 48000)
        return p6_fail(error, P6_ERROR_UNSUPPORTED, "audio rate must be 44100 or 48000 Hz");
    if (c.flags != 0 || c.reserved[0] || c.reserved[1] || c.reserved[2] || c.reserved[3])
        return p6_fail(error, P6_ERROR_BAD_ARGUMENT, "config flags and reserved fields must be zero");
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
    return P6_OK;
}

void build_preferences(const p6_config &c)
{
    default_prefs(&currprefs, true, 0);
    // The preset names its Kickstart by id through the ROM list, which has no entries here; the
    // firmware is the buffer given to p6_load, so the result of configure_rom() is not needed.
    built_in_prefs(&currprefs, 0, 0, 0, 0);
    currprefs.ntscmode = c.video_standard == P6_VIDEO_NTSC;
    currprefs.seed = fold_seed(c.random_seed);
    currprefs.sound_freq = int(c.audio_rate_hz);
    currprefs.sound_stereo = SND_STEREO;
    currprefs.produce_sound = 3;
    currprefs.sound_auto = 0;
    currprefs.start_gui = false;
    currprefs.gfx_autoresolution = 0;        // the presentation provider serves one fixed raster
    currprefs.gfx_autoresolution_vga = false;
    currprefs.reset_delay = 0;        // config-invariants.json: the delayed-reset wait cannot poll
    currprefs.crash_auto_reset = 0;   // config-invariants.json: a halted CPU stays in the polling wait
    _tcscpy(currprefs.romfile, kFirmwareId);
    fixup_prefs(&currprefs, true);
}

p6_result check_preset(p6_error *error)
{
    const uae_prefs &p = currprefs;
    const char *drifted = nullptr;
    if (p.cpu_model != 68000 || !p.cpu_cycle_exact || !p.cpu_memory_cycle_exact) drifted = "cpu";
    else if (p.chipset_mask != CSMASK_OCS || !p.blitter_cycle_exact) drifted = "chipset";
    else if (p.chipmem.size != 0x80000 || p.bogomem.size != 0x80000 || p.fastmem[0].size || p.z3fastmem[0].size ||
             p.mbresmem_low.size || p.mbresmem_high.size) drifted = "memory";
    else if (p.reset_delay || p.crash_auto_reset) drifted = "reset";
    if (drifted) {
        char text[64];
        snprintf(text, sizeof text, "the A500 preset drifted from the profile (%s)", drifted);
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

p6_result check_loaded_firmware(p6_error *error)
{
    switch (p6_memory_rom_status()) {
    case P6_ROM_LOAD_OK:
        break;
    case P6_ROM_UNLOADED:
    case P6_ROM_OPEN_ERROR:
        return p6_fail(error, P6_ERROR_NEED_FIRMWARE, "firmware was not found");
    case P6_ROM_READ_ERROR:
    case P6_ROM_UNSUPPORTED:
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
