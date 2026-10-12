// PUAE6 libretro core: a thin adapter over the native ABI (bridge/include/p6.h), T16.
//
// retro_run polls the input once, makes exactly one p6_run_frame call, and hands over one picture and the audio of that unit. The
// core has no scheduler of its own, never sleeps and never reads the host clock: the frontend paces it. It includes libretro.h, p6.h and
// its own headers (and the portable SHA-256) only; nothing of the engine is visible here.
//
// Content (T17a): the Kickstart is looked up by known file names in the frontend's system directory and identified by the bridge from its bytes
// (never by its name); the content is a disk, an M3U playlist of disks, or nothing (the Kickstart boots alone). A disk is an ADF, an ADZ, a DMS or a ZIP of one
// of them, decoded in memory to its ADF by container.h; the SHA-256 of the ADF is the medium's identity, whatever the container (T17d). Files are read through the
// frontend's VFS when it offers one and through stdio otherwise. Disks are read-only unless puae6_floppy_writes is on (T27): then the guest can write to them over an
// overlay that lives in the machine (and so in save states, rewind and run-ahead), the files are still never touched, and floppy_save.h says how the changes
// can be kept (only when the content closes, only if puae6_floppy_save asks for it). Every image of the playlist is registered with p6_load,
// and the Disk Control interface only records what the frontend asks: the next retro_run turns it into p6_media_event_enqueue calls, so a
// change lands on one unit boundary (see disk_control.h). Disks added later (T17e: add_image_index makes an empty slot, replace_image_index reads a file, decodes it as at load
// and registers it with p6_media_append at the END of the manifest) are registered inside the callback: registering a medium changes nothing of the machine (no drive, no event,
// no state), so it needs no unit boundary, and the bridge refuses it while a unit runs. Mounting it is still the tray's business, at the next boundary.
//
// Options (T17b): the core options come from the one schema (config/schema/options.json, options.h). The reboot options (machine, video standard, audio rate)
// are read when the content is loaded and become the p6_config; a change made while the machine runs is reported and only applies after a reload. The
// presentation options (aspect, and since T31c crop, position, gamma) are applied at once; the pixel format (Color Depth) is read when the content loads, like a
// reboot option, and a change is reported. A value outside the schema fails the load, naming the key and the value.
//
// Presentation (T31c): the picture the frontend gets is a window, a gamma table and a pixel format applied to a COPY of the engine's raster (present.h); the
// native picture, the state and the cadence never see an option of it, and when none applies the raster itself is handed over.
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
// Virtual keyboard (T31a): Select (or a hotkey) opens the Amiga keyboard over the picture; the pad moves a cursor over it and presses keys as the keyboard's own bits
// of the p6_input packet (vkbd.h, mapper.h), and its memory is in the input memory of the state.
//
// Statusbar and LED (T31b): a bar over the top or bottom of the picture copy shows the drive LED, the two ports and the pad's mode, and the messages of the actions;
// the frontend's LED interface, if it has one, gets the drive light too. Both are outputs read from the unit just run and the input memory (statusbar.h).
//
// Configuration file (T31 closure): a .uae as content is read through config_import.h, which keeps an allowlist of its keys, translates them to the schema's reboot
// options (laid over the frontend's) and takes its disks as the playlist; anything else in the file fails the load with the key and the line.
//
// Mapper (T31d): player 1's RetroPad can be remapped, auto-fire, drive the mouse by D-Pad or analog stick, and a few hotkeys run actions of the core; all of it is
// a function of what the frontend reports and of the input memory (mapper.h), turned into the same p6_input packet before p6_run_frame.
//
// Memory (T75): the Chip, Slow and Fast RAM and the ROM are announced with RETRO_ENVIRONMENT_SET_MEMORY_MAPS at their Amiga bus addresses, RETRO_MEMORY_SYSTEM_RAM is the
// Chip RAM and the core says it supports achievements; memory_map.h has the decisions (order, endianness, what a write of the frontend is).
//
// Hard disks (T63a): an .hdf (or an .hdz, gzip of one, decoded in memory) as content or in an M3U is registered with p6_load as a P6_MEDIA_HDF and mounted from power-on on the UAE
// controller as DH0, DH1... in the order named (hdf.h). They follow every floppy in the manifest, all of them are mounted at once, and Disk Control does not list them. The guest can
// always write to them; the file is never touched, the writes are machine state (save states, rewind, run-ahead carry them) and, like the floppies', they leave the machine only when
// the content closes and puae6_hdf_save asks for it: p6_media_export of the whole image and an atomic replace in the save directory.
//
// Volumes (T64a): a directory, an .lha or a .zip of files as content (or as an entry of an M3U) is read ONCE, here, and turned into one FFS hard file in memory (volume.h, ffs_volume.h,
// lha.h; docs/adr/0011-volumes-from-the-host.md), which is registered exactly like an .hdf. The host is never mounted: what enters the machine is the image, whose content id is the
// SHA-256 of its bytes, so states, rewind and run-ahead carry the guest's writes as for any hard file and the source is never written.
//
// Archives of disks (T59): a .zip, a .7z or an .rp9 with several floppies is a set, read in memory in a deterministic order (PUAE5's, archive.h) and expanded in place into the
// playlist; "file.zip#member.adf" is one member; the sets and members are expanded when the content loads, the archive is never kept and nothing is written. An archive of one disk is
// that disk, as before.
//
// What this adapter does not do yet (see docs/02-contracts.md and the plan): IPF, FDI and the other floppy formats are refused with a message (container.h).
#include "libretro.h"
#include "p6.h"
#include "config_import.h"
#include "container.h"
#include "content.h"
#include "disk_control.h"
#include "firmware.h"
#include "romkey.h"
#include "floppy_save.h"
#include "archive.h"
#include "hdf.h"
#include "input_map.h"
#include "memory_map.h"
#include "model.h"
#include "options.h"
#include "present.h"
#include "sha256.h"
#include "state_frame.h"
#include "statusbar.h"
#include "vkbd.h"
#include "volume.h"

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

static_assert(p6_libretro::kMaxArchiveDisks == P6_MAX_MEDIA, "an archive set must fit the media manifest");

constexpr size_t kPendingAudioFrames = 16384;     // a stalled frontend may hold this many stereo frames back; older ones are dropped
constexpr size_t kMaxFirmwareBytes = 512 * 1024 + p6_libretro::kCloantoMagicBytes;  // the largest Kickstart (512 KiB), or the same ROM encrypted by Amiga Forever (AMIROMTYPE1 in front); what the file claims never decides an allocation

retro_environment_t environment_cb;
retro_video_refresh_t video_cb;
retro_audio_sample_t audio_sample_cb;
retro_audio_sample_batch_t audio_batch_cb;
retro_input_poll_t input_poll_cb;
retro_input_state_t input_state_cb;
retro_log_printf_t log_cb;

