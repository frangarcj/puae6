/* Narrow WinUAE configuration for PUAE6 deterministic A500.
 * SPDX-License-Identifier: GPL-2.0-or-later
 */
#ifndef P6_SYSCONFIG_H
#define P6_SYSCONFIG_H

#include <stdint.h>
#include <stdbool.h>

/* Defining an excluded feature to 0 is still unsafe: upstream uses #ifdef. */
#if defined(JIT) || defined(CUSTOM_SIMPLE) || defined(WITH_THREADED_CPU) || \
    defined(FILESYS) || defined(UAE_FILESYS_THREADS) || defined(NATMEM_OFFSET) || \
    defined(CAPS) || defined(SCP) || defined(FDI2RAW) || defined(FLOPPYBRIDGE) || \
    defined(WITH_PPC) || defined(FPUEMU) || defined(MMUEMU) || defined(PICASSO96) || \
    defined(CD32) || defined(CDTV) || defined(DRIVESOUND) || defined(WITH_LUA) || \
    defined(BSDSOCKET) || defined(WITH_SLIRP) || defined(AHI) || defined(AHI_v2) || \
    defined(AVIOUTPUT) || defined(RETROPLATFORM) || defined(WITH_SPECIALMONITORS) || \
    defined(WITH_BEAMRACER) || defined(SUPPORT_THREADS) || defined(UAE_MINI) || \
    defined(SAHF_SETO_PROFITABLE) || defined(WITH_SOFTFLOAT) || \
    defined(MSVC_LONG_DOUBLE) || defined(USE_LONG_DOUBLE) || defined(TOD_HACK)
#error "P6 A500: excluded feature requested"
#endif

#if !defined(__SIZEOF_SHORT__) || !defined(__SIZEOF_INT__) || \
    !defined(__SIZEOF_LONG__) || !defined(__SIZEOF_LONG_LONG__) || !defined(__SIZEOF_POINTER__)
#error "P6 A500: compiler must provide exact ABI size traits"
#endif
#if __CHAR_BIT__ != 8 || __SIZEOF_SHORT__ != 2 || __SIZEOF_INT__ != 4 || \
    __SIZEOF_LONG_LONG__ != 8 || __SIZEOF_POINTER__ != 8 || \
    (__SIZEOF_LONG__ != 4 && __SIZEOF_LONG__ != 8)
#error "P6 A500: unsupported ABI (requires LP64 or LLP64 with 8-bit bytes)"
#endif

#define P6_PORTABLE 1
#define CPUEMU_13 1
#define SIZEOF_SHORT __SIZEOF_SHORT__
#define SIZEOF_INT __SIZEOF_INT__
#define SIZEOF_LONG __SIZEOF_LONG__
#define SIZEOF_LONG_LONG __SIZEOF_LONG_LONG__
#define SIZEOF_VOID_P __SIZEOF_POINTER__
#define SIZEOF___INT64 0
typedef int32_t uae_atomic; /* Storage contract only; operations are separate. */

#define HAVE_STRING_H 1
#define HAVE_SYS_TYPES_H 1
#define HAVE_VAR_ATTRIBUTE_UNUSED 1
#define MAX_DPATH 1000
#define PACKAGE_STRING "PUAE6"

#endif
