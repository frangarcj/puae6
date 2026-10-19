#define main service_probe_main
#include "akiko_service_probe.cpp"
#undef main
#include "events.h"

addrbank extendedkickmem_bank{};
int savestate_state;
// ROM patching and persistent NVRAM are outside this fixture; reset calls the
// real EEPROM/zfile cleanup with no device/file, never these guarded branches.
void protect_roms(bool) { unexercised("protect_roms"); }
bool is_board_enabled(uae_prefs *, int, int) { unexercised("is_board_enabled"); }
void cfgfile_resolve_path_out_load(const TCHAR *, TCHAR *, int, int) { unexercised("cfgfile_resolve_path_out_load"); }

static void line(unsigned clocks = 227)
{
    currcycle += clocks * CYCLE_UNIT;
    AKIKO_hsync_handler();
}
static int first_pulse()
{
    cdrom_seek_delay = 1000;
    for (int n = 1; n <= 1000; ++n) {
        line();
        if (cdrom_seek_delay != 1000) return n;
    }
    abort();
}
struct Trace {
    std::vector<int> data, sub;
    unsigned q = 0, media = 0;
};
static Trace trace(int lines, bool variable = false)
{
    Trace result;
    cdrom_seek_delay = 100000; cdrom_flags = CDFLAG_SUBCODE; cdrom_playing = 1;
    cdrom_intena = CDINTERRUPT_SUBCODE; uae_sem_init(&sub_sem, 0, 1);
    for (int n = 1; n <= lines; ++n) {
        subcodebufferoffset = 0; subcodebufferoffsetw = 1; subcodebufferinuse[0] = 1;
        memset(subcodebuffer, 0xa5, SUB_CHANNEL_SIZE);
        const int before = cdrom_seek_delay, irq_before = irqs;
        line(variable ? 227 + (n & 1) : 227);
        if (cdrom_seek_delay != before) result.data.push_back(n);
        if (irqs != irq_before) result.sub.push_back(n);
        result.q += akiko_clock.q_due; result.media += akiko_clock.media_due;
    }
    return result;
}
static void equal(const Trace &a, const Trace &b)
{
    assert(a.data == b.data && a.sub == b.sub && a.q == b.q && a.media == b.media);
}
int main()
{
    currprefs.cs_cd32cd = 1; currprefs.cpu_model = 68020; currprefs.cd_speed = 1;
    akiko_inited = true; unitnum = -1;
    akiko_reset(1);
    const int clean = first_pulse();
    for (int n = 0; n < 17; ++n) line();
    akiko_reset(1);
    const int restarted = first_pulse();
    assert(clean == 1 && clean == restarted);
    assert(akiko_service_lifecycle(1));
    for (int word : {0x0110, 0, 1, 0}) write_comm_pipe_u32(&requests, word, 1);
    mute_pending = true; akiko_reset(1);
    assert(!request_count() && !mute_pending && akiko_service_lifecycle(0));
    for (int ntsc : {0, 1}) for (int speed : {1, 2}) {
        currprefs.ntscmode = ntsc; currprefs.turbo_emulation = 0; currprefs.cd_speed = 1;
        akiko_reset(1); cdrom_speed = speed;
        const auto start = get_cycles();
        const auto rate = static_cast<uae_u64>(ntsc ? CHIPSET_CLOCK_NTSC : CHIPSET_CLOCK_PAL) * CYCLE_UNIT;
        const int lines = int((2 * rate + 227 * CYCLE_UNIT - 1) / (227 * CYCLE_UNIT)) + 7;
        const auto normal = trace(lines);
        const auto elapsed = static_cast<uae_u64>(get_cycles() - start);
        assert(normal.data.size() == 1 + elapsed * (75 * speed) / rate);
        assert(akiko_clock.sector == elapsed * (75 * speed) % rate);
        assert(normal.q == 1 + elapsed * 2 / rate && normal.media == 1 + elapsed / (2 * rate));
        for (size_t n = 1; n < normal.data.size(); ++n)
            assert(normal.data[n] - normal.sub[n] == 5);
        // Host presentation turbo and the legacy unlimited-read preference
        // never accelerate the hardware sector clock.
        currprefs.turbo_emulation = 1; currprefs.cd_speed = 0;
        akiko_reset(1); cdrom_speed = speed;
        equal(normal, trace(lines));
        const auto saved = akiko_clock;
        const int saved_sub = subcodecounter, saved_seek = cdrom_seek_delay, saved_irqs = irqs;
        for (int n = 0; n < 20; ++n) AKIKO_hsync_handler(); // no cycles elapsed
        assert(cdrom_seek_delay == saved_seek && irqs == saved_irqs && subcodecounter == saved_sub);
        assert(akiko_clock.cycle == saved.cycle && akiko_clock.sector == saved.sector);
        const auto continuation = trace(1000);
        currcycle = saved.cycle; akiko_clock = saved; subcodecounter = saved_sub;
        equal(continuation, trace(1000)); // phase-only fixture, not a native/WB serializer
        akiko_reset(1); cdrom_speed = speed;
        const auto variable_start = get_cycles();
        const auto varied = trace(997, true);
        const auto variable_elapsed = static_cast<uae_u64>(get_cycles() - variable_start);
        assert(varied.data.size() == 1 + variable_elapsed * (75 * speed) / rate);
        assert(akiko_clock.sector == variable_elapsed * (75 * speed) % rate);
        akiko_reset(1); cdrom_speed = 1; trace(17);
        const auto partial = akiko_clock.sector;
        cdrom_speed = 2; line();
        assert(akiko_clock.sector == (partial + 227 * CYCLE_UNIT * 150) % rate);
        assert(akiko_clock.speed == 2);
    }
}
