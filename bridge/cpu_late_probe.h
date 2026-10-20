// Canonical observation for the Normal, no-MMU 040/060 component. Test tooling
// only; the public profile remains gated by T55c. No padding or host FP values.
#pragma once
#include "p6_fpu_state.h"

template<class Canon> bool p6_probe_cpu_late(Canon &c)
{
    if (currprefs.cpu_model != 68040 && currprefs.cpu_model != 68060) return true;
    c.put(uint32_t(regs.vbr)).put(uint32_t(regs.msp)).put(uint32_t(regs.cacr));
    c.put(uint8_t(regs.sfc)).put(uint8_t(regs.dfc)).put(int32_t(regs.instruction_cnt));
    if (currprefs.cpu_model == 68060) c.put(uint32_t(regs.buscr)).put(uint32_t(regs.pcr));
    if (!currprefs.fpu_model) return true;
    p6_fpu_state f{};
    if (!p6_fpu_state_get(&f)) return false; // Never publish a partial digest.
    c.put(f.context.model).put(f.context.revision).put(f.context.flags);
    for (unsigned i = 0; i < 8; ++i) c.put(f.high[i]).put(f.low[i]);
    c.put(f.fpcr).put(f.fpsr).put(f.fpiar).put(f.state).put(f.exp_state);
    c.put(f.exp_pend).put(f.unimp_pend).put(f.ea).put(f.opword).put(f.flags);
#define FIELD(name) c.put(f.frame.name);
    P6_FPU_FRAME_SCALARS(FIELD)
#undef FIELD
    for (unsigned i = 0; i < 3; ++i) c.put(f.frame.eo[i]).put(f.frame.fpt[i]).put(f.frame.et[i]).put(f.frame.wbt[i]);
    return true;
}
