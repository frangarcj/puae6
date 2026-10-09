// The fixed A500, A600 and A1200 configurations: what p6_config may say, what the preset pins, and the
// checks done before the engine is touched. See config/profiles/a500-ocs.json, a600-ecs.json and a1200-aga.json for the field lists.
#ifndef P6_BRIDGE_CONFIG_H
#define P6_BRIDGE_CONFIG_H
#include "p6.h"

namespace p6_bridge {

// Name under which p6_load registers the firmware buffer with the engine's blob store.
extern const char *const kFirmwareId;

// The memory a config asks for, in KiB, after the machine's own sizes have replaced the zeros of p6_config (T49).
struct Memory {
    uint32_t chip_kib, slow_kib, fast_kib;
};
Memory preset_memory(uint32_t machine);
Memory resolve_memory(const p6_config &config);
// True when resolve_memory gives the machine's own memory: such a config is the series one in every identity (config_hash included).
bool memory_is_preset(const p6_config &config);

// The flags word that config_hash takes (the envelope hashes machine, standard, rate, flags, seed and clock; the memory has no field of its own there so that the
// identity of every preset config stays the one it was): c.flags for a config with the preset's memory, else with bit 31 and the three sizes folded in above the flag bits (fast/1024 at 8, slow/64 at 12, chip/64 at 17; bits 0 to 7 stay p6_config.flags).
uint32_t identity_flags(const p6_config &config);

// Pure checks; the engine is not touched, so a rejected config or firmware costs nothing.
p6_result validate_config(const p6_config &config, p6_error *error);
p6_result validate_memory(const p6_config &config, p6_error *error);
p6_result validate_firmware(const p6_buffer &firmware, p6_error *error);

// Fills currprefs with the preset for `config` (upstream built_in_prefs config 0 of model 0 = A500, 2 = A600 or 4 = A1200, the
// overrides of the profile file, then upstream fixup_prefs). Touches only preference globals.
void build_preferences(const p6_config &config);

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
