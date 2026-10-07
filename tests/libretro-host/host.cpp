// Minimal libretro frontend for the contract tests (T16, LR01). It loads a core with dlopen, like a real frontend, and enforces what
// the libretro contract asks of a core on every retro_run: the input is polled once, exactly one picture is shown, audio arrives as
// whole stereo frames, and the serialization size does not grow. Everything optional can be refused.
//   p6_libretro_host CORE [--system DIR] [--content PATH] [--runs N] [--cycles C] [--reject-optional] [--no-log] [--no-dupe] [--bad-format]
//                    [--audio-partial K] [--audio-stall FIRST:LAST] [--reenter] [--joy PORT:MASK] [--mouse DX,DY,BUTTONS]
//                    [--no-vfs] [--vfs-claim BYTES] [--retry-system DIR] [--legacy-disk-control] [--legacy-message]
//                    [--initial INDEX:PATH] [--disk RUN:COMMAND]... [--disk-during RUN:COMMAND]...
//                    [--options-api 0|1|2] [--no-options] [--set KEY=VALUE]... [--set-at RUN:KEY=VALUE]...
// The core gets the Kickstart from the system directory DIR (the frontend's GET_SYSTEM_DIRECTORY) and the content PATH by name (need_fullpath),
// read through a VFS the host offers (unless --no-vfs) that wraps stdio and prints every open. No --content is a load without content.
// COMMAND is a Disk Control call: eject, insert, index=N, remove=N, replace=N:PATH, add. --disk makes it before run RUN (after run RUN-1),
// --disk-during from inside the video callback of run RUN, where a core must only record it.
// Core options (T17b): the host offers the interface of --options-api (2 by default: v2 with categories; 1: v1 only; 0: the legacy SET_VARIABLES
// only) or none at all (--no-options). --set gives the value the frontend holds for a key from the start (a stale value included: the host does not
// check it, as a frontend with an old configuration does not); --set-at changes it before run RUN, as a person does in the menu, and raises
// GET_VARIABLE_UPDATE once. A key the core did not register is not held.
// Save states (T18): the host plays a frontend that saves, loads and speculates. A "position" is the frame of the emulated timeline a run belongs to: the visible
// run n is at position n, a load puts the position back where its state was saved, and a speculative run after run n is at position n+1, n+2, ... Scripted input
// (--joy-at) and disk commands (--disk, --disk-during) belong to a position, so a branch that runs ahead sees the future input and a replay after a load sees it again.
//   [--do RUN:ACTION]... [--ahead RUN:K]... [--ahead-every K] [--joy-at POSITION:PORT:MASK]...
// ACTION, executed after visible run RUN (0 is before the first run): save:SLOT, load:SLOT[:VARIANT], savefile:PATH, loadfile:PATH[:VARIANT], reset,
// images (print the Disk Control view: count, index, eject), disk:COMMAND (a Disk Control call between two runs, see COMMAND below).
// A saved blob is the whole serialize_size bytes, prefilled with 0xA5 so that bytes the core does not write are noticed. VARIANT is a list of edits made to a
// copy of the blob before it is handed to retro_unserialize: size=D (blob size = serialize_size + D, a longer one is filled with fill=), usedsize=D (relative to
// the end of the frame: header + section + native state, read from the header), fill=0xHH (for appended bytes, 0 by default), xor=OFFSET (flip a byte; a negative
// offset counts from the end of the frame), xorend=K (flip the byte K from the end of the buffer), pos=N (the position a state loaded from a file belongs to).
// --ahead RUN:K is runahead after visible run RUN: save, K speculative runs (picture and audio ignored, as RetroArch's runahead does), load the saved state.
// --ahead-every K does it after every visible run.
// Output, one line per event, parsed by tests/libretro-host/test_libretro.py:
//   run N video=WxH pitch=P dupe=D hash=H px=P audio=F polls=K states=S ahash=A unit=U ticks=T   (px: the pixel in the middle of the picture, XRGB8888;
//                                                          ahash: the audio of that run alone; unit, ticks: from the core's debug log)
//   spec P video=WxH dupe=D hash=H px=P audio=F ahash=A unit=U ticks=T       (a speculative run at position P)
//   state size=S / state save NAME ok=R size=S used=U hash=H section=H / state load NAME VARIANT ok=R size=S / state final ok=R size=S hash=H / state reset
//   log LEVEL TEXT         (only when the log interface is offered)
//   message TEXT           (SET_MESSAGE or SET_MESSAGE_EXT)
//   vfs open PATH / initial result=R / disk-control ext=E / images count=N index=I eject=E / image I path=P label=L / disk RUN COMMAND ...
//   options api=A count=N categories=C / category KEY / option KEY default=D values=A|B|... [category=C] desc=TEXT
//   set RUN KEY=VALUE / geometry WxH aspect=A / av-after RUN base=... (the av line again, after the run that followed a --set-at)
//   cycle C end ... / final ...
// Exit code: 0 ok, 1 a contract violation (printed as VIOLATION), 2 the core could not be loaded, 3 retro_load_game refused.
#include "libretro.h"

