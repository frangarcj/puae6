// Evaluation of the real libretro conversion and a candidate. No engine is replaced.
// SPDX-License-Identifier: GPL-2.0-or-later
#include "present.h"
#include "rgb565_candidate.h"
#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <vector>
#include <sys/mman.h>
#include <unistd.h>

using Convert = void (*)(const uint32_t *, size_t, uint16_t *);
static bool corrupt_candidate = false;
static void require(bool ok, const char *why) { if (!ok) { fprintf(stderr, "%s\n", why); exit(2); } }
__attribute__((noinline)) static void current(const uint32_t *in, size_t n, uint16_t *out) { p6_simd::portable(in, n, out); }
__attribute__((noinline)) static void product(const uint32_t *in, size_t n, uint16_t *out) { p6_libretro::to_rgb565(in, n, out); }
__attribute__((noinline)) static void candidate(const uint32_t *in, size_t n, uint16_t *out)
{
    p6_simd::convert(in, n, out);
    if (corrupt_candidate && n) out[0] ^= 1;
}
__attribute__((noinline)) static void fallback(const uint32_t *in, size_t n, uint16_t *out) { p6_simd::convert(in, n, out, false); }
static uint32_t rng = 1;
static uint64_t rgb_digest = 1469598103934665603ull;
static uint32_t random_word() { rng = rng * 1664525u + 1013904223u; return rng; }

static void verify()
{
    // Independent channel arithmetic, every 24-bit RGB value, with varying X.
    std::vector<uint32_t> input(4096);
    std::vector<uint16_t> expected(4096), output(4096);
    const Convert routes[] = {current, candidate, fallback, product};
    for (uint32_t base = 0; base < (1u << 24); base += 4096) {
        for (size_t i = 0; i < input.size(); ++i) {
            input[i] = (base + uint32_t(i)) | (uint32_t(i * 37) << 24);
            const uint32_t p = base + uint32_t(i);
            expected[i] = uint16_t((p / 65536 / 8) * 2048 + ((p / 256) % 256 / 4) * 32 + (p % 256 / 8));
        }
        for (Convert route : routes) { route(input.data(), input.size(), output.data()); require(output == expected, "RGB domain differs"); }
        for (uint16_t p : output) { rgb_digest = (rgb_digest ^ (p & 255)) * 1099511628211ull; rgb_digest = (rgb_digest ^ (p >> 8)) * 1099511628211ull; }
    }
    for (size_t n : {size_t(0), 1ul, 2ul, 3ul, 4ul, 7ul, 8ul, 15ul, 16ul, 17ul, 31ul, 32ul, 33ul, 63ul, 64ul, 65ul, 755ul, 756ul, 757ul, 1824ul, 756ul * 574}) {
        for (size_t offset = 0; offset < 16; ++offset) {
            std::vector<uint32_t> in(n + 32); for (auto &p : in) p = random_word();
            const auto before = in;
            std::vector<uint16_t> ref(n); p6_simd::scalar(in.data() + offset, n, ref.data());
            for (Convert route : routes) {
                std::vector<uint16_t> out(n + 64, 0xa55a);
                const size_t at = 1 + 2 * offset;
                route(in.data() + offset, n, out.data() + at);
                require(std::equal(ref.begin(), ref.end(), out.begin() + at), "tail/alignment differs");
                for (size_t i = 0; i < out.size(); ++i) if (i < at || i >= at + n) require(out[i] == 0xa55a, "canary changed");
                require(in == before, "source changed");
            }
        }
    }
    // No readable/writable padding beyond the last element, including n=0.
    const size_t page = size_t(sysconf(_SC_PAGESIZE));
    void *a = mmap(nullptr, 3 * page, PROT_NONE, MAP_PRIVATE | MAP_ANON, -1, 0);
    void *b = mmap(nullptr, 3 * page, PROT_NONE, MAP_PRIVATE | MAP_ANON, -1, 0);
    require(a != MAP_FAILED && b != MAP_FAILED, "mmap failed");
    require(!mprotect(static_cast<char *>(a) + page, page, PROT_READ | PROT_WRITE) && !mprotect(static_cast<char *>(b) + page, page, PROT_READ | PROT_WRITE), "guard failed");
    for (bool end : {false, true}) for (size_t n = 0; n <= 65; ++n) {
        auto *in = reinterpret_cast<uint32_t *>(static_cast<char *>(a) + (end ? 2 : 1) * page) - (end ? n : 0);
        auto *out = reinterpret_cast<uint16_t *>(static_cast<char *>(b) + (end ? 2 : 1) * page) - (end ? n : 0);
        for (size_t i = 0; i < n; ++i) in[i] = random_word();
        std::vector<uint16_t> ref(n); p6_simd::scalar(in, n, ref.data());
        for (Convert route : routes) { route(in, n, out); require(std::equal(ref.begin(), ref.end(), out), "guarded output differs"); }
    }
    munmap(a, 3 * page); munmap(b, 3 * page);
}

static void frame(Convert fn, const uint32_t *in, size_t n, uint16_t *out)
{
    // Present's 756-pixel case copies a row into a stack buffer and converts
    // that row; one frame-sized call would hide its four-pixel scalar tails.
    uint32_t row[756];
    // A volatile function pointer prevents folding repeated calls into one.
    Convert volatile route = fn;
    for (size_t at = 0; at < n; at += 756) {
        const size_t count = std::min(size_t(756), n - at);
        memcpy(row, in + at, count * sizeof(uint32_t));
        route(row, count, out + at);
    }
}
static double measure(Convert fn, const uint32_t *in, size_t n, uint16_t *out)
{
    const auto start = std::chrono::steady_clock::now();
    for (unsigned i = 0; i < 300; ++i) frame(fn, in, n, out);
    return std::chrono::duration<double, std::nano>(std::chrono::steady_clock::now() - start).count() / 300;
}
int main(int argc, char **argv)
{
    corrupt_candidate = argc > 1 && !strcmp(argv[1], "--selftest-fail");
    verify();
    printf("{\"rgb_digest\":\"%016llx\",\"verified_rgb_values\":16777216,\"backend\":\"%s\",\"forced_fallback\":\"%s\",\"product_backend\":\"%s\",\"row_pixels\":756,\"row_copy_included\":true,\"warmup\":300,\"iterations_per_sample\":300,\"repetitions\":5,\"samples\":[", static_cast<unsigned long long>(rgb_digest), p6_simd::backend(true), p6_simd::backend(false), p6_simd::product_backend());
    std::vector<uint32_t> in(756 * 574); for (auto &p : in) p = random_word();
    std::vector<uint16_t> out(in.size());
    for (unsigned i = 0; i < 300; ++i) { frame(current, in.data(), in.size(), out.data()); frame(candidate, in.data(), in.size(), out.data()); }
    for (unsigned i = 0; i < 5; ++i) {
        double old_ns, new_ns;
        if (i % 2) { new_ns = measure(candidate, in.data(), in.size(), out.data()); old_ns = measure(current, in.data(), in.size(), out.data()); }
        else { old_ns = measure(current, in.data(), in.size(), out.data()); new_ns = measure(candidate, in.data(), in.size(), out.data()); }
        printf("%s{\"current_ns\":%.1f,\"candidate_ns\":%.1f}", i ? "," : "", old_ns, new_ns);
    }
    printf("]}\n");
}
