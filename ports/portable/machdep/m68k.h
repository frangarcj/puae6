/* Reuse only the pinned pure-C CCR layout/macros from WinUAE, not its host
 * backend. Provenance: manifests/source-manifest.json portable_reuse.
 * SPDX-License-Identifier: GPL-2.0-or-later
 */
#ifndef P6_M68K_H
#define P6_M68K_H
#if defined(SAHF_SETO_PROFITABLE)
#error "P6 CCR requires the default portable flag layout"
#endif
#include <od-win32/machdep/m68k.h>
#endif
