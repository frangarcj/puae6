// The identity of this build as the state envelope binds it (docs/adr/0009-state-format.md, point 3). CMake computes the four hashes and
// passes them as definitions of this file only, so that an edit to any bridge or portable source changes core_build_id and a state
// saved by another build is refused as INCOMPATIBLE instead of being fed to a machine it was not made for.
#include "state.h"

#include <string.h>

#if !defined(P6_PROFILE) || !defined(P6_UPSTREAM_COMMIT) || !defined(P6_PATCH_SERIES_SHA256) || !defined(P6_CODEGEN_SHA256) || \
    !defined(P6_SOURCES_SHA256)
#error "build_identity.cpp needs the definitions CMake gives it (target p6_state)"
#endif

static_assert(sizeof(P6_UPSTREAM_COMMIT) == 41, "the upstream commit is 40 hex digits");
static_assert(sizeof(P6_PATCH_SERIES_SHA256) == 65, "the patch series hash is 64 hex digits");
static_assert(sizeof(P6_CODEGEN_SHA256) == 65, "the codegen hash is 64 hex digits");
static_assert(sizeof(P6_SOURCES_SHA256) == 65, "the sources hash is 64 hex digits");

namespace p6_state {

namespace {

int nibble(char c)
{
    if (c >= '0' && c <= '9') return c - '0';
    if (c >= 'a' && c <= 'f') return c - 'a' + 10;
    if (c >= 'A' && c <= 'F') return c - 'A' + 10;
    return -1;
}

// A build whose identity cannot be read must not run: its states would be bound to nothing.
void decode(const char *text, uint8_t *out, size_t bytes)
{
    for (size_t i = 0; i < bytes; ++i) {
        const int high = nibble(text[2 * i]), low = nibble(text[2 * i + 1]);
        if (high < 0 || low < 0) __builtin_trap();
        out[i] = uint8_t(high << 4 | low);
    }
}

BuildIdentity compute()
{
    BuildIdentity identity = {};
    // The only profile of ABI v1; a new profile gets a new id here and in the ADR.
    if (strcmp(P6_PROFILE, "deterministic-a500") != 0) __builtin_trap();
    identity.profile_id = kProfileA500;
    decode(P6_UPSTREAM_COMMIT, identity.upstream_commit, 20);
    decode(P6_PATCH_SERIES_SHA256, identity.patch_series_hash, 32);
    decode(P6_CODEGEN_SHA256, identity.codegen_hash, 32);
    decode(P6_SOURCES_SHA256, identity.sources_hash, 32);
    core_build_id(identity.upstream_commit, identity.patch_series_hash, identity.codegen_hash, identity.sources_hash, identity.profile_id,
                  identity.core_build_id);
    return identity;
}

}  // namespace

const BuildIdentity &build_identity()
{
    static const BuildIdentity identity = compute();
    return identity;
}

}  // namespace p6_state
