// Private semantic state of the internal SoftFloat FPU, not a wire/public ABI.
#pragma once
#include <stdint.h>
struct p6_fpu_context {
    uint32_t model, revision, flags; // 040 rev40/41, 060 rev0; strict=1, no-unimplemented=2.
};
// All semantic words of fpp.cpp's FSAVE data, including inactive operands.
#define P6_FPU_FRAME_SCALARS(X) \
 X(ccr) X(v) X(fpiarcu) X(cmdreg3b) X(cmdreg1b) X(stag) X(dtag) \
 X(e1) X(e3) X(t) X(grs) X(wbte15) X(wbtm66) \
 X(cusavepc) X(fpr_dirty) X(fpsr_shadow) X(cmdreg2b) X(nmnexc) X(nmcexc) \
 X(ete15) X(fpte15) X(s) X(x) X(u)
struct p6_fpu_state {
    p6_fpu_context context;
    uint16_t high[8];
    uint64_t low[8]; // Only fpx; fpdata's native FP member and padding never travel.
    uint32_t fpcr, fpsr, fpiar, state, exp_state, exp_pend, unimp_pend, ea;
    uint16_t opword;
    uint32_t flags; // ea_set, exp_pre, unimp_ins, exception, branch, bits0..4.
    struct Frame {
#define P6_FPU_DECLARE(name) uint32_t name;
        P6_FPU_FRAME_SCALARS(P6_FPU_DECLARE)
#undef P6_FPU_DECLARE
        uint32_t eo[3], fpt[3], et[3], wbt[3];
    } frame;
};
// Quiescent FPU instruction boundary; CPU registers/RAM/scheduler are external.
// Validate is owner-independent. Preflight/apply require the same initialized
// model/revision/options, no MMU and the portable SoftFloat backend. All endpoints
// do no allocation, I/O, callbacks or advancement; invalid apply changes nothing.
bool p6_fpu_state_get(p6_fpu_state *);
bool p6_fpu_state_validate(const p6_fpu_state *, const p6_fpu_context &);
bool p6_fpu_state_preflight(const p6_fpu_state *);
bool p6_fpu_state_apply(const p6_fpu_state *);
