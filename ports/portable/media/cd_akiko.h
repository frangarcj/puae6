#ifndef P6_MEDIA_CD_AKIKO_H
#define P6_MEDIA_CD_AKIKO_H
#include "cd_disc.h"
#include "sysconfig.h"
#include "tchar.h"
struct TrapContext;
#include "blkdev.h" // Real WinUAE TOC/SUBQ types; no replacement layout.
namespace p6_cd {
// Pure virtual global TOC: all tracks, absolute LBAs, final leadout (ADR 0018).
// Legacy/single-session bytes unchanged; not Session Info or physical Full TOC.
Result akiko_toc(const Disc &disc, cd_toc_head *out);
struct SessionToc { Session session; cd_toc_head toc; };
// Explicit table only, ordinal 1-based. Carries format/CUE0 separately from the
// real upstream TOC type. Pure, errors preserve output; no emulated selection
// and not MMC Session Info (firstaddress here remains INDEX00).
Result akiko_session_toc(const Disc &disc, uint32_t ordinal, SessionToc *out);
// Explicit LBA and caller's audio status. Stored or synthesized subchannels.
// all=true:96 P-W deinterleaved;
// otherwise SUBQ_SIZE16 (status header + Q12). Errors preserve output.
Result akiko_qcode(const Disc &disc, uint32_t lba, uint8_t status, bool all,
                   void *out, size_t capacity);
}
#endif
