#define P6_CDA_FIXTURE_ONLY
#include "../audio/cd_audio_probe.cpp"
#include "media/cd_device.h"
#include <limits.h>
#include <stdio.h>
#include <string.h>
#include <initializer_list>
using namespace p6_cd;
#define CHECK(x) do { if (!(x)) { fprintf(stderr, "line %d: %s\n", __LINE__, #x); return 1; } } while (0)
struct Provider { uint32_t stride, sectors, calls = 0; bool raw = false, fault = false; };
static uint8_t value(const Provider &p, uint64_t at) { return p.raw && at % p.stride == 15 ? 1 : uint8_t(at * 17 + at / 233); }
static bool read(const void *ctx, uint64_t at, void *out, size_t count, size_t *got)
{
    auto &p = *const_cast<Provider *>(static_cast<const Provider *>(ctx)); ++p.calls;
    for (size_t n = 0; n < count; ++n) static_cast<uint8_t *>(out)[n] = value(p, at + n);
    *got = count;
    return at + count <= uint64_t(p.stride) * p.sectors && !(p.fault && at >= p.stride);
}
static bool filled(const uint8_t *p, size_t count, uint8_t v)
{
    for (size_t n = 0; n < count; ++n) if (p[n] != v) return false;
    return true;
}
static int reported_status, status_count;
static int status(int s, int) { reported_status = s; ++status_count; return 0; }
int main()
{
    int16_t dummy[1176]{}; cd_audio_state bootstrap{};
    start(48000, false, bootstrap, dummy);
    audio_cda_new_buffer(&bootstrap, nullptr, -1, 0, nullptr, nullptr);
    Provider providers[] = {{2048, 130}, {2352, 2}, {2448, 4, 0, true}};
    Binding bindings[3]{};
    for (unsigned n = 0; n < 3; ++n) bindings[n] = {{{uint8_t(n + 1)}, uint64_t(providers[n].stride) * providers[n].sectors, 1}, read, &providers[n]};
    Layout layout{}; layout.tracks = 3; layout.leadout = 136; layout.content_id[0] = 42;
    layout.track[0] = {0, 0, 130, 0, 2048, 0, Mode::Mode1_2048, 1, 4, false};
    layout.track[1] = {130, 130, 132, 1, 2352, 0, Mode::Audio, 1, 0, false};
    layout.track[2] = {132, 132, 136, 2, 2448, 0, Mode::Mode1Raw, 1, 4, true};
    Disc disc, absent;
    CHECK(disc.bind(layout, bindings, 3) == Result::Ok);
    CHECK(get_standard_cd_unit(CD_STANDARD_UNIT_CD32) == -1 && sys_command_ismedia(0, 1) == -1);
    CHECK(device_bind(nullptr) == Result::Invalid && device_bind(&absent) == Result::Invalid);
    CHECK(device_bind(&disc) == Result::Ok && device_bind(&disc) == Result::Invalid);
    device_info info;
    CHECK(blkdev_get_info(nullptr, 0, &info) && !sys_command_isopen(0) && device_position() == 0);
    CHECK(!blkdev_get_info(nullptr, 1, &info) && !sys_command_isopen(0));
    for (auto kind : {CD_STANDARD_UNIT_DEFAULT, CD_STANDARD_UNIT_AUDIO, CD_STANDARD_UNIT_CDTV}) {
        CHECK(get_standard_cd_unit(kind) == 0); close_standard_cd_unit(0); CHECK(!sys_command_isopen(0));
    }
    CHECK(!sys_command_open(-1) && !sys_command_open(1));
    CHECK(get_standard_cd_unit(CD_STANDARD_UNIT_CD32) == 0 && sys_command_isopen(0));
    CHECK(sys_command_open(0) == -1 && sys_command_isopen(0) == 2);
    CHECK(blkdev_get_info(nullptr, 0, &info) && sys_command_isopen(0) == 2);
    close_standard_cd_unit(0); CHECK(sys_command_isopen(0) == 1);
    CHECK(sys_command_info(0, &info, 0) == &info && info.type == INQ_ROMD && info.open && info.media_inserted);
    CHECK(info.removable && info.write_protected && info.bytespersector == 2048 && info.sectorspertrack == 136 && info.unitnum == 1);
    CHECK(info.toc.tracks == 3 && info.toc.points == 6 && info.toc.toc[2].control == 0 && info.toc.lastaddress == 136);
    CHECK(blkdev_get_info(nullptr, 0, &info) && !sys_command_info(0, nullptr, 0));
    cd_toc_head toc; CHECK(sys_command_cd_toc(0, &toc) == 1 && toc.tracks == 3);
    uint8_t out[kDeviceMaxBurst * kMaxReadBytes + 16]; memset(out, 0xa5, sizeof out);
    CHECK(sys_command_cd_read(0, out + 8, 0, 128) == 1 && device_position() == 128);
    for (size_t n = 0; n < 128 * 2048; ++n) CHECK(out[8 + n] == value(providers[0], n));
    CHECK(filled(out, 8, 0xa5) && filled(out + 8 + 128 * 2048, sizeof out - 8 - 128 * 2048, 0xa5));
    CHECK(sys_command_cd_rawread(0, out, 129, 2, 2352) == 4704 && device_position() == 131);
    CHECK(out[0] == 0 && out[11] == 0 && out[15] == 1);
    for (size_t n = 0; n < 2048; ++n) CHECK(out[16 + n] == value(providers[0], 129 * 2048 + n));
    for (size_t n = 0; n < 2352; ++n) CHECK(out[2352 + n] == value(providers[1], n));
    CHECK(sys_command_cd_rawread(0, out, 132, 2, 2352) == 4704 && device_position() == 134);
    for (size_t n = 0; n < 2 * 2352; ++n) CHECK(out[n] == value(providers[2], n / 2352 * 2448 + n % 2352));
    CHECK(sys_command_cd_qcode(0, out, -1, false) == 1 && out[1] == AUDIO_STATUS_NO_STATUS && out[3] == 12);
    for (size_t n = 0; n < 12; ++n) CHECK(out[4 + n] == value(providers[2], 2 * 2448 + 2364 + n));
    CHECK(sys_command_cd_qcode(0, out, 132, true) == 1);
    for (size_t n = 0; n < 96; ++n) CHECK(out[n] == value(providers[2], 2352 + n));
    CHECK(sys_command_cd_rawread(0, out, 132, 1, 2448) == 2448);
    for (size_t n = 0; n < 2448; ++n) CHECK(out[n] == value(providers[2], n));
    CHECK(sys_command_cd_read(0, out, 132, 1) == 1);
    for (size_t n = 0; n < 2048; ++n) CHECK(out[n] == value(providers[2], 16 + n));
    CHECK(sys_command_cd_rawread(0, out, 0, 1, 2448) == 2448 && out[15] == 1 && out[2364] == 0x41);
    CHECK(sys_command_cd_qcode(0, out, 0, false) == 1 && out[1] == AUDIO_STATUS_NO_STATUS && out[4] == 0x41);
    const auto position = device_position(), calls = providers[0].calls + providers[1].calls + providers[2].calls;
    memset(out, 0xa5, sizeof out);
    for (int unit : {-1, 1, INT_MAX}) CHECK(!sys_command_cd_rawread(unit, out, 0, 1, 2352));
    for (int count : {-1, 0, 129, INT_MAX}) CHECK(!sys_command_cd_rawread(0, out, 0, count, 2352));
    for (int block : {-1, 136, INT_MAX}) CHECK(!sys_command_cd_rawread(0, out, block, 1, 2352));
    CHECK(!sys_command_cd_rawread(0, nullptr, 0, 1, 2352) && !sys_command_cd_rawread(0, out, 135, 2, 2352));
    CHECK(!sys_command_cd_rawread(0, out, 0, 1, 2368) && device_last_error() == Result::Unsupported);
    CHECK(!sys_command_cd_read(0, out, 130, 1) && device_last_error() == Result::Unsupported);
    CHECK(!sys_command_cd_qcode(0, out, -2, true) && !sys_command_cd_qcode(0, out, 136, false));
    CHECK(calls == providers[0].calls + providers[1].calls + providers[2].calls);
    providers[0].fault = true;
    CHECK(!sys_command_cd_rawread(0, out + 8, 0, 2, 2352) && device_last_error() == Result::ReadError);
    CHECK(filled(out, sizeof out, 0xa5) && device_position() == position);
    providers[0].fault = false;
    CHECK(sys_command_cd_pause(0, 1) == 0 && device_last_error() == Result::Ok);
    CHECK(!sys_command_cd_play(0, 130, 132, 1, status, nullptr) && status_count == 1 && reported_status == AUDIO_STATUS_PLAY_ERROR);
    CHECK(device_last_error() == Result::Unsupported && !sys_command_cd_play(0, 130, 132, -1));
    CHECK(sys_command_cd_volume(0, 0x7fff, 0x7fff) == 0x7fff7fffu && device_last_error() == Result::Ok);
    sys_command_cd_stop(0); CHECK(device_position() == position);
    close_standard_cd_unit(0); CHECK(!sys_command_isopen(0));
    CHECK(!sys_command_cd_qcode(0, out, 132, false) && !sys_command_info(0, &info, 1));
    CHECK(filled(out, sizeof out, 0xa5) && !sys_command_cd_read(0, out, 0, 1));
    device_unbind(); CHECK(!sys_command_isopen(0) && device_position() == 0);
    CHECK(device_bind(&disc) == Result::Ok && sys_command_open(0) == 1);
    const auto no_io = providers[0].calls + providers[1].calls + providers[2].calls;
    CHECK(device_set_inserted(false) == Result::Ok && !device_inserted());
    CHECK(device_bound_disc() == &disc && sys_command_isopen(0) == 1 && sys_command_ismedia(0, 1) == 0);
    CHECK(sys_command_ismedia(1, 1) == -1);
    CHECK(sys_command_info(0, &info, 1) == &info && info.open && !info.media_inserted && !info.toc.points);
    memset(out, 0xa5, sizeof out); memset(&toc, 0xa5, sizeof toc); const auto untouched_toc = toc;
    CHECK(!sys_command_cd_read(0, out, 0, 1) && !sys_command_cd_rawread(0, out, 0, 1, 2352));
    CHECK(!sys_command_cd_toc(0, &toc) && !memcmp(&toc, &untouched_toc, sizeof toc));
    CHECK(!sys_command_cd_qcode(0, out, 0, true) && !sys_command_cd_play(0, 130, 132, 0));
    CHECK(filled(out, sizeof out, 0xa5));
    CHECK(no_io == providers[0].calls + providers[1].calls + providers[2].calls);
    sys_command_close(0); CHECK(!sys_command_isopen(0) && sys_command_open(0) == 1);
    CHECK(device_set_inserted(true) == Result::Ok && device_inserted() && sys_command_ismedia(0, 1) == 1);
    CHECK(sys_command_cd_toc(0, &toc) && toc.points == 6 && toc.lastaddress == 136);
    device_unbind(); CHECK(!sys_command_isopen(0));
    CHECK(device_set_inserted(true) == Result::Invalid && !device_inserted());
    Disc many[kDeviceMaxDiscs]; const Disc *items[kDeviceMaxDiscs];
    for (uint32_t n = 0; n < kDeviceMaxDiscs; ++n) {
        auto different = layout; different.content_id[0] = uint8_t(100 + n);
        CHECK(many[n].bind(different, bindings, 3) == Result::Ok); items[n] = &many[n];
    }
    Disc same_id; CHECK(same_id.bind(layout, bindings, 3) == Result::Ok);
    const Disc *bad[] = {&disc, nullptr}, *duplicate[] = {&disc, &same_id};
    CHECK(device_catalog_bind(nullptr, 1) == Result::Invalid && device_catalog_bind(items, 0) == Result::Invalid);
    CHECK(device_catalog_bind(items, kDeviceMaxDiscs + 1) == Result::Invalid);
    CHECK(device_catalog_bind(items, kDeviceMaxDiscs, kDeviceMaxDiscs) == Result::Invalid);
    CHECK(device_catalog_bind(bad, 2) == Result::Invalid && device_catalog_bind(duplicate, 2) == Result::Invalid);
    CHECK(!device_catalog_size() && !device_catalog_disc(0) && !device_resolve_disc(nullptr));
    CHECK(device_catalog_bind(items, kDeviceMaxDiscs, kDeviceMaxDiscs - 1) == Result::Ok);
    items[0] = items[1]; CHECK(device_catalog_disc(0) == &many[0]); items[0] = &many[0];
    CHECK(device_catalog_size() == kDeviceMaxDiscs && device_bound_disc() == items[kDeviceMaxDiscs - 1]);
    CHECK(sys_command_open(0) == 1 && device_select(0) == Result::Invalid);
    CHECK(device_set_inserted(false) == Result::Ok && device_select(kDeviceMaxDiscs) == Result::Invalid);
    CHECK(device_select(0) == Result::Ok && device_bound_disc() == items[0] && sys_command_isopen(0) == 1);
    CHECK(device_resolve_disc(items[0]->layout().content_id) == items[0]);
    CHECK(device_catalog_disc(kDeviceMaxDiscs) == nullptr && device_resolve_disc(disc.layout().content_id) == nullptr);
    CHECK(device_set_inserted(true) == Result::Ok && device_catalog_bind(items, 1) == Result::Invalid);
    device_unbind(); CHECK(!device_catalog_size() && !device_catalog_disc(0));
}