struct Medium {
    uint32_t type = P6_MEDIA_ADF;         // P6_MEDIA_ADF, or P6_MEDIA_HDF for a hard file (T63a): those follow every floppy in the manifest and are not in the tray
    std::vector<uint8_t> bytes;           // p6_load borrows it until p6_unload
    uint8_t id[P6_CONTENT_ID_SIZE];       // SHA-256 of the bytes: the identity of the disk, whatever it is called or wherever it is
    uint8_t origin[P6_CONTENT_ID_SIZE];   // the identity of the disk as its file gave it: the name of the file its changes are saved to (the same as id unless a saved copy replaced it)
    bool saved_copy = false;              // the bytes are a copy saved by an earlier session (T27, T63a), not the file's
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
    std::vector<uint8_t> last_picture;    // a copy of the picture as presented, kept only when the frontend cannot repeat a picture by itself
    uint32_t last_width = 0, last_height = 0;
    unsigned bytes_per_pixel = 4;         // of the pixel format in force: 4 (XRGB8888) or 2 (RGB565, T31c)
    p6_libretro::StatusData status;       // what the statusbar draws from: the drive light and the packet of the unit just run (T31b); it is derived, not saved
    retro_set_led_state_t set_led = nullptr;       // the frontend's LED interface, if it has one
    int led_state[2] = {0, 0};            // what the LEDs were last set to: 1 (floppy drives) and 3 (DF0), in PUAE5's order
    p6_libretro::Presenter presenter;     // crop, position, gamma and the 16-bit format over a copy of the picture (T31c); the machine never sees it
    uint32_t announced_width = 0, announced_height = 0;
    float announced_ratio = 0;            // the picture aspect ratio the frontend was last told (after the aspect option)
    uint32_t announced_rate_num = 0, announced_rate_den = 0;      // the units per second and the pixel aspect the frontend was last told (the load announces the nominal ones)
    uint32_t announced_aspect_num = 0, announced_aspect_den = 0;
    std::vector<uint8_t> blank;           // what a run shows before the first picture exists
    std::vector<int16_t> pending;         // interleaved stereo int16 the frontend has not taken yet
    bool warned_overflow = false;
    bool state_ready = false;             // the image carries P6_CAP_STATE (and the quirks were announced)
    bool can_append = false;              // the image carries P6_CAP_MEDIA_APPEND: add_image_index / replace_image_index can register a disk after the load (T17e)
    size_t state_bytes = 0;               // the serialization size once asked for: header + adapter section + native bound, constant until unload
    uint32_t max_width = 0, max_height = 0;
    std::vector<uint8_t> section;         // scratch for the encoded adapter section
    p6_libretro::MemoryMap memory_map;    // what the frontend was last told about the memory (T75); the descriptors live here, the pointers are the engine's
    bool memory_announced = false;        // memory_map is valid (the frontend was told, whether or not it took the map)
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

// The LEDs of the frontend (RETRO_ENVIRONMENT_GET_LED_INTERFACE), in the order of PUAE5's core: 0 power, 1 floppy drives, 2 HD/CD/MD, 3 DF0, 4-6 DF1-DF3, 7 HD,
// 8 CD/MD. The ABI reports one drive light (some drive's LED is on; libretro drives DF0 alone), which is what lights "floppy drives" and DF0; the power LED, DF1-3,
// the hard disk and the CD are not part of the profile. Called only when the state of a LED changes, with the light of the unit just run.
void set_leds(bool drive_light)
{
    const int state = drive_light ? 1 : 0;
    if (!core.set_led) return;
    const int leds[2] = {1, 3};
    for (int i = 0; i < 2; ++i)
        if (core.led_state[i] != state) {
            core.led_state[i] = state;
            core.set_led(leds[i], state);
        }
}

// The memory as the frontend sees it (T75). Asked of the bridge after the load, after every unit and after every state load: the engine keeps its buffers while the
// configuration is the same (see memory_map.h), so this is one comparison and the frontend is told once. If a pointer or a size ever did change, the frontend is
// told again, because it keeps the pointers it was given (and a stale one would be read or poked after free).
void refresh_memory_map()
{
    if (!core.loaded || core.faulted) return;
    p6_memory_region regions[8];
    uint32_t count = 0;
    p6_error error = make_error();
    const p6_result result = p6_memory_regions(regions, uint32_t(sizeof regions / sizeof regions[0]), &count, &error);
    if (result != P6_OK) {
        report("p6_memory_regions", result, error);
        return;
    }
    p6_libretro::MemoryMap now;
    if (!p6_libretro::build_memory_map(regions, count, &now)) return;
    if (core.memory_announced && now.same_as(core.memory_map)) return;
    if (core.memory_announced) log(RETRO_LOG_WARN, "the memory moved: the frontend is told again");
    core.memory_map = now;
    core.memory_announced = true;
    retro_memory_map map = {core.memory_map.descriptor, core.memory_map.count};
    if (environment_cb) environment_cb(RETRO_ENVIRONMENT_SET_MEMORY_MAPS, &map);     // optional: a frontend without it still gets RETRO_MEMORY_SYSTEM_RAM
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
    core.announced_ratio = 0;
    set_leds(false);
    core.status = p6_libretro::StatusData();
    core.presenter.forget();
    core.bytes_per_pixel = 4;
    core.announced_rate_num = core.announced_rate_den = core.announced_aspect_num = core.announced_aspect_den = 0;
    core.last_width = core.last_height = 0;
    core.last_picture.clear();
    core.pending.clear();
    core.warned_overflow = false;
    core.input = p6_libretro::InputMemory();
    core.state_ready = false;
    core.memory_map = p6_libretro::MemoryMap();
    core.memory_announced = false;
    core.can_append = false;
    core.state_bytes = 0;
    core.max_width = core.max_height = 0;
    core.section.clear();
    core.section.shrink_to_fit();
}

// The options that shape the picture handed to the frontend, over the running settings; the pixel format is the one the frontend accepted at the load.
p6_libretro::PresentOptions present_options()
{
    p6_libretro::PresentOptions options = p6_libretro::present_options(core.settings);
    options.depth16 = core.bytes_per_pixel == 2;
    // The toggles of the mapper (Toggle Crop, Toggle Aspect Ratio) are in the input memory, so a state brings its own back.
    return p6_libretro::with_toggles(options, core.input.crop_toggled, core.input.aspect_toggled, core.settings.video_standard == P6_VIDEO_NTSC);
}

// The aspect ratio announced for a picture of width x height pixels whose pixel aspect is num:den: the machine's own, or what the aspect option asks for. It
// never changes a pixel of the picture.
float aspect_ratio(uint32_t width, uint32_t height, uint32_t num, uint32_t den)
{
    return p6_libretro::picture_aspect(present_options().aspect, width, height, num, den);        // after the mapper's Toggle Aspect Ratio
}

// What retro_get_system_av_info announces from the load, before any unit has run: the nominal rate of the configured standard (a progressive
// short field) and its pixel aspect. The bridge reports the real ones with every unit.
struct Nominal {
    uint32_t rate_num, rate_den, aspect_num, aspect_den;
};

Nominal nominal_av()
{
    const bool ntsc = core.settings.video_standard == P6_VIDEO_NTSC;
    return ntsc ? Nominal{715909, 11921, 43, 50} : Nominal{3546895, 70824, 26, 25};
}

// A unit that runs at another rate than the one announced is a change of beam (an ECS guest that programs BEAMCON0, T23f). The long field and the
// interlace move the rate of a standard beam by 0.3 % (50.08, 49.92 and 50.00 Hz): a frontend that reinitialises its audio and video for that would
// do so every time a guest toggles the interlace, so a difference up to kRateBandPercent is not announced. A PAL/NTSC change is 19 %.
constexpr uint64_t kRateBandPercent = 1;

bool rate_moved(uint32_t num, uint32_t den)
{
    const uint64_t now = uint64_t(num) * core.announced_rate_den, was = uint64_t(core.announced_rate_num) * den;
    return (now > was ? now - was : was - now) * 100 > was * kRateBandPercent;
}

// The picture of a unit as the frontend will get it: the raster the engine drew and the window of it that is presented (T31c). Made once per unit
// (it moves the automatic window's memory) before anything is announced.
struct Picture {
    bool valid = false;
    const uint8_t *origin = nullptr;
    size_t stride = 0;
    uint32_t native_width = 0, native_height = 0;
    p6_libretro::Window window;
};

Picture plan_picture(const p6_frame &frame, bool have_frame)
{
    Picture picture;
    if (!have_frame || !frame.video || !frame.active.width || !frame.active.height) return picture;
    picture.valid = true;
    picture.origin = static_cast<const uint8_t *>(frame.video) + size_t(frame.active.y) * frame.video_stride_bytes + size_t(frame.active.x) * 4;
    picture.stride = frame.video_stride_bytes;
    picture.native_width = frame.active.width;
    picture.native_height = frame.active.height;
    picture.window = core.presenter.plan(present_options(), picture.origin, picture.stride, picture.native_width, picture.native_height, frame.aspect_num,
                                         frame.aspect_den);
    return picture;
}

// Called at the boundary of each run, before the picture is handed over: tells the frontend when the unit that came back does not run at the
// announced rate (SET_SYSTEM_AV_INFO, with the geometry of the same unit so that the timing and the picture change together) or has another pixel
// aspect (SET_GEOMETRY, announced by deliver_video). Once per change, never per unit; the frontend may refuse, and is not asked again for the same value.
void announce_av_change(const p6_frame &frame, const Picture &picture)
{
    if (core.announced_rate_num == 0) {
        const Nominal nominal = nominal_av();
        core.announced_rate_num = nominal.rate_num;
        core.announced_rate_den = nominal.rate_den;
        core.announced_aspect_num = nominal.aspect_num;
        core.announced_aspect_den = nominal.aspect_den;
    }
    if (!frame.cadence_num || !frame.cadence_den || !rate_moved(frame.cadence_num, frame.cadence_den)) return;
    const uint32_t width = picture.valid ? picture.window.width : (core.announced_width ? core.announced_width : 756);
    const uint32_t height = picture.valid ? picture.window.height : (core.announced_height ? core.announced_height : 574);
    retro_system_av_info info = {};
    info.geometry.base_width = width;
    info.geometry.base_height = height;
    info.geometry.max_width = core.max_width ? core.max_width : 760;
    info.geometry.max_height = core.max_height ? core.max_height : 576;
    info.geometry.aspect_ratio = aspect_ratio(width, height, frame.aspect_num, frame.aspect_den);
    info.timing.fps = double(frame.cadence_num) / double(frame.cadence_den);
    info.timing.sample_rate = core.settings.audio_rate_hz;
    if (!environment_cb(RETRO_ENVIRONMENT_SET_SYSTEM_AV_INFO, &info))
        log(RETRO_LOG_INFO, "the frontend did not apply the new rate %u/%u", frame.cadence_num, frame.cadence_den);
    core.announced_rate_num = frame.cadence_num;
    core.announced_rate_den = frame.cadence_den;
    core.announced_aspect_num = frame.aspect_num;
    core.announced_aspect_den = frame.aspect_den;
    core.announced_size = true;
    core.announced_width = width;
    core.announced_height = height;
    core.announced_ratio = info.geometry.aspect_ratio;
}

// One picture per run. The active raster is what the renderer drew; the frontend gets the window of it that crop, position, gamma and the pixel format make of a
// copy (T31c), or the raster itself, not the padded buffer, when none of them applies.
void deliver_video(const p6_frame &frame, bool have_frame, const Picture &picture)
{
    (void)have_frame;
    if (picture.valid) {
        const p6_libretro::PresentOptions options = present_options();
        // Overlays (T31b): drawn on the copy, from the memory of the input and the unit just run; the picture of the engine is never written to.
        const p6_libretro::StatusbarMode bar_mode = p6_libretro::statusbar_mode(core.settings.statusbar);
        core.status.turbo = (core.settings.turbo_fire != 0) != core.input.turbo_toggled;
        core.status.mouse_mode = core.input.dpad_mouse;
        core.status.message = core.input.message;
        const bool bar = core.input.statusbar_shown;
        const bool strip = !bar && core.settings.statusbar_messages != 0 && core.input.message != 0;
        const bool keyboard = core.input.vkbd_open;
        p6_libretro::Presented shown = core.presenter.compose_xrgb(options, picture.window, picture.origin, picture.stride, picture.native_width,
                                                                    picture.native_height, bar || strip || keyboard);
        if (bar || strip || keyboard) {
            p6_libretro::Canvas canvas;
            canvas.pixels = const_cast<uint32_t *>(static_cast<const uint32_t *>(shown.data));
            canvas.width = shown.width;
            canvas.height = shown.height;
            canvas.stride = shown.pitch / 4;
            if (keyboard) {          // under the statusbar: the keyboard sits above a bar at the bottom
                p6_libretro::VkbdView view;
                view.cursor = core.input.vk_cursor;
                view.held = core.input.vk_held;
                view.latch = core.input.vk_latch;
                view.caps = core.input.caps_latched;
                view.opaque = core.input.vkbd_opaque;
                p6_libretro::VkbdLook look;
                look.theme = core.settings.vkbd_theme / 2;
                look.outline = (core.settings.vkbd_theme & 1) != 0;
                look.transparency = core.settings.vkbd_transparency;
                look.dimming = core.settings.vkbd_dimming;
                look.a1200 = core.settings.machine == P6_MACHINE_A1200;
                const bool bottom_bar = (bar || strip) && !bar_mode.top;
                p6_libretro::draw_vkbd(canvas, view, look, bottom_bar ? p6_libretro::statusbar_height(canvas.width, bar_mode, core.status, bar, strip) : 0);
            }
            if (bar || strip) p6_libretro::draw_statusbar(canvas, bar_mode, core.status, bar, strip);
        }
        shown = core.presenter.finish(options, shown);
        const uint32_t width = shown.width, height = shown.height;
        const float ratio = aspect_ratio(width, height, frame.aspect_num, frame.aspect_den);
        if (!core.announced_size || width != core.announced_width || height != core.announced_height || ratio != core.announced_ratio) {
            retro_game_geometry geometry = {};
            geometry.base_width = geometry.max_width = width;
            geometry.base_height = geometry.max_height = height;
            geometry.aspect_ratio = ratio;
            environment_cb(RETRO_ENVIRONMENT_SET_GEOMETRY, &geometry);      // optional: a refusal only costs the frontend's hint
            core.announced_size = true;
            core.announced_width = width;
            core.announced_height = height;
            core.announced_ratio = ratio;
            core.announced_aspect_num = frame.aspect_num;
            core.announced_aspect_den = frame.aspect_den;
        }
        if (!core.can_dupe) {
            core.last_picture.resize(size_t(width) * height * shown.bytes_per_pixel);
            for (uint32_t y = 0; y < height; ++y)
                memcpy(&core.last_picture[size_t(y) * width * shown.bytes_per_pixel], static_cast<const uint8_t *>(shown.data) + size_t(y) * shown.pitch,
                       size_t(width) * shown.bytes_per_pixel);
        }
        core.last_width = width;
        core.last_height = height;
        video_cb(shown.data, width, height, shown.pitch);
        return;
    }
    const size_t bytes = core.bytes_per_pixel;
    // No new picture in this unit (a mode change, or nothing drawn yet): repeat the previous one.
    if (core.last_width && core.can_dupe) {
        video_cb(nullptr, core.last_width, core.last_height, size_t(core.last_width) * bytes);
    } else if (core.last_width && !core.last_picture.empty()) {
        video_cb(core.last_picture.data(), core.last_width, core.last_height, size_t(core.last_width) * bytes);
    } else {
        const uint32_t width = core.announced_width ? core.announced_width : 756, height = core.announced_height ? core.announced_height : 574;
        core.blank.assign(size_t(width) * height * bytes, 0);
        video_cb(core.blank.data(), width, height, size_t(width) * bytes);
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

void notify_info(const char *format, ...)
{
    va_list arguments;
    va_start(arguments, format);
    vnotify(RETRO_LOG_INFO, format, arguments);
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

// The frontend's save directory, or empty when it has none (nothing can then be kept between sessions).
std::string save_directory()
{
    const char *directory = nullptr;
    if (!environment_cb(RETRO_ENVIRONMENT_GET_SAVE_DIRECTORY, &directory) || !directory || !*directory || strlen(directory) > p6_libretro::kMaxPathBytes) return std::string();
    return directory;
}

// A disk was just read (its bytes and id are the file's): remembers that id as the origin and, with floppy writes on, swaps in the copy an earlier session
// saved for it, if there is one and it is a plain ADF the bridge takes. The copy becomes the disk (its own SHA-256 is its identity); a file that is not usable
// is reported and the original is used.
void use_saved_copy(Medium *medium)
{
    memcpy(medium->origin, medium->id, sizeof medium->id);
    if (!core.settings.floppy_writes) return;
    const std::string path = p6_libretro::saved_disk_path(save_directory(), medium->origin);
    if (path.empty()) return;
    std::vector<uint8_t> bytes;
    const p6_libretro::ReadResult read = core.files.read(path, p6_libretro::kDiskBytes, &bytes);
    if (read == p6_libretro::ReadResult::CannotOpen) return;
    if (read != p6_libretro::ReadResult::Ok || bytes.size() != p6_libretro::kDiskBytes || !p6_libretro::is_plain_adf_start(bytes.data(), bytes.size())) {
        notify_warning("the saved changes of a disk (%s) are not used: %s", path.c_str(),
                       read != p6_libretro::ReadResult::Ok ? describe(read) : "it is not a plain 901120-byte ADF");
        return;
    }
    medium->bytes = std::move(bytes);
    p6_sha256_hash(medium->bytes.data(), medium->bytes.size(), medium->id);
    medium->saved_copy = true;
    log(RETRO_LOG_INFO, "the disk uses the changes an earlier session saved in %s", path.c_str());
}

// When the content closes (and only then): the disks the guest changed. Each is exported (what the machine holds now, the file's disk with the overlay over it);
// if it differs from the bytes it was loaded with it is a change. With puae6_floppy_save on, a change is written to the save directory by an atomic replace;
// otherwise the person is told, once, that changes are being discarded. Nothing here runs during a run, a state load or a rewind, and a failure only costs the changes.
void save_floppy_changes()
{
    if (!core.loaded || core.faulted || !core.settings.floppy_writes) return;
    const bool keep = core.settings.floppy_save == 1;
    const std::string directory = keep ? save_directory() : std::string();
    if (keep && directory.empty()) notify_warning("the frontend has no save directory: the changes to the floppies are discarded");
    std::vector<uint8_t> image;
    unsigned changed = 0, saved = 0, lost = 0;
    try {
        image.resize(p6_libretro::kDiskBytes);
        for (size_t i = 0; i < core.media.size(); ++i) {
            const Medium &medium = core.media[i];
            if (medium.type != P6_MEDIA_ADF) continue;        // the hard files are saved by save_hard_file_changes
            uint64_t written = 0;
            p6_error error = make_error();
            const p6_result result = p6_media_export(uint32_t(i), image.data(), image.size(), &written, &error);
            if (result == P6_ERROR_UNSUPPORTED) {
                notify_warning("disk %zu has a track in a format that is not AmigaDOS, which an ADF cannot hold: its changes are discarded", i + 1);
                ++lost;
                continue;
            }
            if (result != P6_OK) {
                report("p6_media_export", result, error);
                continue;
            }
            if (written == medium.bytes.size() && memcmp(image.data(), medium.bytes.data(), image.size()) == 0) continue;      // as it was loaded
            ++changed;
            if (!keep || directory.empty()) continue;
            if (!p6_libretro::is_plain_adf_start(image.data(), image.size())) {
                notify_warning("disk %zu cannot be saved: its first bytes would make the engine take it for another format", i + 1);
                ++lost;
                continue;
            }
            const std::string path = p6_libretro::saved_disk_path(directory, medium.origin);
            std::string why;
            if (p6_libretro::write_file_atomically(path, image.data(), image.size(), &why)) {
                log(RETRO_LOG_INFO, "disk %zu: the changes were saved in %s", i + 1, path.c_str());
                ++saved;
            } else {
                notify_warning("disk %zu: the changes were not saved: %s", i + 1, why.c_str());
                ++lost;
            }
        }
    } catch (const std::bad_alloc &) {
        notify_warning("not enough memory to save the changes to the floppies");
        return;
    }
    if (saved) notify_info("%u disk(s) saved in %s", saved, directory.c_str());
    if (changed && !keep) notify_warning("%u disk(s) were changed and the changes are discarded: Save Floppy Changes is off", changed);
    (void)lost;
}

// A hard file was just read (its bytes and id are the file's): remembers that id as the origin and, with puae6_hdf_save on, swaps in the copy an earlier session saved for it, if
// there is one and it has the size of the file. The copy becomes the disk (its own SHA-256 is its identity); a file that is not usable is reported and the original is used.
void use_saved_hard_file(Medium *medium)
{
    memcpy(medium->origin, medium->id, sizeof medium->id);
    if (core.settings.hdf_save != 1) return;
    const std::string path = p6_libretro::saved_hard_file_path(save_directory(), medium->origin);
    if (path.empty()) return;
    std::vector<uint8_t> bytes;
    const p6_libretro::ReadResult read = core.files.read(path, p6_libretro::kMaxHdfBytes, &bytes);
    if (read == p6_libretro::ReadResult::CannotOpen) return;
    if (read != p6_libretro::ReadResult::Ok || bytes.size() != medium->bytes.size()) {
        notify_warning("the saved changes of a hard disk (%s) are not used: %s", path.c_str(),
                       read != p6_libretro::ReadResult::Ok ? describe(read) : "it does not have the size of the hard disk it was saved from");
        return;
    }
    medium->bytes = std::move(bytes);
    p6_sha256_hash(medium->bytes.data(), medium->bytes.size(), medium->id);
    medium->saved_copy = true;
    log(RETRO_LOG_INFO, "the hard disk uses the changes an earlier session saved in %s", path.c_str());
}

// When the content closes (and only then): the hard disks the guest changed. p6_media_write_status_get says whether the guest wrote any 4 KiB chunk of one, so an untouched
// disk costs nothing; otherwise the image as it is now (the file's bytes with the overlay over them) is exported and, if it differs from the bytes it was loaded with, it is a
// change. With puae6_hdf_save on, a change replaces puae6-<id>.hdf in the save directory by an atomic replace; otherwise the person is told that the changes are discarded.
void save_hard_file_changes()
{
    if (!core.loaded || core.faulted) return;
    bool any = false;
    for (const Medium &medium : core.media) any = any || medium.type == P6_MEDIA_HDF;
    if (!any) return;
    const bool keep = core.settings.hdf_save == 1;
    const std::string directory = keep ? save_directory() : std::string();
    if (keep && directory.empty()) notify_warning("the frontend has no save directory: the changes to the hard disks are discarded");
    unsigned changed = 0, saved = 0, hard_units = 0;
    try {
        std::vector<uint8_t> image;
        for (size_t i = 0; i < core.media.size(); ++i) {
            const Medium &medium = core.media[i];
            if (medium.type != P6_MEDIA_HDF) continue;
            const unsigned unit = hard_units++;
            p6_media_write_status status = {};
            status.abi_version = P6_ABI_VERSION;
            status.struct_size = sizeof status;
            status.media_index = uint32_t(i);
            p6_error error = make_error();
            p6_result result = p6_media_write_status_get(&status, &error);
            if (result != P6_OK) {
                report("p6_media_write_status_get", result, error);
                continue;
            }
            if (status.medium_sectors == 0) continue;                    // the guest never wrote to it
            image.resize(medium.bytes.size());
            uint64_t written = 0;
            error = make_error();
            result = p6_media_export(uint32_t(i), image.data(), image.size(), &written, &error);
            if (result != P6_OK || written != medium.bytes.size()) {
                report("p6_media_export", result, error);
                continue;
            }
            if (memcmp(image.data(), medium.bytes.data(), image.size()) == 0) continue;      // written with the bytes it already had
            ++changed;
            if (!keep || directory.empty()) continue;
            const std::string path = p6_libretro::saved_hard_file_path(directory, medium.origin);
            std::string why;
            if (p6_libretro::write_file_atomically(path, image.data(), image.size(), &why)) {
                log(RETRO_LOG_INFO, "hard disk DH%u: the changes were saved in %s", unit, path.c_str());
                ++saved;
            } else {
                notify_warning("hard disk DH%u: the changes were not saved: %s", unit, why.c_str());
            }
        }
    } catch (const std::bad_alloc &) {
        notify_warning("not enough memory to save the changes to the hard disks");
        return;
    }
    if (saved) notify_info("%u hard disk(s) saved in %s", saved, directory.c_str());
    if (changed && !keep) notify_warning("%u hard disk(s) were changed and the changes are discarded: Save Hard Disk Changes is off", changed);
}

// Reads the .uae the content is and lays its keys over `settings` (the frontend's options): `disks` are the ones it names. Returns false after saying why, with
// every problem of the file (up to a bound) in the log and the first ones on screen.
bool import_configuration(const retro_game_info *game, p6_options::Settings *settings, std::vector<p6_libretro::PlaylistEntry> *disks)
{
    using namespace p6_libretro;
    const std::string content = game->path;
    std::vector<uint8_t> bytes;
    const ReadResult read = core.files.read(content, kMaxUaeBytes, &bytes);
    if (read != ReadResult::Ok) {
        notify_error("the configuration %s cannot be loaded: %s", content.c_str(), describe(read));
        return false;
    }
    UaeImport imported;
    const bool ok = import_uae(bytes, directory_of(content), *settings, P6_MAX_MEDIA, &imported);
    for (const UaeReport &report : imported.warnings) log(RETRO_LOG_WARN, "%s %s", content.c_str(), describe_report(report).c_str());
    if (imported.warnings_omitted) log(RETRO_LOG_WARN, "%s: %zu more ignored keys", content.c_str(), imported.warnings_omitted);
    if (!ok) {
        for (const UaeReport &report : imported.errors) log(RETRO_LOG_ERROR, "%s %s", content.c_str(), describe_report(report).c_str());
        if (imported.errors_omitted) log(RETRO_LOG_ERROR, "%s: %zu more problems", content.c_str(), imported.errors_omitted);
        const size_t shown = std::min<size_t>(imported.errors.size(), 3);
        std::string first;
        for (size_t i = 0; i < shown; ++i) first += (i ? "; " : "") + describe_report(imported.errors[i]);
        notify_error("the configuration %s is refused (%zu problem(s)): %s", content.c_str(), imported.errors.size() + imported.errors_omitted, first.c_str());
        return false;
    }
    for (const std::string &line : imported.applied) log(RETRO_LOG_INFO, "%s: %s", content.c_str(), line.c_str());
    if (!imported.warnings.empty())
        notify_warning("%zu key(s) of %s were ignored (they do not change the machine; see the log)", imported.warnings.size() + imported.warnings_omitted, content.c_str());
    *settings = imported.settings;
    *disks = std::move(imported.disks);
    return true;
}

// Can the content take one more hard disk? Says why not.
bool can_take_hard_file(const std::string &path, size_t already)
{
    p6_caps caps = {};
    caps.abi_version = P6_ABI_VERSION;
    caps.struct_size = sizeof caps;
    p6_query_caps(&caps, nullptr);
    if (!(caps.flags & P6_CAP_MEDIA_HDF)) {
        notify_error("%s: this build of the core cannot mount hard disks", path.c_str());
        return false;
    }
    if (already >= P6_MAX_HDF) {
        notify_error("%s: the core mounts at most %u hard disks", path.c_str(), P6_MAX_HDF);
        return false;
    }
    return true;
}

// A hard file whose bytes are in `medium` (read and decoded, or built): its identity, the copy an earlier session saved, the checks that apply to every hard disk of the content, and
// its place in `hard_files` (the caller puts them after the floppies).
bool add_hard_file(Medium &&medium, const std::string &path, std::vector<Medium> *hard_files, size_t *total_bytes)
{
    using namespace p6_libretro;
    if (medium.bytes.size() > kMaxHdfTotalBytes - std::min(kMaxHdfTotalBytes, *total_bytes)) {
        notify_error("%s: the hard disks of the content together are larger than the %zu bytes this core holds", path.c_str(), kMaxHdfTotalBytes);
        return false;
    }
    p6_sha256_hash(medium.bytes.data(), medium.bytes.size(), medium.id);
    use_saved_hard_file(&medium);
    for (const Medium &other : *hard_files)
        if (memcmp(other.id, medium.id, sizeof medium.id) == 0) {
            notify_error("%s has the same bytes as an earlier hard disk of the content: the same disk cannot be mounted twice", path.c_str());
            return false;
        }
    *total_bytes += medium.bytes.size();
    log(RETRO_LOG_INFO, "hard disk DH%zu (%s): %s", hard_files->size(), path.c_str(), describe_hard_file(inspect_hard_file(medium.bytes.data(), medium.bytes.size())).c_str());
    hard_files->push_back(std::move(medium));
    return true;
}

// One hard disk of the content (T63a): read through the VFS or stdio with the cap of hdf.h, decoded when it is an .hdz, and appended to `hard_files` (the caller puts them after
// the floppies). `ordinal` is its place among everything the content names. Returns false after saying why.
bool load_hard_file(const std::string &path, size_t ordinal, std::vector<Medium> *hard_files, size_t *total_bytes)
{
    using namespace p6_libretro;
    if (!can_take_hard_file(path, hard_files->size())) return false;
    Medium medium;
    medium.type = P6_MEDIA_HDF;
    const ReadResult read = core.files.read(path, kMaxHdfBytes, &medium.bytes);
    if (read == ReadResult::TooLarge) {
        notify_error("%s (entry %zu) is larger than the %zu bytes of hard disk this core reads", path.c_str(), ordinal + 1, kMaxHdfBytes);
        return false;
    }
    if (read != ReadResult::Ok) {
        notify_error("%s (entry %zu): %s", path.c_str(), ordinal + 1, describe(read));
        return false;
    }
    std::string why;
    if (!decode_hard_file(&medium.bytes, path, kMaxHdfBytes, &why)) {       // in memory: the image is what is hashed, so the container is no part of the identity
        notify_error("%s (entry %zu): %s", path.c_str(), ordinal + 1, why.c_str());
        return false;
    }
    return add_hard_file(std::move(medium), path, hard_files, total_bytes);
}

enum class VolumeSource { Directory, Lha, Zip };

// A volume (T64a): the directory at `path`, or the LHA (read here) or the ZIP (read already, its bytes are `archive`) turned into one FFS hard file in memory and appended to
// `hard_files` like an .hdf. Nothing of the host is kept: the image is the content. Returns false after saying why.
bool load_volume(const std::string &path, size_t ordinal, VolumeSource source, std::vector<uint8_t> *archive, std::vector<Medium> *hard_files, size_t *total_bytes)
{
    using namespace p6_libretro;
    if (!can_take_hard_file(path, hard_files->size())) return false;
    std::vector<VolumeFile> files;
    std::string why;
    bool ok = false;
    const char *what = source == VolumeSource::Directory ? "directory" : source == VolumeSource::Lha ? "LHA archive" : "ZIP file";
    if (source == VolumeSource::Directory) {
        ok = volume_from_directory(core.files, path, &files, &why);
    } else {
        if (source == VolumeSource::Lha) {
            const ReadResult read = core.files.read(path, kMaxVolumeArchiveBytes, archive);
            if (read == ReadResult::TooLarge) {
                notify_error("%s (entry %zu) is larger than the %zu bytes of archive this core reads as a volume", path.c_str(), ordinal + 1, kMaxVolumeArchiveBytes);
                return false;
            }
            if (read != ReadResult::Ok) {
                notify_error("%s (entry %zu): %s", path.c_str(), ordinal + 1, describe(read));
                return false;
            }
            ok = volume_from_lha(*archive, &files, &why);
        } else {
            ok = volume_from_zip(*archive, &files, &why);
        }
        archive->clear();
        archive->shrink_to_fit();
    }
    VolumeSummary summary;
    Medium medium;
    medium.type = P6_MEDIA_HDF;
    if (ok) ok = build_volume_image(&files, &medium.bytes, &summary, &why);
    if (!ok) {
        notify_error("%s (entry %zu) cannot be a volume: the %s %s", path.c_str(), ordinal + 1, what, why.c_str());
        return false;
    }
    log(RETRO_LOG_INFO, "volume DH%zu (%s): %s of %zu files and %zu directories (%zu bytes), built as a hard disk of %zu bytes; the host is not read again", hard_files->size(), path.c_str(),
        what, summary.files, summary.directories, summary.bytes, medium.bytes.size());
    return add_hard_file(std::move(medium), path, hard_files, total_bytes);
}

// The disks the content names, read through the VFS or stdio, with the distinct ones in `media` (the manifest) and one image per playlist
// entry. Returns false after saying why (nothing is half-loaded: the caller drops what was filled). `names` receives the names the automatic model looks at (T76, model.h):
// the path of the content, then each entry of the playlist in order and, after an archive, the members it holds.
bool load_disks(const retro_game_info *game, const std::vector<p6_libretro::PlaylistEntry> *configured, std::vector<Medium> *media,
                std::vector<p6_libretro::Image> *images, std::vector<std::string> *names)
{
    using namespace p6_libretro;
    if (!game) return true;                       // no content: the Kickstart boots alone (SET_SUPPORT_NO_GAME)
    if (!game->path || !*game->path) {
        notify_error("the content has no path; this core reads the file itself");
        return false;
    }
    const std::string content = game->path;
    names->push_back(content);
    std::vector<PlaylistEntry> entries;
    if (configured) {                             // a .uae: the disks it names, already checked and resolved by the importer
        entries = *configured;
    } else if (has_extension(content, "m3u")) {
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
    } else if (core.files.is_directory(content)) {        // a directory is a volume (T64a); a frontend that lists "/" among the extensions hands it over as content
        std::string trimmed = content;
        while (trimmed.size() > 1 && is_separator(trimmed.back())) trimmed.pop_back();
        entries.push_back({content, file_name(trimmed)});
    } else if (is_disk_path(content) || is_hard_file_extension(content) || is_volume_extension(content)) {
        entries.push_back({content, without_extension(file_name(content))});
    } else if (const char *reason = unsupported_format_reason(content)) {
        notify_error("%s cannot be loaded: %s", content.c_str(), reason);
        return false;
    } else {
        notify_error("%s is not content this core loads (an .adf, .adz, .dms, .zip, .7z or .rp9 disk or set of disks, an .hdf or .hdz hard disk, an .lha, a .zip of files or a directory as a volume, or an .m3u playlist of them)",
                     content.c_str());
        return false;
    }
    std::vector<Medium> hard_files;               // the hard disks of the content, in the order named: they follow every floppy in the manifest
    size_t hard_bytes = 0;
    for (size_t i = 0; i < entries.size(); ++i) {
        const PlaylistEntry &entry = entries[i];
        if (entry.path != names->back()) names->push_back(entry.path);
        // "file.zip#member.adf" names one member of an archive (T59); the rest of the checks are about the archive itself.
        std::string archive_path = entry.path, member;
        split_member(entry.path, &archive_path, &member);
        if (member.empty() && core.files.is_directory(entry.path)) {
            if (!load_volume(entry.path, i, VolumeSource::Directory, nullptr, &hard_files, &hard_bytes)) return false;
            continue;
        }
        if (member.empty() && is_hard_file_extension(entry.path)) {
            if (!load_hard_file(entry.path, i, &hard_files, &hard_bytes)) return false;
            continue;
        }
        if (member.empty() && is_volume_extension(entry.path)) {
            std::vector<uint8_t> archive;
            if (!load_volume(entry.path, i, VolumeSource::Lha, &archive, &hard_files, &hard_bytes)) return false;
            continue;
        }
        if (!is_disk_extension(archive_path)) {
            const char *reason = unsupported_format_reason(entry.path);
            notify_error("disk %zu (%s) is not an .adf, .adz, .dms, .zip, .7z, .rp9, .hdf, .hdz or .lha file or a directory%s%s", i + 1, entry.path.c_str(), reason ? ": " : "", reason ? reason : "");
            return false;
        }
        Medium first;
        // The file may be a container, so the cap is the largest container; what it holds is bounded by decode_disk and read_disk_set. A zip may also be a volume (files, no disk), which can be larger.
        const bool zipped = has_extension(archive_path, "zip");
        const size_t cap = is_archive_extension(archive_path) ? kMaxVolumeArchiveBytes : kMaxDiskFileBytes;
        const ReadResult read = core.files.read(archive_path, cap, &first.bytes);
        if (read == ReadResult::TooLarge) {
            notify_error("disk %zu (%s) is larger than any %s this core reads (%zu bytes)", i + 1, entry.path.c_str(), is_archive_extension(archive_path) ? "archive" : "disk file", cap);
            return false;
        }
        if (read != ReadResult::Ok) {
            notify_error("disk %zu (%s): %s", i + 1, entry.path.c_str(), describe(read));
            return false;
        }
        if (zipped && member.empty() && zip_is_volume(first.bytes)) {
            if (!load_volume(entry.path, i, VolumeSource::Zip, &first.bytes, &hard_files, &hard_bytes)) return false;
            continue;
        }
        // The disks this entry stands for, in order: one for a plain disk file or a one-disk archive, the whole set (T59) for an archive with several, the member for "#member".
        struct Piece {
            std::string name;
            Medium medium;
        };
        std::vector<Piece> pieces;
        std::string why;
        bool is_set = false;
        if (is_archive_bytes(first.bytes)) {
            DiskSet set;
            if (!read_disk_set(first.bytes, member, ArchiveLimits(), &set, &why)) {
                notify_error("disk %zu (%s): %s", i + 1, entry.path.c_str(), why.c_str());
                return false;
            }
            for (const std::string &note : set.notes) log(RETRO_LOG_INFO, "%s: %s", entry.path.c_str(), note.c_str());
            is_set = set.disks.size() > 1 || !member.empty();
            if (is_set) {
                std::string names;
                for (const DiskMember &disk : set.disks) names += (names.empty() ? "" : ", ") + disk.name;
                log(RETRO_LOG_INFO, "%s: %s of %zu disk(s), in this order: %s", entry.path.c_str(), set.kind.c_str(), set.disks.size(), names.c_str());
            }
            for (const DiskMember &disk : set.disks) names->push_back(disk.name);
            for (DiskMember &disk : set.disks) {
                Piece piece;
                piece.name = disk.name;
                piece.medium.bytes = std::move(disk.adf);
                pieces.push_back(std::move(piece));
            }
        } else {
            if (!decode_disk(&first.bytes, entry.path, &why)) {      // in memory: the ADF is what is hashed, so the container is no part of the identity
                notify_error("disk %zu (%s): %s", i + 1, entry.path.c_str(), why.c_str());
                return false;
            }
            Piece piece;
            piece.medium.bytes = std::move(first.bytes);
            pieces.push_back(std::move(piece));
        }
        first.bytes.clear();
        if (images->size() + pieces.size() > P6_MAX_MEDIA) {
            notify_error("the content names more than %u disks (disk %zu, %s, adds %zu): the most this core keeps is %u", P6_MAX_MEDIA, i + 1, entry.path.c_str(), pieces.size(), P6_MAX_MEDIA);
            return false;
        }
        const bool explicit_label = entry.label != without_extension(file_name(entry.path));
        for (Piece &piece : pieces) {
            Medium medium = std::move(piece.medium);
            p6_sha256_hash(medium.bytes.data(), medium.bytes.size(), medium.id);
            use_saved_copy(&medium);
            size_t known = 0;
            while (known < media->size() && memcmp((*media)[known].id, medium.id, sizeof medium.id) != 0) ++known;
            if (known == media->size()) {
                log(RETRO_LOG_INFO, "medium %zu sha256=%s", known, to_hex(medium.id, sizeof medium.id).c_str());
                media->push_back(std::move(medium));
            }
            std::string path = entry.path, label = entry.label;
            if (is_set) {
                const std::string member_label = without_extension(file_name(piece.name));
                path = archive_path + "#" + piece.name;
                label = !explicit_label ? member_label : pieces.size() == 1 && !member.empty() ? entry.label : entry.label + " - " + member_label;
            }
            images->push_back({path, label, uint32_t(known)});
            log(RETRO_LOG_INFO, "disk %zu: medium %zu, label \"%s\"", images->size(), known, label.c_str());
        }
    }
    if (media->size() + hard_files.size() > P6_MAX_MEDIA) {
        notify_error("the content names %zu distinct floppies and %zu hard disks: the most this core keeps is %u media", media->size(), hard_files.size(), P6_MAX_MEDIA);
        return false;
    }
    size_t unit = 0;
    for (Medium &hard : hard_files) {              // after the floppies, so that the indices of the tray (the floppies) are the same with or without them
        log(RETRO_LOG_INFO, "hard disk DH%zu: medium %zu sha256=%s", unit++, media->size(), to_hex(hard.id, sizeof hard.id).c_str());
        media->push_back(std::move(hard));
    }
    return true;
}

// The Kickstart of the machine the options chose (T23d): the first file of the system directory under one of that machine's known names that the
// bridge accepts. A name that is there but refused (a damaged copy, another machine's ROM) does not hide a good one under the next name; the names of
// the other machine are not tried, so a missing Kickstart is a clear failure and never another machine's ROM in silence. Returns false after saying why.
// T51: `chosen` is the version puae6_kickstart names (nullptr: Automatic, the machine's own names); then only the names of that version are tried, and the bridge says from the bytes whether this
// machine can run it. A file Amiga Forever encrypted (AMIROMTYPE1) is decrypted with the rom.key of the same system directory before p6_load; the decrypted image is the firmware.
bool load_firmware(const std::vector<p6_media> &manifest, const p6_libretro::FirmwareNames &firmware, const p6_libretro::KickstartChoice *chosen)
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
    const char *const *names = chosen ? chosen->names : firmware.names;
    const size_t count = chosen ? chosen->count : firmware.count;
    std::vector<uint8_t> key;                // rom.key, read once if a ROM is encrypted; never logged, never kept past this function
    bool key_read = false;
    std::string refusals;
    for (size_t n = 0; n < count; ++n) {
        const char *const name = names[n];
        const std::string path = join_path(directory, name);
        const ReadResult read = core.files.read(path, kMaxFirmwareBytes, &core.firmware);
        if (read == ReadResult::CannotOpen) continue;
        if (read != ReadResult::Ok) {
            log(RETRO_LOG_WARN, "%s is not used: %s", name, describe(read));
            refusals += refusals.empty() ? "" : "; ";
            refusals += std::string(name) + " " + describe(read);
            continue;
        }
        bool decrypted = false;
        if (is_cloanto_rom(core.firmware.data(), core.firmware.size())) {
            if (!key_read) {
                key_read = true;
                for (const char *key_name : {"rom.key", "ROM.KEY"}) {
                    if (core.files.read(join_path(directory, key_name), kMaxRomKeyBytes, &key) == ReadResult::Ok && !key.empty()) break;
                    key.clear();
                }
            }
            std::vector<uint8_t> rom;
            const CloantoResult opened = decrypt_cloanto_rom(core.firmware.data(), core.firmware.size(), key.data(), key.size(), &rom);
            if (opened != CloantoResult::Decrypted) {
                const char *why = opened == CloantoResult::NeedsKey ? "is encrypted by Amiga Forever (AMIROMTYPE1) and the system directory has no usable rom.key"
                                                                      : "is encrypted by Amiga Forever (AMIROMTYPE1) and the rom.key of the system directory does not decrypt it";
                log(RETRO_LOG_WARN, "%s %s", name, why);
                refusals += refusals.empty() ? "" : "; ";
                refusals += std::string(name) + " " + why;
                continue;
            }
            core.firmware.swap(rom);         // the decrypted image is the firmware: its SHA-256 is the identity of the states
            decrypted = true;
        }
        p6_error error = make_error();
        const p6_result result = p6_load(p6_buffer{core.firmware.data(), core.firmware.size()}, manifest.data(), uint32_t(manifest.size()), &error);
        if (result == P6_OK) {
            log(RETRO_LOG_INFO, "Kickstart %s%s, %s", name, decrypted ? " (decrypted with rom.key)" : "", core.files.uses_vfs() ? "read through the frontend VFS" : "read with stdio");
            return true;
        }
        report("p6_load", result, error);
        refusals += refusals.empty() ? "" : "; ";
        refusals += std::string(name) + " refused (" + error.message + ")";
    }
    core.firmware.clear();
    if (refusals.empty() && chosen)
        notify_error("no Kickstart %s ROM in the system directory %s (looked for %s, ...); puae6_kickstart asks for it on the %s", chosen->version, directory, chosen->names[0],
                     firmware.model);
    else if (refusals.empty())
        notify_error("no Kickstart %s ROM in the system directory %s (looked for %s, ...) for the %s chosen by puae6_machine", firmware.kickstart, directory,
                     firmware.names[0], firmware.model);
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
    std::string archive_path = path, member;
    split_member(path, &archive_path, &member);
    if (!is_disk_extension(archive_path)) {
        const char *reason = unsupported_format_reason(path);
        *why = "it is not an .adf, .adz, .dms, .zip, .7z or .rp9 disk";
        if (reason) *why += std::string(": ") + reason;
        return false;
    }
    const size_t cap = is_archive_extension(archive_path) ? kMaxVolumeArchiveBytes : kMaxDiskFileBytes;
    const ReadResult read = core.files.read(archive_path, cap, &medium->bytes);
    if (read == ReadResult::TooLarge) {
        *why = "it is larger than any disk file this core reads";
        return false;
    }
    if (read != ReadResult::Ok) {
        *why = describe(read);
        return false;
    }
    if (is_archive_bytes(medium->bytes)) {
        // One disk: an archive of one, or a member by name ("file.zip#member.adf"); a set is added disk by disk.
        DiskSet set;
        if (!read_disk_set(medium->bytes, member, ArchiveLimits(), &set, why)) return false;
        if (set.disks.size() != 1) {
            *why = "the archive holds " + std::to_string(set.disks.size()) + " disks and one is added at a time: name the one to add as file#member.adf";
            return false;
        }
        medium->bytes = std::move(set.disks[0].adf);
    } else if (!decode_disk(&medium->bytes, path, why)) {
        return false;
    }
    p6_sha256_hash(medium->bytes.data(), medium->bytes.size(), medium->id);
    use_saved_copy(medium);
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
        // Saved by a frontend that repeats pictures, or in the other pixel format (a state of a 16-bit session in a 24-bit one): nothing to show again.
        if (section.picture.empty() || section.picture.size() != size_t(core.last_width) * core.last_height * core.bytes_per_pixel)
            core.last_width = core.last_height = 0;
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
    // The frontend's file system, asked for as early as the API recommends; version 1 (open, size, read, close) is enough for files.
    retro_vfs_interface_info vfs = {};
    // Version 3 has stat and the directory calls a directory volume needs (T64a); a frontend that offers less gets files through the VFS and directories through the C library.
    for (const unsigned wanted : {3u, 1u}) {
        vfs = {};
        vfs.required_interface_version = wanted;
        if (environment_cb(RETRO_ENVIRONMENT_GET_VFS_INTERFACE, &vfs) && vfs.iface && vfs.iface->open && vfs.iface->read && vfs.iface->close) {
            core.files.use_vfs(vfs.iface, std::max(wanted, vfs.required_interface_version));
            break;
        }
    }
    register_disk_control();
    bool achievements = true;       // the Chip RAM is RETRO_MEMORY_SYSTEM_RAM and the map describes the rest (T75)
    environment_cb(RETRO_ENVIRONMENT_SET_SUPPORT_ACHIEVEMENTS, &achievements);     // optional
    retro_led_interface led = {};
    if (environment_cb(RETRO_ENVIRONMENT_GET_LED_INTERFACE, &led) && led.set_led_state) core.set_led = led.set_led_state;
}

RETRO_API void retro_deinit(void)
{
    save_floppy_changes();
    save_hard_file_changes();
    stop_image();
    core.files.use_vfs(nullptr);
    log_cb = nullptr;
}

RETRO_API void retro_get_system_info(struct retro_system_info *info)
{
    memset(info, 0, sizeof *info);
    info->library_name = "PUAE6";
    info->library_version = P6_LIBRETRO_VERSION;
    info->valid_extensions = "adf|adz|dms|zip|7z|rp9|lha|lzh|hdf|hdz|m3u|uae|/";
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
    // The nominal active raster of each standard, or the window of it that the crop option asks for (the automatic crop starts with all of it); a unit can draw
    // less, and the geometry is updated when it does.
    const uint32_t native_width = 756, native_height = ntsc ? 484 : 574;
    uint32_t width = native_width, height = native_height;
    p6_libretro::crop_size(present_options(), native_width, native_height, ntsc ? 43 : 26, ntsc ? 50 : 25, ntsc, p6_libretro::Box{0, 0, native_width, native_height},
                           &width, &height);
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
        const p6_input input = p6_libretro::read_input(input_state_cb, core.device, p6_libretro::input_config(core.settings), &core.input);
        core.status.ports[0] = input.ports[0];
        core.status.ports[1] = input.ports[1];
        frame.abi_version = P6_ABI_VERSION;
        frame.struct_size = sizeof frame;
        p6_error error = make_error();
        const p6_result result = p6_run_frame(&input, P6_RENDER_VIDEO | P6_RENDER_AUDIO, &frame, &error);
        if (result == P6_OK) {
            have_frame = true;
            core.status.drive_light = (frame.flags & P6_FRAME_DRIVE_LIGHT) != 0;
            set_leds(core.status.drive_light);
            refresh_memory_map();
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
    const Picture picture = plan_picture(frame, have_frame);
    if (have_frame) announce_av_change(frame, picture);
    deliver_video(frame, have_frame, picture);
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
    refresh_memory_map();
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
    // The options are read before anything is loaded: a value the schema does not allow stops here, and these are the values the machine runs with.
    p6_options::Settings chosen;
    std::string why;
    if (!p6_libretro::read_options(environment_cb, &chosen, &why)) {
        notify_error("%s", why.c_str());
        return false;
    }
    // The pixel format is announced once, here: RGB565 when Color Depth asks for it and the frontend takes it, else XRGB8888 (the format the machine draws in).
    retro_pixel_format format = chosen.video_color_depth == 1 ? RETRO_PIXEL_FORMAT_RGB565 : RETRO_PIXEL_FORMAT_XRGB8888;
    unsigned bytes = format == RETRO_PIXEL_FORMAT_RGB565 ? 2 : 4;
    bool accepted = environment_cb(RETRO_ENVIRONMENT_SET_PIXEL_FORMAT, &format);
    if (!accepted && bytes == 2) {
        notify_warning("the frontend does not accept 16-bit pixels (RGB565): the picture stays in 24-bit");
        format = RETRO_PIXEL_FORMAT_XRGB8888;
        bytes = 4;
        accepted = environment_cb(RETRO_ENVIRONMENT_SET_PIXEL_FORMAT, &format);
    }
    if (!accepted) {
        log(RETRO_LOG_ERROR, "the frontend does not accept XRGB8888, the pixel format the machine draws in");
        return false;
    }
    core.bytes_per_pixel = bytes;
    p6_libretro::offer_input_descriptors(environment_cb);
    note_shadowed_joystick();
    bool can_dupe = false;
    core.can_dupe = environment_cb(RETRO_ENVIRONMENT_GET_CAN_DUPE, &can_dupe) && can_dupe;
    bool updated = false;
    environment_cb(RETRO_ENVIRONMENT_GET_VARIABLE_UPDATE, &updated);       // clears the flag: what was just read is what is in force
    // A .uae as content lays its keys over the frontend's options (config_import.h): `core.requested` stays what the frontend holds, so that only a change in
    // the menu is reported later, and `core.settings` is what the machine runs with.
    p6_options::Settings effective = chosen;
    std::vector<p6_libretro::PlaylistEntry> configured_disks;
    const bool configured = game && game->path && p6_libretro::has_extension(game->path, "uae");
    if (configured) {
        // The automatic model and video standard of a .uae are resolved from its own name before its keys are read (they are checked against the machine and refine it); the disks it
        // names are not looked at, since a .uae that cares about the machine names it. A .uae is never hard disk content here (the importer refuses a hard disk).
        const p6_libretro::ModelResolution resolved = p6_libretro::resolve_model(effective, false, std::vector<std::string>{game->path});
        for (const std::string &note : resolved.notes) log(RETRO_LOG_INFO, "%s", note.c_str());
        for (const std::string &warning : resolved.warnings) notify_warning("%s", warning.c_str());
        effective = resolved.settings;
        if (!import_configuration(game, &effective, &configured_disks)) return false;
    }
    core.settings = effective;
    core.requested = chosen;
    core.input = p6_libretro::InputMemory();
    core.input.dpad_mouse = chosen.joyport == 1;
    core.input.statusbar_shown = chosen.statusbar_startup == 1;       // the bar starts up as the option says; the hotkey or the mapped button flips it from there        // the pad starts as the joystick or as the mouse; the mapper's toggle flips it from there
    try {
        std::vector<p6_libretro::Image> images;
        std::vector<std::string> names;
        if (!load_disks(game, configured ? &configured_disks : nullptr, &core.media, &images, &names)) {
            stop_image();
            return false;
        }
        // The automatic model and video standard (T76) are resolved here, once the content is known and before the machine exists: a pure function of the options, of whether the
        // content has a hard disk and of the names (model.h). What the machine is made with is `core.settings` from here on, so the p6_config below, and with it the state identity,
        // carries the machine a tag picked.
        if (!configured) {          // a .uae was resolved above, before its keys
            bool hard_disk = false;
            for (const Medium &medium : core.media) hard_disk = hard_disk || medium.type == P6_MEDIA_HDF;
            const p6_libretro::ModelResolution resolved = p6_libretro::resolve_model(core.settings, hard_disk, names);
            for (const std::string &note : resolved.notes) log(RETRO_LOG_INFO, "%s", note.c_str());
            for (const std::string &warning : resolved.warnings) notify_warning("%s", warning.c_str());
            core.settings = resolved.settings;
        }
        std::vector<p6_media> manifest;
        for (const Medium &medium : core.media) {
            p6_media entry = {};
            entry.type = medium.type;
            memcpy(entry.content_id, medium.id, sizeof medium.id);
            entry.image = p6_buffer{medium.bytes.data(), medium.bytes.size()};
            manifest.push_back(entry);
        }
        p6_config config = {};
        config.abi_version = P6_ABI_VERSION;
        config.struct_size = sizeof config;
        p6_options::to_config(core.settings, &config);       // machine, standard, audio rate, seed and clock epoch: the schema's values, none of them typed here
        const p6_libretro::FirmwareNames *firmware = p6_libretro::firmware_for(config.machine);
        if (!firmware) {            // the schema only produces the machines the table has: a mismatch is a build defect, not a configuration to run
            notify_error("the machine %u has no Kickstart names in this core", unsigned(config.machine));
            stop_image();
            return false;
        }
        log(RETRO_LOG_INFO, "machine %s (puae6_machine)", firmware->model);
        p6_error error = make_error();
        const p6_result result = p6_init(&config, &error);
        if (result != P6_OK) {
            report("p6_init", result, error);
            stop_image();
            return false;
        }
        core.initialized = true;
        if (!load_firmware(manifest, *firmware, p6_libretro::kickstart_choice(core.settings.kickstart))) {
            stop_image();
            return false;
        }
        core.loaded = true;
        announce_state_support();
        refresh_memory_map();
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
    if (!guard.ok) return;
    save_floppy_changes();
    save_hard_file_changes();
    stop_image();
}

RETRO_API unsigned retro_get_region(void) { return core.settings.video_standard == P6_VIDEO_NTSC ? RETRO_REGION_NTSC : RETRO_REGION_PAL; }
// RETRO_MEMORY_SYSTEM_RAM is the Chip RAM (as in PUAE5), straight from the engine's buffer; the answer is what the frontend was told in the memory map, so it is the same
// pointer from the load to the unload and no engine call is made (a frontend may ask from a thread that is not the one that runs the core).
RETRO_API void *retro_get_memory_data(unsigned id)
{
    if (id != RETRO_MEMORY_SYSTEM_RAM || !core.loaded || core.faulted || !core.memory_announced) return nullptr;
    return const_cast<void *>(core.memory_map.system_ram);
}

RETRO_API size_t retro_get_memory_size(unsigned id)
{
    if (id != RETRO_MEMORY_SYSTEM_RAM || !core.loaded || core.faulted || !core.memory_announced) return 0;
    return core.memory_map.system_ram_bytes;
}

}  // extern "C"
