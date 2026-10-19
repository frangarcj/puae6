#ifndef P6_MEDIA_CD_AKIKO_H
#define P6_MEDIA_CD_AKIKO_H
#include "cd_disc.h"
#include "sysconfig.h"
#include "tchar.h"
struct TrapContext;
#include "blkdev.h" // Real WinUAE TOC/SUBQ types; no replacement layout.
namespace p6_cd {
// Derived metadata only; does not open a device or change playback state.
Result akiko_toc(const Disc &disc, cd_toc_head *out);
// Explicit LBA and caller's audio status. all=true:96 P-W deinterleaved;
// otherwise SUBQ_SIZE16 (status header + Q12). Errors preserve output.
Result akiko_qcode(const Disc &disc, uint32_t lba, uint8_t status, bool all,
                   void *out, size_t capacity);
}
#endif
