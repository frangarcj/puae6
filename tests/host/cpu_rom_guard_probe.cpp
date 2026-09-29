// Executes the extracted production m68k_go() around its ROM-status guards.
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
int m68k_pc_indirect;
addrbank dummy_bank{};
TCHAR savestate_fname[MAX_DPATH]{};
void (*x_do_cycles)(int);

static int rom_status;
static int reset_calls, run_calls, reset2_calls, protect_calls, barrier_calls;
static int prelude_calls;
static int reset_prelude_calls;
static bool reset_hard, reset_keyboard, protect_value, barrier_value;

static void cpu_halt_clear();
static void cpu_do_fallback();
static void m68k_reset_restore();
static void prefs_changed_cpu();
static void build_cpufunctbl();
static void set_x_funcs();
static void warpmode_reset();
static bool haltloop();

extern "C" int p6_memory_rom_status(void) { return rom_status; }
uae_atomic atomic_and(volatile uae_atomic *, uae_u32) { std::abort(); }
void reset_frame_rate_hack() { ++prelude_calls; }
void update_68k_cycles() { ++prelude_calls; }
bool set_cpu_tracer(bool) { ++prelude_calls; return false; }
void init_custom() { std::abort(); }
int inprec_open(const TCHAR *, const TCHAR *) { std::abort(); }
void memory_clear() { std::abort(); }
void fill_prefetch() { std::abort(); }
void custom_prepare() { std::abort(); }
void inprec_startup() { std::abort(); }
void savestate_init() { std::abort(); }
uae_u32 uaerandgetseed() { std::abort(); }
uae_u32 uaesetrandseed(uae_u32) { std::abort(); }
bool savestate_check() { std::abort(); }
void custom_cpuchange() { std::abort(); }
void target_cpu_speed() { std::abort(); }
void uaerandomizeseed() { std::abort(); }
uae_u32 uaerand() { std::abort(); }
addrbank *mem_banks[MEMORY_BANKS]{};  // get_mem_bank() reads it; a null bank fails loudly if reached
void custom_end_drawing() { std::abort(); }
int memory_valid_address(uaecptr, uae_u32) { std::abort(); }
uae_u8 *memory_get_real_address(uaecptr) { std::abort(); }
void check_prefs_changed_audio() { std::abort(); }
void cpu_halt(int) { std::abort(); }
void fixup_cpu(uae_prefs *) { std::abort(); }
void custom_reset(bool hard, bool keyboard)
{ ++reset_calls; reset_hard = hard; reset_keyboard = keyboard; rom_status = P6_ROM_READ_ERROR; }
void m68k_reset_restore() { ++reset_prelude_calls; }
void prefs_changed_cpu() { ++reset_prelude_calls; }
void build_cpufunctbl() { ++reset_prelude_calls; }
void set_x_funcs() { ++reset_prelude_calls; }
void warpmode_reset() { ++reset_prelude_calls; }
bool haltloop() { std::abort(); }
void m68k_reset2(bool) { ++reset2_calls; std::abort(); }
void m68k_run() { ++run_calls; std::abort(); }
void protect_roms(bool enabled) { ++protect_calls; protect_value = enabled; }
void mman_set_barriers(bool enabled) { ++barrier_calls; barrier_value = enabled; }
void write_log(const TCHAR *, ...) { std::abort(); }
static void cpu_halt_clear() { std::abort(); }
static void cpu_do_fallback() { std::abort(); }

#include "cpu_rom_guard_under_test.h"

static void every_non_ok_status_returns_before_prelude()
{
    for (int status : {P6_ROM_UNLOADED, P6_ROM_OPEN_ERROR, P6_ROM_READ_ERROR,
                       P6_ROM_UNSUPPORTED, P6_ROM_NO_MEMORY}) {
        rom_status = status;
        prelude_calls = reset_calls = 0;
        in_m68k_go = 0;
        m68k_go(1);
        assert(prelude_calls == 0 && reset_calls == 0 && in_m68k_go == 0);
    }
}

static void reset_error_stops_before_cpu_reset_and_cleans_up()
{
    rom_status = P6_ROM_LOAD_OK;
    quit_program = UAE_RESET;
    input_play = input_record = 0;
    regs = {};
    reset_calls = run_calls = reset2_calls = protect_calls = barrier_calls = 0;
    reset_prelude_calls = 0;
    prelude_calls = 0;
    reset_hard = reset_keyboard = false;
    protect_value = barrier_value = false;
    in_m68k_go = 0;
    m68k_go(1);
    assert(prelude_calls == 3 && reset_prelude_calls == 4 && reset_calls == 1);
    assert(reset_hard && !reset_keyboard);
    assert(reset2_calls == 0 && run_calls == 0);
    // Host ROM page protection and memory barriers only serve the JIT; patch 0050 removed
    // both calls, so the failed-reset cleanup must not reach the host at all.
    assert(protect_calls == 0 && barrier_calls == 0 && in_m68k_go == 0);
}

int main()
{
    every_non_ok_status_returns_before_prelude();
    reset_error_stops_before_cpu_reset_and_cleans_up();
}
