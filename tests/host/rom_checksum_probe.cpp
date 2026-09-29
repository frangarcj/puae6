// Runs the canonical Kickstart checksum over synthetic 256/512 KiB buffers.
#include <cassert>
#include <cstddef>
#include <cstdint>
#include <vector>
#include "sysconfig.h"
#include "sysdeps.h"

#include "rom_checksum_under_test.h"

static uint32_t independent_sum(const uint8_t *bytes, size_t size)
{
    uint64_t sum = 0;
    for (size_t i = 0; i < size; i += 4) {
        const uint32_t word = (uint32_t(bytes[i]) << 24) |
                              (uint32_t(bytes[i + 1]) << 16) |
                              (uint32_t(bytes[i + 2]) << 8) | uint32_t(bytes[i + 3]);
        sum += word;
    }
    while (sum >> 32)
        sum = (sum & UINT64_C(0xffffffff)) + (sum >> 32);
    return uint32_t(sum);
}

static void set_valid_checksum(std::vector<uint8_t> &bytes)
{
    const size_t offset = bytes.size() - 4;
    bytes[offset] = bytes[offset + 1] = bytes[offset + 2] = bytes[offset + 3] = 0;
    const uint32_t check = ~independent_sum(bytes.data(), bytes.size());
    bytes[offset] = uint8_t(check >> 24);
    bytes[offset + 1] = uint8_t(check >> 16);
    bytes[offset + 2] = uint8_t(check >> 8);
    bytes[offset + 3] = uint8_t(check);
    assert(independent_sum(bytes.data(), bytes.size()) == UINT32_MAX);
}

static void exercise_size(size_t size)
{
    std::vector<uint8_t> all_ones(size, 0xff);
    assert(kickstart_checksum_do(all_ones.data(), int(size)));
    all_ones[size / 2] ^= 1;
    assert(!kickstart_checksum_do(all_ones.data(), int(size)));

    std::vector<uint8_t> mixed(size);
    for (size_t i = 0; i < size; ++i)
        mixed[i] = uint8_t(0x80u + uint32_t(i * 37u + (i >> 5) * 91u));
    set_valid_checksum(mixed);
    assert(kickstart_checksum_do(mixed.data(), int(size)));
    mixed[size / 3] ^= 0x04;
    assert(!kickstart_checksum_do(mixed.data(), int(size)));
}

int main()
{
    exercise_size(262144);
    exercise_size(524288);
}
