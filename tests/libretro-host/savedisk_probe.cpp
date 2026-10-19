#include "../../adapters/libretro/save_disk.h"
#include <iostream>
#include <iterator>

static std::string hex(const std::string &text)
{
    static const char digits[] = "0123456789abcdef";
    std::string out;
    for (unsigned char c : text) { out += digits[c >> 4]; out += digits[c & 15]; }
    return out.empty() ? "-" : out;
}

int main(int argc, char **argv)
{
    using namespace p6_libretro;
    if (argc == 4 && std::string(argv[1]) == "disk") {
        const auto disk = make_save_disk(argv[3], unsigned(std::stoul(argv[2])));
        std::cout.write(reinterpret_cast<const char *>(disk.data()), disk.size());
        return 0;
    }
    if (argc != 2 || std::string(argv[1]) != "parse") return 2;
    const std::string text(std::istreambuf_iterator<char>(std::cin), {});
    Playlist playlist;
    std::string why;
    if (!parse_m3u(std::vector<uint8_t>(text.begin(), text.end()), "/content/", 16, &playlist, &why)) {
        std::cout << "refused " << why << '\n';
        return 0;
    }
    std::cout << "ok " << playlist.save_disks << '\n';
    for (const auto &entry : playlist.entries)
        std::cout << entry.save_disk << ' ' << entry.save_index << ' ' << hex(entry.path) << ' ' << hex(entry.label) << '\n';
}
