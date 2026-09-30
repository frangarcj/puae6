// Minimal libretro frontend for the contract tests (T16, LR01). It loads a core with dlopen, like a real frontend, and enforces what
// the libretro contract asks of a core on every retro_run: the input is polled once, exactly one picture is shown, audio arrives as
// whole stereo frames, and the serialization size does not grow. Everything optional can be refused.
//   p6_libretro_host CORE FIRMWARE [--runs N] [--cycles C] [--reject-optional] [--no-log] [--no-dupe] [--bad-format]
//                    [--audio-partial K] [--audio-stall FIRST:LAST] [--reenter] [--joy PORT:MASK] [--mouse DX,DY,BUTTONS]
// Output, one line per event, parsed by tests/libretro-host/test_libretro.py:
//   run N video=WxH pitch=P dupe=D hash=H audio=F polls=K
//   log LEVEL TEXT         (only when the log interface is offered)
//   cycle C end ... / final ...
// Exit code: 0 ok, 1 a contract violation (printed as VIOLATION), 2 the core could not be loaded, 3 retro_load_game refused.
#include "libretro.h"

#include <dlfcn.h>
#include <inttypes.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <set>
#include <vector>

namespace {

struct Options {
    int runs = 10, cycles = 1;
    bool reject_optional = false, no_log = false, no_dupe = false, bad_format = false, reenter = false;
    size_t audio_partial = 0;
    int stall_first = -1, stall_last = -1;
    unsigned joy[2] = {0, 0};
    bool mouse = false;
    int mouse_dx = 0, mouse_dy = 0;
    unsigned mouse_buttons = 0;
} options;

// The core's entry points.
struct Core {
    void *handle = nullptr;
    unsigned (*api_version)();
    void (*set_environment)(retro_environment_t);
    void (*set_video_refresh)(retro_video_refresh_t);
    void (*set_audio_sample)(retro_audio_sample_t);
    void (*set_audio_sample_batch)(retro_audio_sample_batch_t);
    void (*set_input_poll)(retro_input_poll_t);
    void (*set_input_state)(retro_input_state_t);
    void (*init)();
    void (*deinit)();
    void (*get_system_info)(retro_system_info *);
    void (*get_system_av_info)(retro_system_av_info *);
    void (*set_controller_port_device)(unsigned, unsigned);
    void (*reset)();
    void (*run)();
    size_t (*serialize_size)();
    bool (*serialize)(void *, size_t);
    bool (*unserialize)(const void *, size_t);
    void (*cheat_reset)();
    void (*cheat_set)(unsigned, bool, const char *);
    bool (*load_game)(const retro_game_info *);
    bool (*load_game_special)(unsigned, const retro_game_info *, size_t);
    void (*unload_game)();
    unsigned (*get_region)();
    void *(*get_memory_data)(unsigned);
    size_t (*get_memory_size)(unsigned);
} core;

// What the callbacks have seen in the current run.
struct Run {
    int videos = 0, polls = 0, states = 0;
    bool dupe = false;
    unsigned width = 0, height = 0;
    size_t pitch = 0;
    uint64_t picture_hash = 0;
    uint64_t audio_frames = 0;
} run;

std::set<unsigned> environment_commands;
bool shutdown_requested = false;
int run_number = 0, violations = 0;
uint64_t audio_hash = UINT64_C(1469598103934665603), audio_total = 0;
retro_game_info game_info;
bool in_video_callback = false;

void violation(const char *format, ...)
{
    printf("VIOLATION run %d: ", run_number);
    va_list arguments;
    va_start(arguments, format);
    vprintf(format, arguments);
    va_end(arguments);
    printf("\n");
    ++violations;
}

uint64_t fnv(uint64_t hash, const void *data, size_t bytes)
{
    const uint8_t *p = static_cast<const uint8_t *>(data);
    for (size_t i = 0; i < bytes; ++i) {
        hash ^= p[i];
        hash *= UINT64_C(1099511628211);
    }
    return hash;
}

void RETRO_CALLCONV log_printf(enum retro_log_level level, const char *format, ...)
{
    char text[512];
    va_list arguments;
    va_start(arguments, format);
    vsnprintf(text, sizeof text, format, arguments);
    va_end(arguments);
    size_t length = strlen(text);
    while (length && text[length - 1] == '\n') text[--length] = 0;
    printf("log %d %s\n", int(level), text);
}

bool RETRO_CALLCONV environment(unsigned command, void *data)
{
    environment_commands.insert(command & 0xFFFF);
    switch (command) {
    case RETRO_ENVIRONMENT_SET_PIXEL_FORMAT:
        if (options.bad_format) return false;
        return *static_cast<retro_pixel_format *>(data) == RETRO_PIXEL_FORMAT_XRGB8888;
    case RETRO_ENVIRONMENT_GET_LOG_INTERFACE:
        if (options.no_log || options.reject_optional) return false;
        static_cast<retro_log_callback *>(data)->log = log_printf;
        return true;
    case RETRO_ENVIRONMENT_GET_CAN_DUPE:
        if (options.no_dupe || options.reject_optional) return false;
        *static_cast<bool *>(data) = true;
        return true;
    case RETRO_ENVIRONMENT_SET_GEOMETRY:
        return !options.reject_optional;
    case RETRO_ENVIRONMENT_SHUTDOWN:
        shutdown_requested = true;
        return true;
    default:
        return false;      // every other command is optional for this core and is refused
    }
}

void RETRO_CALLCONV video_refresh(const void *data, unsigned width, unsigned height, size_t pitch)
{
    ++run.videos;
    run.width = width;
    run.height = height;
    run.pitch = pitch;
    if (!data) {
        run.dupe = true;
    } else {
        uint64_t hash = UINT64_C(1469598103934665603);
        for (unsigned y = 0; y < height; ++y) hash = fnv(hash, static_cast<const uint8_t *>(data) + size_t(y) * pitch, size_t(width) * 4);
        run.picture_hash = hash;
        if (pitch < size_t(width) * 4) violation("pitch %zu is smaller than a row of %u pixels", pitch, width);
    }
    if (options.reenter && !in_video_callback && run.videos == 1) {
        // A core must not run, reset, unload or load from inside one of its own callbacks.
        in_video_callback = true;
        core.run();
        core.reset();
        core.unload_game();
        if (core.load_game(&game_info)) violation("retro_load_game succeeded from inside a callback");
        in_video_callback = false;
    }
}

void RETRO_CALLCONV audio_sample(int16_t left, int16_t right)
{
    const int16_t pair[2] = {left, right};
    audio_hash = fnv(audio_hash, pair, sizeof pair);
    ++audio_total;
    ++run.audio_frames;
}

size_t RETRO_CALLCONV audio_batch(const int16_t *data, size_t frames)
{
    if (!data && frames) violation("audio batch without data for %zu frames", frames);
    if (run_number >= options.stall_first && run_number <= options.stall_last) return 0;      // a frontend that cannot take audio now
    size_t taken = frames;
    if (options.audio_partial && taken > options.audio_partial) taken = options.audio_partial;
    if (taken) audio_hash = fnv(audio_hash, data, taken * 2 * sizeof(int16_t));
    audio_total += taken;
    run.audio_frames += taken;
    return taken;
}

void RETRO_CALLCONV input_poll(void) { ++run.polls; }

int16_t RETRO_CALLCONV input_state(unsigned port, unsigned device, unsigned index, unsigned id)
{
    ++run.states;
    if (index != 0 || port > 1) return 0;
    if (device == RETRO_DEVICE_JOYPAD) return int16_t((options.joy[port] >> id) & 1);
    if (device == RETRO_DEVICE_MOUSE && port == 0 && options.mouse) {
        switch (id) {
        case RETRO_DEVICE_ID_MOUSE_X: return int16_t(options.mouse_dx);
        case RETRO_DEVICE_ID_MOUSE_Y: return int16_t(options.mouse_dy);
        case RETRO_DEVICE_ID_MOUSE_LEFT: return int16_t(options.mouse_buttons & 1);
        case RETRO_DEVICE_ID_MOUSE_RIGHT: return int16_t((options.mouse_buttons >> 1) & 1);
        case RETRO_DEVICE_ID_MOUSE_MIDDLE: return int16_t((options.mouse_buttons >> 2) & 1);
        default: return 0;
        }
    }
    return 0;
}

template <class T> bool bind(T &slot, const char *name)
{
    slot = reinterpret_cast<T>(dlsym(core.handle, name));
    if (!slot) fprintf(stderr, "the core does not export %s\n", name);
    return slot != nullptr;
}

bool load_core(const char *path)
{
    core.handle = dlopen(path, RTLD_NOW | RTLD_LOCAL);
    if (!core.handle) {
        fprintf(stderr, "dlopen %s: %s\n", path, dlerror());
        return false;
    }
    return bind(core.api_version, "retro_api_version") && bind(core.set_environment, "retro_set_environment") &&
           bind(core.set_video_refresh, "retro_set_video_refresh") && bind(core.set_audio_sample, "retro_set_audio_sample") &&
           bind(core.set_audio_sample_batch, "retro_set_audio_sample_batch") && bind(core.set_input_poll, "retro_set_input_poll") &&
           bind(core.set_input_state, "retro_set_input_state") && bind(core.init, "retro_init") && bind(core.deinit, "retro_deinit") &&
           bind(core.get_system_info, "retro_get_system_info") && bind(core.get_system_av_info, "retro_get_system_av_info") &&
           bind(core.set_controller_port_device, "retro_set_controller_port_device") && bind(core.reset, "retro_reset") &&
           bind(core.run, "retro_run") && bind(core.serialize_size, "retro_serialize_size") && bind(core.serialize, "retro_serialize") &&
           bind(core.unserialize, "retro_unserialize") && bind(core.cheat_reset, "retro_cheat_reset") &&
           bind(core.cheat_set, "retro_cheat_set") && bind(core.load_game, "retro_load_game") &&
           bind(core.load_game_special, "retro_load_game_special") && bind(core.unload_game, "retro_unload_game") &&
           bind(core.get_region, "retro_get_region") && bind(core.get_memory_data, "retro_get_memory_data") &&
           bind(core.get_memory_size, "retro_get_memory_size");
}

bool read_file(const char *path, std::vector<uint8_t> *data)
{
    FILE *file = fopen(path, "rb");
    if (!file) {
        perror(path);
        return false;
    }
    uint8_t buffer[65536];
    for (size_t n; (n = fread(buffer, 1, sizeof buffer, file)) > 0;) data->insert(data->end(), buffer, buffer + n);
    fclose(file);
    return true;
}

bool parse(int argc, char **argv)
{
    for (int i = 3; i < argc; ++i) {
        const char *a = argv[i];
        auto next = [&]() -> const char * { return i + 1 < argc ? argv[++i] : ""; };
        if (!strcmp(a, "--runs")) options.runs = atoi(next());
        else if (!strcmp(a, "--cycles")) options.cycles = atoi(next());
        else if (!strcmp(a, "--reject-optional")) options.reject_optional = true;
        else if (!strcmp(a, "--no-log")) options.no_log = true;
        else if (!strcmp(a, "--no-dupe")) options.no_dupe = true;
        else if (!strcmp(a, "--bad-format")) options.bad_format = true;
        else if (!strcmp(a, "--reenter")) options.reenter = true;
        else if (!strcmp(a, "--audio-partial")) options.audio_partial = size_t(atol(next()));
        else if (!strcmp(a, "--audio-stall")) sscanf(next(), "%d:%d", &options.stall_first, &options.stall_last);
        else if (!strcmp(a, "--joy")) {
            unsigned port = 0, mask = 0;
            if (sscanf(next(), "%u:%x", &port, &mask) == 2 && port < 2) options.joy[port] = mask;
        } else if (!strcmp(a, "--mouse")) {
            options.mouse = true;
            sscanf(next(), "%d,%d,%u", &options.mouse_dx, &options.mouse_dy, &options.mouse_buttons);
        } else {
            fprintf(stderr, "unknown option %s\n", a);
            return false;
        }
    }
    return options.runs > 0 && options.cycles > 0;
}

}  // namespace

