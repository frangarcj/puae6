// SPDX-License-Identifier: MIT
// Build probe only until the managed runner executes and checks these exports.
#include <cstddef>
#include <cstdint>
#include <new>
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
static thread_local uint64_t tls_counter = 7;
static uint8_t *ram, *rom, *output;
static uint64_t *allocated;

ECL_EXPORT uint64_t smoke_constructor() { return counter.value; }

ECL_EXPORT int smoke_init()
{
	if (ram) return -1;
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
	tls_counter = 7;
	return 0;
}

ECL_EXPORT uint64_t smoke_step(uint64_t input)
{
	if (!ram || !allocated) return UINT64_MAX;
	counter.value += input + 1;
	++tls_counter;
	for (unsigned i = 0; i < 4; ++i)
		ram[i * 4096] = static_cast<uint8_t>(counter.value + i);
	output[0] = static_cast<uint8_t>(counter.value);
	return counter.value + tls_counter + *allocated + rom[13];
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
	result[2] = tls_counter;
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
