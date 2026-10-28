// Bounded, address-free operation journal for reconstructing scalar libmpeg2.
#pragma once
#include <stddef.h>
#include <stdint.h>

namespace p6_fmv {

constexpr uint32_t kJournalBytes = 1194u * 1024u + 4u;
constexpr uint32_t kJournalEvents = 8192;
constexpr uint32_t kSequenceMarks = 64;

enum class Op : uint32_t { Feed = 1, Parse = 2 };
struct Event { uint32_t op, offset, value; };

struct Journal {
    uint32_t valid, event_count, byte_count, sequence_count, sequence_read;
    uint32_t checkpoint_event, checkpoint_byte, checkpoint_ready;
    Event events[kJournalEvents];
    uint32_t sequence_offsets[kSequenceMarks];
    uint8_t bytes[kJournalBytes];
};

struct Replay {
    void *context;
    bool (*reset)(void *);
    bool (*feed)(void *, const uint8_t *, uint32_t);
    int32_t (*parse)(void *);
    bool (*after_parse)(void *, int32_t);
};

void reset(Journal *);
// Returns stable journal-owned bytes for the live decoder, or nullptr after a
// bound violation. The caller may keep decoding from its original buffer, but
// no state may be captured until reset.
const uint8_t *record_feed(Journal *, const uint8_t *, uint32_t);
bool record_parse(Journal *, int32_t result, bool sequence, bool intra_complete,
                  bool buffer_empty);
bool valid(const Journal &);
size_t serialized_size(const Journal &);
bool save(const Journal &, void *, size_t capacity, size_t *written);
// Validation is complete before destination is changed.
bool load(Journal *, const void *, size_t bytes);
bool replay(const Journal &, const Replay &);

} // namespace p6_fmv