int main(int argc, char **argv)
{
    setvbuf(stdout, nullptr, _IOLBF, 0);
    if (argc < 3 || !parse(argc, argv)) {
        fprintf(stderr, "usage: %s CORE FIRMWARE [--runs N] [--cycles C] [options]\n", argv[0]);
        return 64;
    }
    if (!load_core(argv[1])) return 2;
    if (core.api_version() != RETRO_API_VERSION) {
        printf("VIOLATION api version %u\n", core.api_version());
        return 1;
    }
    std::vector<uint8_t> firmware;
    if (!read_file(argv[2], &firmware)) return 2;
    game_info = {argv[2], firmware.data(), firmware.size(), nullptr};

    core.set_environment(environment);
    core.set_video_refresh(video_refresh);
    core.set_audio_sample(audio_sample);
    core.set_audio_sample_batch(audio_batch);
    core.set_input_poll(input_poll);
    core.set_input_state(input_state);
    core.init();
    retro_system_info info = {};
    core.get_system_info(&info);
    printf("core name=%s version=%s extensions=%s need_fullpath=%d\n", info.library_name, info.library_version, info.valid_extensions,
           int(info.need_fullpath));
    if (options.mouse) core.set_controller_port_device(0, RETRO_DEVICE_MOUSE);

    int status = 0;
    for (int cycle = 1; cycle <= options.cycles && !status; ++cycle) {
        run_number = 0;
        if (!core.load_game(&game_info)) {
            printf("load refused\n");
            status = 3;
            break;
        }
        retro_system_av_info av = {};
        core.get_system_av_info(&av);
        printf("av base=%ux%u max=%ux%u aspect=%.6f fps=%.6f rate=%.0f region=%u\n", av.geometry.base_width, av.geometry.base_height,
               av.geometry.max_width, av.geometry.max_height, double(av.geometry.aspect_ratio), av.timing.fps, av.timing.sample_rate,
               core.get_region());
        const size_t serialize_size = core.serialize_size();
        audio_hash = UINT64_C(1469598103934665603);
        audio_total = 0;
        for (int n = 1; n <= options.runs && !shutdown_requested; ++n) {
            run_number = n;
            run = Run{};
            core.run();
            printf("run %d video=%ux%u pitch=%zu dupe=%d hash=%016" PRIx64 " audio=%" PRIu64 " polls=%d states=%d\n", n, run.width, run.height,
                   run.pitch, int(run.dupe), run.picture_hash, run.audio_frames, run.polls, run.states);
            if (run.videos != 1) violation("%d pictures were shown (exactly one is required)", run.videos);
            if (run.polls != 1) violation("the input was polled %d times (once is required)", run.polls);
            if (core.serialize_size() != serialize_size) violation("the serialization size changed");
            if (core.serialize_size() != 0) violation("a size of %zu was announced for a state nothing can write", core.serialize_size());
        }
        core.unload_game();
        printf("cycle %d end runs=%d audio_frames=%" PRIu64 " audio_hash=%016" PRIx64 "\n", cycle, run_number, audio_total, audio_hash);
        if (shutdown_requested) status = 1;
    }
    core.deinit();
    printf("final violations=%d environment=", violations);
    bool first = true;
    for (unsigned command : environment_commands) {
        printf("%s%u", first ? "" : ",", command);
        first = false;
    }
    printf("\n");
    dlclose(core.handle);
    return status ? status : violations ? 1 : 0;
}
