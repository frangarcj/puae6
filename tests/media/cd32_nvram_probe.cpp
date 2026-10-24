#define main service_probe_main
#include "akiko_service_probe.cpp"
#undef main
#include "devices.cpp" // Real registry and the same leave-callback executor used by virtualdevice_free.
#include "cd32_nvram.h"

static_assert(sizeof(p6_cd32_nvram_state) == 1064, "Private view has explicit padding");
struct Edge { unsigned reg, value, result; };
static unsigned mmio(unsigned reg, unsigned value)
{
    akiko_bput(AKIKO_BASE + reg, value);
    return akiko_bget(AKIKO_BASE + reg);
}
static p6_cd32_nvram_state state()
{
    p6_cd32_nvram_state result{};
    assert(p6_akiko_nvram_get(&result)); return result;
}
static void equal(const p6_cd32_nvram_state &a, const p6_cd32_nvram_state &b)
{
    assert(!memcmp(&a, &b, sizeof a));
}
static void begin()
{
    reset_device_items(); // virtualdevice_init begins with this real registry reset.
    currprefs.cs_cd32nvram = 1; currprefs.cs_cd32nvram_size = 1024; currprefs.cpu_model = 68020;
    assert(akiko_init() && device_resets_cnt == 1 && device_leave_cnt == 1);
}
static void end()
{
    execute_device_items(device_leaves, device_leave_cnt); // Exact registered exit path, no full-machine claim.
    assert(!cd32_nvram && !cd32_eeprom && !cd32_nvram_size);
}
struct Bus {
    unsigned latch = 0xc0;
    std::vector<Edge> edges;
    unsigned put(unsigned reg, unsigned value)
    {
        const auto result = mmio(reg, value);
        edges.push_back({reg, value, result}); return result;
    }
    int set(int line, int high)
    {
        const unsigned mask = line ? 0x80 : 0x40;
        latch = high ? latch | mask : latch & ~mask;
        return (put(0x30, latch) & 0x40) != 0;
    }
    void start() { set(1, 0); set(0, 1); set(1, 1); set(0, 0); }
    void stop() { set(1, 0); set(0, 0); set(1, 1); set(0, 1); }
    int bit(int v) { set(1, 0); set(0, v); return set(1, 1); }
    void write(int value)
    {
        for (int n = 7; n >= 0; --n) bit((value >> n) & 1);
        assert(bit(1) == 0);
    }
    void address(int address)
    {
        start(); write(0xa0 | ((address >> 8) << 1)); write(address & 255);
    }
    int read(bool last)
    {
        int result = 0;
        for (int n = 0; n < 8; ++n) result = (result << 1) | bit(1);
        bit(last); return result;
    }
    void exercise()
    {
        assert(put(0x32, 0xdf) == 0xdf); // Preserve DDR low bits, which real MMIO returns.
        address(0x3ff); write(0xa5); write(0x5a); stop();
        address(0); write(0x9d); stop();
        address(0x3ff); start(); write(0xa7);
        assert(read(false) == 0xa5 && read(true) == 0x9d); stop();
        set(1, 0); set(0, 0);
        put(0x32, 0x1f); // A DDR write alone does not recompute the latched lines.
        put(0x32, 0xdf); set(0, 1); set(1, 1);
    }
};
int main()
{
    begin();
    const auto initial = state();
    for (auto byte : initial.chip.memory) assert(byte == 0);
    Bus bus; bus.exercise();
    const auto golden = state();
    assert(golden.chip.memory[0x3ff] == 0xa5 && golden.chip.memory[0x3f0] == 0x5a && golden.chip.memory[0] == 0x9d);
    // Reset keeps acknowledged bytes, but matches a new chip's complete protocol.
    bus.address(5); bus.bit(1);
    const auto partial = state(); assert(partial.chip.state && partial.chip.buffer);
    akiko_reset(0);
    auto reset = state(), expected = initial;
    memcpy(expected.chip.memory, golden.chip.memory, 1024); equal(reset, expected);
    akiko_reset(1); equal(state(), expected);
    // Init/restore CD teardown is not the session's NVRAM lifetime.
    akiko_free(); equal(state(), expected); assert(akiko_init()); equal(state(), expected);
    bool bit = false, ack = false, ddr = false;
    for (size_t cut = 0; cut <= bus.edges.size(); ++cut) {
        assert(p6_akiko_nvram_set(&initial));
        for (size_t n = 0; n < cut; ++n) {
            const auto &e = bus.edges[n]; assert(mmio(e.reg, e.value) == e.result);
        }
        const auto saved = state();
        bit |= saved.chip.state > 1; ack |= saved.chip.device_out == 0;
        ddr |= saved.direction == 0x1f && saved.scl == 0 && saved.sda == 0;
        end(); begin(); assert(p6_akiko_nvram_set(&saved)); equal(state(), saved);
        for (size_t n = cut; n < bus.edges.size(); ++n) {
            const auto &e = bus.edges[n]; assert(mmio(e.reg, e.value) == e.result);
        }
        equal(state(), partial); // Includes the unfinished byte appended after exercise.
    }
    assert(bit && ack && ddr);
    const auto before = state();
    auto reject = [&](p6_cd32_nvram_state bad) {
        bad.chip.memory[1] ^= 0xff;
        assert(!p6_akiko_nvram_set(&bad)); equal(state(), before);
    };
    auto bad = before; bad.reserved = 1; reject(bad);
    bad = before; bad.scl = 2; reject(bad);
    bad = before; bad.sda ^= 1; reject(bad);
    bad = before; bad.chip.state = 20; reject(bad);
    assert(!p6_akiko_nvram_get(nullptr) && !p6_akiko_nvram_set(nullptr));
    end(); end(); assert(!p6_akiko_nvram_get(&bad) && !p6_akiko_nvram_set(&before));
    begin(); equal(state(), initial); end(); // A different power-on inherits no old bytes/protocol.
}
