// Headless PUAE6 runner. For now it only proves that the A500 profile links without
// any windowing, audio or plugin dependency and reports the identity of the build and
// what the native ABI implements. It does not boot a machine.
#include "p6.h"
#include <stdio.h>
#include <string.h>

#ifndef P6_PROFILE
#error "P6_PROFILE is provided by the CMake target"
#endif

static int identity(void)
{
    printf("core: puae6-headless\n");
    printf("profile: %s\n", P6_PROFILE);
    printf("machines: A500\n");
    printf("abi: %u\n", (unsigned)p6_abi_version());
    printf("upstream: WinUAE %s\n", P6_UPSTREAM_COMMIT);
    printf("patches: %s (%s)\n", P6_PATCH_COUNT, P6_PATCH_SERIES_SHA256);
    p6_caps caps = {};
    caps.abi_version = P6_ABI_VERSION;
    caps.struct_size = sizeof caps;
    p6_error error = {};
    error.abi_version = P6_ABI_VERSION;
    error.struct_size = sizeof error;
    if (p6_query_caps(&caps, &error) != P6_OK) {
        fprintf(stderr, "p6_query_caps failed: %s\n", error.message);
        return 2;
    }
    printf("caps: 0x%llx\n", (unsigned long long)caps.flags);
    printf("boot: not implemented\n");
    return 0;
}

int main(int argc, char **argv)
{
    if (argc == 1 || (argc == 2 && !strcmp(argv[1], "--identity")))
        return identity();
    fprintf(stderr, "usage: %s [--identity]\n", argv[0]);
    return 64;
}
