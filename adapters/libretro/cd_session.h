// Host session boundary: the core does not include WinUAE device headers.
#pragma once
#include <stdint.h>
#include <memory>
#include <string>
#include <vector>
struct retro_vfs_interface;
namespace p6_libretro {
class FileReader;
struct FirmwareNames;
struct PlaylistEntry;
struct Image;
class CdSession {
public:
    CdSession();
    ~CdSession(); // only after p6_unload/p6_shutdown
    CdSession(const CdSession &) = delete;
    CdSession &operator=(const CdSession &) = delete;
    static bool content(const char *path);
    static const FirmwareNames &firmware_names();
    bool open(const std::vector<PlaylistEntry> &, unsigned selected, const retro_vfs_interface *, std::vector<Image> *, std::string *why);
    unsigned count() const; // unique native catalog entries, not frontend aliases
    bool apply(bool mount, uint32_t medium); // only at a quiescent unit boundary
    // Main was already decoded by load_firmware; publish EXT+MAIN atomically.
    bool extend_firmware(const FileReader &, const char *directory, std::vector<uint8_t> *rom, std::string *why);
private:
    struct Data;
    std::unique_ptr<Data> data_;
};
}
