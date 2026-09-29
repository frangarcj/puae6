// The fixed A500 configuration of ABI v1: what p6_config may say, what the preset pins, and the
// checks done before the engine is touched. See config/profiles/a500-ocs.json for the field list.
#ifndef P6_BRIDGE_CONFIG_H
#define P6_BRIDGE_CONFIG_H
#include "p6.h"

namespace p6_bridge {

// Name under which p6_load registers the firmware buffer with the engine's blob store.
extern const char *const kFirmwareId;

// Pure checks; the engine is not touched, so a rejected config or firmware costs nothing.
p6_result validate_config(const p6_config &config, p6_error *error);
p6_result validate_firmware(const p6_buffer &firmware, p6_error *error);

// Fills currprefs with the preset for `config` (upstream built_in_prefs model 0 config 0, the
// overrides of the profile file, then upstream fixup_prefs). Touches only preference globals.
void build_preferences(const p6_config &config);

// After build_preferences: the result is exactly the machine ABI v1 promises. A mismatch means the
// preset drifted from the profile (an upstream change), and is reported as an internal error.
p6_result check_preset(p6_error *error);

// After memory_reset: the loader's verdict on the firmware, and the checksum of the decoded image.
p6_result check_loaded_firmware(p6_error *error);

}  // namespace p6_bridge
#endif
