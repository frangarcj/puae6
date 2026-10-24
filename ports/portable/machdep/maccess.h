/* PUAE6 portable implementation of WinUAE 6030's machdep/maccess.h contract.
 * Engine callers supply uae_u8/u16/u32/u64 with the exact widths below.
 * SPDX-License-Identifier: GPL-2.0-or-later
 */
#ifndef P6_MACCESS_H
#define P6_MACCESS_H
#include <limits.h>
#include <stdint.h>
#include <string.h>

#if defined(__cplusplus)
static_assert(CHAR_BIT == 8 && sizeof(uae_u8) == 1 && sizeof(uae_u16) == 2 &&
              sizeof(uae_u32) == 4 && sizeof(uae_u64) == 8, "unsupported UAE widths");
#else
_Static_assert(CHAR_BIT == 8 && sizeof(uae_u8) == 1 && sizeof(uae_u16) == 2 &&
               sizeof(uae_u32) == 4 && sizeof(uae_u64) == 8, "unsupported UAE widths");
#endif

/* Raw engine memory: the caller owns bounds/lifetime. Byte accesses accept
 * unaligned addresses and alias any object; no typed dereference is performed. */
static inline uae_u32 do_get_mem_byte(const void *address)
{ return *(const unsigned char *)address; }
static inline void do_put_mem_byte(void *address, uae_u8 value)
{ *(unsigned char *)address = value; }

#define P6_MEMORY_ACCESS(name, type, bytes) \
static inline type do_get_mem_##name(const void *address) \
{ \
    const unsigned char *p = (const unsigned char *)address; \
    type value = 0; \
    for (unsigned i = 0; i < bytes; ++i) value = (type)((value << 8) | p[i]); \
    return value; \
} \
static inline void do_put_mem_##name(void *address, type value) \
{ \
    unsigned char *p = (unsigned char *)address; \
    for (unsigned i = 0; i < bytes; ++i) { \
        p[bytes - 1 - i] = (unsigned char)value; \
        value = (type)(value >> 8); \
    } \
}
P6_MEMORY_ACCESS(word, uae_u16, 2)
P6_MEMORY_ACCESS(long, uae_u32, 4)
P6_MEMORY_ACCESS(quad, uae_u64, 8)
#undef P6_MEMORY_ACCESS

static inline uae_u16 do_byteswap_16(uae_u16 value)
{ return (uae_u16)((value << 8) | (value >> 8)); }
static inline uae_u32 do_byteswap_32(uae_u32 value)
{ return ((uae_u32)do_byteswap_16((uae_u16)value) << 16) | do_byteswap_16((uae_u16)(value >> 16)); }
static inline uae_u64 do_byteswap_64(uae_u64 value)
{ return ((uae_u64)do_byteswap_32((uae_u32)value) << 32) | do_byteswap_32((uae_u32)(value >> 32)); }

/* Used only when upstream dispatch explicitly selects native-endian opcodes. */
static inline uae_u32 do_get_mem_word_unswapped(const void *address)
{ uae_u16 value; memcpy(&value, address, sizeof(value)); return value; }

/* Preserve the upstream mask semantics: bytes to address ending in binary 11. */
#define ALIGN_POINTER_TO32(p) ((~(uintptr_t)(p)) & (uintptr_t)3)
#define call_mem_get_func(func, addr) ((*(func))(addr))
#define call_mem_put_func(func, addr, value) ((*(func))(addr, value))
#endif
