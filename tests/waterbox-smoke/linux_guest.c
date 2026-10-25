/* SPDX-License-Identifier: MIT
 * Own WB01 miniature, not an Amiga core. Captured musl _start initializes libc
 * and constructors; emulibc owns __wbxsysinfo and the real Waterbox allocators.
 */
#include <errno.h>
#include <pthread.h>
#include <stdint.h>
#include <stdlib.h>
#include <unistd.h>
#include "emulibc.h"

struct Range { uint64_t start, size; };
struct Layout { struct Range elf, main_thread, alt_thread, sbrk, sealed, invis, plain, mmap; };
extern struct Layout __wbxsysinfo;
static uint64_t constructed, counter, steps, seed;
static uint8_t *ram, *rom, *scratch;
static uint64_t *allocated;
static uint32_t video[4];
static int16_t pcm[4];
static pthread_key_t key;
__attribute__((constructor)) static void construct(void) { constructed = 43; }

ECL_EXPORT int linux_init(uint64_t initial_seed)
{
    if (ram || constructed != 43 || pthread_key_create(&key, NULL)) return -1;
    ram = alloc_plain(16384); scratch = alloc_plain(16384);
    rom = alloc_sealed(4096); allocated = malloc(sizeof *allocated);
    if (!ram || !scratch || !rom || !allocated) return -2;
    seed = initial_seed; *allocated = 19;
    for (unsigned i = 0; i < 16384; ++i) ram[i] = 0;
    ram[0] = 0xa5; ram[16383] = 0x5a;
    for (unsigned i = 0; i < 4096; ++i) rom[i] = (uint8_t)(i * 7 + seed);
    return pthread_setspecific(key, (void *)(uintptr_t)7);
}
ECL_EXPORT int linux_observe(uint64_t *out, size_t capacity)
{
    if (!out || capacity < 12 || !ram) return -1;
    out[0] = counter; out[1] = (uintptr_t)pthread_getspecific(key);
    out[2] = out[3] = 0;
    for (unsigned i = 0; i < 4; ++i) out[2] += ram[1 + i * 4096];
    for (unsigned i = 0; i < 4096; ++i) out[3] += rom[i];
    out[4] = video[0] | (uint64_t)video[1] << 32;
    out[5] = video[2] | (uint64_t)video[3] << 32;
    out[6] = 0;
    for (unsigned i = 0; i < 4; ++i) out[6] |= (uint64_t)(uint16_t)pcm[i] << (16 * i);
    out[7] = *allocated; out[8] = steps; out[9] = seed;
    out[10] = ram[0]; out[11] = ram[16383]; return 0;
}
ECL_EXPORT int linux_frame(uint64_t input, uint64_t *out, size_t capacity)
{
    if (!out || capacity < 12 || !ram || input > 255) return -1;
    counter += input + 1; ++steps;
    if (pthread_setspecific(key, (void *)((uintptr_t)pthread_getspecific(key) + 1))) return -2;
    for (unsigned i = 0; i < 4; ++i) {
        ram[1 + i * 4096] = (uint8_t)(counter + i);
        video[i] = 0xff000000u | (uint32_t)((counter + i) & 255) * 0x010101u;
        pcm[i] = (int16_t)((counter * 17 + i * 13) % 2048) - 1024;
    }
    return linux_observe(out, capacity);
}
ECL_EXPORT uint64_t linux_base(void) { return __wbxsysinfo.elf.start; }
ECL_EXPORT uint64_t linux_scratch(void) { return (uintptr_t)scratch; }
ECL_EXPORT int linux_touch(uint64_t page, uint64_t offset, uint64_t value)
{
    if (!scratch || page >= 4 || offset >= 4096 || value > 255) return -1;
    scratch[page * 4096 + offset] = (uint8_t)value; return 0;
}
ECL_EXPORT uint64_t linux_scratch_byte(uint64_t page, uint64_t offset)
{
    return !scratch || page >= 4 || offset >= 4096 ? UINT64_MAX : scratch[page * 4096 + offset];
}
ECL_EXPORT int linux_capacity(void) { return alloc_plain(32768 + 16) == NULL ? 1 : -1; }

/* Preserve SysV nonvolatiles across a real guest->host callback. RSP is aligned
 * before CALL; the original registers, including RBP, are restored on exit. */
typedef uint64_t (*Callback)(uint64_t, uint64_t, uint64_t, uint64_t, uint64_t, uint64_t);
extern void linux_registers(Callback callback, uint64_t *out);
__asm__(
    ".text\n.global linux_registers\n.type linux_registers,@function\nlinux_registers:\n"
    "push %rbp\npush %rbx\npush %r12\npush %r13\npush %r14\npush %r15\npush %rsi\n"
    "mov %rdi,%r11\nmov $0xb001,%rbx\nmov $0xb002,%r12\nmov $0xb003,%r13\n"
    "mov $0xb004,%r14\nmov $0xb005,%r15\nmov $0xb006,%rbp\n"
    "mov $17,%rdi\nmov $34,%rsi\nmov $51,%rdx\nmov $68,%rcx\nmov $85,%r8\nmov $102,%r9\n"
    "call *%r11\nmov (%rsp),%r10\nmov %rbx,0(%r10)\nmov %r12,8(%r10)\n"
    "mov %r13,16(%r10)\nmov %r14,24(%r10)\nmov %r15,32(%r10)\nmov %rbp,40(%r10)\nmov %rax,48(%r10)\n"
    "add $8,%rsp\npop %r15\npop %r14\npop %r13\npop %r12\npop %rbx\npop %rbp\nret\n"
    ".size linux_registers,.-linux_registers\n");
ECL_EXPORT int linux_abi(Callback callback, uint64_t *out, size_t capacity)
{
    if (!callback || !out || capacity < 15) return -1;
    uintptr_t before, after;
    __asm__ volatile("mov %%gs:0x18,%0" : "=r"(before));
    errno = 0x1234; linux_registers(callback, out);
    __asm__ volatile("mov %%gs:0x18,%0" : "=r"(after));
    out[7] = before; out[8] = after;
    out[9] = (uintptr_t)pthread_getspecific(key); out[10] = (unsigned)errno;
    out[11] = (uint64_t)(int64_t)close(-1); out[12] = (unsigned)errno;
    out[13] = (uintptr_t)pthread_getspecific(key); out[14] = constructed;
    return 0;
}
