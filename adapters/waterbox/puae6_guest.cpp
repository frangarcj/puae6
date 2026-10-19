// PUAE6 guest for Waterbox (T19b): the exports the host calls, over the same p6_* ABI the headless and libretro adapters use. There is
// no scheduler here: p6w_frame_advance makes exactly one p6_run_frame call. See p6w.h for who owns which pointer.
//
// Lifecycle (docs/03-waterbox.md): the sandbox is created and the constructors run; the host calls p6w_init, which copies the firmware
// and the media into the sealed heap and initializes the configured machine, A500 or A600 (p6_init + p6_load); the host seals; every unit is p6w_frame_advance.
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
        sizeof(p6_frame), sizeof(p6_input), sizeof(p6_config), offsetof(p6_frame, audio), offsetof(p6_frame, master_ticks),
        sizeof(p6w_memory_area), offsetof(p6w_memory_area, name), offsetof(p6w_memory_area, size), offsetof(p6w_memory_area, flags),
        sizeof(p6w_media_event), offsetof(p6w_media_event, event), offsetof(p6w_media_event, result), offsetof(p6w_media_event, error),
        sizeof(p6w_media_write_status_args), offsetof(p6w_media_write_status_args, status), offsetof(p6w_media_write_status_args, result), offsetof(p6w_media_write_status_args, error),
        sizeof(p6w_media_export_args), offsetof(p6w_media_export_args, buffer), offsetof(p6w_media_export_args, written), offsetof(p6w_media_export_args, result), offsetof(p6w_media_export_args, error)};
    if (!out || capacity < P6W_LAYOUT_VALUES) return 0;
    memcpy(out, values, sizeof values);
    return P6W_LAYOUT_VALUES;
}

namespace {

uint64_t memory_refused;       // accesses to the machine's memory that p6w_memory_access did not do (diagnostic: the access hooks cannot report)

// The one place where the host reaches the machine's memory. The region is asked for at every call: the engine owns its RAM and a pointer
// kept across a unit could dangle.
void memory_access(uint32_t type, void *buffer, int64_t address, int64_t count, bool write)
{
    p6_memory_region regions[P6W_MAX_AREAS];
    uint32_t n = 0;
    const p6_memory_region *region = nullptr;
    if (p6_memory_regions(regions, P6W_MAX_AREAS, &n, nullptr) == P6_OK)
        for (uint32_t i = 0; i < n; ++i)
            if (regions[i].type == type) region = &regions[i];
    const bool usable = buffer && count >= 0;       // a buffer of `count` bytes is the host's side of the contract
    const bool inside = region && usable && address >= 0 && uint64_t(address) <= region->bytes && uint64_t(count) <= region->bytes - uint64_t(address);
    if (!inside || (write && !(region->flags & P6_REGION_WRITABLE))) {
        ++memory_refused;
        if (!write && usable) memset(buffer, 0, size_t(count));
        return;
    }
    uint8_t *bytes = static_cast<uint8_t *>(const_cast<void *>(region->data)) + address;
    if (write) memcpy(bytes, buffer, size_t(count));
    else memcpy(buffer, bytes, size_t(count));
}

// One function per area, because BizHawk's hook has no area argument.
void access_chip_ram(void *buffer, int64_t address, int64_t count, bool write) { memory_access(P6_REGION_CHIP_RAM, buffer, address, count, write); }
void access_slow_ram(void *buffer, int64_t address, int64_t count, bool write) { memory_access(P6_REGION_SLOW_RAM, buffer, address, count, write); }
void access_fast_ram(void *buffer, int64_t address, int64_t count, bool write) { memory_access(P6_REGION_FAST_RAM, buffer, address, count, write); }
void access_z3_ram(void *buffer, int64_t address, int64_t count, bool write) { memory_access(P6_REGION_Z3_FAST_RAM, buffer, address, count, write); }
void access_rom(void *buffer, int64_t address, int64_t count, bool write) { memory_access(P6_REGION_ROM, buffer, address, count, write); }

struct AreaSpec {
    uint32_t type;
    const char *name;
    int64_t flags;
    void (*access)(void *, int64_t, int64_t, bool);
};
const AreaSpec area_specs[] = {
    {P6_REGION_CHIP_RAM, "Chip RAM", P6W_AREA_PRIMARY, access_chip_ram},     // the one BizHawk treats as main memory
    {P6_REGION_SLOW_RAM, "Slow RAM", 0, access_slow_ram},
    {P6_REGION_FAST_RAM, "Fast RAM", 0, access_fast_ram},      // T49: the Zorro II board, at $200000 once the Kickstart has configured it
    {P6_REGION_Z3_FAST_RAM, "Z3 Fast RAM", 0, access_z3_ram},
    {P6_REGION_ROM, "ROM", 0, access_rom},
};

}  // namespace

