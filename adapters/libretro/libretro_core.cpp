// PUAE6 libretro core: a thin adapter over the native ABI (bridge/include/p6.h), T16.
//
// retro_run polls the input once, makes exactly one p6_run_frame call, and hands over one picture and the audio of that unit. The
// core has no scheduler of its own, never sleeps and never reads the host clock: the frontend paces it. It includes libretro.h and
// p6.h only; nothing of the engine is visible here.
//
// What this minimal adapter does not do yet (see docs/02-contracts.md and the plan): core options, the content and system-directory
// file handling of Kickstart and disks (T17), save states (T18: serialize_size is 0, which is the honest answer until then), the
// keyboard, and any memory region. The content given to retro_load_game is the firmware image itself.
#include "libretro.h"
#include "p6.h"

#include <algorithm>
#include <stdarg.h>
#include <stdio.h>
#include <string.h>
#include <vector>

#ifndef P6_LIBRETRO_VERSION
#define P6_LIBRETRO_VERSION "dev"
#endif

namespace {

constexpr uint32_t kAudioRateHz = 48000;
constexpr int64_t kRtcEpoch = 946684800;          // 2000-01-01T00:00:00Z, the contract's default, as the headless runner uses
constexpr uint64_t kSeed = 1;
constexpr size_t kPendingAudioFrames = 16384;     // a stalled frontend may hold this many stereo frames back; older ones are dropped

retro_environment_t environment_cb;
retro_video_refresh_t video_cb;
retro_audio_sample_t audio_sample_cb;
retro_audio_sample_batch_t audio_batch_cb;
retro_input_poll_t input_poll_cb;
retro_input_state_t input_state_cb;
retro_log_printf_t log_cb;

struct Core {
    bool initialized = false;             // p6_init succeeded and p6_shutdown has not been called
    bool loaded = false;                  // p6_load succeeded and p6_unload has not been called
    bool faulted = false;                 // the image failed: only unload recovers
    bool running = false;                 // a retro_* call is in progress (they are not reentrant)
    bool can_dupe = false;
    bool announced_size = false;
    uint32_t standard = P6_VIDEO_PAL;
    uint32_t device[2] = {RETRO_DEVICE_JOYPAD, RETRO_DEVICE_JOYPAD};
    std::vector<uint8_t> firmware;        // p6_load borrows it until p6_unload; libretro's buffer lives only during retro_load_game
    std::vector<uint8_t> last_picture;    // a copy, kept only when the frontend cannot repeat a picture by itself
    uint32_t last_width = 0, last_height = 0;
    uint32_t announced_width = 0, announced_height = 0;
    std::vector<uint8_t> blank;           // what a run shows before the first picture exists
    std::vector<int16_t> pending;         // interleaved stereo int16 the frontend has not taken yet
    bool warned_overflow = false, warned_port = false;
} core;

struct CallGuard {
    bool ok;
    CallGuard() : ok(!core.running) { core.running = true; }
    ~CallGuard() { if (ok) core.running = false; }
    CallGuard(const CallGuard &) = delete;
    CallGuard &operator=(const CallGuard &) = delete;
};

void log(retro_log_level level, const char *format, ...)
{
    if (!log_cb && level < RETRO_LOG_WARN) return;      // nobody asked for debug or info text: do not format it every unit
    char text[512];
    va_list arguments;
    va_start(arguments, format);
    vsnprintf(text, sizeof text, format, arguments);
    va_end(arguments);
    if (log_cb)
        log_cb(level, "[PUAE6] %s\n", text);
    else if (level >= RETRO_LOG_WARN)
        fprintf(stderr, "[PUAE6] %s\n", text);
}

p6_error make_error()
{
    p6_error error = {};
    error.abi_version = P6_ABI_VERSION;
    error.struct_size = sizeof error;
    return error;
}

void report(const char *what, p6_result result, const p6_error &error)
{
    log(RETRO_LOG_ERROR, "%s failed (%d): %s", what, int(result), error.message);
}

void stop_image()
{
    if (core.loaded) {
        p6_error error = make_error();
        const p6_result result = p6_unload(&error);
        if (result != P6_OK) report("p6_unload", result, error);
    }
    if (core.initialized) p6_shutdown(nullptr);
    core.loaded = core.initialized = core.faulted = false;
    core.announced_size = false;
    core.last_width = core.last_height = 0;
    core.last_picture.clear();
    core.pending.clear();
    core.warned_overflow = false;
}

p6_input read_input()
{
    p6_input input = {};
    input.abi_version = P6_ABI_VERSION;
    input.struct_size = sizeof input;
    for (unsigned port = 0; port < 2; ++port) {
        p6_port_input &out = input.ports[port];
        if (core.device[port] == RETRO_DEVICE_JOYPAD) {
            auto pressed = [&](unsigned id) { return input_state_cb(port, RETRO_DEVICE_JOYPAD, 0, id) != 0; };
            out.type = P6_PORT_JOYSTICK;
            if (pressed(RETRO_DEVICE_ID_JOYPAD_B)) out.buttons |= P6_BUTTON_1;
            if (pressed(RETRO_DEVICE_ID_JOYPAD_A)) out.buttons |= P6_BUTTON_2;
            if (pressed(RETRO_DEVICE_ID_JOYPAD_X)) out.buttons |= P6_BUTTON_3;
            if (pressed(RETRO_DEVICE_ID_JOYPAD_UP)) out.buttons |= P6_BUTTON_UP;
            if (pressed(RETRO_DEVICE_ID_JOYPAD_DOWN)) out.buttons |= P6_BUTTON_DOWN;
            if (pressed(RETRO_DEVICE_ID_JOYPAD_LEFT)) out.buttons |= P6_BUTTON_LEFT;
            if (pressed(RETRO_DEVICE_ID_JOYPAD_RIGHT)) out.buttons |= P6_BUTTON_RIGHT;
        } else if (core.device[port] == RETRO_DEVICE_MOUSE && port == 0) {
            auto state = [&](unsigned id) { return int(input_state_cb(0, RETRO_DEVICE_MOUSE, 0, id)); };
            out.type = P6_PORT_MOUSE;
            out.dx = int16_t(std::clamp(state(RETRO_DEVICE_ID_MOUSE_X), -32768, 32767));
            out.dy = int16_t(std::clamp(state(RETRO_DEVICE_ID_MOUSE_Y), -32768, 32767));
            if (state(RETRO_DEVICE_ID_MOUSE_LEFT)) out.buttons |= P6_BUTTON_1;
            if (state(RETRO_DEVICE_ID_MOUSE_RIGHT)) out.buttons |= P6_BUTTON_2;
            if (state(RETRO_DEVICE_ID_MOUSE_MIDDLE)) out.buttons |= P6_BUTTON_3;
        } else if (core.device[port] == RETRO_DEVICE_MOUSE && !core.warned_port) {
            core.warned_port = true;
            log(RETRO_LOG_WARN, "a mouse is only supported on port 0; port %u is left empty", port);
        }
    }
    return input;
}

// One picture per run. The active raster is what the renderer drew, so that is what the frontend gets, not the padded buffer.
void deliver_video(const p6_frame &frame, bool have_frame)
{
    if (have_frame && frame.video && frame.active.width && frame.active.height) {
        const uint8_t *origin = static_cast<const uint8_t *>(frame.video) + size_t(frame.active.y) * frame.video_stride_bytes +
                                size_t(frame.active.x) * 4;
        const uint32_t width = frame.active.width, height = frame.active.height;
        if (!core.announced_size || width != core.announced_width || height != core.announced_height) {
            retro_game_geometry geometry = {};
            geometry.base_width = geometry.max_width = width;
            geometry.base_height = geometry.max_height = height;
            geometry.aspect_ratio = float(double(width) * frame.aspect_num / (double(height) * frame.aspect_den));
            environment_cb(RETRO_ENVIRONMENT_SET_GEOMETRY, &geometry);      // optional: a refusal only costs the frontend's hint
            core.announced_size = true;
            core.announced_width = width;
            core.announced_height = height;
        }
        if (!core.can_dupe) {
            core.last_picture.resize(size_t(width) * height * 4);
            for (uint32_t y = 0; y < height; ++y)
                memcpy(&core.last_picture[size_t(y) * width * 4], origin + size_t(y) * frame.video_stride_bytes, size_t(width) * 4);
        }
        core.last_width = width;
        core.last_height = height;
        video_cb(origin, width, height, frame.video_stride_bytes);
        return;
    }
    // No new picture in this unit (a mode change, or nothing drawn yet): repeat the previous one.
    if (core.last_width && core.can_dupe) {
        video_cb(nullptr, core.last_width, core.last_height, size_t(core.last_width) * 4);
    } else if (core.last_width && !core.last_picture.empty()) {
        video_cb(core.last_picture.data(), core.last_width, core.last_height, size_t(core.last_width) * 4);
    } else {
        const uint32_t width = core.announced_width ? core.announced_width : 756, height = core.announced_height ? core.announced_height : 574;
        core.blank.assign(size_t(width) * height * 4, 0);
        video_cb(core.blank.data(), width, height, size_t(width) * 4);
    }
}

// The frontend may take less than it is offered, or nothing for a while. What it does not take stays queued, bounded, and is offered
// again first in the next run; the core never waits for it.
void deliver_audio(const p6_frame &frame, bool have_frame)
{
    if (have_frame && frame.audio && frame.audio_frames) {
        core.pending.insert(core.pending.end(), frame.audio, frame.audio + size_t(frame.audio_frames) * 2);
        if (core.pending.size() > kPendingAudioFrames * 2) {
            core.pending.erase(core.pending.begin(), core.pending.end() - std::ptrdiff_t(kPendingAudioFrames * 2));
            if (!core.warned_overflow) {
                core.warned_overflow = true;
                log(RETRO_LOG_WARN, "the frontend is not taking audio: the oldest frames are dropped");
            }
        }
    }
    size_t offered = 0;
    while (offered * 2 < core.pending.size()) {
        const size_t left = core.pending.size() / 2 - offered;
        size_t taken = 0;
        if (audio_batch_cb) {
            taken = audio_batch_cb(core.pending.data() + offered * 2, left);
        } else if (audio_sample_cb) {
            for (; taken < left; ++taken) audio_sample_cb(core.pending[(offered + taken) * 2], core.pending[(offered + taken) * 2 + 1]);
        }
        if (taken == 0) break;
        offered += std::min(taken, left);
    }
    core.pending.erase(core.pending.begin(), core.pending.begin() + std::ptrdiff_t(offered * 2));
}

}  // namespace

