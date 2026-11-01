"""The native chipset bodies compile without enabling desktop SAVESTATE."""
from pathlib import Path
import platform
import subprocess
import tempfile
import unittest

from tests.host import ensure_generated

ROOT = Path(__file__).resolve().parents[2]


class StateBodyTests(unittest.TestCase):
    def test_custom_body_matches_the_selected_engine_layout(self):
        ensure_generated()
        tree = ROOT / "build/upstream"
        with tempfile.TemporaryDirectory(prefix="p6-state-body-") as directory:
            for optimization in ("-O0", "-O2"):
                subprocess.run([
                    "clang++", "-std=c++17", "-UNDEBUG", "-DP6_STATE_BODIES", optimization,
                    "-I" + str(ROOT / "ports/portable"),
                    "-I" + str(ROOT / "build/generated"),
                    "-I" + str(tree / "include"), "-I" + str(tree),
                    "-c", str(tree / "custom.cpp"),
                    "-o", str(Path(directory) / ("custom-" + optimization[2:] + ".o")),
                ], check=True, timeout=120)

    def test_beta21_beam_and_copper_roundtrip(self):
        ensure_generated()
        tree = ROOT / "build/upstream"
        source = r'''
#include "custom.cpp"
#include <cassert>
#include <cstring>
uae_prefs currprefs{}, changed_prefs{};
regstruct regs{};
amigadisplay adisplays[MAX_AMIGADISPLAYS]{};
int config_changed, config_changed_flags, interlace_seen, pissoff;
int savestate_state;
evt_t currcycle_cck;
int max_uae_width = 2048, max_uae_height = 2048;
bool aga_mode, ecs_agnus, ecs_denise, ecs_denise_only;
bltinfo blt_info{};
static int publishes, scheduled, draw_erase, draw_center, draw_locked;
static uint32_t video_status;
uaecptr *p6_audio_pt(int channel) { static uaecptr p[4]; return &p[channel]; }
uaecptr *p6_disk_pt() { static uaecptr p; return &p; }
bool target_graphics_buffer_update(int monid, bool) { assert(monid == 0); ++publishes; return true; }
void event2_newevent_xx(int, evt_t, uae_u32, evfunc2) { ++scheduled; }
extern "C" uint32_t p6_video_status() { return video_status; }
void p6_draw_flags_get(int *erase, int *center, int *locked) { *erase = draw_erase; *center = draw_center; *locked = draw_locked; }
void p6_draw_flags_set(int erase, int center, int locked) { draw_erase = erase; draw_center = center; draw_locked = locked; }
int main() {
    int32_t before[P6_BEAM_WORDS]{}, after[P6_BEAM_WORDS]{};
    maxhpos_short = 227; rga_slot_first_offset = 3;
    rga_pipe[3].bltadd = -2; rga_pipe[0].bltadd = 0;
    rga_pipe[1].bltadd = 2; rga_pipe[2].bltadd = -2;
    rga_pipe[3].conflict2 = p6_audio_pt(2);
    current_linear_vpos = 287; paula_dmal_shifter = 0x12345678;
    last_vsync_evt = evt_t(0x1020304050607080ULL);
    assert(p6_custom_beam_get(before, P6_BEAM_WORDS));
    current_linear_vpos = 0; paula_dmal_shifter = 0; last_vsync_evt = 0;
    rga_slot_first_offset = 0;
    for (auto &slot : rga_pipe) { slot.bltadd = 0; slot.conflict2 = nullptr; }
    assert(p6_custom_beam_set(before, P6_BEAM_WORDS));
    assert(p6_custom_beam_get(after, P6_BEAM_WORDS));
    assert(!std::memcmp(before, after, sizeof before));
    assert(current_linear_vpos == 287 && paula_dmal_shifter == 0x12345678);
    assert(last_vsync_evt == evt_t(0x1020304050607080ULL));
    assert(rga_pipe[0].bltadd == -2 && rga_pipe[2].bltadd == 2);
    assert(rga_pipe[0].conflict2 == p6_audio_pt(2));
    pfunc_active_count = 1;
    assert(!p6_custom_beam_get(after, P6_BEAM_WORDS));
    pfunc_active_count = 0;
    int32_t copper_before[P6_COPPER_WORDS]{}, copper_after[P6_COPPER_WORDS]{};
    cop_state.vcmp = 123; cop_state.hcmp = 42; cop_state.vblankip = 0x2468;
    cop_state.inst = 2; cop_state.start = 1; cop_state.prelatch = 0xa55a;
    copper_dma_change_cycle = evt_t(0x1122334455667788ULL);
    copper_dma_change_cycle_pending = true;
    assert(p6_custom_copper_get(copper_before, P6_COPPER_WORDS));
    std::memset(&cop_state, 0, sizeof cop_state);
    copper_dma_change_cycle = 0; copper_dma_change_cycle_pending = false;
    assert(p6_custom_copper_set(copper_before, P6_COPPER_WORDS));
    assert(p6_custom_copper_get(copper_after, P6_COPPER_WORDS));
    assert(!std::memcmp(copper_before, copper_after, sizeof copper_before));
    auto &vi = adisplays[0].gfxvidinfo; vi.inbuffer = &vi.drawbuffer;
    auto &vb = vi.drawbuffer;
    vb.inwidth = 720; vb.inheight = 574; vb.outwidth = 704; vb.outheight = 568;
    vb.width_allocated = 768; vb.height_allocated = 600;
    static uint32_t pixels[8]; vb.bufmem = reinterpret_cast<uae_u8 *>(pixels); vb.pixbytes = 4; vb.rowbytes = 768 * 4;
    vb.extrawidth = 4; vb.extraheight = 6; vb.xoffset = -2; vb.yoffset = 3; vb.inxoffset = 5; vb.inyoffset = -7;
    current_linear_hpos = 227; current_linear_vpos = 312; current_linear_hpos_short = 227; current_linear_vpos_nom = 312;
    current_linear_hpos_temp = 228; current_linear_vpos_temp = 313; current_linear_temp_change = 2;
    video_status = 2; display_redraw = true; draw_erase = 1; draw_center = 1; draw_locked = 1;
    int32_t geometry_before[P6_FRAME_GEOMETRY_WORDS]{}, geometry_after[P6_FRAME_GEOMETRY_WORDS]{};
    assert(p6_frame_geometry_get(geometry_before, P6_FRAME_GEOMETRY_WORDS));
    int raster_width = 0, raster_height = 0, raster_stride = 0; uint8_t *raster_first = nullptr;
    assert(p6_frame_raster(&raster_width, &raster_height, &raster_stride, &raster_first));
    assert(raster_width == 768 && raster_height == 600 && raster_stride == 768 * 4 && raster_first == vb.bufmem);
    display_redraw = false; draw_erase = draw_center = draw_locked = 0;
    assert(p6_frame_geometry_set(geometry_before, P6_FRAME_GEOMETRY_WORDS));
    assert(p6_frame_geometry_get(geometry_after, P6_FRAME_GEOMETRY_WORDS));
    assert(!std::memcmp(geometry_before, geometry_after, sizeof geometry_before));
    assert(display_redraw && draw_erase == 1 && draw_center == 1 && draw_locked == 1);
    p6_custom_ce020_extra_set(CYCLE_UNIT - 1);
    assert(p6_custom_ce020_extra_get() == CYCLE_UNIT - 1);
    int32_t ring_before[P6_DENISE_RING_CURSORS]{17, 29, -3, 41, 53}, ring_after[P6_DENISE_RING_CURSORS]{};
    assert(p6_denise_ring_cursors_set(ring_before, P6_DENISE_RING_CURSORS));
    assert(p6_denise_ring_cursors_get(ring_after, P6_DENISE_RING_CURSORS));
    assert(!std::memcmp(ring_before, ring_after, sizeof ring_before));
    ring_before[0] = DENISE_RGA_SLOT_TOTAL;
    assert(!p6_denise_ring_cursors_set(ring_before, P6_DENISE_RING_CURSORS));
    fmode = 3; fmode_inuse = -1; p6_custom_rebuild_derived();
    assert(fmode_inuse == fmode && scheduled == 0 && publishes == 2);
}
'''
        common = ["clang++", "-std=c++17", "-UNDEBUG", "-DP6_STATE_BODIES",
                  "-ffunction-sections", "-fdata-sections",
                  "-I" + str(ROOT / "ports/portable"),
                  "-I" + str(ROOT / "build/generated"),
                  "-I" + str(tree / "include"), "-I" + str(tree)]
        strip = "-Wl,-dead_strip" if platform.system() == "Darwin" else "-Wl,--gc-sections"
        with tempfile.TemporaryDirectory(prefix="p6-state-roundtrip-") as directory:
            directory = Path(directory)
            probe = directory / "probe.cpp"
            probe.write_text(source)
            for optimization in ("-O0", "-O2"):
                executable = directory / ("probe-" + optimization[2:])
                subprocess.run([*common, optimization, strip, str(probe), "-o", str(executable)],
                               check=True, timeout=120)
                subprocess.run([str(executable)], check=True, timeout=30)

    def test_blitter_phase_roundtrip(self):
        ensure_generated()
        tree = ROOT / "build/upstream"
        source = r'''
#include "blitter.cpp"
#include <cassert>
#include <cstring>
int main() {
    int32_t before[P6_BLITTER_PHASE_WORDS]{}, after[P6_BLITTER_PHASE_WORDS]{};
    blt_info.blit_queued = 3; blt_info.blit_count_done = 7;
    blt_info.blt_change_cycles = evt_t(0x1020304050607080ULL);
    blitter_delayed_update = true; blit_dmacount = 5; blit_cyclecount = 11;
    blt_info.bltahold2 = 0x1234; blt_info.bltdpt_prev = 0x2468ace0;
    assert(p6_blitter_phase_get(before, P6_BLITTER_PHASE_WORDS));
    blt_info = {}; blitter_delayed_update = false; blit_dmacount = blit_cyclecount = 0;
    assert(p6_blitter_phase_set(before, P6_BLITTER_PHASE_WORDS));
    assert(p6_blitter_phase_get(after, P6_BLITTER_PHASE_WORDS));
    assert(!std::memcmp(before, after, sizeof before));
}
'''
        common = ["clang++", "-std=c++17", "-UNDEBUG", "-DP6_STATE_BODIES",
                  "-ffunction-sections", "-fdata-sections",
                  "-I" + str(ROOT / "ports/portable"),
                  "-I" + str(ROOT / "build/generated"),
                  "-I" + str(tree / "include"), "-I" + str(tree)]
        strip = "-Wl,-dead_strip" if platform.system() == "Darwin" else "-Wl,--gc-sections"
        with tempfile.TemporaryDirectory(prefix="p6-blitter-phase-") as directory:
            directory = Path(directory)
            probe = directory / "probe.cpp"
            probe.write_text(source)
            for optimization in ("-O0", "-O2"):
                executable = directory / ("probe-" + optimization[2:])
                subprocess.run([*common, optimization, strip, str(probe), "-o", str(executable)],
                               check=True, timeout=120)
                subprocess.run([str(executable)], check=True, timeout=30)

    def test_68020_instruction_cache_roundtrip(self):
        ensure_generated()
        tree = ROOT / "build/upstream"
        source = r'''
#include "newcpu.cpp"
#include <cassert>
#include <cstring>
int main() {
    uae_u32 data[CACHELINES020]{}, tags[CACHELINES020]{}, got_data[CACHELINES020]{}, got_tags[CACHELINES020]{};
    uae_u8 valid[CACHELINES020]{}, got_valid[CACHELINES020]{};
    for (unsigned i = 0; i < CACHELINES020; ++i) {
        data[i] = 0x10203040u + i; tags[i] = 0xf8000000u + 4 * i; valid[i] = uae_u8(i % 3);
    }
    assert(p6_caches020_set(data, tags, valid, CACHELINES020));
    assert(p6_caches020_get(got_data, got_tags, got_valid, CACHELINES020));
    assert(!std::memcmp(data, got_data, sizeof data));
    assert(!std::memcmp(tags, got_tags, sizeof tags));
    for (unsigned i = 0; i < CACHELINES020; ++i) assert(got_valid[i] == (valid[i] != 0));
    assert(!p6_caches020_get(got_data, got_tags, got_valid, CACHELINES020 - 1));
    assert(!p6_caches020_set(data, tags, valid, CACHELINES020 + 1));
}
'''
        common = ["clang++", "-std=c++17", "-UNDEBUG", "-DP6_STATE_BODIES",
                  "-ffunction-sections", "-fdata-sections",
                  "-I" + str(ROOT / "ports/portable"), "-I" + str(tree),
                  "-I" + str(ROOT / "build/generated"), "-I" + str(tree / "include")]
        strip = "-Wl,-dead_strip" if platform.system() == "Darwin" else "-Wl,--gc-sections"
        with tempfile.TemporaryDirectory(prefix="p6-cache020-") as directory:
            directory = Path(directory)
            probe = directory / "cache020-probe.cpp"
            probe.write_text(source)
            for optimization in ("-O0", "-O2"):
                executable = directory / ("probe-" + optimization[2:])
                subprocess.run([*common, optimization, strip, str(probe), "-o", str(executable)],
                               check=True, timeout=180)
                subprocess.run([str(executable)], check=True, timeout=30)

    def test_beta21_audio_disk_and_denise_roundtrips(self):
        ensure_generated()
        tree = ROOT / "build/upstream"
        sources = {
            "audio": r'''
#include "audio.cpp"
#include <cassert>
#include <cstring>
uae_prefs currprefs{}, changed_prefs{}; regstruct regs{}; evt_t currcycle;
int main() {
    int32_t before[P6_AUDIO_PHASE_WORDS]{}, after[P6_AUDIO_PHASE_WORDS]{};
    currcycle = 0x200000; last_cycles = currcycle - 1234;
    next_sample_evtime = 17.25f; audio_work_to_do = 9; led_filter_on = true;
    sound_filter_state[2].rc4 = 3.5f; audio_channel[1].dmaenstore = true;
    audio_channel[1].ptx = 0x2468ace0; audio_channel[1].data.sample_accum = -123456;
    assert(p6_audio_phase_get(before, P6_AUDIO_PHASE_WORDS));
    next_sample_evtime = 0; audio_work_to_do = 0; led_filter_on = false;
    sound_filter_state[2].rc4 = 0; audio_channel[1] = {};
    assert(p6_audio_phase_set(before, P6_AUDIO_PHASE_WORDS));
    assert(p6_audio_phase_get(after, P6_AUDIO_PHASE_WORDS));
    assert(!std::memcmp(before, after, sizeof before));
    assert(last_cycles == currcycle - 1234);
    currprefs.sound_interpol = 2;
    assert(!p6_audio_phase_get(after, P6_AUDIO_PHASE_WORDS));
}
''',
            "disk": r'''
#include "disk.cpp"
#include <cassert>
#include <cstring>
uae_prefs currprefs{}, changed_prefs{}; regstruct regs{}; evt_t currcycle;
int main() {
    int32_t before[P6_DISK_PHASE_WORDS]{}, after[P6_DISK_PHASE_WORDS]{};
    currcycle = 0x200000; disk_hpos = 12345; dsklen2 = 0x8123; dsksync_on = true; indexdecay = 7;
    DISK_select_set(0x5b);
    floppy[2].dskchange_time = 91; floppy[2].motordelay = true;
    floppy[2].steplimit = 1; floppy[2].steplimitcycle = currcycle - 222;
    floppy[2].floppybitcounter = 17; floppy[2].tracklen = 100000;
    floppy[2].trackspeed = 256; floppy[2].skipoffset = 9; floppy[2].indexoffset = 11;
    assert(p6_disk_phase_get(before, P6_DISK_PHASE_WORDS));
    disk_hpos = dsklen2 = indexdecay = 0; dsksync_on = false; floppy[2] = {}; DISK_select_set(0xff);
    assert(p6_disk_phase_set(before, P6_DISK_PHASE_WORDS));
    assert(p6_disk_phase_get(after, P6_DISK_PHASE_WORDS));
    assert(!std::memcmp(before, after, sizeof before));
    assert(floppy[2].steplimitcycle == currcycle - 222);
    assert(prev_data == 0x5b && selected == 0x0b && side == 1 && direction == 1 && prev_step == 1);
    assert(p6_disk_newname_set(2, "disk.adf"));
    assert(!std::strcmp(floppy[2].newname, "disk.adf"));
}
''',
            "denise": r'''
#include "drawing.cpp"
#include <cassert>
#include <cstring>
int main() {
    int32_t before[P6_DENISE_PIPE_WORDS]{}, after[P6_DENISE_PIPE_WORDS]{};
    for (int h = 0; h < 2; ++h) for (int i = 0; i < 4; ++i) {
        dtbuf[h][i] = 0x10203040u + h * 16 + i; dtgbuf[h][i] = uae_u8(h * 16 + i);
    }
    assert(p6_denise_pipe_get(before, P6_DENISE_PIPE_WORDS));
    std::memset(dtbuf, 0, sizeof dtbuf); std::memset(dtgbuf, 0, sizeof dtgbuf);
    assert(p6_denise_pipe_set(before, P6_DENISE_PIPE_WORDS));
    assert(p6_denise_pipe_get(after, P6_DENISE_PIPE_WORDS));
    assert(!std::memcmp(before, after, sizeof before));
}
''',
        }
        common = ["clang++", "-std=c++17", "-UNDEBUG", "-DP6_STATE_BODIES",
                  "-ffunction-sections", "-fdata-sections",
                  "-I" + str(ROOT / "ports/portable"), "-I" + str(tree),
                  "-I" + str(ROOT / "build/generated"), "-I" + str(tree / "include")]
        strip = "-Wl,-dead_strip" if platform.system() == "Darwin" else "-Wl,--gc-sections"
        with tempfile.TemporaryDirectory(prefix="p6-device-phase-") as directory:
            directory = Path(directory)
            for name, source in sources.items():
                probe = directory / (name + "-probe.cpp")
                probe.write_text(source)
                for optimization in ("-O0", "-O2"):
                    executable = directory / (name + "-" + optimization[2:])
                    subprocess.run([*common, optimization, strip, str(probe), "-o", str(executable)],
                                   check=True, timeout=180)
                    subprocess.run([str(executable)], check=True, timeout=30)


if __name__ == "__main__":
    unittest.main()
