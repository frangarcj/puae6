#include "emulibc.h"
#include "p6w_cd.h"
#include "p6.h"
#include "media/cd_disc.h"
#include <stddef.h>
#include <string.h>
namespace {
p6_cd::Disc cd_disc;
p6_cd::Source cd_source;
p6w_cd_read_fn cd_callback;
bool cd_bound;
static_assert(sizeof(p6w_cd_info) == 2432, "private CD descriptor layout");
bool read_host(const void *, uint64_t at, void *out, size_t count, size_t *got)
{
 int32_t read = cd_callback ? cd_callback(at, out, uint32_t(count)) : -1;
 *got = read < 0 ? 0 : size_t(read);
 return read >= 0;
}
int result(p6_cd::Result r)
{
 using R = p6_cd::Result;
 return r == R::Ok ? P6_OK : r == R::Incompatible ? P6_ERROR_WRONG_MEDIA
  : r == R::ReadError ? P6_ERROR_IO : r == R::Unsupported ? P6_ERROR_UNSUPPORTED : P6_ERROR_BAD_ARGUMENT;
}
}
ECL_EXPORT uint64_t p6w_cd_layout(uint64_t *out, uint64_t capacity)
{
 const uint64_t layout[] = {sizeof(p6w_cd_info), offsetof(p6w_cd_info, bytes), offsetof(p6w_cd_info, tracks)};
 if (!out || capacity < 3) return 0;
 memcpy(out, layout, sizeof layout); return 3;
}
ECL_EXPORT int32_t p6w_cd_bind(const p6w_cd_info *info, p6w_cd_read_fn callback, uint32_t rebind)
{
 if (!info || !callback || rebind > 1 || info->version != 1 || info->size != sizeof *info
  || !info->count || info->count > 99 || info->bytes != uint64_t(info->leadout) * 2448) return P6_ERROR_BAD_ARGUMENT;
 if (bool(rebind) != cd_bound) return P6_ERROR_BAD_STATE;
 p6_cd::Layout layout{};
 memcpy(layout.content_id, info->id, 32); layout.tracks = info->count; layout.leadout = info->leadout;
 for (uint32_t n = 0; n < 99; ++n) {
  const auto &t = info->tracks[n];
  if (t.reserved || t.mode > 2 || (n >= info->count && (t.index0 || t.index1 || t.end || t.mode || t.control)))
   return P6_ERROR_BAD_ARGUMENT;
  if (n < info->count) layout.track[n] = {t.index0, t.index1, t.end, 0, 2448, uint64_t(t.index0) * 2448,
   t.mode == 0 ? p6_cd::Mode::Audio : t.mode == 1 ? p6_cd::Mode::Mode1Raw : p6_cd::Mode::Mode2Raw, 1, uint8_t(t.control), true};
  if (t.control > 15) return P6_ERROR_BAD_ARGUMENT;
 }
 p6_cd::Binding binding{};
 memcpy(binding.identity.content_id, info->id, 32);
 binding.identity.bytes = info->bytes; binding.identity.sector_bytes = 1; binding.read = read_host;
 auto source = cd_source; auto disc = cd_disc;
 auto r = rebind ? source.rebind(binding.identity, read_host, nullptr) : source.bind(binding.identity, read_host, nullptr);
 if (r == p6_cd::Result::Ok) r = rebind ? disc.rebind(layout, &binding, 1) : disc.bind(layout, &binding, 1);
 if (r != p6_cd::Result::Ok) return result(r);
 cd_source = source; cd_disc = disc; cd_callback = callback; cd_bound = true; return P6_OK;
}
ECL_EXPORT int32_t p6w_cd_read_at(uint64_t at, void *out, uint32_t count)
{
 return cd_bound ? result(cd_source.read_at(at, out, count)) : P6_ERROR_BAD_STATE;
}
ECL_EXPORT int32_t p6w_cd_read_sector(uint32_t lba, uint32_t view, void *out, uint32_t capacity)
{
 return cd_bound ? result(cd_disc.read_sector(lba, view, out, capacity)) : P6_ERROR_BAD_STATE;
}