#include <algorithm>
#include <dlfcn.h>
#include <inttypes.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <map>
#include <set>
#include <string>
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
    std::string system_dir, content, retry_system;
    bool has_system = false, has_content = false, no_vfs = false, legacy_disk = false, legacy_message = false, has_initial = false;
    int64_t vfs_claim = -1;
    unsigned initial_index = 0;
    std::string initial_path;
    int options_api = 2;
    bool no_options = false;
    std::map<std::string, std::string> held;                    // the value the frontend holds for a key
    struct SetStep {
        int run;
        std::string key, value;
    };
    std::vector<SetStep> set_steps;
    struct DiskStep {
        int run;
        bool during;
        std::string command;
    };
    std::vector<DiskStep> disk_steps;
    struct Action {
        int run;
        std::string text;
    };
    std::vector<Action> actions;
    std::vector<std::pair<int, int>> aheads;                    // run, k
    int ahead_every = 0;
    struct JoyStep {
        int position;
        unsigned port, mask;
    };
    std::vector<JoyStep> joy_steps;
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
    uint32_t first_pixel = 0;       // the middle of the picture
    uint64_t audio_frames = 0;
    uint64_t audio_hash = UINT64_C(1469598103934665603);
    uint64_t unit = 0, ticks = 0;       // from the core's debug log line of the run
} run;

std::set<unsigned> environment_commands;
bool shutdown_requested = false;
int run_number = 0, violations = 0;
int position = 0;               // the frame of the emulated timeline the run being made belongs to (see the header)
bool muted = false;             // a speculative run: the picture and the audio are not shown
size_t state_size = 0;          // what retro_serialize_size said after the load: it must not change
uint64_t audio_hash = UINT64_C(1469598103934665603), audio_total = 0;
retro_game_info game_info;
bool in_video_callback = false;
bool has_game = false;
retro_disk_control_ext_callback disk = {};      // what the core registered (the legacy interface fills its first seven members)
bool disk_registered = false, disk_is_ext = false;
int vfs_opens = 0;

// What the core registered: key -> default and value names, in the order it gave them.
struct Registered {
    std::string default_value;
    std::vector<std::string> values;
};
std::map<std::string, Registered> registered;
bool variable_update = false;

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

void register_option(const char *key, const char *default_value, std::vector<std::string> values, const char *category, const char *desc)
{
    // What a frontend would silently ignore, or confuse with another option, is a contract violation of the core.
    if (!key || strncmp(key, "puae6_", 6) != 0) violation("an option key without the core's prefix");
    if (!default_value || std::find(values.begin(), values.end(), default_value) == values.end()) violation("option %s: the default is not among the values", key ? key : "?");
    if (key && registered.count(key)) violation("duplicate option key %s", key);
    if (!key) return;
    printf("option %s default=%s values=", key, default_value ? default_value : "");
    for (size_t i = 0; i < values.size(); ++i) printf("%s%s", i ? "|" : "", values[i].c_str());
    printf(" category=%s desc=%s\n", category ? category : "", desc ? desc : "");
    registered[key] = {default_value ? default_value : "", std::move(values)};
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
    unsigned long long unit = 0, ticks = 0;
    if (sscanf(text, "[PUAE6] unit=%llu ticks=%llu", &unit, &ticks) == 2) {
        run.unit = unit;
        run.ticks = ticks;
    }
    printf("log %d %s\n", int(level), text);
}


