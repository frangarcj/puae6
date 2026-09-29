#include <assert.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
typedef uint8_t uae_u8;
typedef uint16_t uae_u16;
typedef uint32_t uae_u32;
typedef uint64_t uae_u64;
#include "machdep/maccess.h"

static uint32_t calls, last_address, last_value;
static uint32_t memory_read(uint32_t address) { ++calls; last_address = address; return 42; }
static void memory_write(uint32_t address, uint32_t value) { ++calls; last_address = address; last_value = value; }

int main(void)
{
    unsigned char bytes[32];
    const uint64_t values[] = { 0, 1, UINT64_MAX, UINT64_C(0x0123456789abcdef), UINT64_C(0x8000000080008000) };
    for (unsigned offset = 0; offset < 16; ++offset) {
        for (unsigned n = 0; n < sizeof(values)/sizeof(values[0]); ++n) {
            for (unsigned width = 1; width <= 8; width *= 2) {
                memset(bytes, 0xa5, sizeof(bytes));
                void *p = bytes + offset;
                uint64_t value = values[n], actual;
                switch (width) {
                case 1: value = (uint8_t)value; do_put_mem_byte(p, (uint8_t)value); actual = do_get_mem_byte(p); break;
                case 2: value = (uint16_t)value; do_put_mem_word(p, (uint16_t)value); actual = do_get_mem_word(p); break;
                case 4: value = (uint32_t)value; do_put_mem_long(p, (uint32_t)value); actual = do_get_mem_long(p); break;
                default: do_put_mem_quad(p, value); actual = do_get_mem_quad(p); break;
                }
                assert(actual == value);
                for (unsigned i = 0; i < sizeof(bytes); ++i)
                    assert(bytes[i] == (i >= offset && i < offset + width ?
                        (unsigned char)(value >> ((width - 1 - (i - offset)) * 8)) : 0xa5));
            }
        }
    }
    /* Independently known byte-order vectors, including native-endian load. */
    const unsigned char vector[] = { 0x01, 0x23, 0x45, 0x67, 0x89, 0xab, 0xcd, 0xef };
    assert(do_get_mem_word(vector) == 0x0123);
    assert(do_get_mem_long(vector) == UINT32_C(0x01234567));
    assert(do_get_mem_quad(vector) == UINT64_C(0x0123456789abcdef));
    uint16_t native = 0;
    memcpy(&native, vector + 1, 2);
    assert(do_get_mem_word_unswapped(vector + 1) == native);
    assert(do_byteswap_16(0x0123) == 0x2301);
    assert(do_byteswap_32(UINT32_C(0x01234567)) == UINT32_C(0x67452301));
    assert(do_byteswap_64(UINT64_C(0x0123456789abcdef)) == UINT64_C(0xefcdab8967452301));
    /* ASan redzones ensure a load/store cannot touch bytes outside its width. */
    for (unsigned width = 1; width <= 8; width *= 2) {
        unsigned char *allocation = (unsigned char *)malloc(width + 1);
        assert(allocation); void *p = allocation + 1;
        switch (width) {
        case 1: do_put_mem_byte(p, 255); assert(do_get_mem_byte(p) == 255); break;
        case 2: do_put_mem_word(p, 65535); assert(do_get_mem_word(p) == 65535); break;
        case 4: do_put_mem_long(p, UINT32_MAX); assert(do_get_mem_long(p) == UINT32_MAX); break;
        default: do_put_mem_quad(p, UINT64_MAX); assert(do_get_mem_quad(p) == UINT64_MAX); break;
        }
        free(allocation);
    }
    uint32_t address = 10, value = 20;
    assert(call_mem_get_func(memory_read, address++) == 42);
    call_mem_put_func(memory_write, address++, value++);
    assert(calls == 2 && address == 12 && value == 21 && last_address == 11 && last_value == 20);
    for (uintptr_t p = 0; p < 16; ++p) assert(ALIGN_POINTER_TO32(p) == 3 - p % 4);
    return 0;
}
