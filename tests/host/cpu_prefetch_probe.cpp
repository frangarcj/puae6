// Exercise the upstream 68000 prefetch path and shared T0 helper.
#include <newcpu.cpp>
#include "diagnostics.h"
#include <cassert>
#include <string>

uae_prefs currprefs{};
// The front door of the FPU (T54, FPUEMU) that newcpu.cpp calls; these probes run a 68000 (fpu_model 0), which never reaches it, and fpp.cpp is not part of them.
void fpu_reset(void) {}
void fpu_modechange(void) {}
uae_u32 fpp_get_fpsr(void) { return 0; }
FPP_PRINT fpp_print;
evt_t currcycle;   // read by the 68020 cycle helpers newcpu.cpp now compiles (T24a); never executed here
static uaecptr addresses[4];
static unsigned reads;

static uae_u32 fetch_word(uaecptr addr)
{
 assert(reads < 4);
 addresses[reads++] = addr;
 if (addr == 0x100) return 0x4e71;
 if (addr == 0x102) return 0xa55a;
 assert(false);
 return 0;
}

static void reset_reads() { reads = 0; }

static void a500_prefetch()
{
 uae_u8 pc_storage[8]{};
 currprefs.cpu_model = 68000; currprefs.cpu_compatible = true;
 currprefs.cachesize = 0;
 regs.pc = 0x100; regs.pc_p = regs.pc_oldp = pc_storage;
 regs.ir = regs.ird = regs.irc = regs.read_buffer = 0;
 regs.pipeline_pos = 17; regs.pipeline_stop = 9;
 regs.pipeline_r8[0] = 3; regs.pipeline_r8[1] = 5;
 x_get_word = fetch_word; reset_reads();
 fill_prefetch();
 assert(reads == 2 && addresses[0] == 0x100 && addresses[1] == 0x102);
 assert(regs.ir == 0x4e71 && regs.ird == 0x4e71);
 assert(regs.irc == 0xa55a && regs.read_buffer == 0xa55a);
 assert(regs.pipeline_pos == 0 && regs.pipeline_stop == 0);
 assert(regs.pipeline_r8[0] == -1 && regs.pipeline_r8[1] == -1);

 regs.ir = 1; regs.ird = 2; regs.irc = 3; regs.read_buffer = 4;
 regs.pipeline_pos = 7; regs.pipeline_stop = 8;
 reset_reads(); currprefs.cachesize = 1;
 fill_prefetch();
 assert(reads == 0 && regs.ir == 1 && regs.ird == 2 && regs.irc == 3);
 assert(regs.read_buffer == 4 && regs.pipeline_pos == 7 && regs.pipeline_stop == 8);
 currprefs.cachesize = 0; currprefs.cpu_compatible = false;
 fill_prefetch();
 assert(reads == 0 && regs.ir == 1 && regs.ird == 2 && regs.irc == 3);
 assert(regs.read_buffer == 4 && regs.pipeline_pos == 7 && regs.pipeline_stop == 8);
}

static void common_t0_trace()
{
 currprefs.cpu_model = 68000; regs.t0 = 1; regs.t1 = 0;
 regs.spcflags = SPCFLAG_TRACE | SPCFLAG_INT; pissoff = 23;
 check_t0_trace();
 assert(regs.spcflags == (SPCFLAG_TRACE | SPCFLAG_INT) && pissoff == 23);

 currprefs.cpu_model = 68020; regs.spcflags = SPCFLAG_TRACE | SPCFLAG_INT;
 check_t0_trace();
 assert(regs.spcflags == (SPCFLAG_DOTRACE | SPCFLAG_INT) && pissoff == 0);
}

static void unavailable_disassembly()
{
 uaecptr next = 0x1234; uae_u32 source = 0x5678, dest = 0x9abc;
 p6_diag_reset();
 m68k_disasm_ea(0x100, &next, 1, &source, &dest, 0);
 m68k_disasm(0x100, &next, 0, 1);
 assert(next == 0x1234 && source == 0x5678 && dest == 0x9abc);
 p6_diagnostic messages[2];
 assert(p6_diag_read(messages, 2) == 2);
 for (const auto &message : messages)
  assert(strstr(message.text, "requires DEBUGGER") != nullptr);
}

static void dump_without_debugger()
{
 uae_u8 pc_storage[8]{};
 for (bool compatible : {false, true}) {
  for (bool next_output : {false, true}) {
   currprefs.cpu_model = 68000; currprefs.cpu_compatible = compatible;
   regs.pc = 0x100; regs.pc_p = regs.pc_oldp = pc_storage;
   regs.s = 1; regs.m = regs.t0 = regs.t1 = 0; regs.intmask = 7;
   SET_XFLG(0); SET_NFLG(0); SET_ZFLG(0); SET_VFLG(0); SET_CFLG(0);
   m68k_dreg(regs, 0) = 0x12345678; m68k_areg(regs, 7) = 0x87654320;
   regs.ir = 0x4e71; regs.irc = 0xa55a; regs.chipset_latch_rw = 0xdeadbeef;
   uaecptr next = 0xfeed;
   p6_diag_reset(); reset_reads();
   // Previously decoded an uninitialized word, then looped forever because
   // unavailable m68k_disasm never advanced prevpc towards PC.
   m68k_dumpstate(next_output ? &next : nullptr, 0xfe);
   assert(next == 0xfeed && reads == 0);
   assert(m68k_getpc() == 0x100 && regs.isp == 0x87654320 && regs.sr == 0x2700);
   assert(regs.ir == 0x4e71 && regs.irc == 0xa55a && regs.chipset_latch_rw == 0xdeadbeef);
   p6_diagnostic messages[64];
   const auto count = p6_diag_read(messages, 64);
   assert(count > 0 && count < 64);
   std::string output;
   for (int i = 0; i < count; ++i) output += messages[i].text;
   assert(output.find("D0 12345678") != std::string::npos);
   assert(output.find("A7 87654320") != std::string::npos);
   assert(output.find("SR=2700") != std::string::npos);
   assert((output.find("Chip latch DEADBEEF") != std::string::npos) == compatible);
   assert(output.find("requires DEBUGGER") != std::string::npos);
   assert(output.find("Next PC:") == std::string::npos);
  }
 }
}

int main()
{
 a500_prefetch(); common_t0_trace(); unavailable_disassembly(); dump_without_debugger();
}