// --- the frontend's VFS: stdio behind libretro's interface, printing every open so that a test can tell which path the core read a file by ---

}  // namespace
struct retro_vfs_file_handle {          // libretro's opaque handle names a global type
    FILE *file;
    std::string path;
};
namespace {

retro_vfs_file_handle *RETRO_CALLCONV vfs_open(const char *path, unsigned mode, unsigned)
{
    if (mode != RETRO_VFS_FILE_ACCESS_READ) return nullptr;        // the core only reads
    FILE *file = fopen(path, "rb");
    printf("vfs open %s%s\n", path, file ? "" : " (failed)");
    ++vfs_opens;
    return file ? new retro_vfs_file_handle{file, path} : nullptr;
}
int RETRO_CALLCONV vfs_close(retro_vfs_file_handle *handle)
{
    fclose(handle->file);
    delete handle;
    return 0;
}
int64_t RETRO_CALLCONV vfs_size(retro_vfs_file_handle *handle)
{
    if (options.vfs_claim >= 0) return options.vfs_claim;        // a frontend that says a file is huge: the core must not believe it
    const long here = ftell(handle->file);
    fseek(handle->file, 0, SEEK_END);
    const long end = ftell(handle->file);
    fseek(handle->file, here, SEEK_SET);
    return end;
}
int64_t RETRO_CALLCONV vfs_read(retro_vfs_file_handle *handle, void *buffer, uint64_t length)
{
    const size_t got = fread(buffer, 1, size_t(length), handle->file);
    return got == 0 && ferror(handle->file) ? -1 : int64_t(got);
}
const char *RETRO_CALLCONV vfs_get_path(retro_vfs_file_handle *handle) { return handle->path.c_str(); }
int64_t RETRO_CALLCONV vfs_tell(retro_vfs_file_handle *handle) { return ftell(handle->file); }
int64_t RETRO_CALLCONV vfs_seek(retro_vfs_file_handle *handle, int64_t offset, int position)
{
    const int whence = position == RETRO_VFS_SEEK_POSITION_START ? SEEK_SET : position == RETRO_VFS_SEEK_POSITION_CURRENT ? SEEK_CUR : SEEK_END;
    return fseek(handle->file, long(offset), whence) ? -1 : ftell(handle->file);
}

retro_vfs_interface vfs_interface = {vfs_get_path, vfs_open, vfs_close, vfs_size, vfs_tell, vfs_seek, vfs_read};

// --- Disk Control as a frontend drives it ---

void disk_command(int run_at, const std::string &command)
{
    const char *prefix = muted ? "spec-disk" : "disk";
    if (!disk_registered) {
        printf("%s %d %s no-interface\n", prefix, run_at, command.c_str());
        return;
    }
    bool result = false;
    unsigned number = 0;
    char path[1024];
    if (command == "eject") result = disk.set_eject_state(true);
    else if (command == "insert") result = disk.set_eject_state(false);
    else if (sscanf(command.c_str(), "index=%u", &number) == 1) result = disk.set_image_index(number);
    else if (sscanf(command.c_str(), "remove=%u", &number) == 1) result = disk.replace_image_index && disk.replace_image_index(number, nullptr);
    else if (sscanf(command.c_str(), "replace=%u:%1023s", &number, path) == 2) {
        const retro_game_info info = {path, nullptr, 0, nullptr};
        result = disk.replace_image_index(number, &info);
    } else if (command == "add") result = disk.add_image_index();
    else violation("unknown disk command %s", command.c_str());
    printf("%s %d %s result=%d eject=%d index=%u count=%u\n", prefix, run_at, command.c_str(), int(result), int(disk.get_eject_state()),
           disk.get_image_index(), disk.get_num_images());
}

void print_images()
{
    if (!disk_registered) return;
    printf("images count=%u index=%u eject=%d\n", disk.get_num_images(), disk.get_image_index(), int(disk.get_eject_state()));
    if (!disk_is_ext) return;
    for (unsigned i = 0; i < disk.get_num_images(); ++i) {
        char path[4096] = "", label[1024] = "";
        const bool have_path = disk.get_image_path(i, path, sizeof path), have_label = disk.get_image_label(i, label, sizeof label);
        printf("image %u path=%s label=%s%s\n", i, path, label, have_path && have_label ? "" : " (missing)");
    }
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
    case RETRO_ENVIRONMENT_SET_GEOMETRY: {
        if (options.reject_optional) return false;
        const retro_game_geometry *geometry = static_cast<const retro_game_geometry *>(data);
        printf("geometry %ux%u aspect=%.6f\n", geometry->base_width, geometry->base_height, double(geometry->aspect_ratio));
        return true;
    }
    case RETRO_ENVIRONMENT_GET_SYSTEM_DIRECTORY:
        if (!options.has_system) return false;
        *static_cast<const char **>(data) = options.system_dir.c_str();
        return true;
    case RETRO_ENVIRONMENT_GET_VFS_INTERFACE: {
        retro_vfs_interface_info *info = static_cast<retro_vfs_interface_info *>(data);
        if (options.no_vfs || options.reject_optional || info->required_interface_version > 3) return false;
        info->required_interface_version = 3;
        info->iface = &vfs_interface;
        return true;
    }
    case RETRO_ENVIRONMENT_GET_DISK_CONTROL_INTERFACE_VERSION:
        if (options.legacy_disk || options.reject_optional) return false;
        *static_cast<unsigned *>(data) = 1;
        return true;
    case RETRO_ENVIRONMENT_SET_DISK_CONTROL_EXT_INTERFACE:
        if (options.legacy_disk) return false;
        disk = *static_cast<retro_disk_control_ext_callback *>(data);
        disk_registered = disk_is_ext = true;
        return true;
    case RETRO_ENVIRONMENT_SET_DISK_CONTROL_INTERFACE:
        disk = {};
        memcpy(&disk, data, sizeof(retro_disk_control_callback));
        disk_registered = true;
        disk_is_ext = false;
        return true;
    case RETRO_ENVIRONMENT_GET_MESSAGE_INTERFACE_VERSION:
        if (options.legacy_message || options.reject_optional) return false;
        *static_cast<unsigned *>(data) = 1;
        return true;
    case RETRO_ENVIRONMENT_SET_MESSAGE_EXT:
        printf("message %s\n", static_cast<retro_message_ext *>(data)->msg);
        return true;
    case RETRO_ENVIRONMENT_SET_MESSAGE:
        printf("message %s\n", static_cast<retro_message *>(data)->msg);
        return true;
    case RETRO_ENVIRONMENT_SET_SUPPORT_NO_GAME:
        return true;
    case RETRO_ENVIRONMENT_GET_CORE_OPTIONS_VERSION:
        if (options.no_options || options.reject_optional || options.options_api == 0) return false;
        *static_cast<unsigned *>(data) = unsigned(options.options_api);
        return true;
    case RETRO_ENVIRONMENT_SET_CORE_OPTIONS_V2: {
        if (options.no_options || options.reject_optional || options.options_api < 2) return false;
        const retro_core_options_v2 *set = static_cast<const retro_core_options_v2 *>(data);
        int categories = 0, count = 0;
        for (const retro_core_option_v2_category *c = set->categories; c && c->key; ++c, ++categories) printf("category %s\n", c->key);
        for (const retro_core_option_v2_definition *d = set->definitions; d->key; ++d, ++count) {
            std::vector<std::string> values;
            for (const retro_core_option_value *v = d->values; v->value; ++v) values.push_back(v->value);
            register_option(d->key, d->default_value, values, d->category_key, d->desc_categorized);
        }
        printf("options api=2 count=%d categories=%d\n", count, categories);
        return true;
    }
    case RETRO_ENVIRONMENT_SET_CORE_OPTIONS: {
        if (options.no_options || options.reject_optional || options.options_api < 1) return false;
        int count = 0;
        for (const retro_core_option_definition *d = static_cast<const retro_core_option_definition *>(data); d->key; ++d, ++count) {
            std::vector<std::string> values;
            for (const retro_core_option_value *v = d->values; v->value; ++v) values.push_back(v->value);
            register_option(d->key, d->default_value, values, "", d->desc);
        }
        printf("options api=1 count=%d categories=0\n", count);
        return true;
    }
    case RETRO_ENVIRONMENT_SET_VARIABLES: {
        if (options.no_options || options.reject_optional) return false;
        int count = 0;
        for (const retro_variable *v = static_cast<const retro_variable *>(data); v->key; ++v, ++count) {
            // "Description; first|second|...": the first value is the default.
            const std::string text = v->value;
            const size_t semicolon = text.find("; ");
            std::vector<std::string> values;
            for (size_t at = semicolon + 2; semicolon != std::string::npos && at <= text.size();) {
                const size_t bar = text.find('|', at);
                values.push_back(text.substr(at, bar == std::string::npos ? std::string::npos : bar - at));
                if (bar == std::string::npos) break;
                at = bar + 1;
            }
            if (values.empty()) violation("option %s: the legacy string has no values", v->key);
            else register_option(v->key, values[0].c_str(), values, "", text.substr(0, semicolon).c_str());
        }
        printf("options api=0 count=%d categories=0\n", count);
        return true;
    }
    case RETRO_ENVIRONMENT_GET_VARIABLE: {
        retro_variable *variable = static_cast<retro_variable *>(data);
        const auto known = registered.find(variable->key ? variable->key : "");
        if (known == registered.end()) {
            variable->value = nullptr;
            return false;
        }
        const auto held = options.held.find(known->first);
        variable->value = (held == options.held.end() ? known->second.default_value : held->second).c_str();
        return true;
    }
    case RETRO_ENVIRONMENT_GET_VARIABLE_UPDATE:
        *static_cast<bool *>(data) = variable_update;
        variable_update = false;
        return true;
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
        run.first_pixel = *reinterpret_cast<const uint32_t *>(static_cast<const uint8_t *>(data) + size_t(height / 2) * pitch + size_t(width / 2) * 4);
        if (pitch < size_t(width) * 4) violation("pitch %zu is smaller than a row of %u pixels", pitch, width);
    }
    if (run.videos == 1)
        for (const Options::DiskStep &step : options.disk_steps)
            if (step.during && step.run == position) disk_command(position, step.command);
    if (options.reenter && !in_video_callback && run.videos == 1) {
        // A core must not run, reset, unload or load from inside one of its own callbacks.
        in_video_callback = true;
        core.run();
        core.reset();
        core.unload_game();
        if (core.load_game(has_game ? &game_info : nullptr)) violation("retro_load_game succeeded from inside a callback");
        in_video_callback = false;
    }
}

