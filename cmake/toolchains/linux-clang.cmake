# Toolchain of the Linux rows built with Clang in the portability matrix (T25), for ARM64 and x86_64 alike: the container image of
# tests/portability/Dockerfile (Ubuntu 24.04, distribution compilers) is built for each ISA and this file is the same in both. Native build
# inside the container: no CMAKE_SYSTEM_NAME (CMakeLists.txt refuses cross builds), no -march=native and no other ISA flag. The code
# generator host tools use the same compiler, so that the generated sources have a single compiler identity per row.
set(CMAKE_C_COMPILER /usr/bin/clang CACHE FILEPATH "C compiler")
set(CMAKE_CXX_COMPILER /usr/bin/clang++ CACHE FILEPATH "C++ compiler")
set(P6_HOST_CC /usr/bin/clang CACHE FILEPATH "Compiler of the code generator host tools")
set(P6_HOST_CXX /usr/bin/clang++ CACHE FILEPATH "Compiler of the code generator host tools")
