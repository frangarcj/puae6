#include "cd_ccd_metadata.h"
#include <stdio.h>
#include <string.h>
using namespace p6_cd;
struct Input { FILE *file; int fault; };
static int line(void *ctx, char *out, size_t capacity)
{
    auto &input = *static_cast<Input *>(ctx);
    if (input.fault == 1) return -1;
    if (input.fault == 2) return int(capacity);
    if (input.fault == 3) { memcpy(out, "A\0B", 4); return 3; }
    if (input.fault == 4) { memcpy(out, "AB", 2); return 1; }
    const auto start = ftell(input.file);
    if (!fgets(out, int(capacity), input.file)) return ferror(input.file) ? -1 : 0;
    return int(ftell(input.file) - start);
}
int main(int argc, char **argv)
{
    if (argc < 2 || argc > 3) return 64;
    Input input{fopen(argv[1], "rb"), argc == 3 ? argv[2][0] - '0' : 0};
    if (!input.file) return 66;
    struct { uint64_t before; CcdMetadata metadata; uint64_t after; } output;
    memset(&output, 0xa5, sizeof output);
    const auto status = ccd_metadata(&input, line, &output.metadata);
    fclose(input.file);
    if (output.before != UINT64_C(0xa5a5a5a5a5a5a5a5) || output.after != output.before) return 70;
    if (status != Result::Ok) {
        const auto *bytes = reinterpret_cast<const unsigned char *>(&output.metadata);
        for (size_t n = 0; n < sizeof output.metadata; ++n) if (bytes[n] != 0xa5) return 71;
        printf("error=%u\n", unsigned(status)); return 2;
    }
    const auto &m = output.metadata;
    printf("ccd %u %u %u %u\n", m.tracks, m.entries, m.pregap_mode, m.pregap_subc);
    for (unsigned n = 0; n < m.entries; ++n) {
        printf("entry"); for (auto value : m.entry[n].value) printf(" %d", value); puts("");
    }
    for (unsigned n = 0; n < m.tracks; ++n) printf("track %d %u %d %d\n", m.track[n].mode, m.track[n].has_index0, m.track[n].index0, m.track[n].index1);
}
