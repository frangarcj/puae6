// The importer of .uae files (T31 closure, docs/options.md "Importador .uae"). A .uae is the configuration file of WinUAE and PUAE: PUAE5 accepts any
// of them as content and so can write a machine that this core has no profile for. Here a .uae is a list of `key=value` lines of which only an allowlist
// is read, each key translated to an option of the common schema (config/schema/options.json) and nothing else:
//   * a key that changes the machine and that the schema has (model, memory, FPU, video standard, audio rate, write protection) sets that option;
//   * a key that describes the machine the profile already is (chipset, CPU, cycle exact) is checked against the machine and refused when it differs;
//   * a key that names a disk (floppy0, diskimage0..15) is a path relative to the folder of the .uae, read through the frontend's VFS like a playlist entry;
//   * a key that only touches the host's window, the input or the file's own metadata is ignored (the first kind with a warning);
//   * everything else is refused with the key and the line: a host directory, a hard disk, a CD, a Kickstart by path, SCSI, RTG, JIT, any absolute path,
//     and every key this importer does not know (it cannot tell that an unknown key leaves the machine alone, and a silent success is how two sessions diverge).
// The parser is bounded (file size, line length, number of lines, number of reports), has no includes, no environment variables, no plugins and no shell,
// and never touches the engine or the ABI: the result is a Settings (the reboot options) and a list of disks, which libretro_core.cpp lays over what the
// frontend's options gave.
#ifndef P6_LIBRETRO_CONFIG_IMPORT_H
#define P6_LIBRETRO_CONFIG_IMPORT_H

#include "content.h"
#include "options_generated.h"

#include <stddef.h>
#include <stdint.h>
#include <string>
#include <vector>

namespace p6_libretro {

constexpr size_t kMaxUaeBytes = 128 * 1024;        // a PUAE5 file written by the GUI is a fifth of this
constexpr size_t kMaxUaeLineBytes = 4096;          // = kMaxPathBytes: a line holds at most one path
constexpr size_t kMaxUaeLines = 4096;
constexpr size_t kMaxUaeReports = 32;              // errors and warnings kept; the rest is only counted
constexpr unsigned kMaxUaeDiskImages = 16;         // diskimage0 .. diskimage15, the swap list of WinUAE

struct UaeReport {
    unsigned line = 0;               // 1-based; 0 for a problem of the file as a whole
    std::string key;                 // lower case as matched; empty for a problem of the file as a whole
    std::string message;
};

struct UaeImport {
    p6_options::Settings settings;                     // the options the frontend gave with the file's keys laid over (reboot options only)
    std::vector<PlaylistEntry> disks;                  // floppy0, then diskimage0..15 in number order, paths resolved against the .uae's folder
    std::vector<UaeReport> errors;                     // the load is refused when there is any
    std::vector<UaeReport> warnings;                   // keys that were read and have no effect on the machine
    std::vector<std::string> applied;                  // "key=value -> option=value" for the log, in the order of the file
    size_t errors_omitted = 0, warnings_omitted = 0;   // reports beyond kMaxUaeReports
};

// Imports the bytes of a .uae. `directory` is the .uae's own folder with its trailing separator (directory_of), `base` the options the frontend holds
// (read_options, preset applied), `max_disks` the most disks a content may name. Returns true when there is no error; `out` is then complete. On false
// `out->errors` says why, and `out->settings` must not be used.
//
// Precedence, from the weakest: the schema's defaults, the frontend's options (its preset expanded), the preset a .uae names (puae6_preset), the keys
// of the .uae. The order of the lines of the file does not matter. Presentation, input and OSD options are never read from the file.
bool import_uae(const std::vector<uint8_t> &bytes, const std::string &directory, const p6_options::Settings &base, size_t max_disks, UaeImport *out);

// "line 12 (filesystem2): ..." for a report; the file's name is added by the caller.
std::string describe_report(const UaeReport &report);

}  // namespace p6_libretro

#endif
