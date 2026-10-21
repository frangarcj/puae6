// Private local component and complete native CDTV restore/future proof.
#include "p6.h"
#include "sysconfig.h"
#include "sysdeps.h"
#include "options.h"
#include "memory.h"
#include "events.h"
#include "newcpu.h"
#include "../../bridge/config.h"
#include "p6_state_access.h"
#include "cdtv_state.h"
#include "media/cd_device.h"
#include <cassert>
#include <cstdio>
#include <memory>
#include <vector>
#include <string>
#include "../../bridge/state.h"
#include "../../bridge/session.h"
extern bool p6_expansion_state_get(uint32_t *, unsigned, uint8_t *);
static p6_error error{P6_ABI_VERSION, sizeof error};
static unsigned reads;
static uint64_t *future_hash;
static void observe(uint64_t &, const void *, size_t);
static bool image_read(const void *, uint64_t at, void *out, size_t size, size_t *got)
{
    ++reads; for (size_t n = 0; n < size; ++n) static_cast<uint8_t *>(out)[n] = uint8_t((at + n) * 13);
    *got = size; return true;
}
static void frames(unsigned count = 1)
{
    p6_input in{P6_ABI_VERSION, sizeof in}; p6_frame out{P6_ABI_VERSION, sizeof out};
    while (count--) {
        assert(p6_run_frame(&in, future_hash ? P6_RENDER_VIDEO | P6_RENDER_AUDIO : P6_RENDER_AUDIO, &out, &error) == P6_OK);
        if (future_hash) { observe(*future_hash,out.video,out.video_bytes); observe(*future_hash,out.audio,out.audio_frames*4); observe(*future_hash,&out.master_ticks,sizeof out.master_ticks); }
    }
}
static void local(const p6_cd::Disc &disc, unsigned mode)
{
    auto original = std::make_unique<p6_cdtv_state>(), candidate = std::make_unique<p6_cdtv_state>(), actual = std::make_unique<p6_cdtv_state>();
    assert(p6_cdtv_state_get(original.get()) && original->callback_mode == mode);
    p6_cdtv_context context{int64_t(get_cycles()), 0, 41179, &disc};
    ev2 slots[ev2_max]; memcpy(slots, eventtab2, sizeof slots);
    const auto callbacks = p6_cdtv_callback_observations(); const unsigned before_reads = reads;
    *candidate = *original; candidate->tp_a ^= 1; candidate->nvram[1] ^= 0xa5;
    candidate->cdrom_command_cnt_in = 1; candidate->input[0] = 1;
    candidate->cdrom_command_cnt_out = 0; candidate->cdrom_command_size_out = 1; candidate->output[0] = 0xaa;
    memset(candidate->queue, 0, sizeof candidate->queue); candidate->queue[0] = 0x81; candidate->request_size = 1;
    candidate->sub_used[0] = 1; candidate->sub[0][0] ^= 0x42; candidate->transfer[0] ^= 0x77;
    assert(p6_cdtv_state_validate(candidate.get(), context) && p6_cdtv_state_apply(candidate.get(), context));
    assert(p6_cdtv_state_get(actual.get()) && !memcmp(actual.get(), candidate.get(), sizeof *actual));
    for (unsigned n = 0; n < 9; ++n) {
        *candidate = *original;
        switch (n) {
        case 0: candidate->request_size = 101; break; case 1: candidate->subcodeoffset = 96; break;
        case 2: candidate->callback_mode = 3; break; case 3: candidate->device.disc_id[0] ^= 1; break;
        case 4: candidate->cdrom_offset |= 1; break; case 5: candidate->sub_used[1] = 2; break;
        case 6: candidate->cdrom_command_cnt_in = 16; break; case 7: candidate->toc.points = 104; break;
        case 8: candidate->device.audio.callbacks ^= 3; break;
        }
        assert(!p6_cdtv_state_validate(candidate.get(), context) && !p6_cdtv_state_apply(candidate.get(), context));
        assert(p6_cdtv_state_get(candidate.get()) && !memcmp(actual.get(), candidate.get(), sizeof *actual));
    }
    auto *bank = &get_mem_bank(0xe90000); map_banks(&chipmem_bank, 0xe9, 1, 0);
    assert(!p6_cdtv_state_apply(original.get(), context)); map_banks(bank, 0xe9, 1, 0);
    assert(p6_cdtv_state_apply(original.get(), context) && p6_cdtv_state_get(actual.get()));
    assert(!memcmp(actual.get(), original.get(), sizeof *actual) && get_cycles() == context.cycle);
    assert(reads == before_reads && p6_cdtv_callback_observations() == callbacks && !memcmp(slots, eventtab2, sizeof slots));
}
static std::vector<uint8_t> save(p6_state::Options options = {})
{
    uint64_t size = 0, used = 0; assert(p6_state_bound(&size, &error) == P6_OK);
    std::vector<uint8_t> state(size); assert(p6_state::save(state.data(), size, &used, options, &error) == P6_OK);
    state.resize(used); return state;
}
static void observe(uint64_t &hash, const void *ptr, size_t bytes)
{
    const auto *p = static_cast<const uint8_t *>(ptr); while (bytes--) hash = (hash ^ *p++) * 1099511628211ull;
}
static uint64_t future(unsigned cut)
{
    uint64_t hash = 1469598103934665603ull;
    if (cut == 0) put_byte(0xe900a1, 0);
    if (cut == 1) { unsigned value = get_byte(0xe900a1); assert(value == 0x55); observe(hash, &value, sizeof value); }
    if (cut == 4) { assert(get_byte(0xe90041) & 0x20); put_byte(0xe900e4, 0); }
    if (cut == 6) assert(p6_cdtv_private_request(0x106));
    bool signal = false; p6_input in{P6_ABI_VERSION, sizeof in}; p6_frame out{P6_ABI_VERSION, sizeof out};
    for (unsigned n = 0; n < (cut == 2 ? 30u : 4u); ++n) {
        assert(p6_run_frame(&in, P6_RENDER_VIDEO | P6_RENDER_AUDIO, &out, &error) == P6_OK);
        if (cut >= 5) for (uint64_t n = 0; n < out.audio_frames * 2; ++n) signal |= out.audio[n] != 0;
        observe(hash, out.video, out.video_bytes); observe(hash, out.audio, out.audio_frames * 4); observe(hash, &out.master_ticks, sizeof out.master_ticks);
    }
    if (cut >= 5) assert(signal);
    if (cut == 0 || cut == 2) assert(get_byte(0xe900a1) == 0xaa && get_byte(0xe900a1) == 0x55);
    for (unsigned n = 0; n < 8192; ++n) { unsigned value = get_byte(0x1fffe + n); observe(hash, &value, sizeof value); }
    if (cut == 3) assert(get_word(0x22000) == 0x000d && !p6_cdtv_private_status().error);
    if (cut == 6) { assert(p6_cdtv_private_request(0x104)); frames(); }
    auto final = save(); observe(hash, final.data(), final.size()); return hash;
}
static std::vector<uint8_t> file_bytes(const std::string &path)
{
    FILE *f = fopen(path.c_str(), "rb"); assert(f); assert(!fseek(f, 0, SEEK_END)); long size = ftell(f); rewind(f);
    std::vector<uint8_t> bytes(size); assert(fread(bytes.data(), 1, bytes.size(), f) == bytes.size()); fclose(f); return bytes;
}
static void write_bytes(const std::string &path, const void *data, size_t size)
{
    FILE *f = fopen(path.c_str(), "wb"); assert(f); assert(fwrite(data, 1, size, f) == size); fclose(f);
}
static void send(std::initializer_list<unsigned> bytes) { for (auto byte : bytes) put_byte(0xe900a1, byte); }
static void dma(unsigned words, unsigned address)
{
    for (unsigned n = 0; n < 4; ++n) { put_byte(0xe90080 + n, words >> (24 - n * 8)); put_byte(0xe90084 + n, address >> (24 - n * 8)); }
    put_byte(0xe90043, 0x90); put_byte(0xe900e0, 0);
}
static void cut_at(unsigned cut)
{
    if (cut == 0) { send({0x80}); frames(); }
    if (cut == 1) { send({0x80, 0}); frames(); assert(get_byte(0xe900a1) == 0xaa); }
    if (cut == 2) { send({1, 0, 0, 0, 0, 0, 0}); frames(); send({0x80, 0}); assert(p6_cdtv_private_status().seek); }
    if (cut == 3 || cut == 4) {
        for (unsigned n = 0; n < 4100; ++n) put_byte(0x1fffe + n, 0xcc);
        send({2, 0, 0, 0, 0, cut == 3 ? 2u : 1u, 0}); frames(); dma(cut == 3 ? 2048 : 1024, 0x20000); frames();
        for (unsigned n = 0; n < (cut == 3 ? 4096u : 2048u); ++n) assert(get_byte(0x20000 + n) == uint8_t(n * 13));
        assert(get_word(0x1fffe) == 0xcccc && get_word(cut == 3 ? 0x21000 : 0x20800) == 0xcccc);
        if (cut == 3) { assert(p6_cdtv_private_status().finished); send({2, 0, 0, 1, 0, 1, 0}); dma(1, 0x22000); }
        else assert(get_byte(0xe90041) & 0x20);
    }
    if (cut >= 5) {
        // Program the original serial DAC latch to audible gain through 6525 port B.
        for (unsigned n = 0; n < 12; ++n) { put_byte(0xe900b2, 0x20); put_byte(0xe900b2, 0x60); }
        put_byte(0xe900b2, 0xa0);
        for (auto word : {0x111u, 2u, 258u, 0u}) assert(p6_cdtv_private_request(word)); frames();
        bool event = false;
        for (unsigned n = 0; n < 104 && !event; ++n) {
            for (const auto &slot : eventtab2) event |= slot.active && slot.handler == p6_cdtv_subcode_handler();
            if (!event) frames();
        }
        assert(event);
        if (cut == 6) { assert(p6_cdtv_private_request(0x105)); frames(); }
    }
}
struct Fields { uint64_t queue = 0, event = 0; };
static void field(void *context, uint32_t, uint64_t at, uint64_t, const char *name)
{
    auto &found = *static_cast<Fields *>(context);
    if (!strcmp(name, "cdtv.request_size")) found.queue = at;
    if (!strcmp(name, "ev2.handler") && !found.event) found.event = at;
}
static void refusals()
{
    Fields found; p6_state::Options options; options.on_field = field; options.on_field_context = &found;
    auto good = save(options); const auto callbacks = p6_cdtv_callback_observations(); const auto cycle = get_cycles(); unsigned prior_reads = reads;
    for (auto at : {found.queue, found.event}) {
        assert(at); auto bad = good; bad[at] = at == found.queue ? 101 : 15;
        p6_state::seal(bad.data(), bad.size() - p6_state::kHeaderSize, p6_state::chunk_count(), p6_state::current_identity(),
                       p6_session::session.unit_index, p6_session::session.master_ticks);
        assert(p6_state_load(bad.data(), bad.size(), &error) == P6_ERROR_CORRUPT_STATE && save() == good);
    }
    assert(cycle == get_cycles() && reads == prior_reads && callbacks == p6_cdtv_callback_observations());
}
static void native(const std::vector<uint8_t> &rom, const p6_config &config, const char *role, const char *directory)
{
    p6_cd::Layout layout{}; layout.tracks = 2; layout.leadout = 258; layout.content_id[0] = 1;
    layout.track[0] = {0, 0, 2, 0, 2048, 0, p6_cd::Mode::Mode1_2048, 1, 4, false};
    layout.track[1] = {2, 2, 258, 0, 2352, 4096, p6_cd::Mode::Audio, 1, 0, false};
    p6_cd::Binding binding{{{1}, 4096 + 256 * 2352, 1}, image_read, nullptr}; p6_cd::Disc disc;
    assert(disc.bind(layout, &binding, 1) == p6_cd::Result::Ok); const bool writer = !strcmp(role, "write");
    const char *selected = getenv("P6_CDTV_STATE_CUT");
    const unsigned first = selected ? unsigned(strtoul(selected, nullptr, 10)) : 0, last = selected ? first + 1 : 7;
    assert(first < 7);
    for (unsigned cut = first; cut < last; ++cut) {
        assert(p6_init(&config, &error) == P6_OK && p6_load({rom.data(), rom.size()}, nullptr, 0, &error) == P6_OK);
        if (writer) frames(); assert(p6_cd::device_bind(&disc) == p6_cd::Result::Ok && p6_cdtv_private_start());
        const std::string path = std::string(directory) + "/" + std::to_string(cut);
        if (writer) { frames(2); cut_at(cut); } // Reader restores before its first run.
        auto state = writer ? save() : file_bytes(path + ".state");
        if (!writer) assert(p6_state_load(state.data(), state.size(), &error) == P6_OK && save() == state);
        refusals(); const uint64_t expected = future(cut);
        assert(p6_state_load(state.data(), state.size(), &error) == P6_OK && save() == state && future(cut) == expected);
        if (writer) { write_bytes(path + ".state", state.data(), state.size()); write_bytes(path + ".future", &expected, sizeof expected); }
        else { auto prior = file_bytes(path + ".future"); assert(prior.size() == sizeof expected && !memcmp(prior.data(), &expected, sizeof expected)); }
        assert(p6_shutdown(&error) == P6_OK); p6_cd::device_unbind();
        if (!writer) {
            assert(p6_init(&config, &error) == P6_OK && p6_load({rom.data(), rom.size()}, nullptr, 0, &error) == P6_OK);
            frames(); assert(p6_cd::device_bind(&disc) == p6_cd::Result::Ok); auto before = save();
            const auto cycle = get_cycles(); const auto callbacks = p6_cdtv_callback_observations(); const unsigned prior_reads = reads;
            assert(p6_state_load(state.data(), state.size(), &error) == P6_ERROR_INCOMPATIBLE && save() == before);
            assert(get_cycles() == cycle && reads == prior_reads && p6_cdtv_callback_observations() == callbacks);
            assert(p6_shutdown(&error) == P6_OK); p6_cd::device_unbind();
        }
        if (!writer && cut == 6) {
            auto other = binding; other.identity.content_id[0] = 2; p6_cd::Disc foreign;
            auto foreign_layout = layout; foreign_layout.content_id[0] = 2;
            assert(foreign.bind(foreign_layout, &other, 1) == p6_cd::Result::Ok);
            assert(p6_init(&config, &error) == P6_OK && p6_load({rom.data(), rom.size()}, nullptr, 0, &error) == P6_OK); frames();
            assert(p6_cd::device_bind(&foreign) == p6_cd::Result::Ok && p6_cdtv_private_start()); frames(); auto before = save();
            const auto cycle = get_cycles(); const auto callbacks = p6_cdtv_callback_observations(); const unsigned prior_reads = reads;
            assert(p6_state_load(state.data(), state.size(), &error) == P6_ERROR_INCOMPATIBLE && save() == before);
            assert(get_cycles() == cycle && reads == prior_reads && p6_cdtv_callback_observations() == callbacks);
            assert(p6_shutdown(&error) == P6_OK); p6_cd::device_unbind();
        }
        printf("CDTV cut %u %s exact\n", cut, role);
    }
}

