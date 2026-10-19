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
#define CD32 1 /* T66k1: real Akiko is linked; machine profile remains gated. */
#define DRIVESOUND 1 /* T74: portable mechanical PCM, no physical drive or external sample loader. */
/* T29a: the hard file units of the engine (uaehf.device, the UAEFS board, the trap area) are compiled; a machine has them only when its media list holds a
 * HDF (bridge/media.cpp). FILESYS, UAE_FILESYS_THREADS and SUPPORT_THREADS stay excluded above: these macros name the single-thread subset instead. */
#define P6_FILESYS 1
#define P6_NATIVE2AMIGA 1 /* the queue that carries a finished request to the 68k (upstream keys it on SUPPORT_THREADS) */
#define CPUEMU_0 1 /* T53: generic 68000, emulated instruction cycles only */
#define CPUEMU_11 1 /* T53: compatible 68000 prefetch */
#define CPUEMU_13 1
#define CPUEMU_20 1 /* T53: compatible 68020 prefetch */
#define CPUEMU_21 1 /* 68020 cycle exact, no MMU (ADR 0010); chosen only by a 68020 preset */
/* 68881/68882 (T54): upstream's fpp.cpp over its integer SoftFloat (fpp_softfloat.cpp), never fpp_native.cpp, a long double or the host FPU. FPUEMU makes
 * cpuemu_21 compile the coprocessor handlers; whether a machine has an FPU is currprefs.fpu_model (0 = none), a configuration choice of p6_config. */
#define FPUEMU 1
#define WITH_SOFTFLOAT 1
/* FDI images (T57b): upstream's fdi2raw.cpp, the reader of WinUAE, in memory (the zfile of the portable layer), without a library and with patches 0332 to 0334 (bounded, and the draws of
 * its weak bits a function of the track and the revolution). The guard above refuses it from outside like the others. */
#define FDI2RAW 1
/* IPF images (T58): the disk.cpp branches that name P6_IPF read the image through ports/portable/media/ipf_track.h (the free decoder of MAME, third_party/mame-ipf, patch 0340). CAPS, the
 * capsimg hook of upstream, stays refused above. */
#define P6_IPF 1
#define AGA 1 /* Alice/Lisa: without it fixup_prefs clears the AGA bit of chipset_mask; A500/A600 never set it */
#define SIZEOF_SHORT __SIZEOF_SHORT__
#define SIZEOF_INT __SIZEOF_INT__
#define SIZEOF_LONG __SIZEOF_LONG__
#define SIZEOF_LONG_LONG __SIZEOF_LONG_LONG__
#define SIZEOF_VOID_P __SIZEOF_POINTER__
#define SIZEOF___INT64 0
typedef int32_t uae_atomic; /* Storage contract only; operations are separate. */

#define HAVE_STRING_H 1
#define HAVE_FCNTL_H 1 /* only the O_* constants that filesys.cpp names in the code of host-file volumes (rejected at configuration) */
#define FSDB_DIR_SEPARATOR_S "/"
#define HAVE_SYS_TYPES_H 1
#define HAVE_VAR_ATTRIBUTE_UNUSED 1
#define MAX_DPATH 1000
#define PACKAGE_STRING "PUAE6"

#endif
