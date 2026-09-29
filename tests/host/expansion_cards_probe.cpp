// Real card enumeration and naming; autoconfig hardware is an observed edge.
#include "sysconfig.h"
#include "sysdeps.h"
#include "options.h"
#include "uae.h"
#include "memory.h"
#include "rommgr.h"
#include "autoconf.h"
#include <cassert>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>

uae_prefs currprefs{};
addrbank expamem_null{}, expamem_none{};
uae_u32 expamem_highmem_pointer, expamem_board_size;
uaecptr expamem_board_pointer, expamem_z3_pointer_real, expamem_z3_pointer_uae;
uaecptr expamem_z3_highram_real, expamem_z3_highram_uae;
static struct boardromconfig board_rom{};
static struct romconfig rom_config{};
static struct expansionromtype expansion_rom{};
static struct uae_prefs prefs{};
static unsigned reset_calls, initnum_calls, initrc_calls, romtype_calls;
static uae_u32 observations=2166136261u;
static void observe(uae_u32 value)
{ for (int i=0;i<4;i++) observations=(observations^((value>>(8*i))&255u))*16777619u; }
static bool init_success=true;
static uae_u32 callback_highram;
static uae_u32 callback_start, callback_size;
static struct romconfig *expected_rc;

#include "expansion_cards_under_test.h"

static void reset_ac_data(struct uae_prefs *p)
{ assert(p==&prefs); ++reset_calls; expamem_highmem_pointer=0; }
const struct expansionromtype *get_device_expansion_rom(int type)
{ assert(type==42); ++romtype_calls; return &expansion_rom; }
static void expansion_parse_autoconfig(struct card_data *, const uae_u8 *) { std::abort(); }
static uae_u8 autoconfig_read(const uae_u8 *, int) { std::abort(); }
static int get_order(struct uae_prefs *, struct card_data *) { std::abort(); }
bool expamem_z3hack(struct uae_prefs *) { std::abort(); }
void write_log(const TCHAR *, ...) { std::abort(); }

static bool observed_initnum(struct autoconfig_info *aci)
{ ++initnum_calls; aci->last_high_ram=callback_highram; return init_success; }
static bool observed_initrc(struct autoconfig_info *aci)
{ ++initrc_calls; assert(aci->rc==expected_rc); aci->last_high_ram=callback_highram;
  aci->start=callback_start; aci->size=callback_size; return init_success; }

static void setup(unsigned devnum)
{
    memset(&cards_set[0],0,sizeof cards_set[0]);
    cards[0]=&cards_set[0]; cardno=1;
    cards_set[0].flags=(int)(devnum<<16);
    cards_set[0].zorro=0;
    cards_set[0].initnum=observed_initnum;
    cards_set[0].initrc=nullptr;
    callback_highram=0; init_success=true;
    callback_start=callback_size=0;
    expected_rc=nullptr;
    reset_calls=initnum_calls=initrc_calls=romtype_calls=0;
}

static void parse()
{
    expansion_parse_cards(&prefs,false); assert(reset_calls==1);
    const auto &cd=cards_set[0];
    assert(cd.aci.prefs==&prefs && cd.aci.devnum==((cd.flags>>16)&255));
    for (auto b:cd.aci.autoconfig_raw) assert(b==0xff);
    for (auto b:cd.aci.autoconfig_bytes) assert(b==0xff);
    for (const unsigned char *p=(const unsigned char*)cd.aci.name; *p; p++) observe(*p);
    observe(cd.base); observe(cd.size); observe(cd.aci.start); observe(cd.aci.size);
    observe(cd.aci.devnum); observe(cd.aci.parent_of_previous); observe(expamem_highmem_pointer);
    observe(initnum_calls); observe(initrc_calls); observe(romtype_calls);
}

int main()
{
    expansion_rom.friendlyname=_T("Friendly ROM");
    expansion_rom.romtype=42;
    board_rom.device_type=42;
    rom_config.back=&board_rom;

    setup(0);
    struct cpuboardsubtype cst{}; cst.name=_T("CPU board");
    cards_set[0].cst=&cst; cards_set[0].rc=&rom_config;
    cards_set[0].aci.label=_T("lower priority"); callback_highram=0x12340000;
    parse();
    assert(!_tcscmp(cards_set[0].aci.name,_T("CPU board")));
    assert(cards_set[0].aci.devnum==0 && cards_set[0].aci.parent_of_previous==false);
    assert(expamem_highmem_pointer==callback_highram && initnum_calls==1 && romtype_calls==0);

    setup(1); cards_set[0].rc=&rom_config; expected_rc=&rom_config;
    cards_set[0].initnum=nullptr; cards_set[0].initrc=observed_initrc;
    cards_set[0].aci.addrbank=&expamem_none; cards_set[0].aci.label=_T("ignored label");
    cards_set[0].aci.start=0x12345678; cards_set[0].aci.size=0x4321;
    callback_start=0x12345678; callback_size=0x4321;
    cards_set[0].flags|=CARD_FLAG_CHILD; callback_highram=0x20000000;
    parse();
    assert(!_tcscmp(cards_set[0].aci.name,_T("Friendly ROM [2]")));
    assert(cards_set[0].base==0x12345678 && cards_set[0].size==0x4321);
    assert(cards_set[0].aci.parent_of_previous && expamem_highmem_pointer==callback_highram);
    assert(initrc_calls==1 && romtype_calls==1);

    setup(255); cards_set[0].aci.label=_T("chosen label");
    addrbank labeled_bank{}; labeled_bank.label=_T("bank fallback");
    cards_set[0].aci.addrbank=&labeled_bank;
    parse(); assert(!_tcscmp(cards_set[0].aci.name,_T("chosen label [256]")));

    setup(255); addrbank fallback_bank{}; fallback_bank.label=_T("bank fallback");
    cards_set[0].aci.addrbank=&fallback_bank;
    parse(); assert(!_tcscmp(cards_set[0].aci.name,_T("bank fallback [256]")));

    setup(0); parse(); assert(!_tcscmp(cards_set[0].aci.name,_T("<no name>")));

    const std::string exact(121,'N');
    setup(255); cards_set[0].aci.label=exact.c_str(); parse();
    assert(strlen(cards_set[0].aci.name)==127 &&
           !strcmp(cards_set[0].aci.name,(exact+" [256]").c_str()));

    setup(255); cards_set[0].aci.name[0]='Q'; cards_set[0].aci.name[1]=0;
    callback_highram=0x30000000; init_success=false; parse();
    assert(cards_set[0].aci.name[0]=='Q' && cards_set[0].aci.name[1]==0);
    assert(expamem_highmem_pointer==callback_highram && initnum_calls==1);
    printf("%08x\n",observations);
}
