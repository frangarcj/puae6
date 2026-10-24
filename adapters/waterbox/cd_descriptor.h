#ifndef P6W_CD_DESCRIPTOR_H
#define P6W_CD_DESCRIPTOR_H
#include "p6w_cd.h"
#include "media/cd_disc.h"
#include <string.h>
namespace p6_waterbox {
// Pure preflight for a single canonical LBA2448 binding. Borrowed arguments do
// not escape. Disc validates geometry/regions without invoking binding.read;
// only a fully validated layout is published. This does not bind a sandbox.
template<class Info>
inline p6_cd::Result cd_descriptor_regions(const Info *info, const p6_cd::Binding &binding,
 p6_cd::Layout *out, uint32_t version, const p6w_cd_session *sessions=nullptr, uint32_t count=0)
{
 using namespace p6_cd;
 if (!info || !out || info->version != version || info->size != sizeof *info ||
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
 if (count>99) return Result::Invalid;
 next.sessions=count;
 if (sessions) for(uint32_t n=0;n<99;++n) {
  const auto &s=sessions[n];
  if (n>=count) {
   if (s.first_track || s.last_track || s.format || s.cue0 || s.a2) return Result::Invalid;
  } else {
   if (s.first_track>99 || s.last_track>99 || (s.format!=0 && s.format!=0x10 && s.format!=0x20)) return Result::Invalid;
   next.session[n]={uint8_t(s.first_track),uint8_t(s.last_track),uint8_t(s.format),s.cue0,s.a2};
  }
 }
 Disc validation;
 const auto result=validation.bind(next,&binding,1);
 if (result==Result::Ok) *out=next;
 return result;
}
inline p6_cd::Result cd_descriptor_v2(const p6w_cd_info_v2 *info,
                                    const p6_cd::Binding &binding, p6_cd::Layout *out)
 { return cd_descriptor_regions(info,binding,out,2); }
inline p6_cd::Result cd_descriptor_v3(const p6w_cd_info_v3 *info,
                                    const p6_cd::Binding &binding, p6_cd::Layout *out)
{
 // Check the fixed prefix before reading any V3-only field.
 if (!info || info->version!=3 || info->size!=sizeof *info) return p6_cd::Result::Invalid;
 return cd_descriptor_regions(info,binding,out,3,info->sessions,info->session_count);
}

}
#endif
