// Exercise upstream A500 status-register and hardware-error boundaries.
#include <newcpu.cpp>
#include <cassert>

uae_prefs currprefs{}, changed_prefs{};
// The front door of the FPU (T54, FPUEMU) that newcpu.cpp calls; these probes run a 68000 (fpu_model 0), which never reaches it, and fpp.cpp is not part of them.
void fpu_reset(void) {}
void fpu_modechange(void) {}
uae_u32 fpp_get_fpsr(void) { return 0; }
FPP_PRINT fpp_print;
evt_t currcycle;   // read by the 68020 cycle helpers newcpu.cpp now compiles (T24a); never executed here

// Observed boundaries for reset only: synthetic vectors/ROM, not a machine.
uaecptr rtarea_base;
static uae_u8 pc_storage[8];
static unsigned vector_reads, prefetch_reads, config_updates, fakekick_calls;
uae_u32 memory_get_long(uaecptr addr)
{
	assert(addr == (vector_reads == 0 ? 4u : 0u));
	assert(vector_reads++ < 2);
	return addr == 4 ? 0x100 : 0x80000;
}
uae_u8 *memory_get_real_address(uaecptr addr)
{
	assert(addr == 0x100);
	return pc_storage;
}
static uae_u32 reset_fetch_word(uaecptr addr)
{
	assert(addr == 0x100 + 2 * prefetch_reads);
	assert(prefetch_reads++ < 2);
	return addr == 0x100 ? 0x4e71 : 0xa55a;
}
void set_config_changed(int flags) { assert(flags == 0); ++config_updates; }
void a3000_fakekick(int mode) { assert(mode == 0); ++fakekick_calls; }
uaecptr cpuboard_get_reset_pc(uaecptr *) { assert(false); return 0; }
uaecptr need_uae_boot_rom(uae_prefs *) { assert(false); return 0; }

static void a500_setup()
{
	currprefs.cpu_model = 68000;
	currprefs.cpu_compatible = true;
	currprefs.cpu_cycle_exact = true;
	currprefs.cachesize = 0;
	currprefs.mmu_model = 0;
	regs.spcflags = 0;
	pissoff = 0;
}

static void status_roundtrip()
{
	a500_setup();
	for (unsigned ccr = 0; ccr < 32; ++ccr) {
		SET_XFLG((ccr >> 4) & 1); SET_NFLG((ccr >> 3) & 1);
		SET_ZFLG((ccr >> 2) & 1); SET_VFLG((ccr >> 1) & 1);
		SET_CFLG(ccr & 1);
		regs.t1 = 1; regs.t0 = 0; regs.s = 1; regs.m = 1; regs.intmask = 5;
		MakeSR();
		assert((regs.sr & 0x1f) == ccr);
		assert((regs.sr & 0xefff) == ((1u << 15) | (1u << 13) | (5u << 8) | ccr));
		regs.sr = 0x2500 | ccr;
		SET_XFLG(0); SET_NFLG(0); SET_ZFLG(0); SET_VFLG(0); SET_CFLG(0);
		MakeFromSR();
		MakeSR();
		assert(regs.sr == (0x2500 | ccr));
	}
}

static void supervisor_switch_and_flags()
{
	a500_setup();
	regs.s = 1; regs.m = 1; regs.t0 = 1; regs.t1 = 0;
	m68k_areg(regs, 7) = 0x1000; regs.usp = 0x2000; regs.isp = 0x3000;
	regs.intmask = 5; regs.ipl_pin = 5; regs.sr = 0x5000 | 0x001b;
	MakeFromSR();
	assert(regs.s == 0 && regs.m == 0 && regs.t0 == 0);
	assert(regs.isp == 0x1000 && regs.usp == 0x2000 && m68k_areg(regs, 7) == 0x2000);
	assert(regs.intmask == 0 && (regs.spcflags & SPCFLAG_INT));
	assert(GET_XFLG() == 1 && GET_NFLG() == 1 && GET_ZFLG() == 0);
	assert(GET_VFLG() == 1 && GET_CFLG() == 1);

	regs.sr = 0x2000; regs.usp = 0x4000; regs.isp = 0x5000;
	m68k_areg(regs, 7) = 0x4000; regs.intmask = 0;
	MakeFromSR();
	assert(regs.s == 1 && m68k_areg(regs, 7) == 0x5000 && regs.usp == 0x4000);
	assert(regs.isp == 0x5000);
	assert(regs.m == 0 && regs.t0 == 0);
}

