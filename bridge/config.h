// The fixed A500, A600 and A1200 configurations: what p6_config may say, what the preset pins, and the
// checks done before the engine is touched. See config/profiles/a500-ocs.json, a600-ecs.json and a1200-aga.json for the field lists.
#ifndef P6_BRIDGE_CONFIG_H
#define P6_BRIDGE_CONFIG_H
#include "p6.h"
#include <stdint.h>

namespace p6_bridge {

// Shared stock CD32 profile; the alias keeps device integration independent of frontend names.
constexpr uint32_t kCd32Machine = P6_MACHINE_CD32;
// Shared stock CDTV profile; the public identifier preserves the existing state identity.
constexpr uint32_t kCdtvMachine = P6_MACHINE_CDTV;
// Set only after validated init; restored on shutdown. Caps remains engine independent.
extern bool profile_state_supported;

// Name under which p6_load registers the firmware buffer with the engine's blob store.
extern const char *const kFirmwareId;
// Canonical 256 KiB CD32 FMV cartridge ROM copied by p6_cartridge_bind. It is
// an immutable session owner, never a path or a pointer borrowed from the host.
extern const char *const kFmvCartridgeId;

// The memory a config asks for, in KiB, after the machine's own sizes have replaced the zeros of p6_config (T49).
struct Memory {
    uint32_t chip_kib, slow_kib, fast_kib;
};
Memory preset_memory(uint32_t machine);
Memory resolve_memory(const p6_config &config);
constexpr uint16_t kZ3MiB[] = {0, 16, 1, 2, 4, 8, 32, 64, 128, 256, 512};
constexpr unsigned kZ3Choices = sizeof kZ3MiB / sizeof *kZ3MiB;
inline uint32_t z3_bytes(const p6_config &config) {
    return config.z3_memory < kZ3Choices ? uint32_t(kZ3MiB[config.z3_memory]) << 20 : 0;
}
// True when resolve_memory gives the machine's own memory: such a config is the series one in every identity (config_hash included).
bool memory_is_preset(const p6_config &config);

// The flags word that config_hash takes (the envelope hashes machine, standard, rate, flags, seed and clock; the memory has no field of its own there so that the
// identity of every preset config stays the one it was): c.flags for a config with the preset's memory, else with bit 31 and the three sizes folded in above the flag bits (fast/1024 at 8, slow/64 at 12, chip/64 at 17; bits 0 to 7 stay p6_config.flags); T54 adds the FPU (23, 24), T62 the floppy speed (25 to 27) and T72 the raster (28 to 30, only off hires and double lines).
uint32_t identity_flags(const p6_config &config);

// The floppy drive speed (T62, p6_config.floppy_speed) as upstream's floppy_speed: 100, 200, 400 or 800 (percent of the real drive), 0 for the turbo mode that transfers a whole DSKLEN at once.
int resolve_floppy_speed(const p6_config &config);
uint32_t resolve_floppy_drives(const p6_config &config);
int resolve_collision_level(const p6_config &config);
// The CPU of the config (T52, p6_config.cpu_model): 68000, 68010 or 68020 (the machine's own when the field is 0). cpu_identity is true when it is not the machine's own and writes the
// two bytes config_hash is derived from (state.cpp: domain p6/config/cpu/v1, over the hash so far and these bytes): the model, 0 = 68000, 1 = 68010, 2 = 68020, and the multiplier of the clock (0 = the machine's own).
int resolve_cpu(const p6_config &config);
// The multiplier of the CPU clock of the config (T52, p6_config.cpu_multiplier): 1, 2, 4, 8, 10, 12 or 16, the machine's own (2 with a 68000, 4 with the A1200's 68EC020) when the field is 0.
int resolve_cpu_multiplier(const p6_config &config);
bool cpu_identity(const p6_config &config, uint8_t out[2]);
// Paula's output options (T73, p6_config.sound_*), resolved to upstream's own values: stereo separation 0..10 (tenths of 100 %), sound_interpol (0 none, 1 anti, 3 rh, 4 crux; 2 is sinc, refused),
// sound_filter (0 off, 1 emulated, 2 on) and sound_filter_type (0 A500, 1 A1200). The profile's setting is 7, anti, emulated and the model of the machine's own output stage.
constexpr int kProfileSeparation = 7, kProfileInterpolation = 1, kProfileFilter = 1;
struct Audio {
    int separation, interpolation, filter, filter_type;
};
Audio resolve_audio(const p6_config &config);
// True when the options leave the profile's output (the filter model counts only while the filter is on): such a config is the series one in every identity.
bool audio_is_profile(const p6_config &config);
// When the config is not the profile's: the four resolved settings as the bytes config_hash is derived from (state.cpp: domain p6/config/audio/v1, over the hash of the flags word and these bytes).
bool audio_identity(const p6_config &config, uint8_t out[4]);

// The IDE port of Gayle (T56, p6_config.hdf_controller): the A600 and the A1200 have it, and it is switched on only while a hard file is behind it (Gayle with nothing attached is never
// emulated). True when the config asks for IDE on a machine that has Gayle and the list given to p6_load holds `hard_files` hard files (at least one). A config for which this is false
// is the machine the default config is, in every identity; one for which it is true derives config_hash from the one above and a byte (state.cpp: domain p6/config/ide/v1).
bool ide_attached(const p6_config &config, uint32_t hard_files);
// The A4000 motherboard has a physical IDE controller even with no disk attached.
inline bool motherboard_ide(const p6_config &config) { return config.machine == P6_MACHINE_A4030 || config.machine == P6_MACHINE_A4040; }

// Pure checks; the engine is not touched, so a rejected config or firmware costs nothing.
p6_result validate_config(const p6_config &config, p6_error *error);
p6_result validate_memory(const p6_config &config, p6_error *error);
p6_result validate_firmware(const p6_buffer &firmware, uint32_t machine, p6_error *error);

// Shared AR1/2/3 raw owner, used by the public bind and private codec probes.
p6_result bind_action_replay(uint32_t model, p6_buffer image, p6_error *error);
// Private AR1 codec probe; shares the owner used by the public model1 binding.
p6_result bind_ar1(p6_buffer image, p6_error *error);
bool ar_bound();
bool ar_identity(uint8_t out[32]);

// AR1200 shares the monitor slot between its private probe and public binding.
p6_result bind_ar1200(p6_buffer image, p6_error *error);
bool ar1200_bound();

// CD32 FMV firmware owner (T43a). Binding does not claim that the decoder is
// present; lifecycle rejects the load explicitly until FMV-B links it.
p6_result bind_cd32_fmv(p6_buffer image, p6_error *error);
bool cd32_fmv_bound();
bool cd32_fmv_identity(uint8_t out[32]);

// Private HRTMon vertical: the pinned raw builtin, copied before load; Freeze only at a running A1200 boundary.
p6_result bind_hrtmon(p6_buffer image, p6_error *error);
p6_result freeze_hrtmon(p6_error *error);
bool hrtmon_bound();
bool hrtmon_identity(uint8_t out[32]);
// Internal load bootstrap: caller owns the session Guard and has validated config/firmware/media.
p6_result load_builtin_hrtmon(p6_error *error);
// Internal boundary action, without Guard. An ineligible press is consumed by input, never queued.
bool request_hrtmon_freeze();
bool request_cartridge_freeze();

// Fills currprefs with the preset for `config` (upstream built_in_prefs config 0 of model 0 = A500, 2 = A600 or 4 = A1200, the
// overrides of the profile file, then upstream fixup_prefs). Touches only preference globals. `ide` is ide_attached(config, hard files of the list): the preset then has Gayle's IDE port.
void build_preferences(const p6_config &config, bool ide);

// Frees the device names and configuration names that the engine's input matching (matchdevices) gives to the preference
// globals on every load and never releases. currprefs and changed_prefs share them (copy_prefs copies the pointers), so each
// distinct string is freed once. Call after the engine has stopped using them, i.e. when a session ends.
void release_input_device_names();

// After build_preferences: the result is exactly the machine config.machine promises. A mismatch means the
// preset drifted from the profile (an upstream change), and is reported as an internal error.
p6_result check_preset(p6_error *error);

// After memory_reset: the loader's verdict on the firmware, and the checksum of the decoded image.
p6_result check_loaded_firmware(p6_error *error);

}  // namespace p6_bridge
#endif
