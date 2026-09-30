// SPDX-License-Identifier: MIT
// Build probe only until the managed runner executes and checks these exports.
#include <cerrno>
#include <cstddef>
#include <cstdint>
#include <new>
#include <pthread.h>
#include "emulibc.h"

struct WbxRange { uint64_t start, size; };
struct WbxLayout {
	WbxRange elf, main_thread, alt_thread, sbrk, sealed, invis, plain, mmap;
};
extern "C" { extern WbxLayout __wbxsysinfo; }

static volatile uint64_t constructor_seed = 3;
struct Counter {
	uint64_t value;
	Counter() noexcept : value(constructor_seed + 40) {}
};
static Counter counter;
// Thread-specific data goes through musl's pthread_key, which reaches the sandbox
// thread via x28. Compiler TLS (thread_local) is forbidden in guests: XNU
// overwrites TPIDR_EL0 at every context switch, see docs/adr/waterbox-compiler-tls.md.
static pthread_key_t tls_key, probe_key;
static uint64_t tls_get() { return reinterpret_cast<uintptr_t>(pthread_getspecific(tls_key)); }
static void tls_set(uint64_t value)
{
	pthread_setspecific(tls_key, reinterpret_cast<void *>(static_cast<uintptr_t>(value)));
}
static uint8_t *ram, *rom, *output;
static uint64_t *allocated;

ECL_EXPORT uint64_t smoke_constructor() { return counter.value; }

ECL_EXPORT int smoke_init()
{
	if (ram) return -1;
	if (pthread_key_create(&tls_key, nullptr) != 0) return -3;
	if (pthread_key_create(&probe_key, nullptr) != 0) return -3;
	ram = alloc_plain<uint8_t>(16384);
	rom = alloc_sealed<uint8_t>(16384);
	output = alloc_invisible<uint8_t>(16384);
	allocated = new (std::nothrow) uint64_t(19);
	if (!ram || !rom || !output || !allocated) return -2;
	for (unsigned i = 0; i < 16384; ++i) {
		ram[i] = 0;
		rom[i] = static_cast<uint8_t>(i);
		output[i] = 0;
	}
	counter.value = 0;
	tls_set(7);
	return 0;
}

ECL_EXPORT uint64_t smoke_step(uint64_t input)
{
	if (!ram || !allocated) return UINT64_MAX;
	counter.value += input + 1;
	tls_set(tls_get() + 1);
	for (unsigned i = 0; i < 4; ++i)
		ram[i * 4096] = static_cast<uint8_t>(counter.value + i);
	output[0] = static_cast<uint8_t>(counter.value);
	return counter.value + tls_get() + *allocated + rom[13];
}

ECL_EXPORT int smoke_frame(uint64_t input, uint64_t *result, size_t capacity)
{
	if (!result || capacity < 6) return -1;
	if (!ram || !rom || !allocated || !output) return -2;
	uint64_t value = smoke_step(input);
	uint64_t packed = 0;
	for (unsigned i = 0; i < 4; ++i)
		packed |= static_cast<uint64_t>(ram[i * 4096]) << (i * 8);
	result[0] = value;
	result[1] = counter.value;
	result[2] = tls_get();
	result[3] = packed;
	result[4] = 0;
	for (unsigned i = 0; i < 16384; ++i) result[4] += rom[i];
	result[5] = *allocated;
	return 0;
}

ECL_EXPORT int smoke_poison(uint8_t byte)
{
	if (!output) return -1;
	for (unsigned i = 0; i < 16384; ++i) output[i] = byte;
	return 0;
}

ECL_EXPORT int smoke_capacity()
{
	if (!allocated) return -1;
	return alloc_plain<uint8_t>(16384 + 16) == nullptr ? 1 : -2;
}

ECL_EXPORT int smoke_memory_info(uint64_t *out, size_t capacity)
{
	if (!out || capacity < 10) return -1;
	const WbxRange ranges[] = { __wbxsysinfo.sbrk, __wbxsysinfo.sealed,
		__wbxsysinfo.invis, __wbxsysinfo.plain, __wbxsysinfo.mmap };
	for (size_t i = 0; i < 5; ++i) {
		out[i * 2] = ranges[i].start;
		out[i * 2 + 1] = ranges[i].size;
	}
	return 10;
}

ECL_EXPORT uintptr_t smoke_ram() { return reinterpret_cast<uintptr_t>(ram); }

// WB13: cross a host callback with canaries in every AAPCS64 callee-saved register.
// out[0..1] x18/x28 before, [2..3] after, [4..12] x19..x27 after, [13] callback result,
// [14..15] x29 before/after, [16..17] sp before/after, [18..25] d8..d15 after.
// x18 and x28 are only read: the guest ABI reserves both (-ffixed-x18 -ffixed-x28).
typedef uint64_t (*HostCallback)(uint64_t, uint64_t, uint64_t, uint64_t, uint64_t, uint64_t);
extern "C" void wbx_probe_abi(HostCallback callback, uint64_t *out);
asm(R"(
	.text
	.balign 4
	.global wbx_probe_abi
	.type wbx_probe_abi, %function
wbx_probe_abi:
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
	.size wbx_probe_abi, .-wbx_probe_abi
)");

ECL_EXPORT int smoke_abi_probe(HostCallback callback, uint64_t *out, size_t capacity)
{
	if (!callback || !out || capacity < 30) return -1;
	pthread_setspecific(probe_key, reinterpret_cast<void *>(0x7157));
	errno = 0x1234;
	out[26] = reinterpret_cast<uintptr_t>(pthread_getspecific(probe_key));
	out[28] = static_cast<uint64_t>(errno);
	wbx_probe_abi(callback, out);
	out[27] = reinterpret_cast<uintptr_t>(pthread_getspecific(probe_key));
	out[29] = static_cast<uint64_t>(errno);
	return 0;
}

// Same registers seen from a guest function entered while the host is inside a callback.
ECL_EXPORT int smoke_identity(uint64_t *out, size_t capacity)
{
	if (!out || capacity < 3) return -1;
	uint64_t context;
	__asm__ volatile("mov %0, x28" : "=r"(context));
	out[0] = context;
	out[1] = reinterpret_cast<uintptr_t>(pthread_getspecific(probe_key));
	out[2] = static_cast<uint64_t>(errno);
	return 0;
}

// WB14: each buffer is one 16 KiB host group made of four 4 KiB logical pages. RAM is written
// by init (its group is dirty from the start); scratch is taken on demand from the second plain
// group, which init never touches, so its first write can be observed.
static uint8_t *scratch;
static uint8_t *buffer_at(uint64_t which) { return which == 0 ? ram : which == 1 ? scratch : nullptr; }

ECL_EXPORT uintptr_t smoke_scratch()
{
	if (!scratch) scratch = alloc_plain<uint8_t>(16384);
	return reinterpret_cast<uintptr_t>(scratch);
}

ECL_EXPORT int smoke_fill(uint64_t which, uint64_t seed)
{
	uint8_t *buffer = buffer_at(which);
	if (!buffer) return -1;
	for (unsigned i = 0; i < 16384; ++i) buffer[i] = static_cast<uint8_t>(i * 31 + seed);
	return 0;
}

ECL_EXPORT int smoke_touch(uint64_t which, uint64_t page, uint64_t offset, uint64_t value)
{
	uint8_t *buffer = buffer_at(which);
	if (!buffer) return -1;
	if (page >= 4 || offset >= 4096) return -2;
	buffer[page * 4096 + offset] = static_cast<uint8_t>(value);
	return 0;
}

ECL_EXPORT uint64_t smoke_block_base() { return __wbxsysinfo.elf.start; }