void RETRO_CALLCONV audio_sample(int16_t left, int16_t right)
{
    const int16_t pair[2] = {left, right};
    run.audio_hash = fnv(run.audio_hash, pair, sizeof pair);
    ++run.audio_frames;
    if (muted) return;
    audio_hash = fnv(audio_hash, pair, sizeof pair);
    ++audio_total;
}

size_t RETRO_CALLCONV audio_batch(const int16_t *data, size_t frames)
{
    if (!data && frames) violation("audio batch without data for %zu frames", frames);
    if (!muted && position >= options.stall_first && position <= options.stall_last) return 0;      // a frontend that cannot take audio now
    size_t taken = frames;
    if (options.audio_partial && taken > options.audio_partial) taken = options.audio_partial;
    if (taken) run.audio_hash = fnv(run.audio_hash, data, taken * 2 * sizeof(int16_t));
    run.audio_frames += taken;
    if (muted) return taken;                  // a runahead frontend swallows the audio of a speculative run
    if (taken) audio_hash = fnv(audio_hash, data, taken * 2 * sizeof(int16_t));
    audio_total += taken;
    return taken;
}

void RETRO_CALLCONV input_poll(void) { ++run.polls; }

int16_t RETRO_CALLCONV input_state(unsigned port, unsigned device, unsigned index, unsigned id)
{
    ++run.states;
    if (index != 0 || port > 1) return 0;
    if (device == RETRO_DEVICE_JOYPAD) {
        unsigned mask = options.joy[port];
        for (const Options::JoyStep &step : options.joy_steps)            // the last change at or before this position holds
            if (step.port == port && step.position <= position) mask = step.mask;
        return int16_t((mask >> id) & 1);
    }
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

// --- save states as a frontend uses them ---

struct Slot {
    std::vector<uint8_t> bytes;
    int done;                       // the position the state was saved at
};
std::map<std::string, Slot> slots;
int done = 0;                       // the position of the last run on the timeline (a load puts it back)
const uint64_t kFnvBasis = UINT64_C(1469598103934665603);

std::vector<std::string> split(const std::string &text, char separator)
{
    std::vector<std::string> parts;
    size_t from = 0;
    for (;;) {
        const size_t at = text.find(separator, from);
        parts.push_back(text.substr(from, at == std::string::npos ? std::string::npos : at - from));
        if (at == std::string::npos) return parts;
        from = at + 1;
    }
}

bool save_blob(std::vector<uint8_t> *out)
{
    const size_t size = core.serialize_size();
    if (size != state_size) violation("the serialization size changed from %zu to %zu", state_size, size);
    out->assign(size, 0xA5);                                // what the core does not write stays 0xA5, so a missing padding is noticed
    return size && core.serialize(out->data(), size);
}

// The length of header + adapter section + native state, read from the header the adapter writes (frame version 1, adapters/libretro/state_frame.h):
// this is what a test needs to cut a state exactly at the end of its content. A blob that is not a frame is all content.
size_t frame_length(const std::vector<uint8_t> &blob)
{
    if (blob.size() < 64 || memcmp(blob.data(), "P6LRSTAT", 8) != 0) return blob.size();
    auto le = [&](size_t at, int bytes) { uint64_t value = 0; for (int i = bytes - 1; i >= 0; --i) value = value << 8 | blob[at + i]; return value; };
    const uint64_t length = 64 + le(24, 4) + le(16, 8);
    return length <= blob.size() ? size_t(length) : blob.size();
}

// A hash of the adapter section alone (what lies between the 64-byte header and the native state): the adapter's side of a state, apart from the engine's.
uint64_t section_hash(const std::vector<uint8_t> &blob)
{
    if (blob.size() < 64) return 0;
    uint32_t length = 0;
    for (int i = 3; i >= 0; --i) length = length << 8 | blob[24 + i];
    return length <= blob.size() - 64 ? fnv(kFnvBasis, blob.data() + 64, length) : 0;
}

// Edits a copy of a blob as the VARIANT says (see the header); returns false for a variant it does not understand.
bool edit_blob(std::vector<uint8_t> *blob, const std::string &variant, int *position_out)
{
    uint8_t fill = 0;
    const size_t frame = frame_length(*blob);
    std::vector<std::string> tokens = split(variant, ',');
    for (const std::string &token : tokens)
        if (!token.compare(0, 5, "fill=")) fill = uint8_t(strtoul(token.c_str() + 5, nullptr, 0));
    for (const std::string &token : tokens) {
        if (token.empty() || !token.compare(0, 5, "fill=")) continue;
        const size_t equals = token.find('=');
        if (equals == std::string::npos) return false;
        const std::string key = token.substr(0, equals);
        const long value = strtol(token.c_str() + equals + 1, nullptr, 0);
        if (key == "xor") {
            const long at = value < 0 ? long(frame) + value : value;
            if (at < 0 || size_t(at) >= blob->size()) return false;
            (*blob)[size_t(at)] ^= 0x5a;
        } else if (key == "xorend") {
            if (value < 0 || size_t(value) >= blob->size()) return false;
            (*blob)[blob->size() - 1 - size_t(value)] ^= 0x5a;
        } else if (key == "pos") {
            *position_out = int(value);
        } else if (key == "size" || key == "usedsize") {
            const long base = key == "size" ? long(blob->size()) : long(frame);
            if (base + value < 0) return false;
            blob->resize(size_t(base + value), fill);
        } else {
            return false;
        }
    }
    return true;
}

bool load_blob(const std::string &name, std::vector<uint8_t> blob, const std::string &variant, int blob_position)
{
    int target = blob_position;
    if (!edit_blob(&blob, variant, &target)) {
        violation("state variant %s is not understood", variant.c_str());
        return false;
    }
    std::vector<uint8_t> exact(blob.begin(), blob.end());       // an allocation of exactly the size handed over: an overread is the sanitizer's to see
    static uint8_t nothing;
    const bool ok = core.unserialize(exact.empty() ? &nothing : exact.data(), exact.size());
    printf("state load %s %s ok=%d size=%zu\n", name.c_str(), variant.empty() ? "exact" : variant.c_str(), int(ok), exact.size());
    if (ok) done = target;
    return ok;
}

void do_action(const std::string &text)
{
    const std::vector<std::string> parts = split(text, ':');
    const std::string &command = parts[0];
    const std::string argument = parts.size() > 1 ? parts[1] : "";
    const std::string variant = parts.size() > 2 ? parts[2] : "";
    if (command == "save" && (variant == "short" || variant == "long")) {
        // A buffer that is not exactly serialize_size: a smaller one must be refused, a larger one is written up to serialize_size and no further.
        const size_t size = core.serialize_size(), given = variant == "short" ? size - 1 : size + 64;
        std::vector<uint8_t> buffer(given, 0xA5);
        const bool ok = core.serialize(buffer.data(), given);
        bool tail_intact = true;
        for (size_t i = size; i < given; ++i) tail_intact = tail_intact && buffer[i] == 0xA5;
        printf("state save %s %s ok=%d tail=%s\n", argument.c_str(), variant.c_str(), int(ok), tail_intact ? "intact" : "written");
    } else if (command == "save" || command == "savefile") {
        std::vector<uint8_t> blob;
        const bool ok = save_blob(&blob);
        printf("state save %s ok=%d size=%zu used=%zu hash=%016" PRIx64 " section=%016" PRIx64 "\n", argument.c_str(), int(ok), blob.size(),
               ok ? frame_length(blob) : size_t(0), fnv(kFnvBasis, blob.data(), blob.size()), ok ? section_hash(blob) : uint64_t(0));
        if (!ok) return;
        if (command == "save") {
            slots[argument] = {blob, done};
        } else if (FILE *file = fopen(argument.c_str(), "wb")) {
            fwrite(blob.data(), 1, blob.size(), file);
            fclose(file);
        } else {
            violation("cannot write %s", argument.c_str());
        }
    } else if (command == "load") {
        const auto slot = slots.find(argument);
        if (slot == slots.end()) violation("no state saved in slot %s", argument.c_str());
        else load_blob(argument, slot->second.bytes, variant, slot->second.done);
    } else if (command == "loadfile") {
        std::vector<uint8_t> blob;
        if (FILE *file = fopen(argument.c_str(), "rb")) {
            uint8_t chunk[65536];
            for (size_t got; (got = fread(chunk, 1, sizeof chunk, file)) > 0;) blob.insert(blob.end(), chunk, chunk + got);
            fclose(file);
            load_blob(argument, blob, variant, done);
        } else {
            violation("cannot read %s", argument.c_str());
        }
    } else if (command == "reset") {
        core.reset();
        printf("state reset\n");
    } else if (command == "images") {
        printf("state images count=%u index=%u eject=%d\n", disk_registered ? disk.get_num_images() : 0, disk_registered ? disk.get_image_index() : 0,
               int(disk_registered && disk.get_eject_state()));
    } else if (command == "disk") {
        disk_command(done, text.substr(5));
    } else {
        violation("unknown action %s", text.c_str());
    }
}

// One retro_run of the script: at `at` on the emulated timeline, visible or speculative.
void make_run(int at, bool speculative)
{
    position = at;
    muted = speculative;
    run = Run{};
    for (const Options::DiskStep &step : options.disk_steps)
        if (!step.during && step.run == at) disk_command(at, step.command);
    bool changed_options = false;
    if (!speculative)
        for (const Options::SetStep &step : options.set_steps)
            if (step.run == run_number) {
                options.held[step.key] = step.value;
                variable_update = changed_options = true;
                printf("set %d %s=%s\n", run_number, step.key.c_str(), step.value.c_str());
            }
    core.run();
    muted = false;
    if (speculative) {
        printf("spec %d video=%ux%u dupe=%d hash=%016" PRIx64 " px=%08x audio=%" PRIu64 " ahash=%016" PRIx64 " unit=%" PRIu64 " ticks=%" PRIu64 "\n", at,
               run.width, run.height, int(run.dupe), run.picture_hash, run.first_pixel, run.audio_frames, run.audio_hash, run.unit, run.ticks);
    } else {
        printf("run %d video=%ux%u pitch=%zu dupe=%d hash=%016" PRIx64 " px=%08x audio=%" PRIu64 " polls=%d states=%d ahash=%016" PRIx64 " unit=%" PRIu64
               " ticks=%" PRIu64 "\n", run_number, run.width, run.height, run.pitch, int(run.dupe), run.picture_hash, run.first_pixel, run.audio_frames,
               run.polls, run.states, run.audio_hash, run.unit, run.ticks);
        done = at;
    }
    if (changed_options) {
        // What the core announces after a change made while it runs: a reboot option must leave all of this as it was.
        retro_system_av_info after = {};
        core.get_system_av_info(&after);
        printf("av-after %d base=%ux%u max=%ux%u aspect=%.6f fps=%.6f rate=%.0f region=%u\n", run_number, after.geometry.base_width, after.geometry.base_height,
               after.geometry.max_width, after.geometry.max_height, double(after.geometry.aspect_ratio), after.timing.fps, after.timing.sample_rate, core.get_region());
    }
    if (run.videos != 1) violation("%d pictures were shown (exactly one is required)", run.videos);
    if (run.polls != 1) violation("the input was polled %d times (once is required)", run.polls);
    if (core.serialize_size() != state_size) violation("the serialization size changed from %zu to %zu", state_size, core.serialize_size());
}

// Runahead as a frontend does it: take a state, run k frames nobody sees, and go back to the state.
void run_ahead(int k)
{
    std::vector<uint8_t> blob;
    if (!save_blob(&blob)) {
        violation("runahead: the state could not be saved");
        return;
    }
    const int base = done;
    for (int j = 1; j <= k && !shutdown_requested; ++j) make_run(base + j, true);
    std::vector<uint8_t> exact(blob.begin(), blob.end());
    if (!core.unserialize(exact.data(), exact.size())) violation("runahead: the state could not be loaded");
    done = position = base;
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

bool parse(int argc, char **argv)
{
    for (int i = 2; i < argc; ++i) {
        const char *a = argv[i];
        auto next = [&]() -> const char * { return i + 1 < argc ? argv[++i] : ""; };
        if (!strcmp(a, "--runs")) options.runs = atoi(next());
        else if (!strcmp(a, "--cycles")) options.cycles = atoi(next());
        else if (!strcmp(a, "--reject-optional")) options.reject_optional = true;
        else if (!strcmp(a, "--no-log")) options.no_log = true;
        else if (!strcmp(a, "--no-dupe")) options.no_dupe = true;
        else if (!strcmp(a, "--bad-format")) options.bad_format = true;
        else if (!strcmp(a, "--reenter")) options.reenter = true;
        else if (!strcmp(a, "--system")) options.system_dir = next(), options.has_system = true;
        else if (!strcmp(a, "--content")) options.content = next(), options.has_content = true;
        else if (!strcmp(a, "--no-vfs")) options.no_vfs = true;
        else if (!strcmp(a, "--vfs-claim")) options.vfs_claim = atoll(next());
        else if (!strcmp(a, "--retry-system")) options.retry_system = next();
        else if (!strcmp(a, "--legacy-disk-control")) options.legacy_disk = true;
        else if (!strcmp(a, "--legacy-message")) options.legacy_message = true;
        else if (!strcmp(a, "--initial")) {
            const std::string spec = next();
            const size_t colon = spec.find(':');
            if (colon == std::string::npos) return false;
            options.has_initial = true;
            options.initial_index = unsigned(atoi(spec.substr(0, colon).c_str()));
            options.initial_path = spec.substr(colon + 1);
        } else if (!strcmp(a, "--disk") || !strcmp(a, "--disk-during")) {
            const std::string spec = next();
            const size_t colon = spec.find(':');
            if (colon == std::string::npos) return false;
            options.disk_steps.push_back({atoi(spec.substr(0, colon).c_str()), !strcmp(a, "--disk-during"), spec.substr(colon + 1)});
        }
        else if (!strcmp(a, "--options-api")) options.options_api = atoi(next());
        else if (!strcmp(a, "--no-options")) options.no_options = true;
        else if (!strcmp(a, "--set") || !strcmp(a, "--set-at")) {
            std::string spec = next();
            int run_at = 0;
            if (!strcmp(a, "--set-at")) {
                const size_t colon = spec.find(':');
                if (colon == std::string::npos) return false;
                run_at = atoi(spec.substr(0, colon).c_str());
                spec = spec.substr(colon + 1);
            }
            const size_t equals = spec.find('=');
            if (equals == std::string::npos) return false;
            if (!strcmp(a, "--set")) options.held[spec.substr(0, equals)] = spec.substr(equals + 1);
            else options.set_steps.push_back({run_at, spec.substr(0, equals), spec.substr(equals + 1)});
        }
        else if (!strcmp(a, "--do")) {
            const std::string spec = next();
            const size_t colon = spec.find(':');
            if (colon == std::string::npos) return false;
            options.actions.push_back({atoi(spec.substr(0, colon).c_str()), spec.substr(colon + 1)});
        } else if (!strcmp(a, "--ahead")) {
            int at = 0, k = 0;
            if (sscanf(next(), "%d:%d", &at, &k) != 2 || k < 1) return false;
            options.aheads.push_back({at, k});
        } else if (!strcmp(a, "--ahead-every")) options.ahead_every = atoi(next());
        else if (!strcmp(a, "--joy-at")) {
            Options::JoyStep step = {};
            if (sscanf(next(), "%d:%u:%x", &step.position, &step.port, &step.mask) != 3 || step.port > 1) return false;
            options.joy_steps.push_back(step);
        }
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
    if (argc < 2 || !parse(argc, argv)) {
        fprintf(stderr, "usage: %s CORE [--system DIR] [--content PATH] [--runs N] [--cycles C] [options]\n", argv[0]);
        return 64;
    }
    if (!load_core(argv[1])) return 2;
    if (core.api_version() != RETRO_API_VERSION) {
        printf("VIOLATION api version %u\n", core.api_version());
        return 1;
    }
    has_game = options.has_content;
    game_info = {options.content.c_str(), nullptr, 0, nullptr};      // need_fullpath: the frontend hands over the path only

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
        for (int attempt = 0; attempt < 2; ++attempt) {
            if (options.has_initial && core.load_game) {
                if (disk_registered && disk_is_ext && disk.set_initial_image)
                    printf("initial result=%d\n", int(disk.set_initial_image(options.initial_index, options.initial_path.c_str())));
                else
                    printf("initial unsupported\n");
            }
            if (core.load_game(has_game ? &game_info : nullptr)) break;
            printf("load refused\n");
            if (attempt == 0 && !options.retry_system.empty()) {
                options.system_dir = options.retry_system;
                options.has_system = true;       // a person fixes the system directory and loads again, in the same process
                continue;
            }
            status = 3;
            break;
        }
        if (status) break;
        print_images();
        retro_system_av_info av = {};
        core.get_system_av_info(&av);
        printf("av base=%ux%u max=%ux%u aspect=%.6f fps=%.6f rate=%.0f region=%u\n", av.geometry.base_width, av.geometry.base_height,
               av.geometry.max_width, av.geometry.max_height, double(av.geometry.aspect_ratio), av.timing.fps, av.timing.sample_rate,
               core.get_region());
        state_size = core.serialize_size();
        printf("state size=%zu\n", state_size);
        audio_hash = kFnvBasis;
        audio_total = 0;
        done = position = 0;
        slots.clear();
        auto act = [&](int after) {
            for (const Options::Action &action : options.actions)
                if (action.run == after) do_action(action.text);
        };
        act(0);
        for (int n = 1; n <= options.runs && !shutdown_requested; ++n) {
            run_number = n;
            make_run(done + 1, false);
            act(n);
            for (const auto &ahead : options.aheads)
                if (ahead.first == n) run_ahead(ahead.second);
            if (options.ahead_every > 0) run_ahead(options.ahead_every);
        }
        if (state_size) {
            std::vector<uint8_t> blob;
            const bool ok = save_blob(&blob);
            printf("state final ok=%d size=%zu hash=%016" PRIx64 "\n", int(ok), blob.size(), fnv(kFnvBasis, blob.data(), blob.size()));
        }
        core.unload_game();
        printf("cycle %d end runs=%d audio_frames=%" PRIu64 " audio_hash=%016" PRIx64 "\n", cycle, run_number, audio_total, audio_hash);
        if (shutdown_requested) status = 1;
    }
    core.deinit();
    printf("final violations=%d vfs_opens=%d environment=", violations, vfs_opens);
    bool first = true;
    for (unsigned command : environment_commands) {
        printf("%s%u", first ? "" : ",", command);
        first = false;
    }
    printf("\n");
    dlclose(core.handle);
    return status ? status : violations ? 1 : 0;
}
