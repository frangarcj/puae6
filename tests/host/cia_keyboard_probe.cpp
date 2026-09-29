// Real CIA mode-0 keyboard path, key queue, and event scheduler.
#include <cia.cpp>
#include <stdlib.h>

uae_prefs currprefs{};
ev eventtab[ev_max]{};
ev2 eventtab2[ev2_max]{};
int pissoff;
int vpos, key_swap_hack, pause_emulation;
int slow_cpu_access;
regstruct regs{};
uae_u32 hsync_counter, vsync_counter;
int maxvpos = 312, maxhpos = 227;
int maxvpos_nom = 312;
float vblank_hz = 50;
uae_u8 agnus_hpos;
static unsigned irq_count;

void safe_interrupt_set(int source, int id, bool level6)
{
	assert(source == IRQ_SOURCE_CIA && id == 0 && !level6);
	++irq_count;
}
void map_overlay(int) { abort(); }
void akiko_mute(int) { abort(); }
void handle_cd32_joystick_cia(uae_u8, uae_u8) { abort(); }
void dongle_cia_write(int, int, uae_u8, uae_u8) { abort(); }
int do_cycles_cck(int) { abort(); }
void event_doint_delay_do_ext(uae_u32) { abort(); }
void inputdevice_do_kb_reset() { abort(); }

static const uae_prefs empty_prefs{};

static void reset_events()
{
	currcycle = currcycle_cck = 0;
	nextevent = EVT_MAX;
	pissoff = 0;
	clear_events();
	eventtab[ev_cia].handler = CIA_handler;
	eventtab[ev_misc].handler = MISC_handler;
	eventtab[ev_cia].oldcycles = get_cycles();
}

static void setup()
{
	currprefs = empty_prefs;
	currprefs.keyboard_mode = KB_UAE;
	currprefs.cpu_compatible = true;
	currprefs.cpu_memory_cycle_exact = true;
	currprefs.m68k_speed = 0;
	currprefs.cpuboard_type = 0;
	currprefs.cs_kbhandshake = 0;
	currprefs.cs_ciaoverlay = true;
	maxvpos = 312;
	hsync_counter = 0;
	agnus_hpos = 0;
	memset(cia, 0, sizeof cia);
	memset(eventtab2, 0, sizeof eventtab2);
	cia_cycle_accurate = true;
	internaleclockphase = 0;
	cia_set_eclockphase();
	reset_events();
	irq_count = 0;
}

static void line(unsigned n, uae_u8 expected)
{
	hsync_counter = n;
	CIA_hsync_posthandler(false, false);
	assert(cia[0].sdr == expected);
	assert(kblostsynccnt == 8 * maxvpos * 8);
	// A second eligible scan cannot deliver the next byte before handshake.
	int state = kbstate;
	CIA_hsync_posthandler(false, false);
	assert(cia[0].sdr == expected && kbstate == state);
	uae_u32 flags = 0;
	WriteCIAA(14, CR_INMODE1, &flags);
	WriteCIAA(14, 0, &flags);
	assert(kblostsynccnt == 0);
}

static void startup_and_keys()
{
	setup();
	keyboard_connected(true);
	hsync_counter = 1; // Outside the 16-line software keyboard cadence.
	CIA_hsync_posthandler(false, false);
	assert(kbstate == 0);
	assert(cia[0].sdr == 0 && kblostsynccnt == 0);
	line(16, 0);
	assert(kbstate == 1);
	line(32, 0x04); // ~ROL8(AK_INIT_POWERUP)
	assert(kbstate == 2);
	line(48, 0x02); // ~ROL8(AK_TERM_POWERUP)
	assert(kbstate == 3);
	assert(record_key_direct(0x40, true));
	assert(record_key_direct(0x41, true));
	line(64, 0xbf); // pressed key $40
	line(80, 0xbe); // release bit set on scancode $40
	assert(!keys_available());
}

static void serial_event()
{
	setup();
	cia[0].imask = ICR_SP;
	cia_keyreq(0xa5);
	assert(cia[0].sdr == 0xa5 && (cia[0].icr2 & ICR_SP));
	assert(!(cia[0].icr1 & ICR_SP) && irq_count == 0);
	evt_t deadline = 8 * CYCLE_UNIT;
	bool found = false;
	for (int i = 0; i < ev2_max; ++i) {
		if (eventtab2[i].active && eventtab2[i].handler == event_CIA_synced_interrupt) {
			assert(eventtab2[i].evtime == deadline);
			found = true;
		}
	}
	assert(found);
	do_cycles_normal((int)deadline - 1);
	assert(!(cia[0].icr1 & ICR_SP) && irq_count == 0);
	do_cycles_normal(1);
	assert((cia[0].icr1 & (ICR_SP | 0xe0)) == (ICR_SP | 0xe0));
	assert(irq_count == 1);
	assert(ReadCIAReg(0, 12) == 0xa5);
	assert(ReadCIAReg(0, 13) == (ICR_SP | 0x80));
	assert(ReadCIAReg(0, 13) == 0); // Reading ICR acknowledges the request.
}

int main()
{
	startup_and_keys();
	serial_event();
}
