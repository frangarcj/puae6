// Exercise the real device lifecycle registry and A500 call ordering.
#include <devices.cpp>
#include <cassert>

volatile uae_atomic uae_int_requested = 0;
struct uae_prefs currprefs;       // T49: virtualdevice_init allocates the Zorro II Fast RAM board only while one is configured

enum : int {
	CONFIG, CHECK_AUDIO, CHECK_CUSTOM, CHECK_CPU,
	VS_AUDIO, VS_CIA, VS_INPUT, VS_PRE, VS_POST, HS_DISK, HS_AUDIO, HS_BLIT,
	HS_CB, RE_CIA, RE_CB, RE_UAE, RESET_EVENTTAB, RESET_MEMORY, RESET_DISK,
	RESET_CIA, RESET_A1000, RESET_DONGLE, RESET_CB, IMM_CB, EARLY, LATE,
	INPUT_CLOSE, DISK_FREE, DUMP_COUNTS, STATE_FREE, MEMORY_FREE, DRAW_FREE,
	GRAPHICS_LEAVE, SOUND_CLOSE, SOUND_UPDATE, EXPANSION_INIT, EXPANSION_CLEANUP
};
static int log_events[128], log_size;
static int last_hardreset = -1, blit_hpos = 0x7fffffff;
static float sound_clock = -1;

static void note(int e) { assert(log_size < 128); log_events[log_size++] = e; }
static void clear_log() { log_size = 0; }
static void callback_config() { note(CONFIG); }
static void callback_pre() { note(VS_PRE); }
static void callback_post() { note(VS_POST); }
static void callback_hsync() { note(HS_CB); }
static void callback_rethink() { note(RE_CB); }
static void callback_reset(int hard) { assert(hard == last_hardreset); note(RESET_CB); }
static void callback_imm(int hard) { assert(hard == last_hardreset); note(IMM_CB); }
static void callback_early() { note(EARLY); }
static void callback_late() { note(LATE); }

void check_prefs_changed_audio() { note(CHECK_AUDIO); }
void check_prefs_changed_custom() { note(CHECK_CUSTOM); }
void check_prefs_changed_cpu() { note(CHECK_CPU); }
void audio_vsync() { note(VS_AUDIO); }
void CIA_vsync_prehandler() { note(VS_CIA); }
void inputdevice_vsync() { note(VS_INPUT); }
void DISK_hsync() { note(HS_DISK); }
void audio_hsync() { note(HS_AUDIO); }
void decide_blitter(int hpos) { blit_hpos = hpos; note(HS_BLIT); }
void rethink_cias() { note(RE_CIA); }
void rethink_uae_int() { note(RE_UAE); }
void init_eventtab() { note(RESET_EVENTTAB); }
void memory_reset() { note(RESET_MEMORY); }
void DISK_reset() { note(RESET_DISK); }
void CIA_reset(int hard) { last_hardreset = hard; note(RESET_CIA); }
void a1000_reset() { note(RESET_A1000); }
void dongle_reset() { note(RESET_DONGLE); }
void update_sound(float clk) { sound_clock = clk; note(SOUND_UPDATE); }
void inputdevice_close() { note(INPUT_CLOSE); }
void DISK_free() { note(DISK_FREE); }
void dump_counts() { note(DUMP_COUNTS); }
void savestate_free() { note(STATE_FREE); }
void memory_cleanup() { note(MEMORY_FREE); }
void expansion_init() { note(EXPANSION_INIT); }
void expansion_cleanup() { note(EXPANSION_CLEANUP); }
void drawing_free() { note(DRAW_FREE); }
void graphics_leave() { note(GRAPHICS_LEAVE); }
void close_sound() { note(SOUND_CLOSE); }

static void expect(const int *expected, int n)
{
	assert(log_size == n);
	for (int i = 0; i < n; ++i) assert(log_events[i] == expected[i]);
}

