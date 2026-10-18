// T35 evaluation candidate, not enabled in a product target. SPDX-License-Identifier: GPL-2.0-or-later
#ifndef P6_SIMD_RGB565_H
#define P6_SIMD_RGB565_H
#include <stddef.h>
#include <stdint.h>
#if defined(__aarch64__) && defined(__ARM_NEON) && !defined(P6_SIMD_NO_NEON)
#include <arm_neon.h>
#define P6_RGB565_NEON 1
#else
#define P6_RGB565_NEON 0
#endif
namespace p6_simd {
// The caller owns count naturally aligned uint32_t/uint16_t elements; the
// buffers do not overlap. No vector alignment or padding after them is needed.
inline void scalar(const uint32_t *in, size_t count, uint16_t *out)
{
    for (size_t i = 0; i < count; ++i) {
        const uint32_t p = in[i];
        out[i] = uint16_t(((p >> 16) & 255) / 8 * 2048 + ((p >> 8) & 255) / 4 * 32 + (p & 255) / 8);
    }
}
inline const char *backend(bool allow_simd) { return P6_RGB565_NEON && allow_simd ? "neon" : "scalar"; }
inline void convert(const uint32_t *in, size_t count, uint16_t *out, bool allow_simd = true)
{
    size_t i = 0;
#if P6_RGB565_NEON
    if (allow_simd) {
        for (; count - i >= 4; i += 4) {
            const uint32x4_t p = vld1q_u32(in + i);
            const uint32x4_t r = vandq_u32(vshrq_n_u32(p, 8), vdupq_n_u32(0xf800));
            const uint32x4_t g = vandq_u32(vshrq_n_u32(p, 5), vdupq_n_u32(0x07e0));
            const uint32x4_t b = vandq_u32(vshrq_n_u32(p, 3), vdupq_n_u32(0x001f));
            vst1_u16(out + i, vmovn_u32(vorrq_u32(vorrq_u32(r, g), b)));
        }
    }
#else
    (void)allow_simd;
#endif
    if (count != i) scalar(in + i, count - i, out + i);
}
} // namespace p6_simd
#endif
