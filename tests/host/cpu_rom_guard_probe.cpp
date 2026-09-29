// Executes the extracted production m68k_go_unit(): ROM-status guards and the unit sequencing.
#include <cassert>
#include <cstdlib>
#include "sysconfig.h"
#include "sysdeps.h"
#include "options.h"
#include "events.h"
#include "uae.h"
#include "memory.h"
#include "newcpu.h"
#include "cpummu.h"
#include "cpummu030.h"
#include <cputbl.h>
#include "cpu_prefetch.h"
#include "autoconf.h"
#include "traps.h"
#include "debug.h"
#include "debugmem.h"
#include "gui.h"
#include "savestate.h"
#include "blitter.h"
#include "ar.h"
#include "inputrecord.h"
#include "inputdevice.h"
#include "audio.h"
#include "fpp.h"
#include "cpuboard.h"
#include "devices.h"
#include "custom.h"
#include "rom_status.h"

uae_prefs currprefs{}, changed_prefs{};
regstruct regs{};
int quit_program, in_m68k_go;
bool cpu_hardreset, cpu_keyboardreset;
int cpu_prefs_changed_flag;
evt_t start_cycles;
int input_play, input_record;
struct cputracestruct cputrace{};
evt_t currcycle;
bool event_wait;
struct ev eventtab[ev_max]{};
uae_u32 hsync_counter, vsync_counter;
int m68k_pc_indirect = 1;
int pissoff;  // cycles_do_special() clears it
addrbank dummy_bank{};
TCHAR savestate_fname[MAX_DPATH]{};
static void fake_cycles(int);
void (*x_do_cycles)(int) = fake_cycles;

static int rom_status;
static int reset_calls, run_calls, reset2_calls, protect_calls, barrier_calls;
static int prelude_calls, memory_clear_calls, prepare_calls, end_drawing_calls, special_calls;
static int reset_prelude_calls, restore_calls, audio_calls;
static bool reset_hard, reset_keyboard, protect_value, barrier_value;
static bool run_raises_unit_end, reset_breaks_rom;
static int run_kind;  // 0: leave at a boundary, 1: end the run with a reset request instead
static addrbank fake_bank{};

static void cpu_halt_clear();
static void cpu_do_fallback();
static void m68k_reset_restore();
static void prefs_changed_cpu();
static void build_cpufunctbl();
static void set_x_funcs();
static void warpmode_reset();
static bool haltloop();
static int do_specialties(int);

static void fake_cycles(int)
{
    // Time passing in the reset preamble crosses a video boundary.
    atomic_or(&regs.spcflags, SPCFLAG_UNIT_END);
}

extern "C" int p6_memory_rom_status(void) { return rom_status; }
uae_atomic atomic_and(volatile uae_atomic *p, uae_u32 v) { return __atomic_fetch_and(p, v, __ATOMIC_SEQ_CST); }
uae_atomic atomic_or(volatile uae_atomic *p, uae_u32 v) { return __atomic_fetch_or(p, v, __ATOMIC_SEQ_CST); }
void reset_frame_rate_hack() { ++prelude_calls; }
void update_68k_cycles() { ++prelude_calls; }
bool set_cpu_tracer(bool) { ++prelude_calls; return false; }
void init_custom() { std::abort(); }
int inprec_open(const TCHAR *, const TCHAR *) { std::abort(); }
void memory_clear() { ++memory_clear_calls; }
void fill_prefetch() { std::abort(); }
void custom_prepare() { ++prepare_calls; }
void inprec_startup() { std::abort(); }
void savestate_init() { std::abort(); }
uae_u32 uaerandgetseed() { return 0; }
uae_u32 uaesetrandseed(uae_u32) { return 0; }
bool savestate_check() { return true; }
void custom_cpuchange() { std::abort(); }
void target_cpu_speed() { std::abort(); }
void uaerandomizeseed() {}
uae_u32 uaerand() { return 0; }
addrbank *mem_banks[MEMORY_BANKS]{};  // get_mem_bank() reads it; a null bank fails loudly if reached
void custom_end_drawing() { ++end_drawing_calls; }
int memory_valid_address(uaecptr, uae_u32) { std::abort(); }
uae_u8 *memory_get_real_address(uaecptr) { std::abort(); }
void check_prefs_changed_audio() { ++audio_calls; }
void cpu_halt(int) { std::abort(); }
void fixup_cpu(uae_prefs *) { std::abort(); }
void custom_reset(bool hard, bool keyboard)
{ ++reset_calls; reset_hard = hard; reset_keyboard = keyboard; if (reset_breaks_rom) rom_status = P6_ROM_READ_ERROR; }
void m68k_reset_restore() { ++restore_calls; ++reset_prelude_calls; }
void prefs_changed_cpu() { ++reset_prelude_calls; }
void build_cpufunctbl() { ++reset_prelude_calls; }
void set_x_funcs() { ++reset_prelude_calls; }
void warpmode_reset() { ++reset_prelude_calls; }
bool haltloop() { std::abort(); }
void m68k_reset2(bool) { ++reset2_calls; }
void m68k_run()
{
    ++run_calls;
    if (run_kind == 1) {
        run_kind = 0;
        quit_program = -UAE_RESET;
        return;
    }
    if (run_raises_unit_end)
        atomic_or(&regs.spcflags, SPCFLAG_UNIT_END);
}
void protect_roms(bool enabled) { ++protect_calls; protect_value = enabled; }
void mman_set_barriers(bool enabled) { ++barrier_calls; barrier_value = enabled; }
void write_log(const TCHAR *, ...) {}
static void cpu_halt_clear() { std::abort(); }
static void cpu_do_fallback() { std::abort(); }
static int wait_result;
static int do_specialties(int)
{
    // Stands for the wait loop of a halted CPU: it ends at the next boundary or when the CPU restarts.
    ++special_calls;
    if (wait_result)
        atomic_or(&regs.spcflags, SPCFLAG_UNIT_END);
    else
        regs.halted = 0;
    return wait_result;
}