// The memory areas of the loaded machine (see p6w_memory_area): Chip RAM first and primary, Slow RAM, Fast RAM and the ROM when the machine has
// them, all big endian with 16-bit words. Returns how many were written; zero (and nothing written) when no machine is loaded or
// `capacity` is too small. Call it after p6w_init_machine and before the seal: the areas are function hooks and stay valid for the life of the sandbox.
ECL_EXPORT size_t p6w_memory_areas(p6w_memory_area *out, size_t capacity)
{
    if (!out || capacity < P6W_MAX_AREAS) return 0;
    p6_memory_region regions[P6W_MAX_AREAS];
    uint32_t n = 0;
    if (p6_memory_regions(regions, P6W_MAX_AREAS, &n, nullptr) != P6_OK) return 0;
    size_t written = 0;
    for (const AreaSpec &spec : area_specs)
        for (uint32_t i = 0; i < n; ++i)
            if (regions[i].type == spec.type) {
                const int64_t writable = (regions[i].flags & P6_REGION_WRITABLE) ? P6W_AREA_WRITABLE : 0;
                out[written++] = p6w_memory_area{reinterpret_cast<const void *>(spec.access), spec.name, int64_t(regions[i].bytes),
                                                 spec.flags | writable | P6W_AREA_BIG_ENDIAN | P6W_AREA_WORD_SIZE_2 | P6W_AREA_FUNCTION_HOOK};
            }
    return written;
}

// The same access the hooks do, by area type (P6_REGION_*), for hosts that cannot call a function pointer of the guest.
ECL_EXPORT void p6w_memory_access(uint32_t type, void *buffer, int64_t address, int64_t count, uint32_t write)
{
    memory_access(type, buffer, address, count, write != 0);
}

// How many accesses were refused (out of range, a write to the ROM or to nothing, no machine) since the sandbox was created.
ECL_EXPORT uint64_t p6w_memory_refused(void) { return memory_refused; }

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
        // A hard file is not copied into the sealed heap (T29d): it can be tens of MiB, p6_load copies it into the blob store (the mmap heap, where the base and the room for its write
        // overlay are the budget of the sandbox) and the bridge keeps no pointer to the buffer it was given. The sealed heap is for what is never patched and stays small.
        const void *copy = given.type == P6_MEDIA_HDF ? given.data : seal_copy(given.data, given.bytes);
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

// A media event for the next boundary (docs/02-contracts.md): mount a disk that p6w_init was given, or eject one. The bridge validates it and keeps the
// queue and the last sequence number in the sandbox, so they are part of a snapshot.
ECL_EXPORT int32_t p6w_media_event_enqueue(p6w_media_event *call)
{
    if (!call) return P6_ERROR_BAD_ARGUMENT;
    call->error[0] = 0;
    call->result = P6_OK;
    if (call->abi_version != P6W_ABI_VERSION || call->struct_size != sizeof *call) {
        set_error(call->error, "p6w_media_event has the wrong ABI version or size");
        return call->result = P6_ERROR_BAD_ARGUMENT;
    }
    p6_error error = {P6_ABI_VERSION, sizeof error, {}};
    call->result = p6_media_event_enqueue(&call->event, &error);
    if (call->result != P6_OK) set_error(call->error, error.message);
    return call->result;
}

// The write overlay (T28): its status and the export of a disk. The overlay is memory of the sandbox, captured by the snapshot, so these read what a restore puts back;
// nothing here writes to the host except the bytes of the export into the host's own buffer.
ECL_EXPORT int32_t p6w_media_write_status(p6w_media_write_status_args *call)
{
    if (!call) return P6_ERROR_BAD_ARGUMENT;
    call->error[0] = 0;
    call->result = P6_OK;
    if (call->abi_version != P6W_ABI_VERSION || call->struct_size != sizeof *call) {
        set_error(call->error, "p6w_media_write_status has the wrong ABI version or size");
        return call->result = P6_ERROR_BAD_ARGUMENT;
    }
    p6_error error = {P6_ABI_VERSION, sizeof error, {}};
    call->result = p6_media_write_status_get(&call->status, &error);
    if (call->result != P6_OK) set_error(call->error, error.message);
    return call->result;
}

ECL_EXPORT int32_t p6w_media_export(p6w_media_export_args *call)
{
    if (!call) return P6_ERROR_BAD_ARGUMENT;
    call->error[0] = 0;
    call->result = P6_OK;
    call->written = 0;
    if (call->abi_version != P6W_ABI_VERSION || call->struct_size != sizeof *call) {
        set_error(call->error, "p6w_media_export has the wrong ABI version or size");
        return call->result = P6_ERROR_BAD_ARGUMENT;
    }
    p6_error error = {P6_ABI_VERSION, sizeof error, {}};
    call->result = p6_media_export(call->media_index, call->buffer, call->capacity, &call->written, &error);
    if (call->result != P6_OK) set_error(call->error, error.message);
    return call->result;
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
