// Execute pinned CPU helpers against an instrumented bus, not a chipset.
#include "sysconfig.h"
#include "sysdeps.h"
#include "options.h"
#include "memory.h"
#include "newcpu.h"
#include "diagnostics.h"

regstruct regs{};
uae_prefs currprefs{};
struct access { unsigned address, size, value; };
static access writes[32];
static unsigned write_count;
static unsigned char ram[256];
static void put(unsigned address, unsigned value, unsigned size)
{
 assert(!(address & 1) && address + size <= sizeof ram && write_count < 32);
 writes[write_count++] = {address, size, value};
 for (unsigned i = 0; i < size; ++i)
  ram[address + i] = value >> (8 * (size - i - 1));
}
static void word(uaecptr address, uae_u32 value) { put(address, value, 2); }
static void longword(uaecptr address, uae_u32 value) { put(address, value, 4); }
void (*x_put_word)(uaecptr, uae_u32) = word;
void (*x_put_long)(uaecptr, uae_u32) = longword;
static void reset()
{
 regs = {}; regs.sr = 0x2515; m68k_areg(regs, 7) = 128;
 write_count = 0; memset(ram, 0xcc, sizeof ram); p6_diag_reset();
}
static void check(unsigned i, unsigned address, unsigned size, unsigned value)
{
 assert(i < write_count && writes[i].address == address);
 assert(writes[i].size == size && writes[i].value == value);
}
static void multiply_cycles()
{
 currprefs.cpu_model = 68000;
 for (unsigned src = 0; src <= 0xffff; ++src) {
  // Booth recoding: transitions between adjacent multiplier bits and bit -1=0.
  unsigned transitions = (src ^ (src << 1)) & 0xffff;
  assert(getMulu68kCycles(src) == 34 + 2 * __builtin_popcount(src));
  assert(getMuls68kCycles(src) == 34 + 2 * __builtin_popcount(transitions));
 }
 assert(getMulu68kCycles(0xffff) == 66);
 assert(getMuls68kCycles(0xffff) == 36);
 assert(getMuls68kCycles(0x5555) == 66);
}
static void address_error_frame()
{
 for (unsigned mode = 0; mode < 32; ++mode) {
  reset();
  Exception_build_68000_address_error_stack_frame(mode, 0x33c7, 0x12345679, 0xabcdef02);
  assert(m68k_areg(regs, 7) == 114 && write_count == 5);
  check(0, 114, 2, 0x33c0 | mode); check(1, 116, 4, 0x12345679);
  check(2, 120, 2, 0x33c7); check(3, 122, 2, 0x2515);
  check(4, 124, 4, 0xabcdef02);
  const unsigned char expected[] = {
   0x33, static_cast<unsigned char>(0xc0 | mode), 0x12, 0x34, 0x56, 0x79,
   0x33, 0xc7, 0x25, 0x15, 0xab, 0xcd, 0xef, 0x02
  };
  assert(!memcmp(ram + 114, expected, sizeof expected));
  for (unsigned i = 0; i < sizeof ram; ++i)
   if (i < 114 || i >= 128) assert(ram[i] == 0xcc);
  assert(regs.sr == 0x2515);
  p6_diagnostic diag{}; assert(p6_diag_read(&diag, 1) == 0);
 }
}
static void frame_feature_boundary()
{
 for (int format : {3, 7, 9, 10, 15}) {   // 0xB is the 68020 long bus cycle fault frame without MMU state (T24a)
  reset();
  Exception_build_stack_frame(0x11112222, 0x33334444, 0x55556666, 2, format);
  assert(m68k_areg(regs, 7) == 128 && !write_count);
  for (auto byte : ram) assert(byte == 0xcc);
  p6_diagnostic diag{};
  assert(p6_diag_read(&diag, 1) == 1 && diag.kind == P6_DIAG_LOG);
  assert(strstr(diag.text, "Unknown exception stack frame format:") == diag.text);
  assert(p6_diag_read(&diag, 1) == 0);
 }
 // Retained formats do not depend on excluded backends. Format 4 is mixed
 // FPU-unimplemented/68060 bus fault; format 8 is a non-MMU 68010 frame.
 for (int format : {0, 1, 2, 4, 8}) {
  reset(); regs.irc = 0x4e71; regs.mmu_fault_addr = 0xabcdef01;
  regs.read_buffer = 0x1234; regs.write_buffer = 0x5678;
  Exception_build_stack_frame(0x11112222, 0x33334444, 0x55556666, 2, format);
  unsigned length = format == 8 ? 58 : format == 4 ? 16 : format == 2 ? 12 : 8;
  assert(m68k_areg(regs, 7) == 128 - length);
  check(write_count - 3, 126 - (length - 8), 2, (format << 12) | 8);
  check(write_count - 2, 122 - (length - 8), 4, 0x33334444);
  check(write_count - 1, 128 - length, 2, 0x2515);
  p6_diagnostic diag{}; assert(p6_diag_read(&diag, 1) == 0);
 }
 // Format B (68020 address error, CPUEMU_21): 46 words, the MMU fields written as zero; the fields a 68020 reads back sit
 // at their documented offsets from the frame start (M68020UM, long bus cycle fault stack frame).
 reset(); regs.mmu_fault_addr = 0xabcdef01; regs.prefetch020[1] = 0x1111; regs.prefetch020[2] = 0x2222;
 Exception_build_stack_frame(0x11112222, 0x33334444, 0x55556666, 3, 0xb);
 assert(m68k_areg(regs, 7) == 128 - 92);
 {
  const unsigned char *frame = ram + 128 - 92;
  auto w = [&](unsigned o) { return unsigned(frame[o] << 8 | frame[o + 1]); };
  assert(w(0) == 0x2515 && w(2) == 0x3333 && w(4) == 0x4444 && w(6) == (0xb000 | 12));
  assert(w(10) == 0x6666 && w(12) == 0x1111 && w(14) == 0x2222);          // SSW, stage C, stage B
  assert(w(16) == 0xabcd && w(18) == 0xef01);                              // data cycle fault address
  assert(w(44) == 0xabcd && w(46) == 0xef01);                              // data input buffer = fault address
  for (unsigned o = 20; o < 92; ++o) assert((o >= 44 && o < 48) || frame[o] == 0);   // all written (the RAM was 0xCC), the rest zero
  assert(frame[-1] == 0xcc && write_count > 20);
  p6_diagnostic diag{}; assert(p6_diag_read(&diag, 1) == 0);
 }
 // Common dispatcher must retain non-FPU exception routing with no FP state.
 currprefs.cpu_model = 68010;
 for (int nr : {5, 6, 7, 9, 11, 48, 55, 60, 61}) {
  reset(); regs.instruction_pc = 0x12345678;
  Exception_build_stack_frame_common(0, 0xabcdef02, 0, nr, nr);
  assert(m68k_areg(regs, 7) == 120 && write_count == 3);
  check(0, 126, 2, nr * 4);
  check(1, 122, 4, nr == 60 || nr == 61 ? 0x12345678 : 0xabcdef02);
  check(2, 120, 2, 0x2515);
 }
}
int main() { multiply_cycles(); address_error_frame(); frame_feature_boundary(); }
