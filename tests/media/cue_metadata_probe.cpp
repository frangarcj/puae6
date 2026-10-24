#include "cd_cue_metadata.h"
#include <stdio.h>
#include <string.h>
using namespace p6_cd;
struct Input { FILE *file; unsigned calls = 0; int fault = 0; };
static int line(void *ctx, char *out, size_t capacity)
{
    auto &input = *static_cast<Input *>(ctx);
    if (input.fault == 1) return -1;
    if (input.fault == 2) return int(capacity);
    if (input.fault == 5) { memcpy(out, "A\0B", 4); return 3; }
    if (input.fault == 6) { memcpy(out, "AB", 2); return 1; }
    const auto start = ftell(input.file);
    if (!fgets(out, int(capacity), input.file)) return ferror(input.file) ? -1 : 0;
    return int(ftell(input.file) - start);
}
static Result resolve(void *ctx, const char *name, Encoding type, CueSource *out)
{
    auto &input = *static_cast<Input *>(ctx); ++input.calls;
    if (input.fault == 3) return Result::ReadError;
    if (strcmp(name, "mixed.bin") && strcmp(name, "audio.wav")) return Result::Invalid;
    if ((type == Encoding::AudioPcmS16Le) != !strcmp(name, "audio.wav")) return Result::Invalid;
    *out = {unsigned(type == Encoding::AudioPcmS16Le), input.fault == 4 ? 0 : UINT64_C(2352) * 1000};
    return Result::Ok;
}
int main(int argc, char **argv)
{
    if (argc < 2 || argc > 3) return 64;
    Input input{fopen(argv[1], "rb")};
    if (!input.file) return 66;
    if (argc == 3) input.fault = argv[2][0] - '0';
    CueMetadata result; memset(&result, 0xa5, sizeof result);
    const auto status = cue_metadata({&input, line, resolve}, &result);
    fclose(input.file);
    if (status != Result::Ok) {
        const auto *bytes = reinterpret_cast<const unsigned char *>(&result);
        for (size_t n = 0; n < sizeof result; ++n) if (bytes[n] != 0xa5) return 70;
        printf("error=%u calls=%u\n", unsigned(status), input.calls); return 2;
    }
    printf("tracks=%u calls=%u\n", result.tracks, input.calls);
    for (unsigned n = 0; n < result.tracks; ++n) {
        const auto &t = result.track[n];
        printf("%u %u %u %u %u %u %u %u %u %u %u\n", n + 1, t.source, t.stride, unsigned(t.mode), t.control,
               t.has_index0, t.index0, t.index1, t.pregap, t.postgap, t.new_file);
    }
}
