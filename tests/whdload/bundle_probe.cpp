// Standalone public builder probe; no engine, fake frontend or user binaries.
#include "../../adapters/libretro/whdload_provider.h"
#include <fstream>
#include <iostream>
using namespace p6_libretro;

static std::vector<uint8_t> read(const char *path)
{
    std::ifstream in(path, std::ios::binary);
    if (!in) throw std::runtime_error("cannot read input");
    return {std::istreambuf_iterator<char>(in), std::istreambuf_iterator<char>()};
}
static std::string unhex(const std::string &s)
{
    if (s == "-") return {};
    if (s.size() % 2) throw std::runtime_error("odd hex");
    std::string out;
    for (size_t n = 0; n < s.size(); n += 2) out += char(std::stoul(s.substr(n, 2), nullptr, 16));
    return out;
}
int main(int argc, char **argv) try
{
    if (argc == 3 && std::string(argv[1]) == "sha1") { std::cout << whdload_sha1(read(argv[2])) << '\n'; return 0; }
    if (argc == 4 && std::string(argv[1]) == "zipkind") {
        const auto bytes = read(argv[2]);
        std::cout << (zip_is_volume(bytes) || (std::string(argv[3]) == "auto" && zip_has_whdload_slave(bytes)) ? "volume" : "disk") << '\n'; return 0;
    }
    if (argc == 3 && std::string(argv[1]) == "provider") {
        WhdLoadContent pack;
        std::string why;
        if (!read_whdload_provider(FileReader(), argv[2], &pack, &why)) { std::cerr << why << '\n'; return 2; }
        std::cout << pack.program.size() << ' ' << pack.vfs.size() << '\n'; return 0;
    }
    if (argc != 8) return 64; // mode, input, program, VFS, kickstarts manifest, output prefix, ButtonWait
    std::vector<VolumeFile> files;
    std::map<std::string, std::vector<uint8_t>> kickstarts;
    std::string why, kind, path, data;
    const std::string mode = argv[1];
    auto manifest = [&](const char *name, bool roms) {
        std::ifstream in(name);
        if (!in) throw std::runtime_error("cannot read manifest");
        while (in >> kind >> path >> data) {
            auto p = unhex(path), d = unhex(data);
            if (roms) kickstarts[p] = {d.begin(), d.end()};
            else files.push_back({p, kind == "D", {d.begin(), d.end()}});
        }
    };
    if (mode == "tree") manifest(argv[2], false);
    else {
        const auto bytes = read(argv[2]);
        if (bytes.size() > kMaxVolumeArchiveBytes) throw std::runtime_error("archive exceeds limit");
        const bool ok = mode == "lha" ? volume_from_lha(bytes, &files, &why) : mode == "zip" && volume_from_zip(bytes, &files, &why);
        if (!ok) throw std::runtime_error(why);
    }
    manifest(argv[5], true);
    WhdLoadBundle result;
    result.images[0] = {1}; // a rejected call must also clear an old result
    if (!build_whdload_bundle(files, read(argv[3]), read(argv[4]), kickstarts, std::string(argv[7]) == "1", &result, &why)) {
        for (const auto &image : result.images) if (!image.empty()) return 4;
        std::cerr << why << '\n'; return 2;
    }
    for (size_t n = 0; n < result.images.size(); ++n) {
        std::ofstream out(std::string(argv[6]) + whdload_data::kNames[n], std::ios::binary);
        out.write(reinterpret_cast<const char *>(result.images[n].data()), result.images[n].size());
        if (!out) return 3;
    }
    return 0;
} catch (const std::exception &error) { std::cerr << error.what() << '\n'; return 2; }
