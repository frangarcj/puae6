#include "cd_subchannel.h"
#include <string.h>
namespace p6_cd {
static uint8_t bcd(uint32_t value)
{
 return uint8_t((value / 10) * 16 + value % 10);
}
static void msf(uint8_t *out, uint32_t frames)
{
 out[0] = bcd(frames / (60 * 75));
 out[1] = bcd((frames / 75) % 60);
 out[2] = bcd(frames % 75);
}
Result synth_subchannel(const Track &t, uint32_t number, uint32_t lba,
                        bool pause, void *out, size_t capacity)
{
 if (!out || capacity < 96 || !number || number > 99 || t.adr != 1 || t.control > 15
  || t.index0 > t.index1 || t.index1 >= t.end) return Result::Invalid;
 if (lba < t.index0 || lba >= t.end) return Result::Range;
 const uint64_t absolute = uint64_t(lba) + 150;
 const uint32_t relative = lba < t.index1 ? t.index1 - lba : lba - t.index1;
 constexpr uint32_t max_msf = 100 * 60 * 75 - 1;
 if (absolute > max_msf || relative > max_msf) return Result::Unsupported;
 uint8_t bytes[96] = {};
 if (pause) memset(bytes, 0xff, 12);
 uint8_t *q = bytes + 12;
 q[0] = uint8_t((t.control << 4) | t.adr);
 q[1] = bcd(number); q[2] = lba < t.index1 ? 0 : 1;
 msf(q + 3, relative); msf(q + 7, uint32_t(absolute));
 uint16_t crc = 0;
 for (unsigned n = 0; n < 10; ++n) {
  crc ^= uint16_t(q[n]) << 8;
  for (unsigned bit = 0; bit < 8; ++bit)
   crc = uint16_t((uint32_t(crc) << 1) ^ ((crc & 0x8000) ? 0x1021 : 0));
 }
 q[10] = uint8_t(~(crc >> 8)); q[11] = uint8_t(~crc);
 memcpy(out, bytes, sizeof bytes);
 return Result::Ok;
}
}