int main()
{
	virtualdevice_init();
	last_hardreset = 1;
	device_add_check_config(callback_config); device_add_check_config(callback_config);
	device_add_vsync_pre(callback_pre); device_add_vsync_pre(callback_pre);
	device_add_vsync_post(callback_post); device_add_hsync(callback_hsync);
	device_add_rethink(callback_rethink); device_add_reset(callback_reset);
	device_add_reset(callback_reset); device_add_reset_imm(callback_imm);
	device_add_exit(callback_late, callback_early);
	assert(log_size == 1 && log_events[0] == IMM_CB);

	clear_log(); device_check_config();
	const int config_expected[] = { CONFIG, CHECK_AUDIO, CHECK_CUSTOM, CHECK_CPU };
	expect(config_expected, 4);
	clear_log(); devices_vsync_pre();
	const int pre_expected[] = { VS_AUDIO, VS_CIA, VS_INPUT, VS_PRE };
	expect(pre_expected, 4);
	clear_log(); devices_vsync_post(); assert(log_size == 1 && log_events[0] == VS_POST);
	clear_log(); devices_hsync();
	const int hs_expected[] = { HS_DISK, HS_AUDIO, HS_BLIT, HS_CB };
	expect(hs_expected, 4); assert(blit_hpos == -1);
	clear_log(); devices_rethink();
	const int rethink_expected[] = { RE_CIA, RE_CB, RE_UAE };
	expect(rethink_expected, 3);
	clear_log(); devices_update_sound(123.5f, 0.25f);
	assert(log_size == 1 && log_events[0] == SOUND_UPDATE && sound_clock == 123.5f);

	clear_log(); devices_reset_ext(1);
	assert(log_size == 1 && log_events[0] == RESET_CB);
	clear_log(); devices_reset_ext(1); assert(log_size == 0);
	clear_log(); last_hardreset = 1; uae_int_requested = 0x1234;
	devices_reset(1);
	const int reset_expected[] = { RESET_EVENTTAB, RESET_MEMORY, RESET_DISK, RESET_CIA,
		RESET_A1000, RESET_DONGLE };
	expect(reset_expected, 6); assert(last_hardreset == 1 && uae_int_requested == 0);
	clear_log(); devices_reset_ext(1);
	assert(log_size == 2 && log_events[0] == RESET_CB && log_events[1] == IMM_CB);
	clear_log(); devices_reset_ext(1); assert(log_size == 0);
	clear_log(); last_hardreset = 0; devices_reset(0);
	const int soft_reset_expected[] = { RESET_EVENTTAB, RESET_MEMORY, RESET_DISK,
		RESET_CIA, RESET_A1000, RESET_DONGLE };
	expect(soft_reset_expected, 6);
	devices_reset_ext(0);
	assert(log_size == 8 && log_events[6] == RESET_CB && log_events[7] == IMM_CB);

	clear_log(); do_leave_program();
	assert(log_events[0] == EARLY);
	const int teardown_expected[] = { EARLY, INPUT_CLOSE, DISK_FREE, DUMP_COUNTS, EXPANSION_CLEANUP,
		STATE_FREE, MEMORY_FREE, DRAW_FREE, LATE, GRAPHICS_LEAVE, SOUND_CLOSE };
	expect(teardown_expected, 11);

	clear_log(); virtualdevice_init();
	devices_vsync_pre(); devices_vsync_post(); devices_hsync(); devices_rethink();
	devices_reset_ext(1);
	const int reinit_expected[] = { VS_AUDIO, VS_CIA, VS_INPUT, HS_DISK,
		HS_AUDIO, HS_BLIT, RE_CIA, RE_UAE };
	expect(reinit_expected, 8); // Re-init clears all previously registered callbacks.

	// The Fast RAM board is allocated with the machine, and only for a machine that has one.
	clear_log(); virtualdevice_init(); assert(log_size == 0);
	currprefs.fastmem[0].size = 0x100000;
	clear_log(); virtualdevice_init(); assert(log_size == 1 && log_events[0] == EXPANSION_INIT);
}
