#include "cd_device.h"
#include <limits.h>
#include <string.h>
namespace {
const p6_cd::Disc *disc;
uint32_t opens, position;
p6_cd::Result error = p6_cd::Result::Invalid;
bool ready(int unit)
{
    if (unit != 0 || !disc || !opens) { error = p6_cd::Result::Invalid; return false; }
    return true;
}
int read_sectors(int unit, uae_u8 *out, int block, int count, int view)
{
    using namespace p6_cd;
    if (!ready(unit)) return 0;
    if (!out || block < 0 || count <= 0 || unsigned(count) > kDeviceMaxBurst) { error = Result::Invalid; return 0; }
    if (view != 2048 && view != 2352 && view != 2448) { error = Result::Unsupported; return 0; }
    const uint32_t end = disc->layout().leadout;
    if (uint32_t(block) >= end || uint32_t(count) > end - uint32_t(block)) { error = Result::Range; return 0; }
    // Bounded burst, transactional output even if the final sector fails. This
    // also preserves position; no caller sees a half-published buffer as valid.
    uint8_t staging[kDeviceMaxBurst * kMaxReadBytes];
    for (int n = 0; n < count; ++n) {
        error = disc->read_sector(uint32_t(block + n), uint32_t(view), staging + size_t(n) * view, size_t(view));
        if (error != Result::Ok) return 0;
    }
    memcpy(out, staging, size_t(count) * view);
    position = uint32_t(block) + uint32_t(count);
    return count * view;
}
}
namespace p6_cd {
Result device_bind(const Disc *candidate)
{
    if (disc || !candidate || !candidate->layout().tracks) return error = Result::Invalid;
    disc = candidate; opens = position = 0; return error = Result::Ok;
}
void device_unbind() { disc = nullptr; opens = position = 0; error = Result::Invalid; }
Result device_last_error() { return error; }
uint32_t device_position() { return position; }
}
int sys_command_open(int unit)
{
    if (unit != 0 || !disc || opens == INT_MAX) { error = p6_cd::Result::Invalid; return 0; }
    const bool already_open = opens != 0;
    ++opens; error = p6_cd::Result::Ok; return already_open ? -1 : 1;
}
int sys_command_isopen(int unit) { return unit == 0 && disc ? int(opens) : 0; }
void sys_command_close(int unit)
{
    if (!ready(unit)) return;
    --opens; error = p6_cd::Result::Ok;
}
int get_standard_cd_unit(cd_standard_unit kind)
{
    if (kind < CD_STANDARD_UNIT_DEFAULT || kind > CD_STANDARD_UNIT_CD32 || !sys_command_open(0)) return -1;
    return 0;
}
void close_standard_cd_unit(int unit) { sys_command_close(unit); }
int sys_command_ismedia(int unit, int) { return ready(unit) ? 1 : -1; }
int sys_command_cd_toc(int unit, cd_toc_head *out)
{
    if (!ready(unit)) return 0;
    error = p6_cd::akiko_toc(*disc, out); return error == p6_cd::Result::Ok;
}
device_info *sys_command_info(int unit, device_info *out, int)
{
    if (!ready(unit) || !out) { error = p6_cd::Result::Invalid; return nullptr; }
    device_info info{};
    info.open = true; info.type = INQ_ROMD; info.media_inserted = info.removable = info.write_protected = 1;
    info.bytespersector = 2048; info.cylinders = info.trackspercylinder = 1;
    info.sectorspertrack = int(disc->layout().leadout); info.unitnum = 1;
    strcpy(info.label, "PUAE6 immutable CD"); strcpy(info.vendorid, "PUAE6");
    strcpy(info.productid, "CD IMAGE"); strcpy(info.revision, "1.0"); info.backend = "OFFSET";
    error = p6_cd::akiko_toc(*disc, &info.toc);
    if (error != p6_cd::Result::Ok) return nullptr;
    *out = info; return out;
}
bool blkdev_get_info(uae_prefs *, int unit, device_info *out)
{
    const bool temporary = !sys_command_isopen(unit);
    if (temporary && !sys_command_open(unit)) return false;
    const bool ok = sys_command_info(unit, out, 1) != nullptr;
    const auto reason = error;
    if (temporary) sys_command_close(unit);
    error = reason; return ok;
}
int sys_command_cd_read(int unit, uae_u8 *out, int block, int count) { return read_sectors(unit, out, block, count, 2048) != 0; }
int sys_command_cd_rawread(int unit, uae_u8 *out, int block, int count, int view) { return read_sectors(unit, out, block, count, view); }
int sys_command_cd_qcode(int unit, uae_u8 *out, int lba, bool all)
{
    if (!ready(unit)) return 0;
    if (lba < -1) { error = p6_cd::Result::Invalid; return 0; }
    error = p6_cd::akiko_qcode(*disc, lba < 0 ? position : uint32_t(lba), AUDIO_STATUS_NO_STATUS,
                             all, out, all ? SUB_CHANNEL_SIZE : SUBQ_SIZE);
    return error == p6_cd::Result::Ok;
}
// CDDA remains unavailable. Do not acknowledge play/pause/volume as implemented.
int sys_command_cd_pause(int unit, int)
{
    if (ready(unit)) error = p6_cd::Result::Unsupported;
    return -1;
}
void sys_command_cd_stop(int unit) { if (ready(unit)) error = p6_cd::Result::Ok; }
int sys_command_cd_play(int unit, int, int, int, play_status_callback status, play_subchannel_callback)
{
    if (ready(unit)) {
        if (status) status(AUDIO_STATUS_PLAY_ERROR, int(position));
        error = p6_cd::Result::Unsupported;
    }
    return 0;
}
int sys_command_cd_play(int unit, int start, int end, int scan) { return sys_command_cd_play(unit, start, end, scan, nullptr, nullptr); }
uae_u32 sys_command_cd_volume(int unit, uae_u16, uae_u16)
{
    if (ready(unit)) error = p6_cd::Result::Unsupported;
    return UINT32_MAX; // blkdev's unavailable volume callback returns -1.
}