static void bus_program()
{
    // Poll RAM command; CPU writes $E80048=EA or $E8004C=0, then clears it.
    const uint16_t code[] = {0x4a39,1,0,0x67f8,0x0c39,1,1,0,0x670a,0x13fc,0,0xe8,0x4c,0x6008,
                             0x13fc,0xea,0xe8,0x48,0x4239,1,0,0x60d4};
    for (unsigned n = 0; n < sizeof code / sizeof *code; ++n) {
        put_word(0x12000 + n * 2, code[n]); assert(get_word(0x12000 + n * 2) == code[n]);
    }
    put_byte(0x10000, 0); assert(!get_byte(0x10000));
    regs.sr = 0x2700; MakeFromSR(); m68k_setpc_normal(0x12000); fill_prefetch(); frames();
    assert(m68k_getpc() >= 0x12000 && m68k_getpc() < 0x1202c);
}
static uint64_t bus_future(unsigned cut)
{
    uint64_t hash = 1469598103934665603ull; future_hash = &hash;
    if (cut == 0) { put_byte(0x10000, 1); frames(); }
    if (cut != 2) {
        for (auto word : {0x80u, 0u}) put_byte(0xea00a1, word); frames();
        unsigned reply = get_byte(0x10000); assert(reply == 0); // The CPU consumed its command.
        assert(get_byte(0xea00a1) == 0xaa && get_byte(0xea00a1) == 0x55);
        put_byte(0xea00a1, 0x81); frames(); const unsigned status = get_byte(0xea00a1);
        assert(bool(status & 0x40) == (cut == 3)); observe(hash, &status, sizeof status);
        for (auto byte : {0x83u,0u,0u,0u,0u,0u,0u}) put_byte(0xea00a1, byte); frames();
        for (char byte : "MATSHITA0.96") if (byte) assert(get_byte(0xea00a1) == unsigned(byte));
        if (cut != 3) {
            const unsigned prior_reads = reads; auto empty = std::make_unique<p6_cdtv_state>();
            put_byte(0xea00a1, 0x86); frames();
            assert(p6_cdtv_state_get(empty.get()) && empty->cd_error && empty->cdrom_command_size_out == -1);
            for (auto byte : {0x0bu,1u,1u,0u,0u,0u,0u}) put_byte(0xea00a1, byte); frames();
            assert(p6_cdtv_state_get(empty.get()) && empty->cd_error && !empty->device.audio.bound && reads == prior_reads);
        }
        for (auto byte : {2u,0u,0u,0u,0u,1u,0u}) put_byte(0xea00a1, byte); frames();
        put_word(0x1fffe, 0x6a7b); put_word(0x20000, 0xbeef); put_word(0x20002, 0x8c9d);
        for (unsigned n = 0; n < 4; ++n) { put_byte(0xea0080 + n, 1u >> (24 - n * 8)); put_byte(0xea0084 + n, 0x20000u >> (24 - n * 8)); }
        put_byte(0xea0043, 0x90); put_byte(0xea00e0, 0); frames();
        assert(get_word(0x1fffe) == 0x6a7b && get_word(0x20002) == 0x8c9d && get_word(0x20000) == (cut == 3 ? 0x000d : 0xbeef));
        auto current = std::make_unique<p6_cdtv_state>(); assert(p6_cdtv_state_get(current.get()));
        assert(current->configured == 0xea && current->unitnum == (cut == 3 ? 0 : -1));
        assert(current->device.opens == (cut == 3 ? 1u : 0u) && bool(current->cd_error) == (cut != 3));
    } else {
        auto current = std::make_unique<p6_cdtv_state>(); assert(p6_cdtv_state_get(current.get()) && current->configured == -1);
        assert(&get_mem_bank(0xe80000) == &dummy_bank && &get_mem_bank(0xea0000) == &dummy_bank);
    }
    frames(2); observe(hash, chipmem_bank.baseaddr, chipmem_bank.allocated_size); auto final = save();
    observe(hash, final.data(), final.size()); future_hash = nullptr; return hash;
}
struct BusFields { uint64_t bank = 0, prom = 0, configured = 0, mask = 0; };
static void bus_field(void *context, uint32_t, uint64_t at, uint64_t, const char *name)
{
    auto &f = *static_cast<BusFields *>(context);
    if (!strcmp(name, "ram.fast_bank_current")) f.bank = at;
    if (!strcmp(name, "ram.fast_autoconfig") && !f.prom) f.prom = at;
    if (!strcmp(name, "cdtv.configured")) f.configured = at;
    if (!strcmp(name, "cdtv.dma_mask")) f.mask = at;
}
static void bus_refusals()
{
    BusFields fields; p6_state::Options options; options.on_field = bus_field; options.on_field_context = &fields;
    auto state = save(options); const auto cycle = get_cycles(); const auto callbacks = p6_cdtv_callback_observations(); const unsigned prior_reads = reads;
    for (auto offset : {fields.bank, fields.prom, fields.configured, fields.mask}) {
        assert(offset); auto bad = state; bad[offset] = offset == fields.configured ? 0xa0 : bad[offset] ^ 3;
        if (offset == fields.mask) for (unsigned n = 0; n < 4; ++n) bad[offset + n] = n == 3; // Invalid intermediate 0x01000000.
        p6_state::seal(bad.data(), bad.size() - p6_state::kHeaderSize, p6_state::chunk_count(), p6_state::current_identity(),
                       p6_session::session.unit_index, p6_session::session.master_ticks);
        assert(p6_state_load(bad.data(), bad.size(), &error) == P6_ERROR_CORRUPT_STATE && save() == state);
    }
    assert(cycle == get_cycles() && callbacks == p6_cdtv_callback_observations() && reads == prior_reads);
}
static void bus_difference(const std::vector<uint8_t> &expected, const std::vector<uint8_t> &actual)
{
    const size_t limit = std::min(expected.size(), actual.size()); size_t first = p6_state::kHeaderSize;
    while (first < limit && expected[first] == actual[first]) ++first;
    if (first == limit) { first = 0; while (first < limit && expected[first] == actual[first]) ++first; }
    char tag[5] = "HEAD"; size_t body = 0;
    for (size_t at = p6_state::kHeaderSize; first >= p6_state::kHeaderSize && at + p6_state::kChunkHeaderSize <= limit;) {
        uint64_t length = 0; for (unsigned n = 0; n < 8; ++n) length |= uint64_t(expected[at + 8 + n]) << (n * 8);
        if (length > limit - at - p6_state::kChunkHeaderSize) break;
        if (first < at + p6_state::kChunkHeaderSize + length) { memcpy(tag, expected.data() + at, 4); body = at + p6_state::kChunkHeaderSize; break; }
        at += p6_state::kChunkHeaderSize + length;
    }
    fprintf(stderr,"CDTV immediate state expected=%zu actual=%zu first=%zu %s+%zu",expected.size(),actual.size(),first,tag,first >= body ? first-body : first);
    for (size_t n = first; n < std::min(limit, first + 8); ++n) fprintf(stderr," %02x/%02x",expected[n],actual[n]);
    fputc('\n',stderr);
}
static void bus(const std::vector<uint8_t> &rom, p6_config config, const char *role, const char *directory)
{
    config.machine = p6_bridge::kCdtvMachine; const bool writer = !strcmp(role, "bus-write");
    p6_cd::Layout layout{}; layout.content_id[0] = 8; layout.tracks = 1; layout.leadout = 2;
    layout.track[0] = {0,0,2,0,2048,0,p6_cd::Mode::Mode1_2048,1,4,false};
    p6_cd::Binding binding{{{8},4096,2048},image_read,nullptr}; p6_cd::Disc disc;
    assert(disc.bind(layout, &binding, 1) == p6_cd::Result::Ok);
    const char *selected = getenv("P6_CDTV_BUS_CUT"); const unsigned first = selected ? unsigned(strtoul(selected,nullptr,10)) : 0;
    const unsigned last = selected ? first + 1 : 4; assert(first < 4);
    for (unsigned cut = first; cut < last; ++cut) {
        assert(p6_init(&config, &error) == P6_OK && p6_load({rom.data(),rom.size()},nullptr,0,&error) == P6_OK);
        if (cut == 3) assert(p6_cd::device_bind(&disc) == p6_cd::Result::Ok);
        const std::string path = std::string(directory) + "/bus" + std::to_string(cut);
        if (writer) {
            frames(); auto pending = std::make_unique<p6_cdtv_state>(); const bool captured = p6_cdtv_state_get(pending.get());
            if (!captured) {
                uint32_t w[13]{}; uint8_t prom[256]{}; const bool expansion = p6_expansion_state_get(w,13,prom);
                fprintf(stderr,"CDTV capture failed mask=%08x unit=%d configured=%d service=%d expansion=%d ecard=%u cards=%u bank=%u PROM=%02x/%02x\n",
                        pending->dma_mask,pending->unitnum,pending->configured,pending->service_running,expansion,w[0],w[1],w[4],prom[0],prom[2]);
            }
            assert(captured);
            fprintf(stderr, "CDTV first unit configured=%d PC=%x\n", pending->configured, m68k_getpc()); assert(pending->configured == 0 && pending->dma_mask == UINT32_MAX);
            unsigned waited = 0;
            while (&get_mem_bank(0x12000) != &chipmem_bank && waited < 64) {
                frames(); ++waited;
                assert(p6_cdtv_state_get(pending.get()) && pending->configured == 0);
                assert(get_byte(0xe80000) == 0xc0 && get_byte(0xe80002) == 0x10);
            }
            fprintf(stderr,"CDTV Chip RAM visible after %u further units PC=%x\n",waited,m68k_getpc());
            assert(&get_mem_bank(0x12000) == &chipmem_bank);
            assert(get_byte(0xe80000) == 0xc0 && get_byte(0xe80002) == 0x10); bus_program();
            if (cut) { put_byte(0x10000, cut == 2 ? 2 : 1); frames(); }
        }
        auto state = writer ? save() : file_bytes(path + ".state");
        if (!writer) {
            if (cut == 1 || cut == 3) {
                map_banks(&chipmem_bank,0xea,1,0); const auto cycle = get_cycles(); const unsigned prior_reads = reads;
                const auto callbacks = p6_cdtv_callback_observations();
                assert(p6_state_load(state.data(),state.size(),&error) == P6_ERROR_INCOMPATIBLE);
                assert(get_cycles() == cycle && reads == prior_reads && callbacks == p6_cdtv_callback_observations() && &get_mem_bank(0xea0000) == &chipmem_bank);
                map_banks(&dummy_bank,0xea,1,0);
            }
            assert(p6_state_load(state.data(),state.size(),&error) == P6_OK && save() == state);
        }
        bus_refusals(); const auto result = bus_future(cut);
        const auto restored = p6_state_load(state.data(),state.size(),&error);
        if (restored != P6_OK) fprintf(stderr,"CDTV load result=%u %s\n",unsigned(restored),error.message);
        assert(restored == P6_OK); const auto immediate = save();
        if (immediate != state) bus_difference(state,immediate); assert(immediate == state);
        const auto replay = bus_future(cut);
        if (replay != result) fprintf(stderr,"CDTV future expected=%016llx actual=%016llx\n",(unsigned long long)result,(unsigned long long)replay);
        assert(replay == result);
        if (writer) { write_bytes(path+".state",state.data(),state.size()); write_bytes(path+".future",&result,sizeof result); }
        else { auto expected=file_bytes(path+".future"); assert(expected.size()==sizeof result && !memcmp(expected.data(),&result,sizeof result)); }
        assert(p6_shutdown(&error)==P6_OK && !p6_cd::device_open_count()); p6_cd::device_unbind();
        printf("CDTV bus cut %u %s exact\n",cut,role);
    }
}

