// PUAE6 guest for Waterbox (T19a): the smallest adapter that keeps the whole bridge and engine linked into puae6.wbx. The guest ABI
// (initialization from copied firmware, seal, frame advance and its results) is T19b; nothing here runs a machine.
#include "emulibc.h"
#include "p6.h"

ECL_EXPORT uint32_t p6w_abi_version(void) { return p6_abi_version(); }