static void accurate_stop_and_trace()
{
	a500_setup();
	// The included upstream TU makes its private exact-IPL mode available here.
	m68k_accurate_ipl = true;
	regs.s = 1; regs.m = regs.t0 = regs.t1 = 0; regs.intmask = 5;
	regs.ipl[0] = 4; regs.sr = 0x2000 | (3 << 8);
	MakeFromSR_STOP();
	assert(regs.intmask == 3 && regs.ipl[0] == 0);
	assert(!(regs.spcflags & SPCFLAG_INT));

	regs.t1 = 1; regs.stopped = 0; regs.sr = 0x2000;
	regs.spcflags = 0;
	MakeFromSR();
	assert(regs.t1 == 0 && (regs.spcflags & SPCFLAG_DOTRACE));
	m68k_accurate_ipl = false;
}

static void compatible_bus_error_latch()
{
	a500_setup();
	hardware_bus_error = 0;
	hardware_exception2(0x1234, 0xdeadbeef, true, true, sz_word);
	assert(hardware_bus_error == 1);
}

static void reset_vectors_and_prefetch()
{
	for (bool hard : {false, true}) {
		a500_setup();
		regs.halted = 1; regs.stopped = 1;
		regs.t0 = regs.t1 = regs.m = 1;
		regs.intmask = 0; regs.ipl[0] = regs.ipl[1] = regs.ipl_pin = 7;
		gui_data.cpu_halted = gui_data.cpu_stopped = 1;
		uae_interrupts2[0] = uae_interrupts6[0] = uae_interrupt = 1;
		fake_tc_030 = fake_tt0_030 = fake_tt1_030 = 0x87654321;
		fake_srp_030 = fake_crp_030 = 0x123456789ULL;
		fake_mmusr_030 = 0xffff;
		x_get_word = reset_fetch_word;
		vector_reads = prefetch_reads = config_updates = fakekick_calls = 0;
		m68k_reset2(hard);
		assert(vector_reads == 2 && prefetch_reads == 2 && config_updates == 1 && fakekick_calls == 1);
		assert(m68k_getpc() == 0x100 && m68k_areg(regs, 7) == 0x80000);
		assert(regs.ir == 0x4e71 && regs.irc == 0xa55a && regs.read_buffer == 0xa55a);
		assert(!regs.halted && !regs.stopped && !regs.m && !regs.t0 && !regs.t1 && regs.s == 1);
		MakeSR(); assert(regs.sr == 0x2700);
		assert(!regs.ipl[0] && !regs.ipl[1] && !regs.ipl_pin);
		assert(!uae_interrupts2[0] && !uae_interrupts6[0] && !uae_interrupt);
		assert(!gui_data.cpu_halted && !gui_data.cpu_stopped);
		assert(fake_tc_030 == (hard ? 0u : 0x07654321u));
		assert(fake_tt0_030 == fake_tc_030 && fake_tt1_030 == fake_tc_030);
		assert(fake_srp_030 == (hard ? 0 : 0x123456789ULL) && fake_crp_030 == fake_srp_030);
		assert(fake_mmusr_030 == 0 && mmufixup[0].reg == -1 && mmufixup[1].reg == -1);
	}
}

int main()
{
	status_roundtrip();
	supervisor_switch_and_flags();
	accurate_stop_and_trace();
	compatible_bus_error_latch();
	reset_vectors_and_prefetch();
}
