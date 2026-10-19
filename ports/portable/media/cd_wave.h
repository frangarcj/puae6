#ifndef P6_MEDIA_CD_WAVE_H
#define P6_MEDIA_CD_WAVE_H
#include "cd_source.h"
namespace p6_cd {
enum class Encoding { Opaque, Iso2048, AudioPcmS16Le };
struct Slice { uint64_t offset, bytes; Encoding encoding; };
// RIFF WAVE, exactly one fmt/data chunk, PCM16le stereo 44.1kHz, whole CD sectors.
// Failed/short reads and invalid metadata leave the output unchanged.
Result wave_slice(ReadFn read, const void *context, uint64_t file_bytes, Slice *out);
}
#endif
