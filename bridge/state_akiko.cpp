// Native CD32 private component; scheduler, RAM and Paula apply before P6AK.
#include "state.h"
#include "config.h"
#include "session.h"
#include "state_io/akiko.h"
#include <memory>
#include <new>
namespace p6_state {
void visit_akiko(Archive &a)
{
    // The value aggregate is large: ordinary caller-owned heap, never stack or
    // alloc_invisible. Validate fills this scratch without requiring live owners.
    std::unique_ptr<p6_akiko_state> candidate(new (std::nothrow) p6_akiko_state);
    a.require(candidate != nullptr, "Akiko staging allocation failed");
    if (!a.ok()) return;
    if (a.mode() == Mode::Save) {
        a.require(p6_akiko_state_get(candidate.get()), "Akiko cannot be captured");
        if (!a.ok()) return;
    }
    akiko_candidate_fields(a, *candidate);
    if (a.mode() == Mode::Measure) return;
    a.finish();
    if (!a.ok()) return;
    const auto &config = p6_session::session.config;
    const bool ntsc = config.video_standard == P6_VIDEO_NTSC;
    // 0025/0369 fix the physical clock, independent of the programmed beam or
    // host pacing. floor(float(clock * CYCLE_UNIT / 44100)) is 41179/41558.
    const p6_akiko_context context{uint32_t(7 | (ntsc ? 8 : 0)), ntsc ? 41558u : 41179u,
                                   scheduler_cycle(a.mode()), p6_cd::device_bound_disc()};
    a.require(context.cycle >= 0 && p6_akiko_state_validate(candidate.get(), context), "Akiko candidate is invalid");
    if (a.applying() && a.ok())
        a.require(p6_akiko_state_apply(candidate.get(), context), "Akiko destination is unavailable");
}
}
