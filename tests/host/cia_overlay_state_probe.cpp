// Real CIA + state visitor. The mapper is an observer, not a memory-bank model.
#include <cia.cpp>
#include "state.h"
#include "p6_state_access.h"
#include <vector>
uae_prefs currprefs{};
static const uae_prefs empty_prefs{};
ev eventtab[ev_max]{};
ev2 eventtab2[ev2_max]{};
regstruct regs{};
int pissoff, vpos, slow_cpu_access, key_swap_hack, pause_emulation;
uae_u32 hsync_counter, vsync_counter;
int maxvpos = 312, maxvpos_nom = 312, maxhpos = 227;
float vblank_hz = 50;
uae_u8 agnus_hpos;
static std::vector<int> maps;
void map_overlay(int bank) { assert(bank == 0 || bank == 1); maps.push_back(bank); }
// These boundaries are outside the probe; none acknowledges a device operation.
void safe_interrupt_set(int, int, bool) { abort(); }
void handle_cd32_joystick_cia(uae_u8, uae_u8) { abort(); }
void dongle_cia_write(int, int, uae_u8, uae_u8) { abort(); }
void akiko_mute(int) { abort(); }
int do_cycles_cck(int) { abort(); }
void event_doint_delay_do_ext(uae_u32) { abort(); }
void inputdevice_do_kb_reset() { abort(); }
static void setup(bool cd32)
{
    currprefs = empty_prefs; currprefs.cs_ciaoverlay = !cd32;
    currprefs.cs_compatible = cd32 ? CP_CD32 : CP_A500;
    currprefs.cs_cd32cd = currprefs.cs_cd32c2p = currprefs.cs_cd32nvram = cd32;
    currprefs.cs_cd32nvram_size = cd32 ? 1024 : 0;
    memset(cia, 0, sizeof cia); memset(eventtab, 0, sizeof eventtab); memset(eventtab2, 0, sizeof eventtab2);
    clear_events(); set_cycles(0); internaleclockphase = 0; cia_cycle_accurate = false;
    kbstate = kblostsynccnt = resetwarning_phase = resetwarning_timer = 0;
    led = 0; led_cycle = 0; led_cycles_on = led_cycles_off = 0; led_old_brightness = -1;
    gui_data.powerled = gui_data.powerled_brightness = 0;
    cia[0].pra = cia[0].dra = 1; oldovl = true; maps.clear();
}
static std::vector<uint8_t> save()
{
    uint8_t bytes[256]; auto a = p6_state::Archive::saver(bytes, sizeof bytes);
    p6_state::visit_cia(a); assert(a.ok()); return {bytes, bytes + a.position()};
}
static bool load(const std::vector<uint8_t> &bytes, bool apply)
{
    const auto cycles = get_cycles(); const auto primary = eventtab[ev_cia];
    ev2 secondary[ev2_max]; memcpy(secondary, eventtab2, sizeof secondary);
    auto a = apply ? p6_state::Archive::applier(bytes.data(), bytes.size()) : p6_state::Archive::validator(bytes.data(), bytes.size());
    p6_state::visit_cia(a); a.finish();
    assert(get_cycles() == cycles && memcmp(&primary, &eventtab[ev_cia], sizeof primary) == 0);
    assert(memcmp(secondary, eventtab2, sizeof secondary) == 0);
    if (apply && a.ok()) p6_state::rebuild_cia();
    return a.ok();
}
int main()
{
    setup(true); const auto on = save(); assert(on.size() == 144 && on.back() == 1);
    auto measure = p6_state::Archive::measurer(); p6_state::visit_cia(measure); assert(measure.position() == 144);
    uae_u32 flags = 0; WriteCIAA(4, 0x2a, &flags); // Timer latch, not PRA/DDRA.
    assert(!oldovl && cia[0].pra == 1 && cia[0].dra == 1 && maps == std::vector<int>{1});
    WriteCIAA(4, 0x2b, &flags); assert(maps.size() == 1); // One-shot until reset/restore.
    const auto off = save(); assert(off.size() == 144 && off.back() == 0);
    assert(load(on, false) && save() == off && maps.size() == 1); // Validate is pure.
    auto bad = on; bad.back() = 2;
    assert(!load(bad, false) && !load(bad, true) && save() == off && maps.size() == 1);
    bad = on; bad.pop_back(); assert(!load(bad, false) && save() == off);
    assert(load(on, true) && oldovl && maps.back() == 0 && save() == on);
    assert(load(off, true) && !oldovl && maps.back() == 1 && save() == off);
    assert(cia[0].pra == 1 && cia[0].dra == 1); // Same pins, both latch values.
    const char *why = nullptr;
    currprefs.cs_compatible = CP_GENERIC; assert(!p6_cia_boundary(&why));
    const auto mapped = maps.size();
    assert(!load(on, false) && !load(on, true) && !oldovl && maps.size() == mapped);
    currprefs.cs_compatible = CP_CD32; currprefs.cs_cd32cd = false; assert(!p6_cia_boundary(&why));
    currprefs.cs_cd32cd = true; currprefs.keyboard_mode = 1; assert(!p6_cia_boundary(&why));
    currprefs.keyboard_mode = 0; resetwarning_phase = 1; assert(!p6_cia_boundary(&why));
    setup(false); const auto legacy = save();
    assert(legacy.size() == 143 && std::equal(legacy.begin(), legacy.end(), on.begin()));
    measure = p6_state::Archive::measurer(); p6_state::visit_cia(measure); assert(measure.position() == 143);
    oldovl = false; assert(!p6_cia_boundary(&why)); // Existing pin/latch check remains.
    assert(load(legacy, true) && oldovl && maps == std::vector<int>{0} && save() == legacy);
}
