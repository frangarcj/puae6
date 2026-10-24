// RGB565 presentation kernels. SPDX-License-Identifier: GPL-2.0-or-later
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
// T35c measured these compiler families on Linux ARM64. Other toolchains keep
// the portable loop; a compiler upgrade requires measuring its policy again.
#if defined(__linux__) && P6_RGB565_NEON && defined(__BYTE_ORDER__) && __BYTE_ORDER__ == __ORDER_LITTLE_ENDIAN__ && \
    ((defined(__clang__) && __clang_major__ == 18) || (!defined(__clang__) && defined(__GNUC__) && __GNUC__ == 13))
#define P6_RGB565_DEFAULT_NEON 1
#else
#define P6_RGB565_DEFAULT_NEON 0
#endif
namespace p6_simd {
// Naturally aligned, non-overlapping buffers. No vector alignment or padding.
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
// Keep the original expression: Apple Clang already vectorizes it efficiently.
inline void portable(const uint32_t *in, size_t count, uint16_t *out)
{
    for (size_t i = 0; i < count; ++i) {
        const uint32_t p = in[i];
        out[i] = uint16_t(((p >> 8) & 0xF800) | ((p >> 5) & 0x07E0) | ((p >> 3) & 0x001F));
    }
}
inline const char *product_backend() { return P6_RGB565_DEFAULT_NEON ? "neon" : "portable"; }
inline void present(const uint32_t *in, size_t count, uint16_t *out)
{
#ifdef P6_TEST_RGB565_ALTERNATE
    constexpr bool optimized = !bool(P6_RGB565_DEFAULT_NEON);
#else
    constexpr bool optimized = bool(P6_RGB565_DEFAULT_NEON);
#endif
    if constexpr (optimized) convert(in, count, out);
    else portable(in, count, out);
}
} // namespace p6_simd
#endif
