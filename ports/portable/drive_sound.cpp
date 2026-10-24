/* SPDX-License-Identifier: GPL-2.0-or-later
 * Mechanical presentation, using WinUAE 6's driveclick boundary and playback
 * model (driveclick.cpp, Copyright 2004 James Bagg, Toni Wilen). The PCM below
 * is synthesized here, not sampled from third-party recordings. No device,
 * filesystem, host clock, PRNG shared with hardware, or Paula activation.
 */
#include "sysconfig.h"
#include "sysdeps.h"
#include "options.h"
#include "audio.h"
#include "driveclick.h"
#include "drive_sound.h"
#include <array>

namespace {
constexpr unsigned kShift = 10;
constexpr unsigned kLengths[DS_END] = {512, 1024, 1024, 8192, 8192};
constexpr unsigned kEnd(unsigned kind) { return kLengths[kind] << kShift; }
constexpr std::array<int16_t, 8192> wave(unsigned kind)
{
    std::array<int16_t, 8192> out{};
    uint32_t noise = 0x50464453u + kind;
    for (unsigned i = 0; i < kLengths[kind]; ++i) {
        noise = noise * 1664525u + 1013904223u;
        const int grain = int(noise >> 16) - 32768;
        const int tooth = int(i % 128 < 64 ? i % 128 : 128 - i % 128) - 32;
        int v = kind == DS_CLICK ? grain / 3 : tooth * 16 + grain / 64;
        if (kind == DS_SPINND) v += grain / 48;
        if (kind == DS_CLICK || kind == DS_START || kind == DS_SNATCH)
            v = v * int(kLengths[kind] - i) / int(kLengths[kind]);
        out[i] = int16_t(v);
    }
    return out;
}
constexpr std::array<std::array<int16_t, 8192>, DS_END> kSamples = {
    wave(DS_CLICK), wave(DS_SPIN), wave(DS_SPINND), wave(DS_START), wave(DS_SNATCH)};
struct Drive {
    uint32_t starting, spinning, spun, disk, cylinder;
    uint32_t position[DS_END];
} drives[4];
unsigned mix_gain;
bool prefs_ok = true;
unsigned step() { return (44100u << kShift) / unsigned(currprefs.sound_freq); }
bool enabled(int drive) { return drive >= 0 && drive < 4 && currprefs.floppyslots[drive].dfxclick == 1; }
int sample(Drive &d, unsigned kind, bool loop = false)
{
    auto &p = d.position[kind];
    if (loop) p %= kEnd(kind);
    if (p >= kEnd(kind)) return 0;
    const int v = kSamples[kind][p >> kShift];
    p += step();
    return v;
}
int next_sample()
{
    int total = 0, count = 0;
    for (int i = 0; i < 4; ++i) {
        if (!enabled(i)) continue;
        Drive &d = drives[i];
        int value = 0, sources = 0;
        if (d.spinning) {
            ++sources;
            if (d.starting) {
                const unsigned kind = d.spun ? DS_START : DS_SNATCH;
                if (d.position[kind] < kEnd(kind)) value = sample(d, kind);
                else { d.starting = 0; d.spun = 1; }
            }
            if (!d.starting) value = sample(d, d.disk ? DS_SPIN : DS_SPINND, true);
        }
        if (d.position[DS_CLICK] < kEnd(DS_CLICK)) {
            value += sample(d, DS_CLICK);
            ++sources;
        }
        if (sources) { total += value / sources; ++count; }
    }
    return count ? total / count : 0;
}
int16_t add(int16_t value, int extra)
{
    const int sum = int(value) + extra;
    return int16_t(sum < -32768 ? -32768 : sum > 32767 ? 32767 : sum);
}
}

bool driveclick_wave_initialized;
void driveclick_reset()
{
    for (int i = 0; i < 4; ++i) {
        Drive &d = drives[i];
        d = {};
        d.disk = currprefs.floppyslots[i].df[0] != 0;
        for (unsigned k = 0; k < DS_END; ++k) d.position[k] = kEnd(k);
    }
}
void driveclick_init() { prefs_ok = true; driveclick_wave_initialized = true; driveclick_reset(); }
void driveclick_free() { driveclick_wave_initialized = false; mix_gain = 0; driveclick_reset(); }
void driveclick_click(int drive, int cylinder)
{
    if (!enabled(drive) || cylinder < 0 || cylinder >= CLICK_TRACKS) return;
    Drive &d = drives[drive];
    if (!d.cylinder && !cylinder) return; // WinUAE's track-zero noclick.
    d.cylinder = unsigned(cylinder);
    d.position[DS_CLICK] = 0;
}
void driveclick_motor(int drive, int running)
{
    if (!enabled(drive)) return;
    Drive &d = drives[drive];
    if (!running) { d.starting = d.spinning = 0; return; }
    if (d.spinning) return;
    d.starting = d.spinning = 1;
    if (!d.spun) d.position[DS_SNATCH] = 0;
    if (running == 2) d.position[DS_START] = 0;
    d.position[DS_SPIN] = d.position[DS_SPINND] = 0;
}
void driveclick_insert(int drive, int eject)
{
    if (!enabled(drive)) return;
    drives[drive].disk = !eject;
    if (eject) drives[drive].spun = 0;
}
void driveclick_mix(uae_s16 *buffer, int stereo, int mask)
{
    const int v = next_sample() * int(mix_gain) / 100; // Advance even when delivery is muted.
    if (!mix_gain) return; // Preserve Paula's exact sample representation by default.
    if (mask & 1) buffer[0] = add(buffer[0], v);
    if (stereo == SND_STEREO && (mask & 2)) buffer[1] = add(buffer[1], v);
}
void driveclick_check_prefs()
{
    for (unsigned i = 0; i < 4; ++i)
        if (currprefs.floppyslots[i].dfxclick < 0 || currprefs.floppyslots[i].dfxclick > 1
            || changed_prefs.floppyslots[i].dfxclick < 0 || changed_prefs.floppyslots[i].dfxclick > 1)
            prefs_ok = false; // Physical/external sample modes have no portable backend.
}
namespace p6_drive_sound {
void gain(unsigned percent) { mix_gain = percent <= 100 ? percent : 0; }
bool healthy() { return prefs_ok; }
bool get(uint32_t *w, unsigned n)
{
    if (!w || n != kWords) return false;
    for (const Drive &d : drives) {
        *w++ = d.starting; *w++ = d.spinning; *w++ = d.spun; *w++ = d.disk; *w++ = d.cylinder;
        for (auto p : d.position) *w++ = p;
    }
    return true;
}
bool valid(const uint32_t *w, unsigned n)
{
    if (!w || n != kWords) return false;
    for (unsigned i = 0; i < 4; ++i, w += 10) {
        for (unsigned b = 0; b < 4; ++b) if (w[b] > 1) return false;
        if (w[0] && !w[1]) return false;
        if (w[4] >= CLICK_TRACKS) return false;
        for (unsigned k = 0; k < DS_END; ++k) if (w[5 + k] > kEnd(k) + (1u << kShift)) return false;
    }
    return true;
}
bool set(const uint32_t *w, unsigned n)
{
    if (!valid(w, n)) return false;
    for (Drive &d : drives) {
        d.starting = *w++; d.spinning = *w++; d.spun = *w++; d.disk = *w++; d.cylinder = *w++;
        for (auto &p : d.position) p = *w++;
    }
    return true;
}
}
