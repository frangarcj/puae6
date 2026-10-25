/* SPDX-License-Identifier: MIT
 * Run only through tools/ci/linux_waterbox_probe.py, which authenticates the
 * runtime/ELF/tool inputs before exec and hashes them again afterwards. These
 * declarations mirror captured src/cinterface.rs, not a mock or C++ engine ABI.
 */
#define _POSIX_C_SOURCE 200809L
#include <dlfcn.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>
#if !defined(__linux__) || !defined(__x86_64__)
#error This probe requires a real Linux x86_64 process
#endif
struct Result { unsigned char error[1024]; uintptr_t data; };
struct Layout { size_t sbrk, sealed, invisible, plain, mmap; };
struct Blob { unsigned char *data; size_t size, pos; uint64_t seed; };
typedef intptr_t (*Read)(uintptr_t, void *, size_t);
typedef int (*Write)(uintptr_t, const void *, size_t);
typedef uint64_t (*Callback)(uint64_t, uint64_t, uint64_t, uint64_t, uint64_t, uint64_t);
struct Api {
    void (*create)(const struct Layout *, const char *, Read, uintptr_t, struct Result *);
    void (*destroy)(uintptr_t, struct Result *), (*activate)(uintptr_t, struct Result *);
    void (*deactivate)(uintptr_t, struct Result *), (*seal)(uintptr_t, struct Result *);
    void (*proc)(uintptr_t, const char *, struct Result *);
    void (*save)(uintptr_t, Write, uintptr_t, struct Result *);
    void (*load)(uintptr_t, Read, uintptr_t, struct Result *);
    void (*callback)(uintptr_t, Callback, uintptr_t, struct Result *);
    void (*pages)(uintptr_t, struct Result *), (*page)(uintptr_t, uintptr_t, struct Result *);
    void (*evict)(unsigned char);
};
static struct Api api;
static unsigned assertions, callbacks, frames, snapshots, rejections;
static void require(int ok, const char *why)
{
    ++assertions; if (!ok) { fprintf(stderr, "WB01 FAIL: %s\n", why); exit(1); }
}
static uintptr_t checked(struct Result *r) { require(!r->error[0], (const char *)r->error); return r->data; }
static void action(void (*f)(uintptr_t, struct Result *), uintptr_t h)
{ struct Result r = {0}; f(h, &r); (void)checked(&r); }
static intptr_t read_bytes(uintptr_t user, void *out, size_t n)
{
    struct Blob *b = (void *)user;
    if (b->pos > b->size) return -1;
    if (n > b->size - b->pos) n = b->size - b->pos;
    memcpy(out, b->data + b->pos, n); b->pos += n; return (intptr_t)n;
}
static int write_bytes(uintptr_t user, const void *data, size_t n)
{
    struct Blob *b = (void *)user;
    if (b->size > 64 * 1024 * 1024 || n > 64 * 1024 * 1024 - b->size) return -1;
    void *p = realloc(b->data, b->size + n); if (!p && n) return -1;
    b->data = p; memcpy(b->data + b->size, data, n); b->size += n; return 0;
}
static uintptr_t proc(uintptr_t h, const char *name)
{ struct Result r = {0}; api.proc(h, name, &r); uintptr_t p = checked(&r); require(p != 0, name); return p; }
static uint64_t callback(uint64_t a, uint64_t b, uint64_t c, uint64_t d, uint64_t e, uint64_t f)
{ ++callbacks; return a + b * 3 + c * 5 + d * 7 + e * 11 + f * 13; }
typedef int (*Init)(uint64_t);
typedef int (*Frame)(uint64_t, uint64_t *, size_t);
typedef int (*Observe)(uint64_t *, size_t);
typedef int (*Abi)(uintptr_t, uint64_t *, size_t);
typedef int (*Status)(void);
typedef uint64_t (*Value)(void);
typedef int (*Touch)(uint64_t, uint64_t, uint64_t);
typedef uint64_t (*Byte)(uint64_t, uint64_t);
static uintptr_t make(struct Blob *image, uint64_t seed, uint64_t *context)
{
    struct Layout layout = {1 << 20, 4096, 4096, 32768, 1 << 20};
    struct Result r = {0}; image->pos = 0;
    api.create(&layout, "p6-linux-smoke.wbx", read_bytes, (uintptr_t)image, &r);
    uintptr_t h = checked(&r); require(h && image->pos == image->size, "ELF fully consumed");
    action(api.activate, h);
    require(((Init)proc(h, "linux_init"))(seed) == 0, "init / real musl constructor and allocation");
    struct Blob premature = {0};
    api.save(h, write_bytes, (uintptr_t)&premature, &r); free(premature.data);
    require(r.error[0] != 0, "save before seal refused");
    r = (struct Result){0}; api.callback(h, callback, 0, &r); uintptr_t cb = checked(&r);
    uint64_t out[17]; for (unsigned i = 0; i < 17; ++i) out[i] = UINT64_C(0xcafe123456789abc);
    require(((Abi)proc(h, "linux_abi"))(cb, out + 1, 14) == -1, "ABI capacity refusal");
    require(out[1] == UINT64_C(0xcafe123456789abc), "ABI refusal did not write");
    require(((Abi)proc(h, "linux_abi"))(cb, out + 1, 15) == 0, "real callback ABI");
    require(out[0] == UINT64_C(0xcafe123456789abc) && out[16] == out[0], "ABI canaries");
    for (unsigned i = 0; i < 6; ++i) require(out[i + 1] == 0xb001 + i, "SysV nonvolatile register");
    require(out[7] == 3111 && out[8] && out[8] == out[9], "callback result and GS context");
    require(out[10] == 7 && out[11] == 0x1234 && out[12] == UINT64_MAX && out[13] == 9 && out[14] == 7 && out[15] == 43, "TLS/errno survive callback and real close syscall");
    *context = out[8]; require(((Status)proc(h, "linux_capacity"))() == 1, "allocator capacity refusal");
    r = (struct Result){0}; api.proc(h, "nonexistent_export", &r); require(checked(&r) == 0, "missing export is null");
    action(api.seal, h); action(api.deactivate, h); return h;
}
static struct Blob save(uintptr_t h, uint64_t seed)
{
    struct Blob b = {0}; b.seed = seed; struct Result r = {0}; action(api.activate, h);
    api.save(h, write_bytes, (uintptr_t)&b, &r); (void)checked(&r);
    action(api.deactivate, h); require(b.size > 0, "snapshot is nonempty"); ++snapshots; return b;
}
static void equal(const struct Blob *a, const struct Blob *b, const char *why)
{ require(a->size == b->size && !memcmp(a->data, b->data, a->size), why); }
/* This owner/seed envelope is checked before touching the raw runtime loader,
 * whose captured API explicitly warns that malformed raw loads may poison it. */
