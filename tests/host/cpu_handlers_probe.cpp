// Real generated A500 handlers; bus/IPL/cycle hooks below are test instruments.
#include "sysdeps.h"
#include "options.h"
#include "memory.h"
#include "newcpu.h"
#include "cputbl.h"
#include <type_traits>
#if defined(FPUEMU) || defined(MMUEMU)
#error "This probe requires the A500 profile without FPU/MMU"
#endif
template<class T, class = void> struct has_fp : std::false_type {};
template<class T> struct has_fp<T, std::void_t<decltype(T::fp)>> : std::true_type {};
static_assert(!has_fp<regstruct>::value && sizeof(fpdata) == sizeof(double));

regstruct regs{};
flag_struct regflags{};
uae_prefs currprefs{};
int hardware_bus_error;
int cpucycleunit = CYCLE_UNIT / 2;
struct entry { char kind; unsigned value; };
static entry trace[16];
static unsigned trace_count;
static unsigned exception_address;
static int exception_kind;
static int exception_pc_adjust;
static unsigned fault_on_read;
static unsigned reads;
static void log_call(char kind, unsigned value)
{
 assert(!hardware_bus_error && !exception_kind);
 assert(trace_count < 16); trace[trace_count++] = {kind, value};
}
static uae_u16 word_at(unsigned address) { return uae_u16(address ^ 0xa55a); }
static uae_u32 fetch(int offset)
{
 unsigned address = m68k_getpci() + offset;
 assert(!(address & 1)); log_call('R', address);
 if (++reads == fault_on_read) hardware_bus_error = 1;
 return word_at(address);
}
static void cycles(int count) { assert(count > 0); log_call('C', count); }
uae_u32 (*x_get_iword)(int) = fetch;
void (*x_do_cycles)(int) = cycles;
void ipl_fetch_next() { log_call('I', 0); }
void exception3_read_prefetch(uae_u32, uaecptr address)
{
 assert(!exception_kind); exception_kind = 3; exception_address = address;
}
void exception2_fetch_opcode(uae_u32, int offset, int pc_adjust)
{
 assert(hardware_bus_error && !exception_kind);
 exception_kind = 2; exception_address = m68k_getpci() + offset;
 exception_pc_adjust = pc_adjust; // Frame PC adjustment, distinct from fault address.
}
static void reset(unsigned flags, int displacement = 6)
{
 regs = {}; regs.pc = 0x100; regs.irc = uae_u16(displacement);
 SET_CFLG(flags); SET_VFLG(flags >> 1); SET_ZFLG(flags >> 2);
 SET_NFLG(flags >> 3); SET_XFLG(flags >> 4);
 trace_count = reads = fault_on_read = 0;
 exception_kind = hardware_bus_error = 0;
}
static void check(unsigned i, char kind, unsigned value)
{
 assert(i < trace_count && trace[i].kind == kind && trace[i].value == value);
}
static const unsigned truth[] = {
 0x0505, 0xfafa, 0x5555, 0xaaaa, 0x0f0f, 0xf0f0, 0x3333,
 0xcccc, 0x00ff, 0xff00, 0xcc33, 0x33cc, 0x0c03, 0xf3fc
};
static cpuop_func_noret *branches[] = {
 op_6200_14_ff, op_6300_14_ff, op_6400_14_ff, op_6500_14_ff,
 op_6600_14_ff, op_6700_14_ff, op_6800_14_ff, op_6900_14_ff,
 op_6a00_14_ff, op_6b00_14_ff, op_6c00_14_ff, op_6d00_14_ff,
 op_6e00_14_ff, op_6f00_14_ff
};
static void branch_cases()
{
 for (unsigned condition = 0; condition < 14; ++condition) {
  for (unsigned flags = 0; flags < 32; ++flags) {
   for (int displacement : {-6, 0, 6}) {
    reset(flags, displacement);
    flag_struct before = regflags;
    bool taken = (truth[condition] >> (flags & 15)) & 1;
    branches[condition](0x6200 + condition * 256);
    unsigned pc = taken ? 0x102 + displacement : 0x104;
    assert(regs.pc == pc && !exception_kind && !hardware_bus_error);
    assert(regflags.cznv == before.cznv && regflags.x == before.x);
    assert(regs.ir == word_at(pc) && regs.irc == word_at(pc + 2));
    check(0, 'C', 2 * cpucycleunit);
    unsigned i = 1;
    if (!taken) check(i++, 'C', 2 * cpucycleunit);
    check(i++, 'R', pc); check(i++, 'I', 0); check(i++, 'R', pc + 2);
    assert(trace_count == i);
   }
  }
 }
 reset(0, 1); op_6600_14_ff(0x6600); // Odd taken destination: no bus access.
 assert(exception_kind == 3 && exception_address == 0x103 && regs.pc == 0x100);
 assert(trace_count == 1 && !reads);
 for (unsigned failed_read : {1u, 2u}) {
  reset(0); fault_on_read = failed_read; op_6600_14_ff(0x6600);
  assert(exception_kind == 2 && reads == failed_read && regs.pc == 0x108);
  assert(exception_address == 0x108 + 2 * (failed_read - 1));
  assert(exception_pc_adjust == (failed_read == 1 ? -6 : 0));
 }
}
static void register_cases()
{
 for (unsigned byte = 0; byte < 256; ++byte) {
  for (unsigned dest = 0; dest < 8; ++dest) {
   reset(15 | ((byte & 1) << 4)); // MOVEQ must clear old C/V as well as set N/Z.
   for (unsigned r = 0; r < 16; ++r) regs.regs[r] = 0x12340000 + r;
   op_7000_14_ff(0x7000 | (dest << 9) | byte);
   for (unsigned r = 0; r < 16; ++r)
    assert(regs.regs[r] == (r == dest ? uae_u32(uae_s32(uae_s8(byte))) : 0x12340000 + r));
   assert(GET_NFLG() == (byte >= 128) && GET_ZFLG() == (byte == 0));
   assert(!GET_CFLG() && !GET_VFLG() && GET_XFLG() == (byte & 1));
   assert(regs.pc == 0x102 && regs.ir == 6 && regs.irc == word_at(0x104));
   assert(trace_count == 2); check(0, 'I', 0); check(1, 'R', 0x104);
  }
 }
 for (unsigned z : {0u, 1u}) {
  reset(z << 2); regs.regs[3] = 0x12345678;
  op_56c0_14_ff(0x56c3);
  assert(regs.regs[3] == (z ? 0x12345600u : 0x123456ffu));
  assert(regs.pc == 0x102 && GET_ZFLG() == z);
  for (unsigned counter : {0u, 1u, 0xffffu}) {
   reset(z << 2); regs.regs[2] = 0xabcd0000 | counter;
   op_56c8_14_ff(0x56ca);
   assert(regs.regs[2] == (0xabcd0000 | (z ? ((counter - 1) & 0xffff) : counter)));
   assert(regs.pc == (z && counter ? 0x108u : 0x104u));
   assert(!exception_kind && GET_ZFLG() == z);
  }
 }
}
int main() { branch_cases(); register_cases(); return 0; }
