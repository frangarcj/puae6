#ifndef P6W_CD_DESCRIPTOR_H
#define P6W_CD_DESCRIPTOR_H
#include "p6w_cd.h"
#include "media/cd_disc.h"
#include <string.h>
namespace p6_waterbox {
// Pure preflight for a single canonical LBA2448 binding. Borrowed arguments do
// not escape. Disc validates geometry/regions without invoking binding.read;
// only a fully validated layout is published. This does not bind a sandbox.
inline p6_cd::Result cd_descriptor_v2(const p6w_cd_info_v2 *info,
                                    const p6_cd::Binding &binding, p6_cd::Layout *out)
{
 using namespace p6_cd;
 if (!info || !out || info->version != 2 || info->size != sizeof *info ||
     !info->count || info->count > 99 || info->bytes != uint64_t(info->leadout)*2448 ||
     binding.identity.bytes != info->bytes || binding.identity.sector_bytes != 1 ||
     memcmp(binding.identity.content_id,info->id,32)) return Result::Invalid;
 Layout next{}; memcpy(next.content_id,info->id,32);
 next.tracks=info->count; next.leadout=info->leadout;
 auto mode = [](uint32_t value) { return value == 0 ? Mode::Audio : value == 1 ? Mode::Mode1Raw : Mode::Mode2Raw; };
 for (uint32_t n=0;n<99;++n) {
  const auto &t=info->tracks[n];
  if (t.reserved || t.mode > 2 || t.control > 15 || t.stored_pregap > 1 ||
      t.pregap_mode > 2 || t.pregap_control > 15) return Result::Invalid;
  if (n >= info->count) {
   if (t.index0 || t.index1 || t.end || t.mode || t.control || t.stored_pregap ||
       t.synthetic_pregap || t.postgap || t.flags || t.pregap_mode || t.pregap_control) return Result::Invalid;
   continue;
  }
  const uint64_t first = t.stored_pregap ? uint64_t(t.index0)+t.synthetic_pregap : t.index1;
  next.track[n]={t.index0,t.index1,t.end,0,2448,first*2448,mode(t.mode),1,uint8_t(t.control),
                 t.stored_pregap!=0,t.synthetic_pregap,t.postgap};
  // Wire zero means inactive here, rather than active Audio. Disc checks that
  // inactive metadata is zero, known flags and every mode/control combination.
  const bool active = (t.flags & Track::Regions::Explicit) && t.index1 > t.index0;
  next.track[n].regions={t.flags,active ? mode(t.pregap_mode) : Mode::Mode1_2048,t.pregap_control};
  if (!active && t.pregap_mode) return Result::Invalid;
 }
 Disc validation;
 const auto result=validation.bind(next,&binding,1);
 if (result==Result::Ok) *out=next;
 return result;
}
}
#endif
