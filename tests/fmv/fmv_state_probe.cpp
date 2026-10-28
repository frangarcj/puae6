// FMV-D1: reconstruct scalar libmpeg2 from the bounded address-free journal.
#include "fmv_decoder_journal.h"
extern "C" {
#include "mpeg2.h"
#include "mpeg2convert.h"
}

#include <cassert>
#include <cstdio>
#include <cstring>
#include <memory>
#include <vector>

namespace {
char kind(const mpeg2_picture_t *p)
{
    if (!p) return '?';
    switch (p->flags & PIC_MASK_CODING_TYPE) {
    case PIC_FLAG_CODING_TYPE_I: return 'I';
    case PIC_FLAG_CODING_TYPE_P: return 'P';
    case PIC_FLAG_CODING_TYPE_B: return 'B';
    default: return '?';
    }
}

struct Machine {
    mpeg2dec_t *decoder = nullptr;
    const mpeg2_info_t *info = nullptr;
    std::unique_ptr<p6_fmv::Journal> journal{new p6_fmv::Journal};
    std::vector<uint8_t> input, frame;
    size_t input_at = 0;
    bool needs_buffer = true;
    int32_t last_state = STATE_INVALID;

    explicit Machine(const std::vector<uint8_t> &bytes) : input(bytes) { reset_decoder(); p6_fmv::reset(journal.get()); }
    ~Machine() { if (decoder) mpeg2_close(decoder); }
    bool reset_decoder()
    {
        if (decoder) mpeg2_close(decoder);
        decoder = mpeg2_init(); info = decoder ? mpeg2_info(decoder) : nullptr;
        frame.clear(); needs_buffer = true; last_state = STATE_INVALID;
        return decoder && info;
    }
    bool after(int32_t state)
    {
        if (state == STATE_SEQUENCE && mpeg2_convert(decoder, mpeg2convert_rgb32, nullptr)) return false;
        frame.clear();
        if ((state == STATE_SLICE || state == STATE_END) && info->display_fbuf && info->sequence) {
            const size_t bytes = size_t(info->sequence->width) * info->sequence->height * 4;
            frame.assign(info->display_fbuf->buf[0], info->display_fbuf->buf[0] + bytes);
        }
        return true;
    }
    bool step()
    {
        if (needs_buffer) {
            if (input_at == input.size()) return false;
            const uint32_t bytes = uint32_t((input.size() - input_at) < 113 ? input.size() - input_at : 113);
            const uint8_t *stable = p6_fmv::record_feed(journal.get(), input.data() + input_at, bytes);
            assert(stable); auto *mutable_bytes = const_cast<uint8_t *>(stable);
            mpeg2_buffer(decoder, mutable_bytes, mutable_bytes + bytes);
            input_at += bytes; needs_buffer = false;
        }
        last_state = mpeg2_parse(decoder);
        const bool picture = last_state == STATE_SLICE || last_state == STATE_END;
        const bool intra = picture && kind(info->current_picture) == 'I';
        assert(p6_fmv::record_parse(journal.get(), last_state, last_state == STATE_SEQUENCE,
                                    intra, last_state == STATE_BUFFER));
        assert(after(last_state));
        if (last_state == STATE_BUFFER) needs_buffer = true;
        return true;
    }
    static bool hook_reset(void *p) { return static_cast<Machine *>(p)->reset_decoder(); }
    static bool hook_feed(void *p, const uint8_t *data, uint32_t bytes)
    {
        auto &m = *static_cast<Machine *>(p); auto *mutable_data = const_cast<uint8_t *>(data);
        mpeg2_buffer(m.decoder, mutable_data, mutable_data + bytes); m.needs_buffer = false; return true;
    }
    static int32_t hook_parse(void *p)
    {
        auto &m = *static_cast<Machine *>(p); m.last_state = mpeg2_parse(m.decoder);
        if (m.last_state == STATE_BUFFER) m.needs_buffer = true;
        return m.last_state;
    }
    static bool hook_after(void *p, int32_t state) { return static_cast<Machine *>(p)->after(state); }
    bool replay_saved()
    {
        const p6_fmv::Replay hooks{this, hook_reset, hook_feed, hook_parse, hook_after};
        return p6_fmv::replay(*journal, hooks);
    }
};

std::vector<uint8_t> read_file(const char *path)
{
    FILE *f = fopen(path, "rb"); assert(f && fseek(f, 0, SEEK_END) == 0);
    const long n = ftell(f); assert(n > 0 && fseek(f, 0, SEEK_SET) == 0);
    std::vector<uint8_t> out(static_cast<size_t>(n), uint8_t{});
    assert(fread(out.data(), 1, out.size(), f) == out.size());
    fclose(f); const uint8_t end[] = {0, 0, 1, 0xb7, 0, 0, 0, 0};
    out.insert(out.end(), end, end + sizeof end); return out;
}

bool at_cut(const Machine &m, char target)
{
    if (target == 'H') return m.last_state == STATE_SEQUENCE;
    return (m.last_state == STATE_SLICE || m.last_state == STATE_END) && kind(m.info->current_picture) == target;
}

void cut_case(const std::vector<uint8_t> &bytes, char target)
{
    Machine original(bytes);
    for (unsigned n = 0; n < 10000 && !at_cut(original, target); ++n) assert(original.step());
    assert(at_cut(original, target));
    const size_t size = p6_fmv::serialized_size(*original.journal); assert(size && size <= 20 + 12 * p6_fmv::kJournalEvents + p6_fmv::kJournalBytes);
    std::vector<uint8_t> blob(size); size_t written = 0;
    assert(p6_fmv::save(*original.journal, blob.data(), blob.size(), &written) && written == size);

    Machine restored(bytes);
    assert(p6_fmv::load(restored.journal.get(), blob.data(), blob.size()));
    restored.input_at = original.input_at;
    assert(restored.replay_saved());
    assert(restored.last_state == original.last_state && restored.frame == original.frame);
    unsigned future_frames = 0;
    for (unsigned n = 0; n < 10000; ++n) {
        const bool a = original.step(), b = restored.step(); assert(a == b);
        if (!a) break;
        assert(original.last_state == restored.last_state && original.frame == restored.frame);
        future_frames += !original.frame.empty();
        if (original.last_state == STATE_BUFFER && original.input_at == original.input.size()) break;
    }
    assert(future_frames > 0);

    // Header, length and checksum attacks are rejected before the destination changes.
    std::vector<uint8_t> before(p6_fmv::serialized_size(*restored.journal)); size_t before_size = 0;
    assert(p6_fmv::save(*restored.journal, before.data(), before.size(), &before_size));
    for (size_t n : {size_t(0), size_t(4), size - 1}) assert(!p6_fmv::load(restored.journal.get(), blob.data(), n));
    auto bad = blob; bad[bad.size() - 1] ^= 0x80;
    assert(!p6_fmv::load(restored.journal.get(), bad.data(), bad.size()));
    std::vector<uint8_t> after(before_size); size_t after_size = 0;
    assert(p6_fmv::save(*restored.journal, after.data(), after.size(), &after_size));
    assert(after_size == before_size && after == before);
    std::printf("cut %c bytes=%zu events=%u future_frames=%u PASS\n", target, size,
                original.journal->event_count, future_frames);
}

void compact_case(const std::vector<uint8_t> &video)
{
    std::vector<uint8_t> prefixed(4096, 0x55); prefixed.insert(prefixed.end(), video.begin(), video.end());
    Machine machine(prefixed);
    for (unsigned n = 0; n < 20000; ++n) {
        assert(machine.step());
        if (machine.last_state == STATE_BUFFER && machine.input_at == machine.input.size()) break;
    }
    assert(p6_fmv::valid(*machine.journal));
    assert(machine.journal->byte_count == video.size());
    assert(machine.journal->events[0].op == uint32_t(p6_fmv::Op::Feed));
    Machine rebuilt(prefixed); *rebuilt.journal = *machine.journal;
    assert(rebuilt.replay_saved() && rebuilt.last_state == machine.last_state && rebuilt.frame == machine.frame);
    puts("safe sequence-to-I compaction PASS");
}
}

int main(int argc, char **argv)
{
    assert(argc == 2); const auto video = read_file(argv[1]);
    for (char cut : {'H', 'I', 'P', 'B'}) cut_case(video, cut);
    compact_case(video);
    return 0;
}
