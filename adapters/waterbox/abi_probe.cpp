// WB13 inside puae6.wbx: crosses a host callback with canaries in every callee-saved register (AAPCS64 x19-x29, d8-d15, sp) and checks that
// the registers the Waterbox guest ABI reserves (x18, the Darwin platform register, and x28, the sandbox context musl reads its thread pointer
// from) and musl's own thread state (a thread-specific key and errno) come back unchanged. The same code as the T09 smoke guest, linked into
// the real guest so that the check covers this ELF, its libc and its assembly, not only a toy one.
//
// out[0..1] x18/x28 before, [2..3] after, [4..12] x19..x27 after, [13] callback result, [14..15] x29 before/after, [16..17] sp before/after,
// [18..25] d8..d15 after, [26..27] thread-specific value before/after, [28..29] errno before/after.
// x18 and x28 are only read: the guest ABI reserves both (-ffixed-x18 -ffixed-x28).
#include "emulibc.h"

#include <errno.h>
#include <pthread.h>
#include <stddef.h>
#include <stdint.h>

typedef uint64_t (*HostCallback)(uint64_t, uint64_t, uint64_t, uint64_t, uint64_t, uint64_t);
#if defined(__aarch64__)
extern "C" void p6w_probe_abi_registers(HostCallback callback, uint64_t *out);
asm(R"(
	.text
	.balign 4
	.global p6w_probe_abi_registers
	.type p6w_probe_abi_registers, %function
p6w_probe_abi_registers:
	stp x29, x30, [sp, #-16]!
	mov x29, sp
	stp d8, d9, [sp, #-16]!
	stp d10, d11, [sp, #-16]!
	stp d12, d13, [sp, #-16]!
	stp d14, d15, [sp, #-16]!
	stp x19, x20, [sp, #-16]!
	stp x21, x22, [sp, #-16]!
	stp x23, x24, [sp, #-16]!
	stp x25, x26, [sp, #-16]!
	stp x27, x1, [sp, #-16]!
	mov x9, x0
	str x18, [x1, #0]
	str x28, [x1, #8]
	str x29, [x1, #112]
	mov x10, sp
	str x10, [x1, #128]
	movz x19, #0xA113
	movz x20, #0xA114
	movz x21, #0xA115
	movz x22, #0xA116
	movz x23, #0xA117
	movz x24, #0xA118
	movz x25, #0xA119
	movz x26, #0xA11A
	movz x27, #0xA11B
	movz x10, #0xD808
	fmov d8, x10
	movz x10, #0xD809
	fmov d9, x10
	movz x10, #0xD80A
	fmov d10, x10
	movz x10, #0xD80B
	fmov d11, x10
	movz x10, #0xD80C
	fmov d12, x10
	movz x10, #0xD80D
	fmov d13, x10
	movz x10, #0xD80E
	fmov d14, x10
	movz x10, #0xD80F
	fmov d15, x10
	movz x0, #0x11
	movz x1, #0x22
	movz x2, #0x33
	movz x3, #0x44
	movz x4, #0x55
	movz x5, #0x66
	blr x9
	ldr x9, [sp, #8]
	str x0, [x9, #104]
	str x18, [x9, #16]
	str x28, [x9, #24]
	str x19, [x9, #32]
	str x20, [x9, #40]
	str x21, [x9, #48]
	str x22, [x9, #56]
	str x23, [x9, #64]
	str x24, [x9, #72]
	str x25, [x9, #80]
	str x26, [x9, #88]
	str x27, [x9, #96]
	str x29, [x9, #120]
	mov x10, sp
	str x10, [x9, #136]
	fmov x10, d8
	str x10, [x9, #144]
	fmov x10, d9
	str x10, [x9, #152]
	fmov x10, d10
	str x10, [x9, #160]
	fmov x10, d11
	str x10, [x9, #168]
	fmov x10, d12
	str x10, [x9, #176]
	fmov x10, d13
	str x10, [x9, #184]
	fmov x10, d14
	str x10, [x9, #192]
	fmov x10, d15
	str x10, [x9, #200]
	ldp x27, x1, [sp], #16
	ldp x25, x26, [sp], #16
	ldp x23, x24, [sp], #16
	ldp x21, x22, [sp], #16
	ldp x19, x20, [sp], #16
	ldp d14, d15, [sp], #16
	ldp d12, d13, [sp], #16
	ldp d10, d11, [sp], #16
	ldp d8, d9, [sp], #16
	ldp x29, x30, [sp], #16
	ret
	.size p6w_probe_abi_registers, .-p6w_probe_abi_registers
)");

namespace {
pthread_key_t key;
bool key_made;
}  // namespace

ECL_EXPORT int32_t p6w_probe_abi(HostCallback callback, uint64_t *out, size_t capacity)
{
    if (!callback || !out || capacity < 30) return -1;
    if (!key_made) {
        if (pthread_key_create(&key, nullptr)) return -2;
        key_made = true;
    }
    pthread_setspecific(key, reinterpret_cast<void *>(0x7157));
    errno = 0x1234;
    out[26] = reinterpret_cast<uintptr_t>(pthread_getspecific(key));
    out[28] = static_cast<uint64_t>(errno);
    p6w_probe_abi_registers(callback, out);
    out[27] = reinterpret_cast<uintptr_t>(pthread_getspecific(key));
    out[29] = static_cast<uint64_t>(errno);
    return 0;
}

// The same registers seen from a guest function entered while the host is inside a callback.
ECL_EXPORT int32_t p6w_identity(uint64_t *out, size_t capacity)
{
    if (!out || capacity < 3) return -1;
    uint64_t context;
    __asm__ volatile("mov %0, x28" : "=r"(context));
    out[0] = context;
    out[1] = reinterpret_cast<uintptr_t>(pthread_getspecific(key));
    out[2] = static_cast<uint64_t>(errno);
    return 0;
}

#elif defined(__x86_64__)
// SysV64 layout: 15 uint64_t words, deliberately different from AAPCS64's 30.
// [0..1] GS:0x18 sandbox context before/after; [2] callback result;
// [3..8] RBX/RBP/R12/R13/R14/R15 after (B003/B005/B012/B013/B014/B015);
// [9..10] SP before/after; [11..12] pthread key before/after; [13..14] errno.
// SysV64 has no callee-saved SIMD registers. GS is read, never changed.
extern "C" void p6w_probe_abi_registers(HostCallback callback, uint64_t *out);
asm(R"(
    .text
    .balign 16
    .global p6w_probe_abi_registers
    .type p6w_probe_abi_registers, @function
p6w_probe_abi_registers:
    pushq %rbx
    pushq %rbp
    pushq %r12
    pushq %r13
    pushq %r14
    pushq %r15
    subq $24, %rsp
    movq %rdi, 0(%rsp)
    movq %rsi, 8(%rsp)
    movq %gs:0x18, %rax
    movq %rax, 0(%rsi)
    movq %rsp, 72(%rsi)
    movq $0xB003, %rbx
    movq $0xB005, %rbp
    movq $0xB012, %r12
    movq $0xB013, %r13
    movq $0xB014, %r14
    movq $0xB015, %r15
    movl $0x11, %edi
    movl $0x22, %esi
    movl $0x33, %edx
    movl $0x44, %ecx
    movl $0x55, %r8d
    movl $0x66, %r9d
    call *0(%rsp)
    movq 8(%rsp), %r10
    movq %rax, 16(%r10)
    movq %gs:0x18, %rax
    movq %rax, 8(%r10)
    movq %rbx, 24(%r10)
    movq %rbp, 32(%r10)
    movq %r12, 40(%r10)
    movq %r13, 48(%r10)
    movq %r14, 56(%r10)
    movq %r15, 64(%r10)
    movq %rsp, 80(%r10)
    addq $24, %rsp
    popq %r15
    popq %r14
    popq %r13
    popq %r12
    popq %rbp
    popq %rbx
    ret
    .size p6w_probe_abi_registers, .-p6w_probe_abi_registers
)");

namespace {
pthread_key_t key;
bool key_made;
}  // namespace

ECL_EXPORT int32_t p6w_probe_abi(HostCallback callback, uint64_t *out, size_t capacity)
{
    if (!callback || !out || capacity < 15) return -1;
    if (!key_made) {
        if (pthread_key_create(&key, nullptr)) return -2;
        key_made = true;
    }
    if (pthread_setspecific(key, reinterpret_cast<void *>(0x7157))) return -2;
    errno = 0x1234;
    out[11] = reinterpret_cast<uintptr_t>(pthread_getspecific(key));
    out[13] = static_cast<uint64_t>(errno);
    p6w_probe_abi_registers(callback, out);
    out[12] = reinterpret_cast<uintptr_t>(pthread_getspecific(key));
    out[14] = static_cast<uint64_t>(errno);
    return 0;
}

ECL_EXPORT int32_t p6w_identity(uint64_t *out, size_t capacity)
{
    if (!out || capacity < 3) return -1;
    uint64_t context;
    __asm__ volatile("movq %%gs:0x18, %0" : "=r"(context));
    out[0] = context;
    out[1] = reinterpret_cast<uintptr_t>(pthread_getspecific(key));
    out[2] = static_cast<uint64_t>(errno);
    return 0;
}
#else
#error Unsupported Waterbox ABI probe architecture
#endif
