#include "fmv_decoder_journal.h"

#include <string.h>

namespace p6_fmv {
namespace {
constexpr uint32_t kVersion = 1;
constexpr size_t kHeader = 20;

uint32_t get32(const uint8_t *p)
{
    return uint32_t(p[0]) | uint32_t(p[1]) << 8 | uint32_t(p[2]) << 16 | uint32_t(p[3]) << 24;
}
void put32(uint8_t *p, uint32_t v)
{
    p[0] = uint8_t(v); p[1] = uint8_t(v >> 8); p[2] = uint8_t(v >> 16); p[3] = uint8_t(v >> 24);
}
uint32_t checksum(const uint8_t *p, size_t n)
{
    uint32_t value = 2166136261u;
    while (n--) { value ^= *p++; value *= 16777619u; }
    return value;
}
bool event_valid(const Event &e, uint32_t bytes)
{
    if (e.op == uint32_t(Op::Parse)) return e.offset == 0 && int32_t(e.value) >= -1 && int32_t(e.value) <= 32;
    return e.op == uint32_t(Op::Feed) && e.value && e.offset <= bytes && e.value <= bytes - e.offset;
}
void scan_sequences(Journal *j, uint32_t begin)
{
    if (!j->valid || j->byte_count < 4) return;
    if (begin > 3) begin -= 3; else begin = 0;
    for (uint32_t at = begin; at + 4 <= j->byte_count; ++at) {
        if (j->bytes[at] || j->bytes[at + 1] || j->bytes[at + 2] != 1 || j->bytes[at + 3] != 0xb3) continue;
        if (j->sequence_count && j->sequence_offsets[j->sequence_count - 1] == at) continue;
        if (j->sequence_count == kSequenceMarks) { j->valid = 0; return; }
        j->sequence_offsets[j->sequence_count++] = at;
    }
}
bool compact(Journal *j)
{
    if (!j->checkpoint_ready || j->checkpoint_event >= j->event_count || j->checkpoint_byte >= j->byte_count) return false;
    uint32_t feed = j->checkpoint_event;
    while (feed && !(j->events[feed - 1].op == uint32_t(Op::Feed) &&
           j->checkpoint_byte >= j->events[feed - 1].offset &&
           j->checkpoint_byte < j->events[feed - 1].offset + j->events[feed - 1].value)) --feed;
    if (!feed) return false;
    const Event source = j->events[feed - 1];
    const uint32_t first = source.offset + source.value - j->checkpoint_byte;
    const uint32_t retained = j->event_count - j->checkpoint_event;
    memmove(j->bytes, j->bytes + j->checkpoint_byte, j->byte_count - j->checkpoint_byte);
    memmove(j->events + 1, j->events + j->checkpoint_event, retained * sizeof(Event));
    j->events[0] = {uint32_t(Op::Feed), 0, first};
    j->event_count = retained + 1;
    j->byte_count -= j->checkpoint_byte;
    for (uint32_t n = 1; n < j->event_count; ++n)
        if (j->events[n].op == uint32_t(Op::Feed)) j->events[n].offset -= j->checkpoint_byte;
    j->sequence_count = j->sequence_read = 0;
    j->checkpoint_event = j->checkpoint_byte = j->checkpoint_ready = 0;
    scan_sequences(j, 0);
    return j->valid != 0;
}
bool layout_valid(const Journal &j)
{
    if (j.valid != 1 || j.event_count > kJournalEvents || j.byte_count > kJournalBytes) return false;
    uint32_t next = 0;
    for (uint32_t n = 0; n < j.event_count; ++n) {
        const Event &e = j.events[n];
        if (!event_valid(e, j.byte_count)) return false;
        if (e.op == uint32_t(Op::Feed)) {
            if (e.offset != next) return false;
            next += e.value;
        }
    }
    return next == j.byte_count;
}
}

void reset(Journal *j)
{
    if (!j) return;
    memset(j, 0, sizeof *j); j->valid = 1;
}

const uint8_t *record_feed(Journal *j, const uint8_t *data, uint32_t bytes)
{
    if (!j || !data || !bytes || !j->valid || j->event_count == kJournalEvents ||
        bytes > kJournalBytes - j->byte_count) {
        if (j) j->valid = 0;
        return nullptr;
    }
    const uint32_t at = j->byte_count;
    memcpy(j->bytes + at, data, bytes);
    j->byte_count += bytes;
    j->events[j->event_count++] = {uint32_t(Op::Feed), at, bytes};
    scan_sequences(j, at);
    return j->valid ? j->bytes + at : nullptr;
}

bool record_parse(Journal *j, int32_t result, bool sequence, bool intra_complete, bool buffer_empty)
{
    if (!j || !j->valid || result < -1 || result > 32 || j->event_count == kJournalEvents) {
        if (j) j->valid = 0;
        return false;
    }
    j->events[j->event_count++] = {uint32_t(Op::Parse), 0, uint32_t(result)};
    if (sequence && j->sequence_read < j->sequence_count) {
        j->checkpoint_byte = j->sequence_offsets[j->sequence_read++];
        j->checkpoint_event = j->event_count - 1;
        j->checkpoint_ready = 0;
    }
    if (intra_complete && j->checkpoint_event) j->checkpoint_ready = 1;
    if (buffer_empty && j->checkpoint_ready && !compact(j)) j->valid = 0;
    return j->valid != 0;
}

bool valid(const Journal &j) { return layout_valid(j); }

size_t serialized_size(const Journal &j)
{
    return layout_valid(j) ? kHeader + size_t(j.event_count) * 12 + j.byte_count : 0;
}

bool save(const Journal &j, void *output, size_t capacity, size_t *written)
{
    const size_t total = serialized_size(j);
    if (written) *written = total;
    if (!total || !output || capacity < total) return false;
    uint8_t *out = static_cast<uint8_t *>(output);
    memcpy(out, "P6FJ", 4); put32(out + 4, kVersion); put32(out + 8, j.event_count); put32(out + 12, j.byte_count);
    uint8_t *at = out + kHeader;
    for (uint32_t n = 0; n < j.event_count; ++n, at += 12) {
        put32(at, j.events[n].op); put32(at + 4, j.events[n].offset); put32(at + 8, j.events[n].value);
    }
    memcpy(at, j.bytes, j.byte_count);
    put32(out + 16, checksum(out + kHeader, total - kHeader));
    return true;
}

bool load(Journal *j, const void *input, size_t bytes)
{
    if (!j || !input || bytes < kHeader) return false;
    const uint8_t *in = static_cast<const uint8_t *>(input);
    const uint32_t count = get32(in + 8), data = get32(in + 12);
    if (memcmp(in, "P6FJ", 4) || get32(in + 4) != kVersion || count > kJournalEvents ||
        data > kJournalBytes || bytes != kHeader + size_t(count) * 12 + data ||
        get32(in + 16) != checksum(in + kHeader, bytes - kHeader)) return false;
    uint32_t next = 0;
    for (uint32_t n = 0; n < count; ++n) {
        const uint8_t *at = in + kHeader + size_t(n) * 12;
        const Event e{get32(at), get32(at + 4), get32(at + 8)};
        if (!event_valid(e, data)) return false;
        if (e.op == uint32_t(Op::Feed) && (e.offset != next || (next += e.value) > data)) return false;
    }
    if (next != data) return false;
    reset(j); j->event_count = count; j->byte_count = data;
    for (uint32_t n = 0; n < count; ++n) {
        const uint8_t *at = in + kHeader + size_t(n) * 12;
        j->events[n] = {get32(at), get32(at + 4), get32(at + 8)};
    }
    memcpy(j->bytes, in + kHeader + size_t(count) * 12, data);
    scan_sequences(j, 0);
    return layout_valid(*j);
}

bool replay(const Journal &j, const Replay &r)
{
    if (!layout_valid(j) || !r.reset || !r.feed || !r.parse || !r.after_parse || !r.reset(r.context)) return false;
    for (uint32_t n = 0; n < j.event_count; ++n) {
        const Event &e = j.events[n];
        if (e.op == uint32_t(Op::Feed)) {
            if (!r.feed(r.context, j.bytes + e.offset, e.value)) return false;
        } else {
            const int32_t state = r.parse(r.context);
            if (state != int32_t(e.value) || !r.after_parse(r.context, state)) return false;
        }
    }
    return true;
}

} // namespace p6_fmv
