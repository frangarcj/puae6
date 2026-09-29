// Include the complete materialized unit to exercise its internal data paths.
// Selective linking is only for this harness, not an engine linkage proof.
#include <blitter.cpp>
#ifdef DEBUGGER
#error "Probe requires debugger disabled"
#endif
regstruct regs{};
static uae_u16 ram[128];
struct access { unsigned address, value; };
static access writes[8];
static unsigned write_count;
static uae_u32 read_word(uaecptr address)
{
 assert(!(address & 1) && address < sizeof ram);
 return ram[address / 2];
}
static void write_word(uaecptr address, uae_u32 value)
{
 assert(!(address & 1) && address < sizeof ram && value <= 0xffff);
 assert(write_count < 8);
 writes[write_count++] = {address, value}; ram[address / 2] = value;
}
uae_u32 (*chipmem_wget_indirect)(uaecptr) = read_word;
void (*chipmem_wput_indirect)(uaecptr, uae_u32) = write_word;
static unsigned shifted(unsigned current, unsigned previous, int shift, bool desc)
{
 unsigned result = 0;
 for (int bit = 0; bit < 16; ++bit) {
  int from = desc ? bit - shift : bit + shift;
  unsigned value = current;
  if (from < 0) { from += 16; value = previous; }
  if (from >= 16) { from -= 16; value = previous; }
  result |= ((value >> from) & 1) << bit;
 }
 return result;
}
static unsigned truth(unsigned mt, unsigned a, unsigned b, unsigned c)
{
 unsigned result = 0;
 for (unsigned bit = 0; bit < 16; ++bit) {
  unsigned index = (((a >> bit) & 1) << 2) | (((b >> bit) & 1) << 1) | ((c >> bit) & 1);
  result |= ((mt >> index) & 1) << bit;
 }
 return result;
}
static void transfers()
{
 build_blitfilltable();
 for (unsigned mt = 0; mt < 256; ++mt) {
  for (bool desc : {false, true}) {
   for (int shift : {0, 1, 7, 15}) {
    for (unsigned i = 0; i < 128; ++i) ram[i] = (i * 977) ^ 0xa55a;
    uae_u16 before[128]; memcpy(before, ram, sizeof ram);
    blt_info = {}; blt_info.blitzero = 1; blt_info.blit_main = 1;
    blt_info.bltcon0 = (shift << 12) | 0x0f00 | mt;
    blt_info.bltcon1 = ((15 - shift) << 12) | (desc ? 2 : 0);
    blt_info.bltafwm = 0x0ff0; blt_info.bltalwm = 0xf00f;
    blt_info.hblitsize = blt_info.vblitsize = 2;
    blt_info.bltamod = blt_info.bltbmod = blt_info.bltcmod = blt_info.bltdmod = 2;
    blt_info.bltapt = 48; blt_info.bltbpt = 80; blt_info.bltcpt = 112; blt_info.bltdpt = 144;
    write_count = 0; blitfill = blit_dof = log_blitter = 0;
    unsigned expected[4], addresses[4], previous_a = 0, previous_b = 0, nonzero = 0;
    for (unsigned i = 0; i < 4; ++i) {
     int offset = (i / 2) * 6 + (i % 2) * 2;
     if (desc) offset = -offset;
     unsigned a = before[(48 + offset) / 2] & (i % 2 ? 0xf00f : 0x0ff0);
     unsigned b = before[(80 + offset) / 2], c = before[(112 + offset) / 2];
     expected[i] = truth(mt, shifted(a, previous_a, shift, desc),
                        shifted(b, previous_b, 15 - shift, desc), c);
     nonzero |= expected[i]; previous_a = a; previous_b = b;
     addresses[i] = 144 + offset;
    }
    if (desc) blitter_dofast_desc(); else blitter_dofast();
    assert(write_count == 4 && !blt_info.blit_main);
    assert(blt_info.blitzero == !nonzero);
    for (unsigned i = 0; i < 4; ++i) {
     assert(writes[i].address == addresses[i] && writes[i].value == expected[i]);
     before[addresses[i] / 2] = expected[i];
    }
    assert(!memcmp(before, ram, sizeof ram));
    int advance = desc ? -12 : 12;
    assert(blt_info.bltapt == 48 + advance && blt_info.bltbpt == 80 + advance);
    assert(blt_info.bltcpt == 112 + advance && blt_info.bltdpt == 144 + advance);
    assert(blit_masktable[0] == 0xffff && blit_masktable[1] == 0xffff);
   }
  }
 }
}
static void destination()
{
 write_count = 0; log_blitter = blit_dof = 0; regs.chipset_latch_rw = 0xaaaa;
 blit_chipmem_agnus_wput(16, 0x1234, MW_MASK_BLITTER_D_N);
 assert(write_count == 1 && ram[8] == 0x1234 && regs.chipset_latch_rw == 0x1234);
 blit_dof = 1;
 blit_chipmem_agnus_wput(18, 0x5678, MW_MASK_BLITTER_D_L);
 assert(write_count == 2 && ram[9] == 0x1234 && regs.chipset_latch_rw == 0x1234);
 blit_dof = 0;
 blt_info.bltcon0 = BLTCHD; blt_info.bltdpt = 20; blt_info.bltddat = 0x5678;
 blitter_line_write(); assert(write_count == 2); // Line mode requires C, not D.
 blt_info.bltcon0 = BLTCHC;
 blitter_line_write(); assert(write_count == 3 && ram[10] == 0x5678);
 assert(regs.chipset_latch_rw == 0x5678);
}
int main() { transfers(); destination(); }