#include "cpu_rom_guard_under_test.h"

static void reset_counters()
{
    reset_calls = run_calls = reset2_calls = protect_calls = barrier_calls = 0;
    reset_prelude_calls = prelude_calls = memory_clear_calls = prepare_calls = 0;
    end_drawing_calls = special_calls = restore_calls = audio_calls = 0;
    reset_hard = reset_keyboard = protect_value = barrier_value = false;
}

static void every_non_ok_status_returns_before_prelude()
{
    for (int status : {P6_ROM_UNLOADED, P6_ROM_OPEN_ERROR, P6_ROM_READ_ERROR,
                       P6_ROM_UNSUPPORTED, P6_ROM_NO_MEMORY}) {
        rom_status = status;
        reset_counters();
        in_m68k_go = 0;
        assert(m68k_go_unit(M68K_UNIT_BEGIN) == M68K_UNIT_NO_ROM);
        assert(prelude_calls == 0 && reset_calls == 0 && in_m68k_go == 0);
    }
}

static void reset_error_stops_before_cpu_reset_and_cleans_up()
{
    rom_status = P6_ROM_LOAD_OK;
    quit_program = UAE_RESET;
    input_play = input_record = 0;
    regs = {};
    reset_counters();
    in_m68k_go = 0;
    assert(m68k_go_unit(M68K_UNIT_BEGIN) == M68K_UNIT_DONE && in_m68k_go == 1);
    assert(prelude_calls == 3);
    // The reset makes the ROM unreadable (as a media error would): stop before the CPU reset.
    reset_breaks_rom = true;
    assert(m68k_go_unit(M68K_UNIT_APPLY) == M68K_UNIT_NO_ROM);
    reset_breaks_rom = false;
    assert(reset_prelude_calls == 4 && reset_calls == 1);
    assert(reset_hard && !reset_keyboard);
    assert(reset2_calls == 0 && run_calls == 0);
    // Host ROM page protection and memory barriers only serve the JIT; patch 0050 removed
    // both calls, so the failed-reset cleanup must not reach the host at all.
    assert(protect_calls == 0 && barrier_calls == 0 && in_m68k_go == 0);
}