extern "C" {

RETRO_API unsigned retro_api_version(void) { return RETRO_API_VERSION; }

RETRO_API void retro_set_environment(retro_environment_t callback)
{
    environment_cb = callback;
    bool no_game = false;
    if (environment_cb) environment_cb(RETRO_ENVIRONMENT_SET_SUPPORT_NO_GAME, &no_game);   // optional; the core needs content
}

RETRO_API void retro_set_video_refresh(retro_video_refresh_t callback) { video_cb = callback; }
RETRO_API void retro_set_audio_sample(retro_audio_sample_t callback) { audio_sample_cb = callback; }
RETRO_API void retro_set_audio_sample_batch(retro_audio_sample_batch_t callback) { audio_batch_cb = callback; }
RETRO_API void retro_set_input_poll(retro_input_poll_t callback) { input_poll_cb = callback; }
RETRO_API void retro_set_input_state(retro_input_state_t callback) { input_state_cb = callback; }

RETRO_API void retro_init(void)
{
    retro_log_callback logging = {};
    if (environment_cb && environment_cb(RETRO_ENVIRONMENT_GET_LOG_INTERFACE, &logging) && logging.log)
        log_cb = logging.log;
}

RETRO_API void retro_deinit(void)
{
    stop_image();
    log_cb = nullptr;
}

RETRO_API void retro_get_system_info(struct retro_system_info *info)
{
    memset(info, 0, sizeof *info);
    info->library_name = "PUAE6";
    info->library_version = P6_LIBRETRO_VERSION;
    info->valid_extensions = "rom|bin";
    info->need_fullpath = false;
    info->block_extract = false;
}

RETRO_API void retro_get_system_av_info(struct retro_system_av_info *info)
{
    memset(info, 0, sizeof *info);
    p6_caps caps = {};
    caps.abi_version = P6_ABI_VERSION;
    caps.struct_size = sizeof caps;
    p6_query_caps(&caps, nullptr);
    const bool ntsc = core.standard == P6_VIDEO_NTSC;
    // The nominal active raster of each standard; a unit can draw less, and the geometry is updated when it does.
    const uint32_t width = 756, height = ntsc ? 484 : 574;
    info->geometry.base_width = width;
    info->geometry.base_height = height;
    info->geometry.max_width = caps.max_video_width ? caps.max_video_width : 760;
    info->geometry.max_height = caps.max_video_height ? caps.max_video_height : 576;
    info->geometry.aspect_ratio = float(double(width) * (ntsc ? 43 : 26) / (double(height) * (ntsc ? 50 : 25)));
    info->timing.fps = ntsc ? 715909.0 / 11921.0 : 3546895.0 / 70824.0;    // the bridge's rate for a progressive short field
    info->timing.sample_rate = kAudioRateHz;
}

RETRO_API void retro_set_controller_port_device(unsigned port, unsigned device)
{
    if (port < 2 && (device == RETRO_DEVICE_NONE || device == RETRO_DEVICE_JOYPAD || device == RETRO_DEVICE_MOUSE))
        core.device[port] = device;
}

RETRO_API void retro_reset(void)
{
    CallGuard guard;
    if (!guard.ok || !core.loaded || core.faulted) return;
    p6_error error = make_error();
    const p6_result result = p6_reset(&error);
    if (result != P6_OK) report("p6_reset", result, error);
}

RETRO_API void retro_run(void)
{
    CallGuard guard;
    if (!guard.ok) {
        log(RETRO_LOG_ERROR, "retro_run was called from inside a libretro call and is ignored");
        return;
    }
    if (!core.loaded) return;
    p6_frame frame = {};
    bool have_frame = false;
    if (!core.faulted) {
        input_poll_cb();
        const p6_input input = read_input();
        frame.abi_version = P6_ABI_VERSION;
        frame.struct_size = sizeof frame;
        p6_error error = make_error();
        const p6_result result = p6_run_frame(&input, P6_RENDER_VIDEO | P6_RENDER_AUDIO, &frame, &error);
        if (result == P6_OK) {
            have_frame = true;
            log(RETRO_LOG_DEBUG, "unit=%llu ticks=%llu ports=%u:%x:%d:%d,%u:%x:%d:%d", (unsigned long long)frame.unit_index,
                (unsigned long long)frame.master_ticks, input.ports[0].type, input.ports[0].buttons, input.ports[0].dx,
                input.ports[0].dy, input.ports[1].type, input.ports[1].buttons, input.ports[1].dx, input.ports[1].dy);
        } else {
            report("p6_run_frame", result, error);
            core.faulted = true;
            environment_cb(RETRO_ENVIRONMENT_SHUTDOWN, nullptr);    // an image that faulted only recovers through unload
        }
    } else {
        input_poll_cb();        // a run still polls once and shows one picture, so a stalled image never starves the frontend
    }
    deliver_video(frame, have_frame);
    deliver_audio(frame, have_frame);
}

RETRO_API size_t retro_serialize_size(void) { return 0; }                       // no state is implemented yet (T18)
RETRO_API bool retro_serialize(void *, size_t) { return false; }
RETRO_API bool retro_unserialize(const void *, size_t) { return false; }
RETRO_API void retro_cheat_reset(void) {}
RETRO_API void retro_cheat_set(unsigned, bool, const char *) {}

RETRO_API bool retro_load_game(const struct retro_game_info *game)
{
    CallGuard guard;
    if (!guard.ok) return false;
    if (!game || !game->data || game->size == 0) {
        log(RETRO_LOG_ERROR, "no firmware content was given");
        return false;
    }
    if (core.loaded) {
        log(RETRO_LOG_ERROR, "a game is already loaded");
        return false;
    }
    retro_pixel_format format = RETRO_PIXEL_FORMAT_XRGB8888;
    if (!environment_cb(RETRO_ENVIRONMENT_SET_PIXEL_FORMAT, &format)) {
        log(RETRO_LOG_ERROR, "the frontend does not accept XRGB8888, the only pixel format of this core");
        return false;
    }
    bool can_dupe = false;
    core.can_dupe = environment_cb(RETRO_ENVIRONMENT_GET_CAN_DUPE, &can_dupe) && can_dupe;
    try {
        core.firmware.assign(static_cast<const uint8_t *>(game->data), static_cast<const uint8_t *>(game->data) + game->size);
    } catch (...) {
        log(RETRO_LOG_ERROR, "not enough memory for the firmware");
        return false;
    }
    p6_config config = {};
    config.abi_version = P6_ABI_VERSION;
    config.struct_size = sizeof config;
    config.machine = P6_MACHINE_A500;
    config.video_standard = core.standard;
    config.audio_rate_hz = kAudioRateHz;
    config.random_seed = kSeed;
    config.rtc_epoch_seconds = kRtcEpoch;
    p6_error error = make_error();
    p6_result result = p6_init(&config, &error);
    if (result != P6_OK) {
        report("p6_init", result, error);
        return false;
    }
    core.initialized = true;
    result = p6_load(p6_buffer{core.firmware.data(), core.firmware.size()}, nullptr, 0, &error);
    if (result != P6_OK) {
        report("p6_load", result, error);
        stop_image();
        return false;
    }
    core.loaded = true;
    return true;
}

RETRO_API bool retro_load_game_special(unsigned, const struct retro_game_info *, size_t) { return false; }

RETRO_API void retro_unload_game(void)
{
    CallGuard guard;
    if (guard.ok) stop_image();
}

RETRO_API unsigned retro_get_region(void) { return core.standard == P6_VIDEO_NTSC ? RETRO_REGION_NTSC : RETRO_REGION_PAL; }
RETRO_API void *retro_get_memory_data(unsigned) { return nullptr; }
RETRO_API size_t retro_get_memory_size(unsigned) { return 0; }

}  // extern "C"
