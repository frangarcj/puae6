#include "sysdeps.h"
#include "machdep/m68k.h"
#include "machdep/m68k.h" // The portable entry point must be safe to repeat.

flag_struct regflags{};
#if defined(CPU_x86_64)
static_assert(sizeof(flag_struct) == 16 && sizeof(regflags.cznv) == 8);
#else
static_assert(sizeof(flag_struct) == 8 && sizeof(regflags.cznv) == 4);
#endif
static_assert(FLAGBIT_C == 0 && FLAGBIT_N == 7 && FLAGBIT_Z == 6 && FLAGBIT_V == 11);

// State index is NZVC (C bit0, V bit1, Z bit2, N bit3).
// Each mask independently encodes the 16 outcomes of one 68000 condition.
static const unsigned truth[16] = {
 0xffff, 0x0000, 0x0505, 0xfafa, 0x5555, 0xaaaa, 0x0f0f, 0xf0f0,
 0x3333, 0xcccc, 0x00ff, 0xff00, 0xcc33, 0x33cc, 0x0c03, 0xf3fc
};
using flag_word = decltype(regflags.cznv);
static const flag_word cznv_mask = FLAGVAL_C | FLAGVAL_Z | FLAGVAL_N | FLAGVAL_V;

static void set_flag(unsigned flag, int value)
{
 switch (flag) {
 case 0: SET_CFLG(value); break;
 case 1: SET_VFLG(value); break;
 case 2: SET_ZFLG(value); break;
 case 3: SET_NFLG(value); break;
 case 4: SET_XFLG(value); break;
 default: assert(false);
 }
}

int main()
{
 const unsigned positions[] = {FLAGBIT_C, FLAGBIT_V, FLAGBIT_Z, FLAGBIT_N};
 for (flag_word noise : {flag_word(0), ~cznv_mask}) {
  for (unsigned state = 0; state < 32; ++state) {
   regflags.cznv = noise;
   for (unsigned f = 0; f < 5; ++f) set_flag(f, (state >> f) & 1);
   flag_word packed = noise;
   for (unsigned f = 0; f < 4; ++f) packed |= flag_word((state >> f) & 1) << positions[f];
   assert(GET_CZNV() == packed);
   assert(GET_CFLG() == (state & 1));
   assert(GET_VFLG() == ((state >> 1) & 1));
   assert(GET_ZFLG() == ((state >> 2) & 1));
   assert(GET_NFLG() == ((state >> 3) & 1));
   assert(GET_XFLG() == ((state >> 4) & 1));
   for (int cc = 0; cc < 16; ++cc)
    assert(cctrue(cc) == int((truth[cc] >> (state & 15)) & 1));

   for (unsigned f = 0; f < 5; ++f) {
    for (int value : {0, 1, 2, 3, 255, -1}) {
     SET_CZNV(packed); SET_XFLG(state >> 4);
     set_flag(f, value);
     if (f == 4) {
      assert(GET_CZNV() == packed && GET_XFLG() == (value & 1));
     } else {
      flag_word mask = flag_word(1) << positions[f];
      assert(GET_CZNV() == ((packed & ~mask) | (flag_word(value & 1) << positions[f])));
      assert(GET_XFLG() == ((state >> 4) & 1));
     }
    }
   }
   SET_CZNV(packed); SET_XFLG((state >> 4) & 1);
   COPY_CARRY();
   assert(GET_XFLG() == GET_CFLG() && GET_CZNV() == packed);
   // COPY_CARRY copies the packed word; only GET_XFLG interprets bit0.
   assert(regflags.x == (packed >> (FLAGBIT_C - FLAGBIT_X)));
   CLEAR_CZNV();
   assert(GET_CZNV() == 0 && GET_XFLG() == (state & 1));
   IOR_CZNV(packed);
   assert(GET_CZNV() == packed);
  }
 }
 int evaluations = 0;
 SET_NFLG(++evaluations);
 assert(evaluations == 1 && GET_NFLG() == 1);
 return 0;
}
