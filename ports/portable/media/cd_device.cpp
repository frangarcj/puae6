#include "cd_device.h"
#include "cd_read.h"
#include "cd_audio.h"
#include "cd_device_state.h"
#include <limits.h>
#include <string.h>
namespace {
const p6_cd::Disc *disc;
const p6_cd::Disc *catalog[p6_cd::kDeviceMaxDiscs];
uint32_t catalog_count;
uint32_t opens, position;
p6_cd::AudioPlayer audio;
bool inserted, audio_head, terminal_ack;
uint32_t head()
{
    if (!audio_head) return position;
    return audio.position() - (audio.status() == AUDIO_STATUS_PLAY_COMPLETE ? 1u : 0u);
}
void stop_audio()
{
    update_audio();
    const auto last = head(); audio.stop(); position = last;
    audio_head = terminal_ack = false;
}
p6_cd::Result error = p6_cd::Result::Invalid;
bool ready(int unit, bool require_medium = true)
{
    if (unit != 0 || !disc || !opens || (require_medium && !inserted)) { error = p6_cd::Result::Invalid; return false; }
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
        error = canonical_read(*disc, uint32_t(block + n), uint32_t(view), staging + size_t(n) * view, size_t(view));
        if (error != Result::Ok) return 0;
    }
    memcpy(out, staging, size_t(count) * view);
    position = uint32_t(block) + uint32_t(count);
    if (!audio.active()) audio_head = false;
    return count * view;
}
}
namespace p6_cd {
uint32_t device_catalog_size() { return catalog_count; }
const Disc *device_catalog_disc(uint32_t n) { return n < catalog_count ? catalog[n] : nullptr; }
const Disc *device_resolve_disc(const uint8_t id[32])
{
    if (id) for (uint32_t n = 0; n < catalog_count; ++n)
        if (!memcmp(id, catalog[n]->layout().content_id, 32)) return catalog[n];
    return nullptr;
}
const Disc *device_bound_disc() { return disc; }
Result device_select(uint32_t n)
{
    if (inserted || n >= catalog_count) return Result::Invalid;
    if (disc != catalog[n]) { disc = catalog[n]; position = 0; error = Result::Ok; }
    return Result::Ok;
}
bool device_inserted() { return inserted; }
Result device_set_inserted(bool value)
{
    if (!disc) return Result::Invalid;
    if (inserted == value) return Result::Ok;
    stop_audio(); inserted = value;
    return error = Result::Ok;
}
bool device_phase_get(int32_t *out, unsigned n) { return audio.phase_get(out, n); }
bool device_phase_set(const int32_t *in, unsigned n) { return audio.phase_set(in, n); }
bool device_capture(DeviceState *out)
{
    if (!out) return false;
    DeviceState s{};
    if (!audio.capture(&s.audio)) return false;
    s.bound = disc != nullptr; s.inserted = inserted;
    if (disc) memcpy(s.disc_id, disc->layout().content_id, sizeof s.disc_id);
    s.opens = opens; s.position = position; s.audio_head = audio_head; s.terminal_ack = terminal_ack;
    s.error = int32_t(error); // Do not merge asynchronous audio error as device_last_error does.
    *out = s; return true;
}
bool device_validate(const DeviceState &s, const Disc *candidate, uint32_t period)
{
    if (s.bound > 1 || s.inserted > 1 || s.audio_head > 1 || s.terminal_ack > 1 || s.opens > INT_MAX
        || s.error < int(Result::Ok) || s.error > int(Result::Unsupported)) return false;
    if (!s.bound) {
        const uint8_t empty[32]{};
        if (candidate || memcmp(s.disc_id, empty, sizeof empty) || s.inserted || s.opens || s.position || s.audio_head
            || s.terminal_ack || s.audio.bound || s.error != int(Result::Invalid)) return false;
    } else if (!candidate || memcmp(s.disc_id, candidate->layout().content_id, sizeof s.disc_id)
               || !candidate->layout().tracks || s.position > candidate->layout().leadout) return false;
    if ((s.audio.bound && !s.opens) || (s.audio_head && !s.audio.bound) || (s.audio.running && !s.audio_head)) return false;
    if (!s.inserted && (s.audio.bound || s.audio_head || s.terminal_ack)) return false;
    const bool terminal = s.audio.status == AUDIO_STATUS_PLAY_COMPLETE || s.audio.status == AUDIO_STATUS_PLAY_ERROR;
    if (s.terminal_ack && !terminal) return false;
    return AudioPlayer::validate(s.audio, s.audio.bound ? candidate : nullptr, period);
}
bool device_can_restore(const DeviceState &s, const Disc *candidate,
                        play_status_callback status, play_subchannel_callback sub)
{
    return device_validate(s, candidate, s.audio.stream.period) &&
        (!catalog_count || (candidate && device_resolve_disc(candidate->layout().content_id) == candidate)) &&
        audio.can_restore(s.audio, s.audio.bound ? candidate : nullptr, status, sub);
}
bool device_restore(const DeviceState &s, const Disc *candidate,
                    play_status_callback status, play_subchannel_callback sub)
{
    if (!device_can_restore(s, candidate, status, sub)
        || !audio.restore(s.audio, s.audio.bound ? candidate : nullptr, status, sub)) return false;
    disc = candidate; inserted = s.inserted != 0; opens = s.opens; position = s.position;
    // Component callers may restore into an unbound owner. The registered
    // production catalog otherwise remains immutable across a selection change.
    if (!candidate) { catalog_count = 0; memset(catalog, 0, sizeof catalog); }
    else if (!catalog_count) { catalog[0] = candidate; catalog_count = 1; }
    audio_head = s.audio_head; terminal_ack = s.terminal_ack; error = Result(s.error);
    return true;
}
Result device_bind(const Disc *candidate)
{
    return device_catalog_bind(&candidate, 1);
}
Result device_catalog_bind(const Disc *const *items, uint32_t count, uint32_t selected)
{
    if (disc || !items || !count || count > kDeviceMaxDiscs || selected >= count) return Result::Invalid;
    for (uint32_t n = 0; n < count; ++n) {
        if (!items[n] || !items[n]->layout().tracks) return Result::Invalid;
        for (uint32_t p = 0; p < n; ++p)
            if (!memcmp(items[n]->layout().content_id, items[p]->layout().content_id, 32)) return Result::Invalid;
    }
    memcpy(catalog, items, count * sizeof *items); catalog_count = count;
    disc = catalog[selected]; inserted = true; opens = position = 0; return error = Result::Ok;
}
void device_unbind()
{
    stop_audio(); disc = nullptr; inserted = false; opens = position = catalog_count = 0;
    memset(catalog, 0, sizeof catalog); error = Result::Invalid;
}
Result device_last_error() { return error == Result::Ok && audio_head ? audio.error() : error; }
uint32_t device_position() { return head(); }
}
int sys_command_open(int unit)
{
    if (unit != 0 || !disc || opens == INT_MAX) { error = p6_cd::Result::Invalid; return 0; }
    const bool already_open = opens != 0;
    if (!already_open) audio.volume(0x7fff, 0x7fff);
    ++opens; error = p6_cd::Result::Ok; return already_open ? -1 : 1;
}
int sys_command_isopen(int unit) { return unit == 0 && disc ? int(opens) : 0; }
void sys_command_close(int unit)
{
    if (!ready(unit, false)) return;
    if (!--opens) stop_audio();
    error = p6_cd::Result::Ok;
}
int get_standard_cd_unit(cd_standard_unit kind)
{
    if (kind < CD_STANDARD_UNIT_DEFAULT || kind > CD_STANDARD_UNIT_CD32 || !sys_command_open(0)) return -1;
    return 0;
}
void close_standard_cd_unit(int unit) { sys_command_close(unit); }
int sys_command_ismedia(int unit, int) { return ready(unit, false) ? int(inserted) : -1; }
int sys_command_cd_toc(int unit, cd_toc_head *out)
{
    if (!ready(unit)) return 0;
    error = p6_cd::akiko_toc(*disc, out); return error == p6_cd::Result::Ok;
}
device_info *sys_command_info(int unit, device_info *out, int)
{
    if (!ready(unit, false) || !out) { error = p6_cd::Result::Invalid; return nullptr; }
    device_info info{};
    info.open = true; info.type = INQ_ROMD; info.media_inserted = inserted; info.removable = info.write_protected = 1;
    info.bytespersector = 2048; info.cylinders = info.trackspercylinder = 1;
    info.sectorspertrack = inserted ? int(disc->layout().leadout) : 0; info.unitnum = 1;
    strcpy(info.label, "PUAE6 immutable CD"); strcpy(info.vendorid, "PUAE6");
    strcpy(info.productid, "CD IMAGE"); strcpy(info.revision, "1.0"); info.backend = "OFFSET";
    error = inserted ? p6_cd::akiko_toc(*disc, &info.toc) : p6_cd::Result::Ok;
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
    update_audio();
    int status = audio.status();
    const bool terminal = status == AUDIO_STATUS_PLAY_COMPLETE || status == AUDIO_STATUS_PLAY_ERROR;
    if (terminal && terminal_ack) status = AUDIO_STATUS_NO_STATUS;
    error = p6_cd::akiko_qcode(*disc, lba < 0 ? head() : uint32_t(lba), uint8_t(status),
                             all, out, all ? SUB_CHANNEL_SIZE : SUBQ_SIZE);
    if (error == p6_cd::Result::Ok && terminal) terminal_ack = true;
    return error == p6_cd::Result::Ok;
}
int sys_command_cd_pause(int unit, int paused)
{
    if (!ready(unit)) return -1;
    update_audio();
    const int old = audio.status() == AUDIO_STATUS_PAUSED;
    error = audio.active() ? audio.pause(paused != 0) : p6_cd::Result::Ok;
    return error == p6_cd::Result::Ok ? old : -1;
}
void sys_command_cd_stop(int unit)
{
    if (ready(unit, false)) { stop_audio(); error = p6_cd::Result::Ok; }
}
int sys_command_cd_play(int unit, int start, int end, int scan, play_status_callback status, play_subchannel_callback sub)
{
    if (!ready(unit)) return 0;
    if (scan) {
        error = p6_cd::Result::Unsupported;
        if (status) status(AUDIO_STATUS_PLAY_ERROR, int(head()));
        return 0;
    }
    if (start < 0 || end < -1) { error = p6_cd::Result::Range; return 0; }
    error = audio.play(*disc, uint32_t(start), end == -1 ? disc->layout().leadout : uint32_t(end), status, sub);
    if (error != p6_cd::Result::Invalid && error != p6_cd::Result::Range) { audio_head = true; terminal_ack = false; }
    return error == p6_cd::Result::Ok;
}
int sys_command_cd_play(int unit, int start, int end, int scan) { return sys_command_cd_play(unit, start, end, scan, nullptr, nullptr); }
uae_u32 sys_command_cd_volume(int unit, uae_u16 left, uae_u16 right)
{
    if (!ready(unit, false)) return UINT32_MAX;
    const auto old = audio.volume(left, right); error = p6_cd::Result::Ok;
    return old;
}
