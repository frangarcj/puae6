# Toolchain of the Linux rows built with GCC in the portability matrix (T25), for ARM64 and x86_64 alike: the container image of
# tests/portability/Dockerfile (Ubuntu 24.04, distribution compilers) is built for each ISA and this file is the same in both. Native build
# inside the container: no CMAKE_SYSTEM_NAME (CMakeLists.txt refuses cross builds), no -march=native and no other ISA flag. The code
# generator host tools use GCC too (HostTools.cmake would pick Clang first), so that the whole row, generated sources included, has a
# single compiler identity.
set(CMAKE_C_COMPILER /usr/bin/gcc CACHE FILEPATH "C compiler")
set(CMAKE_CXX_COMPILER /usr/bin/g++ CACHE FILEPATH "C++ compiler")
set(P6_HOST_CC /usr/bin/gcc CACHE FILEPATH "Compiler of the code generator host tools")
set(P6_HOST_CXX /usr/bin/g++ CACHE FILEPATH "Compiler of the code generator host tools")

# No section flags here: GCC -O0 keeps the unreferenced `static` functions that name symbols of the modules the profile leaves out, and the link failed
# (reports/platform-matrix.md, "GCC -O0"). CMakeLists.txt now gives every compiler and linker the same treatment (one section per function and datum and
# --gc-sections on ELF, -dead_strip on ld64), so this file only chooses the compiler. Reproducer of the failure: configure the repository with this file and
# -DP6_GC_SECTIONS=OFF, -DCMAKE_BUILD_TYPE=Debug, and link p6_headless or p6_state_probe: undefined references to rtarea_bank, rtarea_base,
# EXPANSION_bootcode, filesys_get_entry(int), mmu030_cache_state, jit_direct_compatible_memory and uae_time().
