// PUAE6 guest for Waterbox (T19b): the exports the host calls, over the same p6_* ABI the headless and libretro adapters use. There is
// no scheduler here: p6w_frame_advance makes exactly one p6_run_frame call. See p6w.h for who owns which pointer.
//
// Lifecycle (docs/03-waterbox.md): the sandbox is created and the constructors run; the host calls p6w_init, which copies the firmware
// and the media into the sealed heap and initializes the A500 (p6_init + p6_load); the host seals; every unit is p6w_frame_advance.
#include "emulibc.h"
#include "p6.h"
#include "p6w.h"

#include <stddef.h>
#include <stdio.h>
#include <string.h>

static_assert(sizeof(void *) == 8, "the guest ABI is LP64");

struct WbxRange { uint64_t start, size; };
struct WbxLayout { WbxRange elf, main_thread, alt_thread, sbrk, sealed, invis, plain, mmap; };
extern "C" { extern WbxLayout __wbxsysinfo; }

namespace {

bool loaded;        // p6_load succeeded: a second p6w_init is refused, like a second p6_load

void set_error(char *text, const char *message)
{
    snprintf(text, P6_ERROR_TEXT_CAPACITY, "%s", message);
}

// Copies the host's bytes into the sealed heap, where they live until the sandbox is disposed. The sealed heap can only be filled
// before the seal and is read-only after it, which is what a firmware and a disk that are never patched need.
const void *seal_copy(const void *data, uint64_t bytes)
{
    void *copy = alloc_sealed(size_t(bytes));
    if (!copy) return nullptr;
    memcpy(copy, data, size_t(bytes));
    return copy;
}

struct Fault {      // a class, like m68k_exception: the same typeinfo machinery as the core's
    int32_t code;
};

struct Witness {
    int32_t *destroyed;
    ~Witness() { ++*destroyed; }
};

// One frame of the recursion per level, each with a destructor to run while the exception unwinds through it.
__attribute__((noinline)) int32_t throw_at(int32_t depth, int32_t *destroyed)
{
    Witness witness{destroyed};
    if (depth == 0) throw Fault{42};            // as THROW(n) in the 68000 core
    return throw_at(depth - 1, destroyed) + 1;
}

}  // namespace

ECL_EXPORT uint32_t p6w_abi_version(void) { return P6W_ABI_VERSION; }

ECL_EXPORT size_t p6w_layout(uint64_t *out, size_t capacity)
{
    const uint64_t values[P6W_LAYOUT_VALUES] = {
        sizeof(p6w_init), offsetof(p6w_init, result), offsetof(p6w_init, error), offsetof(p6w_init, media), sizeof(p6w_media),
        sizeof(p6w_frame), offsetof(p6w_frame, frame), offsetof(p6w_frame, result), offsetof(p6w_frame, error),
        sizeof(p6_frame), sizeof(p6_input), sizeof(p6_config), offsetof(p6_frame, audio), offsetof(p6_frame, master_ticks)};
    if (!out || capacity < P6W_LAYOUT_VALUES) return 0;
    memcpy(out, values, sizeof values);
    return P6W_LAYOUT_VALUES;
}

// The ranges the runtime gave this sandbox, as (start, size) pairs: elf, main stack, alternate stack, sbrk, sealed, invisible, plain,
// mmap. The host uses them to measure what the machine really allocates.
ECL_EXPORT size_t p6w_memory_info(uint64_t *out, size_t capacity)
{
    if (!out || capacity < 16) return 0;
    const WbxRange *ranges = &__wbxsysinfo.elf;
    for (size_t i = 0; i < 8; ++i) {
        out[2 * i] = ranges[i].start;
        out[2 * i + 1] = ranges[i].size;
    }
    return 16;
}

