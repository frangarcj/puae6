// T15a: the native envelope (ports/portable/state_io) behind a line protocol, so that tests/state/test_envelope_native.py can run every
// state of the reference encoder (tests/state/envelope.py) through it and compare byte for byte and verdict for verdict. No engine is
// linked: this is the container alone.
//
//   hash config MACHINE STANDARD RATE FLAGS SEED EPOCH                         -> HEX
//   hash media N TYPE:IDHEX ...                                                -> HEX
//   hash build COMMIT SERIES CODEGEN SOURCES PROFILE                           -> HEX
//   encode PROFILE BUILD COMMIT SERIES CODEGEN CONFIG MEDIA FIRMWARE UNIT TICKS TAG:VERSION:BODYHEX...  -> HEX of the whole state
//   decode PROFILE BUILD COMMIT SERIES CODEGEN CONFIG MEDIA FIRMWARE BOUND TAG:VERSION[:MAXBODY],... STATEHEX
//       -> "ok UNIT TICKS TAG:VERSION:BODYHEX..." or "refused CODE FIELD"
// Every value is hex except the decimal integers; one request per line, one answer per line.
#include "envelope.h"

#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <iostream>
#include <sstream>
#include <string>
#include <vector>

using namespace p6_state;

namespace {

std::vector<uint8_t> from_hex(const std::string &text)
{
    std::vector<uint8_t> out;
    if (text == "-") return out;        // an empty byte string is a dash, so that it is still one word on the line
    for (size_t i = 0; i + 1 < text.size(); i += 2) out.push_back(uint8_t(strtoul(text.substr(i, 2).c_str(), nullptr, 16)));
    return out;
}

std::string to_hex(const uint8_t *data, size_t size)
{
    std::string text;
    char two[3];
    for (size_t i = 0; i < size; ++i) { snprintf(two, sizeof two, "%02x", data[i]); text += two; }
    return text;
}

bool exact(const std::vector<uint8_t> &v, size_t size) { return v.size() == size; }

struct Request {
    std::vector<std::string> words;
    explicit Request(const std::string &line)
    {
        std::istringstream in(line);
        for (std::string word; in >> word;) words.push_back(word);
    }
    bool has(size_t n) const { return words.size() >= n; }
};

bool read_identity(const Request &r, size_t at, Identity *id)
{
    const std::vector<uint8_t> build = from_hex(r.words[at + 1]), commit = from_hex(r.words[at + 2]), series = from_hex(r.words[at + 3]),
        codegen = from_hex(r.words[at + 4]), config = from_hex(r.words[at + 5]), media = from_hex(r.words[at + 6]), firmware = from_hex(r.words[at + 7]);
    if (!exact(build, 32) || !exact(commit, 20) || !exact(series, 32) || !exact(codegen, 32) || !exact(config, 32) || !exact(media, 32) ||
        !exact(firmware, 32))
        return false;
    id->profile_id = uint32_t(strtoul(r.words[at].c_str(), nullptr, 10));
    memcpy(id->core_build_id, build.data(), 32);
    memcpy(id->upstream_commit, commit.data(), 20);
    memcpy(id->patch_series_hash, series.data(), 32);
    memcpy(id->codegen_hash, codegen.data(), 32);
    memcpy(id->config_hash, config.data(), 32);
    memcpy(id->media_manifest_hash, media.data(), 32);
    memcpy(id->firmware_sha256, firmware.data(), 32);
    return true;
}

struct Piece { std::string tag; uint32_t version; std::vector<uint8_t> body; };

bool parse_piece(const std::string &text, Piece *piece)
{
    const size_t a = text.find(':'), b = text.find(':', a + 1);
    if (a != 4 || b == std::string::npos) return false;
    piece->tag = text.substr(0, 4);
    piece->version = uint32_t(strtoul(text.substr(a + 1, b - a - 1).c_str(), nullptr, 10));
    piece->body = from_hex(text.substr(b + 1));
    return true;
}

std::string answer(const Request &r)
{
    const std::string &verb = r.words[0];
    uint8_t out[32];
    if (verb == "hash" && r.has(2)) {
        if (r.words[1] == "config" && r.has(8)) {
            config_hash(uint32_t(strtoul(r.words[2].c_str(), nullptr, 10)), uint32_t(strtoul(r.words[3].c_str(), nullptr, 10)),
                        uint32_t(strtoul(r.words[4].c_str(), nullptr, 10)), uint32_t(strtoul(r.words[5].c_str(), nullptr, 10)),
                        strtoull(r.words[6].c_str(), nullptr, 10), strtoll(r.words[7].c_str(), nullptr, 10), out);
            return to_hex(out, 32);
        }
        if (r.words[1] == "media" && r.has(3)) {
            const uint32_t n = uint32_t(strtoul(r.words[2].c_str(), nullptr, 10));
            if (!r.has(3 + n)) return "error arguments";
            std::vector<uint32_t> types;
            std::vector<std::vector<uint8_t>> ids;
            std::vector<const uint8_t *> pointers;
            for (uint32_t i = 0; i < n; ++i) {
                const std::string &item = r.words[3 + i];
                const size_t colon = item.find(':');
                types.push_back(uint32_t(strtoul(item.substr(0, colon).c_str(), nullptr, 10)));
                ids.push_back(from_hex(item.substr(colon + 1)));
                if (ids.back().size() != 32) return "error content id";
            }
            for (const auto &id : ids) pointers.push_back(id.data());
            media_manifest_hash(n, types.data(), pointers.data(), out);
            return to_hex(out, 32);
        }
        if (r.words[1] == "build" && r.has(7)) {
            const auto commit = from_hex(r.words[2]), series = from_hex(r.words[3]), codegen = from_hex(r.words[4]), sources = from_hex(r.words[5]);
            if (!exact(commit, 20) || !exact(series, 32) || !exact(codegen, 32) || !exact(sources, 32)) return "error sizes";
            core_build_id(commit.data(), series.data(), codegen.data(), sources.data(), uint32_t(strtoul(r.words[6].c_str(), nullptr, 10)), out);
            return to_hex(out, 32);
        }
    }
    if (verb == "encode" && r.has(11)) {
        Identity id;
        if (!read_identity(r, 1, &id)) return "error identity";
        std::vector<uint8_t> payload;
        uint32_t count = 0;
        for (size_t i = 11; i < r.words.size(); ++i, ++count) {
            Piece piece;
            if (!parse_piece(r.words[i], &piece)) return "error chunk";
            uint8_t header[kChunkHeaderSize];
            memcpy(header, piece.tag.data(), 4);
            for (int b = 0; b < 4; ++b) header[4 + b] = uint8_t(piece.version >> (8 * b));
            for (int b = 0; b < 8; ++b) header[8 + b] = uint8_t(uint64_t(piece.body.size()) >> (8 * b));
            payload.insert(payload.end(), header, header + kChunkHeaderSize);
            payload.insert(payload.end(), piece.body.begin(), piece.body.end());
        }
        std::vector<uint8_t> state(kHeaderSize + payload.size());
        if (!payload.empty()) memcpy(state.data() + kHeaderSize, payload.data(), payload.size());
        seal(state.data(), payload.size(), count, id, strtoull(r.words[9].c_str(), nullptr, 10), strtoull(r.words[10].c_str(), nullptr, 10));
        return to_hex(state.data(), state.size());
    }
    if (verb == "decode" && r.has(12)) {
        Identity id;
        if (!read_identity(r, 1, &id)) return "error identity";
        const uint64_t bound = strtoull(r.words[9].c_str(), nullptr, 10);
        std::vector<ChunkSpec> specs;
        std::vector<uint64_t> maxima;
        std::istringstream list(r.words[10]);
        for (std::string item; std::getline(list, item, ',');) {
            const size_t colon = item.find(':'), second = item.find(':', colon + 1);
            if (colon != 4) return "error specs";
            ChunkSpec spec = {};
            memcpy(spec.tag, item.data(), 4);
            spec.version = uint32_t(strtoul(item.c_str() + colon + 1, nullptr, 10));
            specs.push_back(spec);
            maxima.push_back(second == std::string::npos ? bound : strtoull(item.c_str() + second + 1, nullptr, 10));
        }
        const std::vector<uint8_t> state = from_hex(r.words[11]);
        std::vector<ChunkView> views(specs.size());
        uint64_t unit = 0, ticks = 0;
        Refusal refusal = {};
        if (!validate(state.data(), state.size(), id, bound, specs.data(), uint32_t(specs.size()), maxima.data(), views.data(), &unit, &ticks, &refusal))
            return "refused " + std::to_string(refusal.code) + " " + refusal.field;
        std::string text = "ok " + std::to_string(unit) + " " + std::to_string(ticks);
        for (size_t i = 0; i < specs.size(); ++i)
            text += " " + std::string(specs[i].tag) + ":" + std::to_string(views[i].version) + ":" + to_hex(views[i].body, size_t(views[i].length));
        return text;
    }
    return "error request";
}

}  // namespace

int main()
{
    std::string line;
    while (std::getline(std::cin, line)) {
        const Request request(line);
        if (request.words.empty()) continue;
        std::cout << answer(request) << "\n";
    }
    return 0;
}