static int load(uintptr_t h, uint64_t seed, struct Blob *b)
{
    if (b->seed != seed || !b->size || b->size > 64 * 1024 * 1024) { ++rejections; return -1; }
    b->pos = 0; struct Result r = {0}; action(api.activate, h);
    api.load(h, read_bytes, (uintptr_t)b, &r); (void)checked(&r);
    action(api.deactivate, h); require(b->pos == b->size, "snapshot fully consumed"); return 0;
}
static void check_frame(const uint64_t *out, uint64_t counter, uint64_t steps, uint64_t seed)
{
    uint64_t want[12] = {counter, 7 + steps, 0, 0, 0, 0, 0, 19, steps, seed, 0xa5, 0x5a};
    for (unsigned i = 0; i < 4096; ++i) want[3] += (uint8_t)(i * 7 + seed);
    for (unsigned i = 0; i < 4; ++i) {
        want[2] += (uint8_t)(counter + i);
        uint32_t pixel = 0xff000000u | (uint32_t)((counter + i) & 255) * 0x010101u;
        want[4 + i / 2] |= (uint64_t)pixel << (32 * (i % 2));
        want[6] |= (uint64_t)(uint16_t)((int)((counter * 17 + i * 13) % 2048) - 1024) << (16 * i);
    }
    require(!memcmp(out, want, sizeof want), "known counter/RAM/ROM/video/PCM model");
}
static void frame(uintptr_t h, uint64_t input, uint64_t counter, uint64_t steps)
{
    uint64_t out[14]; for (unsigned i = 0; i < 14; ++i) out[i] = UINT64_C(0xd00dcafe12345678);
    action(api.activate, h); Frame f = (Frame)proc(h, "linux_frame");
    require(f(input, out + 1, 11) == -1 && out[1] == out[0], "frame capacity refusal before mutation");
    require(f(input, out + 1, 12) == 0 && out[0] == out[13], "frame and output canaries");
    check_frame(out + 1, counter, steps, 3); action(api.deactivate, h); ++frames;
}
static void pages(uintptr_t h)
{
    action(api.activate, h); uintptr_t base = ((Value)proc(h, "linux_base"))();
    uintptr_t scratch = ((Value)proc(h, "linux_scratch"))(); struct Result r = {0};
    api.pages(h, &r); uintptr_t count = checked(&r); require(scratch >= base && !(scratch & 4095), "guest scratch page alignment");
    uintptr_t index = (scratch - base) / 4096; require(index + 3 < count, "guest page inventory");
    for (unsigned i = 0; i < 4; ++i) { api.page(h, index + i, &r); unsigned flags = checked(&r) & 255; require((flags & 3) == 3 && !(flags & 0xc0), "scratch initially clean, captured RW"); }
    require(((Touch)proc(h, "linux_touch"))(4, 0, 1) == -1, "touch page bound");
    require(((Touch)proc(h, "linux_touch"))(0, 4096, 1) == -1, "touch offset bound");
    require(((Touch)proc(h, "linux_touch"))(2, 123, 0x6d) == 0, "touch one logical page");
    for (unsigned i = 0; i < 4; ++i) { api.page(h, index + i, &r); require(((checked(&r) & 0x80) != 0) == (i == 2), "Linux4K dirty tracking only target page"); }
    api.page(h, count, &r); require(r.error[0] != 0, "page index capacity refused");
    action(api.deactivate, h);
}
static struct Blob image_file(const char *path)
{
    require(path[0] == '/', "absolute authenticated input path required");
    struct stat st; require(!lstat(path, &st) && S_ISREG(st.st_mode) && st.st_size > 0 && st.st_size <= 64 * 1024 * 1024, "regular bounded ELF input");
    FILE *f = fopen(path, "rb"); require(f != NULL, "open guest ELF");
    struct Blob b = {0}; b.size = (size_t)st.st_size; b.data = malloc(b.size); require(b.data != NULL, "host image allocation");
    require(fread(b.data, 1, b.size, f) == b.size && !fclose(f), "read complete guest ELF"); return b;
}
int main(int argc, char **argv)
{
    require(argc == 3 && argv[1][0] == '/', "usage: linux_probe /authenticated/runtime.so /authenticated/guest.wbx");
    require(sysconf(_SC_PAGESIZE) == 4096 && sizeof(uintptr_t) == 8 && sizeof(struct Result) == 1032 && sizeof(struct Layout) == 40, "Linux x86_64/4K C API layouts");
    struct stat st; require(!lstat(argv[1], &st) && S_ISREG(st.st_mode), "regular authenticated runtime path");
    struct Blob image = image_file(argv[2]); void *library = dlopen(argv[1], RTLD_NOW | RTLD_LOCAL); require(library != NULL, "dlopen authenticated runtime");
#define BIND(member, name) do { void *p = dlsym(library, name); require(p != NULL, name); _Static_assert(sizeof api.member == sizeof p, "function pointer layout"); memcpy(&api.member, &p, sizeof p); } while (0)
    BIND(create, "wbx_create_host"); BIND(destroy, "wbx_destroy_host"); BIND(activate, "wbx_activate_host"); BIND(deactivate, "wbx_deactivate_host");
    BIND(seal, "wbx_seal"); BIND(proc, "wbx_get_proc_addr"); BIND(save, "wbx_save_state"); BIND(load, "wbx_load_state");
    BIND(callback, "wbx_get_callback_addr"); BIND(pages, "wbx_get_page_len"); BIND(page, "wbx_get_page_data"); BIND(evict, "wbx_set_always_evict_blocks");
    api.evict(1); uint64_t ca, cb; uintptr_t a = make(&image, 3, &ca), b = make(&image, 9, &cb); require(ca != cb, "two independent live GS contexts");
    frame(a, 2, 3, 1); frame(a, 0, 4, 2); frame(a, 7, 12, 3);
    struct Blob baseline = save(a, 3), before = save(b, 9);
    require(load(b, 9, &baseline) == -1, "foreign seed envelope rejected before runtime load");
    struct Blob after = save(b, 9); equal(&before, &after, "rejection leaves complete B snapshot unchanged"); free(before.data); free(after.data);
    action(api.destroy, b); b = make(&image, 3, &cb); require(load(b, 3, &baseline) == 0, "load into second sandbox");
    before = save(b, 3); equal(&baseline, &before, "complete second-sandbox snapshot equals A"); free(before.data);
    pages(a); require(load(a, 3, &baseline) == 0, "same-sandbox restore");
    action(api.activate, a); require(((Byte)proc(a, "linux_scratch_byte"))(2, 123) == 0, "restore undoes dirty scratch byte"); action(api.deactivate, a);
    before = save(a, 3); equal(&baseline, &before, "complete same-sandbox snapshot equals baseline"); free(before.data);
    const uint64_t input[] = {3, 8, 1, 0}; uint64_t counter = 12;
    for (unsigned i = 0; i < 4; ++i) {
        before = save(b, 3); counter += input[i] + 1;
        frame(a, input[i], counter, 4 + i); after = save(b, 3); equal(&before, &after, "advancing A leaves B unchanged"); free(before.data); free(after.data);
        frame(b, input[i], counter, 4 + i); before = save(a, 3); after = save(b, 3); equal(&before, &after, "full future snapshot in alternating sandboxes"); free(before.data); free(after.data);
    }
    struct Blob future = save(a, 3); require(future.size != baseline.size || memcmp(future.data, baseline.data, future.size), "future state actually differs");
    require(load(a, 3, &baseline) == 0, "restore A for repeat"); counter = 12;
    for (unsigned i = 0; i < 4; ++i) { counter += input[i] + 1; frame(a, input[i], counter, 4 + i); }
    after = save(a, 3); equal(&future, &after, "full future snapshot repeats after restore"); free(after.data);
    free(future.data); free(baseline.data); action(api.destroy, b); action(api.destroy, a); free(image.data); require(!dlclose(library), "unload runtime");
    printf("P6_LINUX_WATERBOX_RESULT {\"schema_version\":1,\"status\":\"PASS\",\"scope\":\"WB01_MINICORE\",\"frames\":%u,\"callbacks\":%u,\"snapshots\":%u,\"mismatch_rejections\":%u,\"assertions\":%u,\"max_live_sandboxes\":2,\"sandboxes_created\":3,\"host_page_bytes\":4096}\n", frames, callbacks, snapshots, rejections, assertions);
    return 0;
}
