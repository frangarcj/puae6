#include "emulibc.h"
#include "p6w_cd.h"
#include "p6.h"
#include "media/cd_disc.h"
#include "sysconfig.h"
#include "sysdeps.h"
#include "media/cd_device.h"
#include "akiko.h"
#include "akiko_controller_state.h"
#include "akiko_state.h"
#include "media/cd_device_state.h"
#include "../../bridge/session.h"
#include "../../bridge/config.h"
#include <stddef.h>
#include <string.h>
namespace {
struct Entry { p6_cd::Disc disc; p6_cd::Source source; };
Entry entries[p6_cd::kDeviceMaxDiscs];
uint32_t cd_count;
bool cd_indexed;
p6w_cd_catalog_read_fn cd_catalog_callback;
p6w_cd_read_fn cd_callback;
bool cd_bound;
bool cd_activated;
static_assert(sizeof(p6w_cd_info) == 2432, "private CD descriptor layout");
bool read_host(const void *context, uint64_t at, void *out, size_t count, size_t *got)
{
 const auto index = uint32_t(reinterpret_cast<uintptr_t>(context));
 int32_t read = cd_indexed ? (cd_catalog_callback ? cd_catalog_callback(index, at, out, uint32_t(count)) : -1)
  : (cd_callback && index == 0 ? cd_callback(at, out, uint32_t(count)) : -1);
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
namespace {
int prepare(const p6w_cd_info *info, uint32_t index, bool rebind, Entry *out)
{
 if (info->version != 1 || info->size != sizeof *info || !info->count || info->count > 99
  || info->bytes != uint64_t(info->leadout) * 2448) return P6_ERROR_BAD_ARGUMENT;
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
 binding.context = reinterpret_cast<const void *>(uintptr_t(index));
 auto source = entries[index].source; auto disc = entries[index].disc;
 auto r = rebind ? source.rebind(binding.identity, read_host, binding.context) : source.bind(binding.identity, read_host, binding.context);
 if (r == p6_cd::Result::Ok) r = rebind ? disc.rebind(layout, &binding, 1) : disc.bind(layout, &binding, 1);
 if (r == p6_cd::Result::Ok && out) { out->source = source; out->disc = disc; }
 return result(r);
}
int bind_catalog(const p6w_cd_info *info, uint32_t count, bool indexed, uint32_t rebind)
{
 if (!info || !count || count > p6_cd::kDeviceMaxDiscs || rebind > 1) return P6_ERROR_BAD_ARGUMENT;
 if (!rebind && p6_session::session.started) return P6_ERROR_BAD_STATE;
 if (bool(rebind) != cd_bound || (rebind && (count != cd_count || indexed != cd_indexed))) return P6_ERROR_BAD_STATE;
 if (p6_cd::device_catalog_size() != (cd_bound ? count : 0)) return P6_ERROR_BAD_STATE;
 for (uint32_t n = 0; n < count; ++n) {
  if (rebind && p6_cd::device_catalog_disc(n) != &entries[n].disc) return P6_ERROR_BAD_STATE;
  for (uint32_t j = 0; j < n; ++j) if (!memcmp(info[n].id, info[j].id, 32)) return P6_ERROR_BAD_ARGUMENT;
  const int r = prepare(info + n, n, rebind != 0, nullptr);
  if (r != P6_OK) return r;
 }
 if (!rebind) {
  const p6_cd::Disc *catalog[p6_cd::kDeviceMaxDiscs]{};
  // Second pass cannot fail: inputs are borrowed only for this guarded call.
  // Only permanent objects escape; rebind leaves their captured bytes intact.
  for (uint32_t n = 0; n < count; ++n) { prepare(info + n, n, false, &entries[n]); catalog[n] = &entries[n].disc; }
  if (p6_cd::device_catalog_bind(catalog, count) != p6_cd::Result::Ok) return P6_ERROR_INTERNAL;
  cd_count = count; cd_indexed = indexed; cd_bound = true;
 }
 return P6_OK;
}
uint32_t selected()
{
 for (uint32_t n = 0; n < cd_count; ++n) if (p6_cd::device_bound_disc() == &entries[n].disc) return n;
 return cd_count;
}
}
ECL_EXPORT int32_t p6w_cd_bind(const p6w_cd_info *info, p6w_cd_read_fn callback, uint32_t rebind)
{
 p6_session::Guard guard;
 if (!guard.ok) return P6_ERROR_BUSY;
 if (!callback) return P6_ERROR_BAD_ARGUMENT;
 const int r = bind_catalog(info, 1, false, rebind);
 if (r == P6_OK) cd_callback = callback;
 return r;
}
ECL_EXPORT int32_t p6w_cd_catalog_bind(const p6w_cd_info *info, uint32_t count, p6w_cd_catalog_read_fn callback, uint32_t rebind)
{
 p6_session::Guard guard;
 if (!guard.ok) return P6_ERROR_BUSY;
 if (!callback) return P6_ERROR_BAD_ARGUMENT;
 const int r = bind_catalog(info, count, true, rebind);
 if (r == P6_OK) cd_catalog_callback = callback;
 return r;
}
ECL_EXPORT int32_t p6w_cd_change(uint32_t index, uint32_t inserted)
{
 p6_session::Guard guard;
 if (!guard.ok) return P6_ERROR_BUSY;
 if (inserted > 1 || index >= cd_count) return P6_ERROR_BAD_ARGUMENT;
 if (!cd_activated || p6_session::session.state != p6_session::State::Loaded) return P6_ERROR_BAD_STATE;
 if (index != selected() && (p6_cd::device_inserted() || p6_cd::device_select(index) != p6_cd::Result::Ok)) return P6_ERROR_BAD_STATE;
 return p6_akiko_media_set(inserted != 0) ? P6_OK : P6_ERROR_BAD_STATE;
}
ECL_EXPORT int32_t p6w_cd_status(uint32_t *out, uint32_t capacity)
{
 p6_session::Guard guard;
 if (!guard.ok) return P6_ERROR_BUSY;
 if (!out || capacity < 4) return P6_ERROR_BAD_ARGUMENT;
 p6_cd::DeviceState state{};
 if (!cd_bound || selected() >= cd_count || !p6_cd::device_capture(&state)) return P6_ERROR_BAD_STATE;
 const uint32_t values[] = {cd_count, selected(), state.inserted, state.opens};
 memcpy(out, values, sizeof values); return P6_OK;
}
ECL_EXPORT int32_t p6w_cd_activate(void)
{
 p6_session::Guard guard;
 if (!guard.ok) return P6_ERROR_BUSY;
 const auto &s = p6_session::session;
 if (!cd_bound || cd_activated || s.state != p6_session::State::Loaded || s.started
  || s.config.machine != p6_bridge::kCd32Machine) return P6_ERROR_BAD_STATE;
 // p6_load ran before Seal, without external CD metadata. Reopen through the
 // real device lifecycle before any CPU advance; registrations deduplicate.
 p6_akiko_controller_state state{};
 if (!akiko_init() || !p6_akiko_controller_get(&state) || state.unitnum != 0) return P6_ERROR_INTERNAL;
 cd_activated = true; return P6_OK;
}
void p6w_cd_session_end(void)
{
 if (cd_bound) p6_cd::device_unbind();
 for (auto &entry : entries) entry = Entry{};
 cd_count = 0; cd_callback = nullptr; cd_catalog_callback = nullptr;
 cd_bound = cd_activated = cd_indexed = false;
}
ECL_EXPORT int32_t p6w_cd_read_at(uint64_t at, void *out, uint32_t count)
{
 p6_session::Guard guard;
 if (!guard.ok) return P6_ERROR_BUSY;
 const auto n = selected();
 return n < cd_count && p6_cd::device_inserted() ? result(entries[n].source.read_at(at, out, count)) : P6_ERROR_BAD_STATE;
}
ECL_EXPORT int32_t p6w_cd_read_sector(uint32_t lba, uint32_t view, void *out, uint32_t capacity)
{
 p6_session::Guard guard;
 if (!guard.ok) return P6_ERROR_BUSY;
 const auto n = selected();
 return n < cd_count && p6_cd::device_inserted() ? result(entries[n].disc.read_sector(lba, view, out, capacity)) : P6_ERROR_BAD_STATE;
}