static void units_resume_without_repeating_the_reset_head()
{
    for (auto &bank : mem_banks)
        bank = &fake_bank;
    currprefs.cpu_compatible = true;
    rom_status = P6_ROM_LOAD_OK;
    regs = {};
    reset_counters();
    in_m68k_go = 0;
    quit_program = UAE_RESET_HARD;
    assert(m68k_go_unit(M68K_UNIT_BEGIN) == M68K_UNIT_DONE);
    // Apply runs the head and nothing else; a boundary crossed by its random delay is not a unit.
    assert(m68k_go_unit(M68K_UNIT_APPLY) == M68K_UNIT_APPLIED);
    assert(reset_calls == 1 && reset2_calls == 1 && memory_clear_calls == 1 && prepare_calls == 1);
    assert(run_calls == 0 && !(regs.spcflags & SPCFLAG_UNIT_END) && reset_hard);

    run_raises_unit_end = true;
    assert(m68k_go_unit(M68K_UNIT_RUN) == M68K_UNIT_DONE);
    assert(run_calls == 1 && !(regs.spcflags & SPCFLAG_UNIT_END));
    // The frame in progress stays open at the boundary: no end-of-drawing, no second reset head.
    assert(end_drawing_calls == 0 && reset_calls == 1 && prepare_calls == 1 && memory_clear_calls == 1);
    for (int unit = 2; unit <= 5; ++unit) {
        assert(m68k_go_unit(M68K_UNIT_APPLY) == M68K_UNIT_APPLIED);
        assert(m68k_go_unit(M68K_UNIT_RUN) == M68K_UNIT_DONE);
        assert(run_calls == unit && end_drawing_calls == 0);
        assert(reset_calls == 1 && reset2_calls == 1 && prepare_calls == 1 && audio_calls == 1);
    }

    // A soft reset requested between units reruns the head once, keeps memory and boots no more.
    quit_program = UAE_RESET;
    assert(m68k_go_unit(M68K_UNIT_APPLY) == M68K_UNIT_APPLIED);
    assert(reset_calls == 2 && reset2_calls == 2 && !reset_hard && !reset_keyboard);
    assert(memory_clear_calls == 1 && prepare_calls == 1 && restore_calls == 1);
    assert(m68k_go_unit(M68K_UNIT_RUN) == M68K_UNIT_DONE && run_calls == 6 && end_drawing_calls == 0);

    // A halted CPU re-enters its wait loop and is not executed; leaving the wait ends the unit.
    regs.halted = 1;
    wait_result = 1;
    assert(m68k_go_unit(M68K_UNIT_RUN) == M68K_UNIT_DONE);
    assert(special_calls == 1 && run_calls == 6 && !(regs.spcflags & SPCFLAG_UNIT_END));
    assert(regs.halted == 1);
    // The CPU restarts (reset released it): the same call falls through to execution.
    wait_result = 0;
    assert(m68k_go_unit(M68K_UNIT_RUN) == M68K_UNIT_DONE);
    assert(special_calls == 2 && run_calls == 7 && regs.halted == 0);
    // Held in reset: same gate, no instruction executed while CPUINRESET stays set.
    regs.spcflags |= SPCFLAG_CPUINRESET;
    wait_result = 1;
    assert(m68k_go_unit(M68K_UNIT_RUN) == M68K_UNIT_DONE);
    assert(special_calls == 3 && run_calls == 7);
    regs.spcflags &= ~SPCFLAG_CPUINRESET;

    // A run that ends for another reason (guest reset request) closes the drawing, resets and
    // still ends the call at the next boundary.
    reset_calls = end_drawing_calls = 0;
    run_kind = 1;
    assert(m68k_go_unit(M68K_UNIT_RUN) == M68K_UNIT_DONE);
    assert(end_drawing_calls == 1 && reset_calls == 1 && quit_program == 0 && run_calls == 9);

    // Leaving clears the marker: a second load must not see a stale request or state.
    atomic_or(&regs.spcflags, SPCFLAG_UNIT_END);
    assert(m68k_go_unit(M68K_UNIT_LEAVE) == M68K_UNIT_DONE);
    assert(in_m68k_go == 0 && !(regs.spcflags & SPCFLAG_UNIT_END));
    assert(m68k_go_unit(M68K_UNIT_RUN) == M68K_UNIT_QUIT);
    assert(m68k_go_unit(M68K_UNIT_BEGIN) == M68K_UNIT_DONE);
    assert(m68k_go_unit(M68K_UNIT_BEGIN) == M68K_UNIT_QUIT); // not reentrant, and no abort()
    assert(m68k_go_unit(M68K_UNIT_LEAVE) == M68K_UNIT_DONE);
}

int main()
{
    every_non_ok_status_returns_before_prelude();
    reset_error_stops_before_cpu_reset_and_cleans_up();
    units_resume_without_repeating_the_reset_head();
}
