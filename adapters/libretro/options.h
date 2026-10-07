// The core options of the libretro adapter (T17b): offering them to the frontend and reading what the person chose, over the tables that
// tools/generate_options.py makes from config/schema/options.json (options_generated.h). Nothing here touches the native ABI or the engine.
//
// Two kinds of option (the schema's classes). A reboot option is part of the machine: the core reads it when the content is loaded, it ends up in
// p6_config (so in the state identity), and a change made while the machine runs is only noted, never applied. A presentation option has no effect
// on the emulation and may be applied at once. A value the schema does not allow is refused with a message that names the key and the value;
// nothing is clamped or replaced by a default, because a stale configuration silently changing the machine is how two sessions diverge.
#ifndef P6_LIBRETRO_OPTIONS_H
#define P6_LIBRETRO_OPTIONS_H

#include "libretro.h"
#include "options_generated.h"

#include <string.h>
#include <string>

namespace p6_libretro {

// How the options were offered; Legacy is SET_VARIABLES, None is a frontend with no options at all (every option then keeps its default).
enum class OptionsApi { None, Legacy, V1, V2 };

inline OptionsApi offer_options(retro_environment_t environment)
{
    unsigned version = 0;
    if (!environment(RETRO_ENVIRONMENT_GET_CORE_OPTIONS_VERSION, &version)) version = 0;
    if (version >= 2 && environment(RETRO_ENVIRONMENT_SET_CORE_OPTIONS_V2, &p6_options::kOptionsV2)) return OptionsApi::V2;
    if (version >= 1 && environment(RETRO_ENVIRONMENT_SET_CORE_OPTIONS, const_cast<retro_core_option_definition *>(p6_options::kDefinitionsV1)))
        return OptionsApi::V1;
    if (environment(RETRO_ENVIRONMENT_SET_VARIABLES, const_cast<retro_variable *>(p6_options::kVariables))) return OptionsApi::Legacy;
    return OptionsApi::None;
}

inline const char *option_value_name(const p6_options::Option &option, uint32_t number)
{
    for (unsigned i = 0; i < option.count; ++i)
        if (option.values[i].number == number) return option.values[i].name;
    return "?";
}

inline std::string allowed_values(const p6_options::Option &option)
{
    std::string text;
    for (unsigned i = 0; i < option.count; ++i) text += std::string(i ? ", " : "") + option.values[i].name;
    return text;
}

// Reads every option the frontend holds into `settings` (which keeps what it had for an option the frontend does not hold: no options support, or
// a frontend that never heard of the key). Returns false, with the reason in `why`, at the first value the schema does not allow; `settings` then
// has the options before it and must not be used.
inline bool read_options(retro_environment_t environment, p6_options::Settings *settings, std::string *why)
{
    for (const p6_options::Option &option : p6_options::kOptions) {
        retro_variable variable = {option.key, nullptr};
        if (!environment(RETRO_ENVIRONMENT_GET_VARIABLE, &variable) || !variable.value) continue;
        unsigned i = 0;
        while (i < option.count && strcmp(option.values[i].name, variable.value) != 0) ++i;
        if (i == option.count) {
            *why = std::string("the option ") + option.key + " has the value \"" + variable.value + "\", which this core does not support (allowed: " +
                   allowed_values(option) + ")";
            return false;
        }
        settings->*option.field = option.values[i].number;
    }
    return true;
}

}  // namespace p6_libretro

#endif
