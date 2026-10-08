// PUAE6 libretro core: a thin adapter over the native ABI (bridge/include/p6.h), T16.
//
// retro_run polls the input once, makes exactly one p6_run_frame call, and hands over one picture and the audio of that unit. The
// core has no scheduler of its own, never sleeps and never reads the host clock: the frontend paces it. It includes libretro.h, p6.h and
// its own headers (and the portable SHA-256) only; nothing of the engine is visible here.
//
// Content (T17a): the Kickstart is looked up by known file names in the frontend's system directory and identified by the bridge from its bytes
// (never by its name); the content is a disk, an M3U playlist of disks, or nothing (the Kickstart boots alone). A disk is an ADF, an ADZ, a DMS or a ZIP of one
// of them, decoded in memory to its ADF by container.h; the SHA-256 of the ADF is the medium's identity, whatever the container (T17d). Files are read through the
// frontend's VFS when it offers one and through stdio otherwise. Disks are read-only. Every image of the playlist is registered with p6_load,
// and the Disk Control interface only records what the frontend asks: the next retro_run turns it into p6_media_event_enqueue calls, so a
// change lands on one unit boundary (see disk_control.h). Disks added later (T17e: add_image_index makes an empty slot, replace_image_index reads a file, decodes it as at load
// and registers it with p6_media_append at the END of the manifest) are registered inside the callback: registering a medium changes nothing of the machine (no drive, no event,
// no state), so it needs no unit boundary, and the bridge refuses it while a unit runs. Mounting it is still the tray's business, at the next boundary.
//
// Options (T17b): the core options come from the one schema (config/schema/options.json, options.h). The reboot options (video standard, audio rate)
// are read when the content is loaded and become the p6_config; a change made while the machine runs is reported and only applies after a reload. The
// presentation option (aspect) is applied at once. A value outside the schema fails the load, naming the key and the value.
//
// Save states (T18): retro_serialize_size is the frame header, the adapter section and the native state bound (p6_state_bound), and stays the same until
// unload. The frame (state_frame.h) carries what the adapter keeps between two runs, so that rewind, runahead and state files leave nothing of an
// abandoned future behind. Without P6_CAP_STATE in the image the size is 0 and no state is offered.
//
// Input (T17c): the keyboard, the mouse and the joystick are mapped in input_map.h, polled once per run like the rest. The ports offer a joystick, a
// mouse (port 0), "keyboard only" and nothing through SET_CONTROLLER_INFO; the Amiga keyboard is attached whatever they hold. Player 1's joystick
// drives the Amiga joystick port and, by default, the mouse the mouse port at the same time; player 2's joystick takes the mouse port when player 1 is set to
// "Joystick only" (the mouse wins otherwise: see input_map.h). What the adapter keeps
// between two runs for the input (the Caps Lock latch, mouse movement that did not fit a unit) is part of the saved adapter section.
//
// What this adapter does not do yet (see docs/02-contracts.md and the plan): any memory region. IPF, FDI and the other floppy formats are refused with a message (container.h).
#include "libretro.h"
#include "p6.h"
#include "container.h"
#include "content.h"
#include "disk_control.h"
#include "input_map.h"
#include "options.h"
#include "sha256.h"
#include "state_frame.h"

#include <algorithm>
#include <stdarg.h>
#include <new>
#include <stdio.h>
#include <string.h>
#include <string>
#include <vector>

#ifndef P6_LIBRETRO_VERSION
#define P6_LIBRETRO_VERSION "dev"
#endif

