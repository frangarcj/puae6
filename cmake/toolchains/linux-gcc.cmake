# Toolchain of the Linux rows built with GCC in the portability matrix (T25), for ARM64 and x86_64 alike: the container image of
# tests/portability/Dockerfile (Ubuntu 24.04, distribution compilers) is built for each ISA and this file is the same in both. Native build
# inside the container: no CMAKE_SYSTEM_NAME (CMakeLists.txt refuses cross builds), no -march=native and no other ISA flag. The code
# generator host tools use GCC too (HostTools.cmake would pick Clang first), so that the whole row, generated sources included, has a
# single compiler identity.
set(CMAKE_C_COMPILER /usr/bin/gcc CACHE FILEPATH "C compiler")
set(CMAKE_CXX_COMPILER /usr/bin/g++ CACHE FILEPATH "C++ compiler")
set(P6_HOST_CC /usr/bin/gcc CACHE FILEPATH "Compiler of the code generator host tools")
set(P6_HOST_CXX /usr/bin/g++ CACHE FILEPATH "Compiler of the code generator host tools")

# Link-level fix found by the matrix (reports/platform-matrix.md, "GCC -O0"). Reproducer: configure with this file minus the three set() lines
# below, -DCMAKE_BUILD_TYPE=Debug, and link p6_headless or p6_state_probe: ld reports undefined references to rtarea_bank, rtarea_base,
# EXPANSION_bootcode, filesys_get_entry(int), mmu030_cache_state, jit_direct_compatible_memory and uae_time(). They come from static functions
# that nothing calls (expamem_map_uaeboard, add_rtarea_pointer, the *_ce030_cicheck helpers, read_processor_time) and that reference modules the
# profile excludes: GCC keeps unreferenced static functions at -O0, Clang does not (nm of expansion.cpp.o shows expamem_map_uaeboard in GCC's
# object and not in Clang's). With one section per function the linker discards the unreachable ones: the code of every function is what
# the compiler generated anyway. Whether the -O2 GCC build needs it is not known: every GCC build of the matrix was made with these flags.
set(CMAKE_C_FLAGS_INIT "-ffunction-sections -fdata-sections")
set(CMAKE_CXX_FLAGS_INIT "-ffunction-sections -fdata-sections")
set(CMAKE_EXE_LINKER_FLAGS_INIT "-Wl,--gc-sections")
