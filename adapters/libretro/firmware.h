// The Kickstart of each machine, by the names a system directory gives it (T23d). A name only finds the file: what it is, and whether the machine
// accepts it, is decided by p6_load from the bytes, never by the name. A machine looks for its own names and no one else's, so that a system
// directory with only the Kickstart 1.3 does not boot an A600 or an A1200 with the wrong ROM (and the other way round): the load fails and says what is missing.
//
// Where the names come from. The first two of each machine are those PUAE5 (libretro-uae, libretro/libretro-core.h uae_kickstarts[]) reads: the
// "normal" name and the Amiga Forever one. The others are the file names of the collections (the TOSEC style of PUAE5's list, and the one of the
// dump the project's authorized copy comes in), which the A500 list has had since T17a.
#ifndef P6_LIBRETRO_FIRMWARE_H
#define P6_LIBRETRO_FIRMWARE_H

#include "p6.h"

#include <stddef.h>
#include <stdint.h>

namespace p6_libretro {

struct FirmwareNames {
    uint32_t machine;                // P6_MACHINE_*
    const char *model;               // "A500": how the person knows the machine (the value of puae6_machine)
    const char *kickstart;           // "1.3": the Kickstart version this machine boots, for the message when it is not there
    const char *const *names;
    size_t count;
};

constexpr const char *kA500KickstartNames[] = {
    "kick34005.A500",
    "amiga-os-130.rom",
    "Kickstart v1.3 rev 34.5 (1987)(Commodore)(A500-A1000-A2000-CDTV)[!].rom",
    "Kickstart v1.3 rev 34.5 (1987)(Commodore)(A500-A1000-A2000-CDTV).rom",
    "Kickstart v1.3 r34.5 (1987)(Commodore)(A500-A1000-A2000-CDTV)[!].rom",
};

constexpr const char *kA600KickstartNames[] = {
    "kick37350.A600",
    "amiga-os-205-a600.rom",
    "Kickstart v2.05 rev 37.350 (1992)(Commodore)(A600HD)[!].rom",
    "Kickstart v2.05 rev 37.350 (1992)(Commodore)(A600HD).rom",
    "Kickstart v2.05 r37.350 (1992-04)(Commodore)(A600HD)[!].rom",
};

// The A1200 boots the Kickstart 3.1 r40.68 (A1200). PUAE5 also reads the 3.0 (kick39106.A1200) for its A1200, but this machine's ROM requirement is the 3.1 and
// the 3.0 is not looked for (T24e). The first two names are PUAE5's "normal" and Amiga Forever ones; the third is its TOSEC name with "[!]" (libretro-core.h:
// uae_kickstarts[A1200_KS31_ROM]), then the one without it and the TOSEC spelling "r40.68" of the project's authorized copy.
constexpr const char *kA1200KickstartNames[] = {
    "kick40068.A1200",
    "amiga-os-310-a1200.rom",
    "Kickstart v3.1 rev 40.68 (1993)(Commodore)(A1200)[!].rom",
    "Kickstart v3.1 rev 40.68 (1993)(Commodore)(A1200).rom",
    "Kickstart v3.1 r40.68 (1993)(Commodore)(A1200)[!].rom",
};

constexpr FirmwareNames kFirmware[] = {
    {P6_MACHINE_A500, "A500", "1.3", kA500KickstartNames, sizeof kA500KickstartNames / sizeof kA500KickstartNames[0]},
    {P6_MACHINE_A600, "A600", "2.05", kA600KickstartNames, sizeof kA600KickstartNames / sizeof kA600KickstartNames[0]},
    {P6_MACHINE_A1200, "A1200", "3.1", kA1200KickstartNames, sizeof kA1200KickstartNames / sizeof kA1200KickstartNames[0]},
};

// The names of `machine`, or nullptr for a number that is no machine (the options never produce one).
inline const FirmwareNames *firmware_for(uint32_t machine)
{
    for (const FirmwareNames &entry : kFirmware)
        if (entry.machine == machine) return &entry;
    return nullptr;
}

}  // namespace p6_libretro

#endif