namespace {

constexpr size_t kPendingAudioFrames = 16384;     // a stalled frontend may hold this many stereo frames back; older ones are dropped
constexpr size_t kMaxFirmwareBytes = 512 * 1024;  // the largest Kickstart of the A500 family; what the file claims never decides an allocation

// The Kickstart 1.3 of the A500, by the names the community and PUAE5 give it in a system directory. A name only finds the file: what it is,
// is decided by p6_load from the bytes.
const char *const kKickstartNames[] = {
    "kick34005.A500",
    "amiga-os-130.rom",
    "Kickstart v1.3 rev 34.5 (1987)(Commodore)(A500-A1000-A2000-CDTV)[!].rom",
    "Kickstart v1.3 rev 34.5 (1987)(Commodore)(A500-A1000-A2000-CDTV).rom",
    "Kickstart v1.3 r34.5 (1987)(Commodore)(A500-A1000-A2000-CDTV)[!].rom",
};

retro_environment_t environment_cb;
retro_video_refresh_t video_cb;
retro_audio_sample_t audio_sample_cb;
retro_audio_sample_batch_t audio_batch_cb;
retro_input_poll_t input_poll_cb;
retro_input_state_t input_state_cb;
retro_log_printf_t log_cb;

struct Medium {
    std::vector<uint8_t> bytes;           // p6_load borrows it until p6_unload
    uint8_t id[P6_CONTENT_ID_SIZE];       // SHA-256 of the bytes: the identity of the disk, whatever it is called or wherever it is
};

struct Core {
    bool initialized = false;             // p6_init succeeded and p6_shutdown has not been called
    bool loaded = false;                  // p6_load succeeded and p6_unload has not been called
    bool faulted = false;                 // the image failed: only unload recovers
    bool running = false;                 // a retro_* call is in progress (they are not reentrant)
    bool can_dupe = false;
    bool announced_size = false;
    p6_options::Settings settings;        // the options of the running machine (the schema's defaults while nothing is loaded)
    p6_options::Settings requested;       // what the frontend last asked for: a reboot option that differs from `settings` was reported once, not at every change
    unsigned device[p6_libretro::kPortCount] = {RETRO_DEVICE_JOYPAD, RETRO_DEVICE_JOYPAD};     // the frontend's choice per port; it outlives an image
    p6_libretro::InputMemory input;       // what the input keeps between two runs (state_frame.h carries it)
    std::vector<uint8_t> firmware;        // p6_load borrows it until p6_unload
    std::vector<Medium> media;            // the manifest: one entry per distinct disk of the content
    p6_libretro::DiskTray tray;           // the images of the playlist and the virtual tray (what the frontend asks for)
    p6_libretro::FileReader files;
    uint64_t event_sequence = 0;          // of the media events handed to the bridge (it asks for strictly increasing numbers per image)
    bool have_initial = false;            // set_initial_image was called before this load
    unsigned initial_index = 0;
    std::string initial_path;
    std::vector<uint8_t> last_picture;    // a copy, kept only when the frontend cannot repeat a picture by itself
    uint32_t last_width = 0, last_height = 0;
    uint32_t announced_width = 0, announced_height = 0;
    std::vector<uint8_t> blank;           // what a run shows before the first picture exists
    std::vector<int16_t> pending;         // interleaved stereo int16 the frontend has not taken yet
    bool warned_overflow = false;
    bool state_ready = false;             // the image carries P6_CAP_STATE (and the quirks were announced)
    bool can_append = false;              // the image carries P6_CAP_MEDIA_APPEND: add_image_index / replace_image_index can register a disk after the load (T17e)
    size_t state_bytes = 0;               // the serialization size once asked for: header + adapter section + native bound, constant until unload
    uint32_t max_width = 0, max_height = 0;
    std::vector<uint8_t> section;         // scratch for the encoded adapter section
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
    core.settings = core.requested = p6_options::Settings();
    core.firmware.clear();                  // only now: p6_unload was the last user of what it borrowed
    core.media.clear();
    core.tray.clear();
    core.event_sequence = 0;
    core.have_initial = false;
    core.initial_path.clear();
    core.announced_size = false;
    core.last_width = core.last_height = 0;
    core.last_picture.clear();
    core.pending.clear();
    core.warned_overflow = false;
    core.input = p6_libretro::InputMemory();
    core.state_ready = false;
    core.can_append = false;
    core.state_bytes = 0;
    core.max_width = core.max_height = 0;
    core.section.clear();
    core.section.shrink_to_fit();
}

// The aspect ratio announced for a picture of width x height pixels whose pixel aspect is num:den: the machine's own, or a plain 4:3 when the
// presentation option asks for it. It never changes a pixel of the picture.
float aspect_ratio(uint32_t width, uint32_t height, uint32_t num, uint32_t den)
{
    if (core.settings.video_aspect == 1) return 4.0f / 3.0f;
    return float(double(width) * num / (double(height) * den));
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
            geometry.aspect_ratio = aspect_ratio(width, height, frame.aspect_num, frame.aspect_den);
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

// Something the person at the frontend has to see, not only the log: the log line and an on-screen message.
void vnotify(retro_log_level level, const char *format, va_list arguments)
{
    char text[512];
    vsnprintf(text, sizeof text, format, arguments);
    log(level, "%s", text);
    if (!environment_cb) return;
    char shown[540];
    snprintf(shown, sizeof shown, "PUAE6: %s", text);
    unsigned version = 0;
    if (environment_cb(RETRO_ENVIRONMENT_GET_MESSAGE_INTERFACE_VERSION, &version) && version >= 1) {
        retro_message_ext message = {};
        message.msg = shown;
        message.duration = 8000;
        message.priority = level == RETRO_LOG_ERROR ? 3 : 2;
        message.level = level;
        message.target = RETRO_MESSAGE_TARGET_ALL;
        message.type = RETRO_MESSAGE_TYPE_NOTIFICATION;
        message.progress = -1;
        environment_cb(RETRO_ENVIRONMENT_SET_MESSAGE_EXT, &message);
    } else {
        retro_message message = {shown, 480};
        environment_cb(RETRO_ENVIRONMENT_SET_MESSAGE, &message);
    }
}

void notify_error(const char *format, ...)
{
    va_list arguments;
    va_start(arguments, format);
    vnotify(RETRO_LOG_ERROR, format, arguments);
    va_end(arguments);
}

void notify_warning(const char *format, ...)
{
    va_list arguments;
    va_start(arguments, format);
    vnotify(RETRO_LOG_WARN, format, arguments);
    va_end(arguments);
}

const char *describe(p6_libretro::ReadResult result)
{
    switch (result) {
    case p6_libretro::ReadResult::CannotOpen: return "it cannot be opened";
    case p6_libretro::ReadResult::TooLarge: return "it is too large";
    case p6_libretro::ReadResult::ReadFailed: return "it cannot be read";
    case p6_libretro::ReadResult::OutOfMemory: return "there is not enough memory to read it";
    default: return "";
    }
}

std::string to_hex(const uint8_t *bytes, size_t count)
{
    static const char digits[] = "0123456789abcdef";
    std::string text;
    for (size_t i = 0; i < count; ++i) {
        text += digits[bytes[i] >> 4];
        text += digits[bytes[i] & 15];
    }
    return text;
}

// The disks the content names, read through the VFS or stdio, with the distinct ones in `media` (the manifest) and one image per playlist
// entry. Returns false after saying why (nothing is half-loaded: the caller drops what was filled).
bool load_disks(const retro_game_info *game, std::vector<Medium> *media, std::vector<p6_libretro::Image> *images)
{
    using namespace p6_libretro;
    if (!game) return true;                       // no content: the Kickstart boots alone (SET_SUPPORT_NO_GAME)
    if (!game->path || !*game->path) {
        notify_error("the content has no path; this core reads the file itself");
        return false;
    }
    const std::string content = game->path;
    std::vector<PlaylistEntry> entries;
    if (has_extension(content, "m3u")) {
        std::vector<uint8_t> text;
        const ReadResult read = core.files.read(content, kMaxPlaylistBytes, &text);
        if (read != ReadResult::Ok) {
            notify_error("the playlist %s cannot be loaded: %s", content.c_str(), describe(read));
            return false;
        }
        Playlist playlist;
        std::string why;
        if (!parse_m3u(text, directory_of(content), P6_MAX_MEDIA, &playlist, &why)) {
            notify_error("the playlist %s is not usable: %s", content.c_str(), why.c_str());
            return false;
        }
        if (playlist.entries.empty()) {
            notify_error("the playlist %s lists no disks", content.c_str());
            return false;
        }
        if (playlist.save_disks_ignored)
            log(RETRO_LOG_WARN, "%u #SAVEDISK line(s) of the playlist are ignored: the disks are read-only", playlist.save_disks_ignored);
        entries = std::move(playlist.entries);
    } else if (is_disk_extension(content)) {
        entries.push_back({content, without_extension(file_name(content))});
    } else if (const char *reason = unsupported_format_reason(content)) {
        notify_error("%s cannot be loaded: %s", content.c_str(), reason);
        return false;
    } else {
        notify_error("%s is not content this core loads (an .adf, .adz, .dms or .zip disk or an .m3u playlist of them)", content.c_str());
        return false;
    }
    for (size_t i = 0; i < entries.size(); ++i) {
        const PlaylistEntry &entry = entries[i];
        if (!is_disk_extension(entry.path)) {
            const char *reason = unsupported_format_reason(entry.path);
            notify_error("disk %zu (%s) is not an .adf, .adz, .dms or .zip disk%s%s", i + 1, entry.path.c_str(), reason ? ": " : "", reason ? reason : "");
            return false;
        }
        Medium medium;
        // The file may be a container, so the cap is the largest container; what it holds is bounded by decode_disk.
        const ReadResult read = core.files.read(entry.path, kMaxDiskFileBytes, &medium.bytes);
        if (read == ReadResult::TooLarge) {
            notify_error("disk %zu (%s) is larger than any disk file this core reads (%zu bytes)", i + 1, entry.path.c_str(), kMaxDiskFileBytes);
            return false;
        }
        if (read != ReadResult::Ok) {
            notify_error("disk %zu (%s): %s", i + 1, entry.path.c_str(), describe(read));
            return false;
        }
        std::string why;
        if (!decode_disk(&medium.bytes, entry.path, &why)) {      // in memory: the ADF is what is hashed, so the container is no part of the identity
            notify_error("disk %zu (%s): %s", i + 1, entry.path.c_str(), why.c_str());
            return false;
        }
        p6_sha256_hash(medium.bytes.data(), medium.bytes.size(), medium.id);
        size_t known = 0;
        while (known < media->size() && memcmp((*media)[known].id, medium.id, sizeof medium.id) != 0) ++known;
        if (known == media->size()) {
            log(RETRO_LOG_INFO, "medium %zu sha256=%s", known, to_hex(medium.id, sizeof medium.id).c_str());
            media->push_back(std::move(medium));
        }
        images->push_back({entry.path, entry.label, uint32_t(known)});
        log(RETRO_LOG_INFO, "disk %zu: medium %zu, label \"%s\"", i + 1, known, entry.label.c_str());
    }
    return true;
}

// The Kickstart: the first file of the system directory under a known name that the bridge accepts. A name that is there but refused (a damaged
// copy, another machine's ROM) does not hide a good one under the next name. Returns false after saying why.
bool load_firmware(const std::vector<p6_media> &manifest)
{
    using namespace p6_libretro;
    const char *directory = nullptr;
    if (!environment_cb(RETRO_ENVIRONMENT_GET_SYSTEM_DIRECTORY, &directory) || !directory || !*directory) {
        notify_error("the frontend has no system directory, where the Kickstart ROM is looked for");
        return false;
    }
    if (strlen(directory) > kMaxPathBytes) {
        notify_error("the system directory path is too long");
        return false;
    }
    std::string refusals;
    for (const char *name : kKickstartNames) {
        const std::string path = join_path(directory, name);
        const ReadResult read = core.files.read(path, kMaxFirmwareBytes, &core.firmware);
        if (read == ReadResult::CannotOpen) continue;
        if (read != ReadResult::Ok) {
            log(RETRO_LOG_WARN, "%s is not used: %s", name, describe(read));
            refusals += refusals.empty() ? "" : "; ";
            refusals += std::string(name) + " " + describe(read);
            continue;
        }
        p6_error error = make_error();
        const p6_result result = p6_load(p6_buffer{core.firmware.data(), core.firmware.size()}, manifest.data(), uint32_t(manifest.size()), &error);
        if (result == P6_OK) {
            log(RETRO_LOG_INFO, "Kickstart %s, %s", name, core.files.uses_vfs() ? "read through the frontend VFS" : "read with stdio");
            return true;
        }
        report("p6_load", result, error);
        refusals += refusals.empty() ? "" : "; ";
        refusals += std::string(name) + " refused (" + error.message + ")";
    }
    core.firmware.clear();
    if (refusals.empty())
        notify_error("no Kickstart 1.3 ROM in the system directory %s (looked for %s, ...)", directory, kKickstartNames[0]);
    else
        notify_error("the Kickstart ROM in the system directory %s is not usable: %s", directory, refusals.c_str());
    return false;
}

// What the frontend asked of the tray since the last unit becomes media events for this boundary, in order.
void apply_disk_requests()
{
    p6_libretro::DiskTray::Change changes[2];
    const unsigned count = core.tray.due(changes);
    for (unsigned i = 0; i < count; ++i) {
        p6_media_event event = {};
        event.abi_version = P6_ABI_VERSION;
        event.struct_size = sizeof event;
        event.type = changes[i].mount ? P6_MEDIA_EVENT_MOUNT : P6_MEDIA_EVENT_EJECT;
        event.drive = 0;
        event.media_index = changes[i].mount ? changes[i].medium : P6_MEDIA_INDEX_EJECT;
        event.sequence = ++core.event_sequence;
        p6_error error = make_error();
        const p6_result result = p6_media_event_enqueue(&event, &error);
        if (result != P6_OK) {
            --core.event_sequence;
            report("p6_media_event_enqueue", result, error);
            break;
        }
        core.tray.applied(changes[i]);
        if (changes[i].mount) log(RETRO_LOG_INFO, "DF0: medium %u inserted", changes[i].medium);
        else log(RETRO_LOG_INFO, "DF0: ejected");
    }
    core.tray.settled();
}

// The options may have changed in the frontend's menu since the last unit. A presentation option is applied at once. A reboot option is part of the
// machine, so the running one keeps its value: the change is reported and applies when the content is loaded again. Nothing the state identity or the
// serialization size depend on moves, because nothing of the machine changed. A value the schema does not allow is reported and ignored.
void refresh_options()
{
    bool updated = false;
    if (!environment_cb(RETRO_ENVIRONMENT_GET_VARIABLE_UPDATE, &updated) || !updated) return;
    p6_options::Settings chosen = core.settings;
    std::string why;
    if (!p6_libretro::read_options(environment_cb, &chosen, &why)) {
        notify_warning("%s; it is ignored while the machine runs, and loading the content with it would fail", why.c_str());
        return;
    }
    for (const p6_options::Option &option : p6_options::kOptions) {
        const uint32_t running = core.settings.*option.field, wanted = chosen.*option.field;
        if (option.reboot) {
            if (wanted == core.requested.*option.field) continue;        // nothing new: it was reported when it changed
            core.requested.*option.field = wanted;
            if (wanted == running) continue;                              // back to what the machine runs with
            notify_warning("%s is now %s but the machine runs with %s: reload the content to apply it", option.key,
                           p6_libretro::option_value_name(option, wanted), p6_libretro::option_value_name(option, running));
        } else if (wanted != running) {
            core.settings.*option.field = wanted;
            core.announced_size = false;        // the presentation options are about the announced geometry: say it again with the next picture
            log(RETRO_LOG_INFO, "%s is now %s", option.key, p6_libretro::option_value_name(option, wanted));
        }
    }
}

// --- Disk Control: the frontend's calls record a request; apply_disk_requests turns them into events at the next retro_run ---------------------

bool RETRO_CALLCONV disk_set_eject_state(bool ejected)
{
    if (!core.loaded) return false;
    return core.tray.set_ejected(ejected);
}

bool RETRO_CALLCONV disk_get_eject_state(void) { return core.loaded && core.tray.ejected(); }
unsigned RETRO_CALLCONV disk_get_image_index(void) { return core.tray.selected(); }

bool RETRO_CALLCONV disk_set_image_index(unsigned index)
{
    if (!core.loaded) return false;
    if (!core.tray.select(index)) {
        log(RETRO_LOG_WARN, "the image can only be chosen while the tray is open, and it must hold a disk");
        return false;
    }
    return true;
}

unsigned RETRO_CALLCONV disk_get_num_images(void) { return core.tray.count(); }

// Reads the disk at `path` as the load does (VFS or stdio, size caps, containers decoded in memory) into `medium`, naming the reason in `why` when it cannot.
bool read_added_disk(const std::string &path, Medium *medium, std::string *why)
{
    using namespace p6_libretro;
    if (path.empty() || path.size() > kMaxPathBytes) {
        *why = "the disk has no path (or a path that is too long); this core reads the file itself";
        return false;
    }
    if (!is_disk_extension(path)) {
        const char *reason = unsupported_format_reason(path);
        *why = "it is not an .adf, .adz, .dms or .zip disk";
        if (reason) *why += std::string(": ") + reason;
        return false;
    }
    const ReadResult read = core.files.read(path, kMaxDiskFileBytes, &medium->bytes);
    if (read == ReadResult::TooLarge) {
        *why = "it is larger than any disk file this core reads";
        return false;
    }
    if (read != ReadResult::Ok) {
        *why = describe(read);
        return false;
    }
    if (!decode_disk(&medium->bytes, path, why)) return false;
    p6_sha256_hash(medium->bytes.data(), medium->bytes.size(), medium->id);
    return true;
}

// The manifest index of a disk that was read: the one it already has (a disk is its content, never its path), or a new one at the end of the manifest, registered with the bridge.
// Nothing is changed when it fails. Returns UINT32_MAX after saying why.
uint32_t register_added_disk(Medium &&medium)
{
    for (size_t known = 0; known < core.media.size(); ++known)
        if (memcmp(core.media[known].id, medium.id, sizeof medium.id) == 0) return uint32_t(known);
    core.media.reserve(core.media.size() + 1);        // so that the push below cannot fail once the bridge holds the medium
    p6_media entry = {};
    entry.type = P6_MEDIA_ADF;
    memcpy(entry.content_id, medium.id, sizeof medium.id);
    entry.image = p6_buffer{medium.bytes.data(), medium.bytes.size()};
    uint32_t index = UINT32_MAX;
    p6_error error = make_error();
    const p6_result result = p6_media_append(&entry, &index, &error);
    if (result != P6_OK) {
        notify_error("the disk cannot be added: %s", error.message);
        return UINT32_MAX;
    }
    if (index != core.media.size()) {                 // the manifest the adapter mirrors and the bridge's disagree: a defect, and nothing the person can fix
        log(RETRO_LOG_ERROR, "the bridge gave the new disk index %u but the adapter's list has %zu", index, core.media.size());
        return UINT32_MAX;
    }
    log(RETRO_LOG_INFO, "medium %u sha256=%s", index, to_hex(medium.id, sizeof medium.id).c_str());
    core.media.push_back(std::move(medium));
    return index;
}

// replace_image_index (T17e): NULL takes the slot out of the list; a file gives the slot a disk, which is registered at the end of the media manifest (a disk with the bytes of one
// the manifest holds uses that one). Only with the tray open, like the removal. The image stays registered for the rest of the session; nothing here touches the machine.
bool RETRO_CALLCONV disk_replace_image_index(unsigned index, const retro_game_info *info)
{
    if (!core.loaded) return false;
    if (!info) {
        if (!core.tray.remove(index)) {
            log(RETRO_LOG_WARN, "image %u cannot be removed (the tray must be open and the image must exist)", index);
            return false;
        }
        return true;
    }
    if (core.faulted) return false;
    if (!core.tray.ejected() || index >= core.tray.count()) {
        log(RETRO_LOG_WARN, "image %u cannot be replaced (the tray must be open and the image must exist)", index);
        return false;
    }
    if (!core.can_append) {
        notify_error("this build of the core cannot add disks after the load");
        return false;
    }
    const std::string path = info->path ? info->path : "";
    try {
        Medium medium;
        std::string why;
        if (!read_added_disk(path, &medium, &why)) {
            notify_error("image %u (%s) cannot be replaced: %s", index, path.c_str(), why.c_str());
            return false;
        }
        const uint32_t manifest = register_added_disk(std::move(medium));
        if (manifest == UINT32_MAX) return false;
        const bool assigned = core.tray.assign(index, {path, p6_libretro::without_extension(p6_libretro::file_name(path)), manifest});
        if (assigned) log(RETRO_LOG_INFO, "image %u: medium %u, \"%s\"", index, manifest, core.tray.image(index)->label.c_str());
        return assigned;
    } catch (const std::bad_alloc &) {
        notify_error("not enough memory to add the disk");
        return false;
    }
}

// add_image_index (T17e): a new slot at the end of the list with no disk; replace_image_index gives it one. The list cannot outgrow what the adapter section of a state can name.
bool RETRO_CALLCONV disk_add_image_index(void)
{
    if (!core.loaded || core.faulted) return false;
    if (!core.can_append) {
        notify_error("this build of the core cannot add disks after the load");
        return false;
    }
    if (core.tray.count() >= p6_libretro::kMaxImages) {
        log(RETRO_LOG_WARN, "no image can be added: the list holds %zu, the most this core keeps", p6_libretro::kMaxImages);
        return false;
    }
    try {
        core.tray.add_empty();
    } catch (const std::bad_alloc &) {
        notify_error("not enough memory to add the disk");
        return false;
    }
    return true;
}

// Called before retro_load_game: the image that was in the drive when the frontend last closed this content. It is used only if the content
// still has that image at that index.
bool RETRO_CALLCONV disk_set_initial_image(unsigned index, const char *path)
{
    if (core.loaded || !path || !*path || strlen(path) > p6_libretro::kMaxPathBytes) return false;
    core.have_initial = true;
    core.initial_index = index;
    core.initial_path = path;
    return true;
}

bool copy_text(const std::string &text, char *out, size_t length)
{
    if (!out || text.size() >= length) return false;
    memcpy(out, text.c_str(), text.size() + 1);
    return true;
}

bool RETRO_CALLCONV disk_get_image_path(unsigned index, char *out, size_t length)
{
    const p6_libretro::Image *image = core.tray.image(index);
    return image && !image->path.empty() && copy_text(image->path, out, length);       // a slot with no disk yet has no path
}

bool RETRO_CALLCONV disk_get_image_label(unsigned index, char *out, size_t length)
{
    const p6_libretro::Image *image = core.tray.image(index);
    return image && !image->path.empty() && copy_text(image->label, out, length);
}

void register_disk_control()
{
    unsigned version = 0;
    if (environment_cb(RETRO_ENVIRONMENT_GET_DISK_CONTROL_INTERFACE_VERSION, &version) && version >= 1) {
        retro_disk_control_ext_callback callbacks = {};
        callbacks.set_eject_state = disk_set_eject_state;
        callbacks.get_eject_state = disk_get_eject_state;
        callbacks.get_image_index = disk_get_image_index;
        callbacks.set_image_index = disk_set_image_index;
        callbacks.get_num_images = disk_get_num_images;
        callbacks.replace_image_index = disk_replace_image_index;
        callbacks.add_image_index = disk_add_image_index;
        callbacks.set_initial_image = disk_set_initial_image;
        callbacks.get_image_path = disk_get_image_path;
        callbacks.get_image_label = disk_get_image_label;
        if (environment_cb(RETRO_ENVIRONMENT_SET_DISK_CONTROL_EXT_INTERFACE, &callbacks)) return;
    }
    retro_disk_control_callback legacy = {};         // an older frontend: the same without the initial image, paths and labels
    legacy.set_eject_state = disk_set_eject_state;
    legacy.get_eject_state = disk_get_eject_state;
    legacy.get_image_index = disk_get_image_index;
    legacy.set_image_index = disk_set_image_index;
    legacy.get_num_images = disk_get_num_images;
    legacy.replace_image_index = disk_replace_image_index;
    legacy.add_image_index = disk_add_image_index;
    environment_cb(RETRO_ENVIRONMENT_SET_DISK_CONTROL_INTERFACE, &legacy);
}

// A state is offered only by an image that links the native state (P6_CAP_STATE). The quirks say what is true of it: the native state is valid in the
// same build, firmware, configuration and media only (so it is not portable across platforms). The state is complete and its size fixed, so neither
// INCOMPLETE nor CORE_VARIABLE_SIZE is set. MUST_INITIALIZE is not set either although retro_serialize fails until the first unit has run: it would mean the
// size is not known after the load, which it is, and RetroArch turns run-ahead off for a core that says so; a frontend that serializes before the first
// run is told no (false), as the contract has it.
void announce_state_support()
{
    p6_caps caps = {};
    caps.abi_version = P6_ABI_VERSION;
    caps.struct_size = sizeof caps;
    p6_query_caps(&caps, nullptr);
    core.can_append = (caps.flags & P6_CAP_MEDIA_APPEND) != 0;
    core.max_width = caps.max_video_width;
    core.max_height = caps.max_video_height;
    core.state_ready = (caps.flags & P6_CAP_STATE) != 0 && core.max_width && core.max_height;
    if (!core.state_ready) return;
    uint64_t quirks = RETRO_SERIALIZATION_QUIRK_ENDIAN_DEPENDENT | RETRO_SERIALIZATION_QUIRK_PLATFORM_DEPENDENT;       // the frontend reads 64 bits
    environment_cb(RETRO_ENVIRONMENT_SET_SERIALIZATION_QUIRKS, &quirks);       // optional: a frontend that ignores it only loses its hint
}

// --- Save states: the native state inside the adapter's frame (state_frame.h) -------------------------------------------------------------------

// The audio the frontend has not taken is bounded by the queue limit; the picture is carried only for a frontend that cannot repeat one, and that is what
// the serialization size makes room for. A state may come from a frontend of the other kind: loading accepts the larger section (the picture is then
// dropped or used), saving never writes more than the size announced.
size_t section_limit(bool any_frontend = false)
{
    return p6_libretro::section_bound(kPendingAudioFrames * 2, core.can_dupe && !any_frontend ? 0 : size_t(core.max_width) * core.max_height * 4);
}

// The one size the frontend allocates for every state of this load. Asked for after the load, it is computed once and kept: the native bound is a
// fact of the configuration and the media, not of time, and the adapter's part has a ceiling of its own. 0 when no state can be offered.
size_t state_size()
{
    if (!core.loaded || core.faulted || !core.state_ready) return 0;
    if (core.state_bytes) return core.state_bytes;
    uint64_t bound = 0;
    p6_error error = make_error();
    const p6_result result = p6_state_bound(&bound, &error);
    if (result != P6_OK) {
        report("p6_state_bound", result, error);
        return 0;
    }
    const size_t head = p6_libretro::kStateHeaderBytes + section_limit();
    if (bound == 0 || bound > SIZE_MAX / 2 - head) {
        log(RETRO_LOG_ERROR, "the native state bound (%llu bytes) does not fit a serialization size", (unsigned long long)bound);
        return 0;
    }
    core.state_bytes = head + size_t(bound);
    return core.state_bytes;
}

p6_libretro::Section capture_section()
{
    p6_libretro::Section section;
    section.tray = core.tray.snapshot();
    section.event_sequence = core.event_sequence;
    section.pending = core.pending;
    section.last_width = core.last_width;
    section.last_height = core.last_height;
    section.input = core.input;
    if (!core.can_dupe && core.last_width) section.picture = core.last_picture;
    return section;
}

// What a load makes of the adapter's side once the machine has accepted the state: the saved timeline's tray, event counter, pending audio and last
// picture replace those of the timeline being abandoned. The geometry already announced is the frontend's knowledge and stays.
void apply_section(p6_libretro::Section &&section)
{
    core.tray.restore(section.tray);
    core.event_sequence = section.event_sequence;
    core.pending = std::move(section.pending);
    core.last_width = section.last_width;
    core.last_height = section.last_height;
    core.input = section.input;
    core.last_picture.clear();
    if (!core.can_dupe) {
        if (section.picture.empty()) core.last_width = core.last_height = 0;       // saved by a frontend that repeats pictures: nothing to show again
        else core.last_picture = std::move(section.picture);
    }
}

// Player 2's joystick has no Amiga port while player 1 has the mouse: say so, since the default devices make it so.
void note_shadowed_joystick()
{
    if (p6_libretro::joystick_shadowed_by_mouse(core.device))
        log(RETRO_LOG_INFO, "player 2's joystick is not sent: the mouse holds its Amiga port (set player 1 to Joystick only to free it)");
}

}  // namespace

extern "C" {

RETRO_API unsigned retro_api_version(void) { return RETRO_API_VERSION; }

RETRO_API void retro_set_environment(retro_environment_t callback)
{
    environment_cb = callback;
    bool no_game = true;                // the Kickstart boots without a disk
    if (environment_cb) {
        environment_cb(RETRO_ENVIRONMENT_SET_SUPPORT_NO_GAME, &no_game);   // optional
        p6_libretro::offer_options(environment_cb);                         // the richest interface the frontend has; none is not an error
        p6_libretro::offer_controllers(environment_cb);
    }
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
    if (!environment_cb) return;
    // The frontend's file system, asked for as early as the API recommends; version 1 (open, size, read, close) is all the core uses.
    retro_vfs_interface_info vfs = {};
    vfs.required_interface_version = 1;
    if (environment_cb(RETRO_ENVIRONMENT_GET_VFS_INTERFACE, &vfs) && vfs.iface && vfs.iface->open && vfs.iface->read && vfs.iface->close)
        core.files.use_vfs(vfs.iface);
    register_disk_control();
}

RETRO_API void retro_deinit(void)
{
    stop_image();
    core.files.use_vfs(nullptr);
    log_cb = nullptr;
}

RETRO_API void retro_get_system_info(struct retro_system_info *info)
{
    memset(info, 0, sizeof *info);
    info->library_name = "PUAE6";
    info->library_version = P6_LIBRETRO_VERSION;
    info->valid_extensions = "adf|adz|dms|zip|m3u";
    info->need_fullpath = true;           // the disks are read by path (through the VFS), the Kickstart by name from the system directory
    info->block_extract = true;           // a zip is read by the core, in memory: the frontend must not extract it to a temporary directory
}

RETRO_API void retro_get_system_av_info(struct retro_system_av_info *info)
{
    memset(info, 0, sizeof *info);
    p6_caps caps = {};
    caps.abi_version = P6_ABI_VERSION;
    caps.struct_size = sizeof caps;
    p6_query_caps(&caps, nullptr);
    const bool ntsc = core.settings.video_standard == P6_VIDEO_NTSC;
    // The nominal active raster of each standard; a unit can draw less, and the geometry is updated when it does.
    const uint32_t width = 756, height = ntsc ? 484 : 574;
    info->geometry.base_width = width;
    info->geometry.base_height = height;
    info->geometry.max_width = caps.max_video_width ? caps.max_video_width : 760;
    info->geometry.max_height = caps.max_video_height ? caps.max_video_height : 576;
    info->geometry.aspect_ratio = aspect_ratio(width, height, ntsc ? 43 : 26, ntsc ? 50 : 25);
    info->timing.fps = ntsc ? 715909.0 / 11921.0 : 3546895.0 / 70824.0;    // the bridge's rate for a progressive short field
    info->timing.sample_rate = core.settings.audio_rate_hz;
}

// The device of a port is the frontend's choice, not part of the machine: the next retro_run reads the port as it is then. A type the port was not
// offered (a mouse in port 1, which the contract does not have) is refused and the port keeps what it had.
RETRO_API void retro_set_controller_port_device(unsigned port, unsigned device)
{
    if (p6_libretro::device_allowed(port, device)) {
        const bool changed = core.device[port] != device;
        core.device[port] = device;
        if (changed) note_shadowed_joystick();
    } else log(RETRO_LOG_WARN, "device %u is not available in port %u; the port keeps device %u", device, port, port < p6_libretro::kPortCount ? core.device[port] : 0);
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
    refresh_options();
    p6_frame frame = {};
    bool have_frame = false;
    if (!core.faulted) {
        apply_disk_requests();      // what the frontend asked of the tray since the last unit lands on this boundary
        input_poll_cb();
        const p6_input input = p6_libretro::read_input(input_state_cb, core.device, &core.input);
        frame.abi_version = P6_ABI_VERSION;
        frame.struct_size = sizeof frame;
        p6_error error = make_error();
        const p6_result result = p6_run_frame(&input, P6_RENDER_VIDEO | P6_RENDER_AUDIO, &frame, &error);
        if (result == P6_OK) {
            have_frame = true;
            log(RETRO_LOG_DEBUG, "unit=%llu ticks=%llu ports=%u:%x:%d:%d,%u:%x:%d:%d keys=%08x%08x%08x%08x", (unsigned long long)frame.unit_index,
                (unsigned long long)frame.master_ticks, input.ports[0].type, input.ports[0].buttons, input.ports[0].dx,
                input.ports[0].dy, input.ports[1].type, input.ports[1].buttons, input.ports[1].dx, input.ports[1].dy, input.keys[3], input.keys[2],
                input.keys[1], input.keys[0]);
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

RETRO_API size_t retro_serialize_size(void)
{
    CallGuard guard;
    return guard.ok ? state_size() : 0;
}

// Writes the frame into exactly state_size() bytes: header, adapter section, native state, zeros. A buffer smaller than that is refused; a larger one
// keeps its tail untouched. What a refused call leaves in the buffer is not defined, and the machine is never changed by a save.
RETRO_API bool retro_serialize(void *data, size_t size)
{
    CallGuard guard;
    if (!guard.ok) return false;
    const size_t total = state_size();
    if (!total || !data) return false;
    if (size < total) {
        log(RETRO_LOG_WARN, "the state needs %zu bytes and the buffer has %zu", total, size);
        return false;
    }
    try {
        p6_libretro::encode_section(capture_section(), &core.section);
    } catch (const std::bad_alloc &) {
        log(RETRO_LOG_ERROR, "not enough memory to save the state");
        return false;
    }
    uint8_t *out = static_cast<uint8_t *>(data);
    const size_t at = p6_libretro::kStateHeaderBytes + core.section.size();
    if (core.section.size() > section_limit() || at >= total) {
        log(RETRO_LOG_ERROR, "the adapter section (%zu bytes) does not fit the serialization size", core.section.size());
        return false;
    }
    uint64_t written = 0;
    p6_error error = make_error();
    const p6_result result = p6_state_save(out + at, total - at, &written, &error);
    if (result != P6_OK) {
        if (result != P6_ERROR_BAD_STATE) report("p6_state_save", result, error);       // before the first run there is no machine to describe: not a fault
        return false;
    }
    memcpy(out + p6_libretro::kStateHeaderBytes, core.section.data(), core.section.size());
    p6_libretro::write_header(out, written, core.section.data(), core.section.size());
    memset(out + at + written, 0, total - at - size_t(written));
    return true;
}

// The frame is checked in full, and the adapter section decoded and fitted to this load, before p6_state_load is asked to touch the machine; the
// adapter's side changes only after the machine accepted the state. A refused load therefore leaves everything as it was.
RETRO_API bool retro_unserialize(const void *data, size_t size)
{
    CallGuard guard;
    if (!guard.ok) return false;
    const size_t total = state_size();
    if (!total || !data) return false;
    p6_libretro::Frame frame;
    const p6_libretro::FrameError refused = p6_libretro::parse_frame(static_cast<const uint8_t *>(data), size, section_limit(true), &frame);
    if (refused != p6_libretro::FrameError::None) {
        log(RETRO_LOG_WARN, "the state is refused: %s", p6_libretro::describe(refused));
        return false;
    }
    p6_libretro::Section section;
    const char *why = nullptr;
    if (!p6_libretro::decode_section(frame.section, frame.section_bytes, kPendingAudioFrames * 2, core.max_width, core.max_height, &section, &why)) {
        log(RETRO_LOG_WARN, "the state is refused: %s", why);
        return false;
    }
    if (!core.tray.fits(section.tray, uint32_t(core.media.size()))) {
        log(RETRO_LOG_WARN, "the state is refused: its disk list does not start with the list of this session (a disk that was added after the load has to be added again, in the same order)");
        return false;
    }
    p6_error error = make_error();
    const p6_result result = p6_state_load(frame.native, frame.native_bytes, &error);
    if (result != P6_OK) {
        report("p6_state_load", result, error);
        if (result == P6_ERROR_INTERNAL) {
            core.faulted = true;                   // the bridge stopped the image: only an unload recovers, as for a failed run
            environment_cb(RETRO_ENVIRONMENT_SHUTDOWN, nullptr);
        }
        return false;
    }
    apply_section(std::move(section));
    return true;
}
RETRO_API void retro_cheat_reset(void) {}
RETRO_API void retro_cheat_set(unsigned, bool, const char *) {}

RETRO_API bool retro_load_game(const struct retro_game_info *game)
{
    CallGuard guard;
    if (!guard.ok) return false;
    if (core.loaded) {
        log(RETRO_LOG_ERROR, "a game is already loaded");
        return false;
    }
    retro_pixel_format format = RETRO_PIXEL_FORMAT_XRGB8888;
    if (!environment_cb(RETRO_ENVIRONMENT_SET_PIXEL_FORMAT, &format)) {
        log(RETRO_LOG_ERROR, "the frontend does not accept XRGB8888, the only pixel format of this core");
        return false;
    }
    p6_libretro::offer_input_descriptors(environment_cb);
    note_shadowed_joystick();
    bool can_dupe = false;
    core.can_dupe = environment_cb(RETRO_ENVIRONMENT_GET_CAN_DUPE, &can_dupe) && can_dupe;
    // The options are read before anything is loaded: a value the schema does not allow stops here, and these are the values the machine runs with.
    p6_options::Settings chosen;
    std::string why;
    if (!p6_libretro::read_options(environment_cb, &chosen, &why)) {
        notify_error("%s", why.c_str());
        return false;
    }
    bool updated = false;
    environment_cb(RETRO_ENVIRONMENT_GET_VARIABLE_UPDATE, &updated);       // clears the flag: what was just read is what is in force
    core.settings = core.requested = chosen;
    try {
        std::vector<p6_libretro::Image> images;
        if (!load_disks(game, &core.media, &images)) {
            stop_image();
            return false;
        }
        std::vector<p6_media> manifest;
        for (const Medium &medium : core.media) {
            p6_media entry = {};
            entry.type = P6_MEDIA_ADF;
            memcpy(entry.content_id, medium.id, sizeof medium.id);
            entry.image = p6_buffer{medium.bytes.data(), medium.bytes.size()};
            manifest.push_back(entry);
        }
        p6_config config = {};
        config.abi_version = P6_ABI_VERSION;
        config.struct_size = sizeof config;
        config.machine = P6_MACHINE_A500;
        p6_options::to_config(core.settings, &config);       // standard, audio rate, seed and clock epoch: the schema's values, none of them typed here
        p6_error error = make_error();
        const p6_result result = p6_init(&config, &error);
        if (result != P6_OK) {
            report("p6_init", result, error);
            stop_image();
            return false;
        }
        core.initialized = true;
        if (!load_firmware(manifest)) {
            stop_image();
            return false;
        }
        core.loaded = true;
        announce_state_support();
        // The image the frontend remembers from its last session, if the content still has it at that index; else the first.
        unsigned initial = 0;
        if (core.have_initial) {
            const bool matches = core.initial_index < images.size() && images[core.initial_index].path == core.initial_path;
            if (matches) initial = core.initial_index;
            else log(RETRO_LOG_WARN, "the remembered disk %u (%s) is not in the content: starting with the first", core.initial_index, core.initial_path.c_str());
        }
        core.tray.start(std::move(images), initial);      // the first boundary mounts it: events before the first unit are the power-on disks
    } catch (const std::bad_alloc &) {
        notify_error("not enough memory to load the content");
        stop_image();
        return false;
    }
    return true;
}

RETRO_API bool retro_load_game_special(unsigned, const struct retro_game_info *, size_t) { return false; }

RETRO_API void retro_unload_game(void)
{
    CallGuard guard;
    if (guard.ok) stop_image();
}

RETRO_API unsigned retro_get_region(void) { return core.settings.video_standard == P6_VIDEO_NTSC ? RETRO_REGION_NTSC : RETRO_REGION_PAL; }
RETRO_API void *retro_get_memory_data(unsigned) { return nullptr; }
RETRO_API size_t retro_get_memory_size(unsigned) { return 0; }

}  // extern "C"
