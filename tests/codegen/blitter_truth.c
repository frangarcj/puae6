/* All 256 three-input minterms, every input bit combination. No engine stubs. */
#include <stdint.h>
#include <stdio.h>
typedef uint32_t uae_u32;
typedef uint8_t uae_u8;
#include <blit.h>
int main(void)
{
    for (unsigned mt = 0; mt < 256; ++mt) {
        for (unsigned bits = 0; bits < 8; ++bits) {
            uint32_t a = (bits & 4) ? UINT32_MAX : 0;
            uint32_t b = (bits & 2) ? UINT32_MAX : 0;
            uint32_t c = (bits & 1) ? UINT32_MAX : 0;
            uint32_t expected = (mt & (1u << bits)) ? UINT32_MAX : 0;
            if (blit_func(a, b, c, (uae_u8)mt) != expected) {
                fprintf(stderr, "Wrong minterm %u, inputs %u\n", mt, bits);
                return 1;
            }
        }
    }
    return 0;
}