int main(int argc, char **argv)
{
    assert(argc == 3 || argc == 4 || argc == 5); FILE *f = fopen(argv[1], "rb"); assert(f);
    assert(!fseek(f, 0, SEEK_END)); long size = ftell(f); assert(size > 0); rewind(f);
    std::vector<uint8_t> rom(size); assert(fread(rom.data(), 1, rom.size(), f) == rom.size()); fclose(f);
    p6_config c{P6_ABI_VERSION, sizeof c}; c.machine = P6_MACHINE_A500; c.audio_rate_hz = 48000; c.video_standard = P6_VIDEO_PAL;
    c.random_seed = 1; c.rtc_epoch_seconds = 946684800;
    if (argc == 5) { auto combined = file_bytes(argv[2]); combined.insert(combined.end(),rom.begin(),rom.end()); bus(combined,c,argv[3],argv[4]); return 0; }
    if (argc == 4) { native(rom, c, argv[2], argv[3]); return 0; }
    assert(p6_init(&c, &error) == P6_OK);
    assert(p6_load({rom.data(), rom.size()}, nullptr, 0, &error) == P6_OK); frames();
    p6_cd::Layout layout{}; layout.tracks = 1; layout.leadout = 32;
    layout.track[0] = {0, 0, 32, 0, 2352, 0, p6_cd::Mode::Audio, 1, 0, false};
    p6_cd::Binding binding{{{1}, 32 * 2352, 1}, image_read, nullptr}; p6_cd::Disc disc;
    assert(disc.bind(layout, &binding, 1) == p6_cd::Result::Ok && p6_cd::device_bind(&disc) == p6_cd::Result::Ok);
    assert(p6_cdtv_private_start()); frames(); local(disc, 0);
    for (unsigned mode = 1; mode <= 2; ++mode) {
        for (auto word : {0x104u, mode == 1 ? 0x110u : 0x111u, 0u, 32u, 0u}) assert(p6_cdtv_private_request(word));
        frames(); local(disc, mode); assert(p6_cdtv_private_request(0x102)); frames(); local(disc, mode);
    }
    uint64_t bound = 9; assert(p6_state_bound(&bound, &error) == P6_OK && bound > 9);
    assert(p6_shutdown(&error) == P6_OK && !p6_cd::device_open_count()); p6_cd::device_unbind();
    puts("CDTV local component roundtrip and transactional refusals ok");
}