// Diagnostic for the host: throws an exception object from `depth` frames down and catches it here, returning the thrown value and, in *destroyed, how
// many destructors the unwinder ran on the way up (depth + 1). The 68000 core models its bus errors with C++ exceptions (THROW in
// newcpu.cpp), so the unwinder of this sandbox has to work; the A500 profile does not reach that throw, hence this proof.
ECL_EXPORT int32_t p6w_probe_exception(int32_t depth, int32_t *destroyed)
{
    if (!destroyed || depth < 0 || depth > 64) return -1;
    *destroyed = 0;
    try {
        throw_at(depth, destroyed);
    } catch (const Fault &fault) {
        return fault.code;
    }
    return -2;
}

ECL_EXPORT int32_t p6w_init_machine(p6w_init *args)
{
    if (!args) return P6_ERROR_BAD_ARGUMENT;
    args->error[0] = 0;
    args->result = P6_OK;
    auto fail = [&](p6_result code, const char *message) {
        set_error(args->error, message);
        return args->result = code;
    };
    if (args->abi_version != P6W_ABI_VERSION || args->struct_size != sizeof *args)
        return fail(P6_ERROR_BAD_ARGUMENT, "p6w_init has the wrong ABI version or size");
    if (loaded) return fail(P6_ERROR_BAD_STATE, "a machine is already loaded");
    if (!args->firmware || !args->firmware_bytes) return fail(P6_ERROR_BAD_ARGUMENT, "no firmware was given");
    if (args->media_count > P6_MAX_MEDIA) return fail(P6_ERROR_BAD_ARGUMENT, "too many media");

    const void *firmware = seal_copy(args->firmware, args->firmware_bytes);
    if (!firmware) return fail(P6_ERROR_CAPACITY, "the sealed heap cannot hold the firmware");
    p6_media media[P6_MAX_MEDIA] = {};
    for (uint32_t i = 0; i < args->media_count; ++i) {
        const p6w_media &given = args->media[i];
        if (!given.data || !given.bytes) return fail(P6_ERROR_BAD_ARGUMENT, "a medium has no data");
        const void *copy = seal_copy(given.data, given.bytes);
        if (!copy) return fail(P6_ERROR_CAPACITY, "the sealed heap cannot hold the media");
        media[i].type = given.type;
        media[i].flags = given.flags;
        memcpy(media[i].content_id, given.content_id, sizeof media[i].content_id);
        media[i].image = p6_buffer{copy, given.bytes};
    }

    p6_error error = {P6_ABI_VERSION, sizeof error, {}};
    p6_result result = p6_init(&args->config, &error);
    if (result == P6_OK)
        result = p6_load(p6_buffer{firmware, args->firmware_bytes}, media, args->media_count, &error);
    if (result != P6_OK) {
        p6_shutdown(nullptr);
        set_error(args->error, error.message);
        return args->result = result;
    }
    loaded = true;
    return P6_OK;
}

ECL_EXPORT int32_t p6w_frame_advance(const p6_input *input, uint32_t render_flags, p6w_frame *out)
{
    if (!out) return P6_ERROR_BAD_ARGUMENT;
    out->error[0] = 0;
    out->result = P6_OK;
    if (out->abi_version != P6W_ABI_VERSION || out->struct_size != sizeof *out) {
        set_error(out->error, "p6w_frame has the wrong ABI version or size");
        return out->result = P6_ERROR_BAD_ARGUMENT;
    }
    p6_error error = {P6_ABI_VERSION, sizeof error, {}};
    out->frame = p6_frame{};
    out->frame.abi_version = P6_ABI_VERSION;
    out->frame.struct_size = sizeof out->frame;
    out->result = p6_run_frame(input, render_flags, &out->frame, &error);
    if (out->result != P6_OK) set_error(out->error, error.message);
    return out->result;
}

ECL_EXPORT int32_t p6w_reset(void)
{
    p6_error error = {P6_ABI_VERSION, sizeof error, {}};
    return p6_reset(&error);
}

ECL_EXPORT int32_t p6w_shutdown(void)
{
    if (loaded) p6_unload(nullptr);
    loaded = false;
    return p6_shutdown(nullptr);
}
