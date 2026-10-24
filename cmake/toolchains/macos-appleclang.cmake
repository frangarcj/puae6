# Toolchain of the macOS ARM64 row of the portability matrix (T25): Apple Clang from the Xcode command line tools, native build.
# The project only supports the native host architecture (CMakeLists.txt refuses CMAKE_CROSSCOMPILING and CMAKE_OSX_ARCHITECTURES), so this
# file chooses compilers and nothing else: no CMAKE_SYSTEM_NAME, no -march, no sysroot. The host tools of the code generator are built
# with the same compiler, so that the generated sources have a single compiler identity.
set(CMAKE_C_COMPILER /usr/bin/clang CACHE FILEPATH "C compiler")
set(CMAKE_CXX_COMPILER /usr/bin/clang++ CACHE FILEPATH "C++ compiler")
set(P6_HOST_CC /usr/bin/clang CACHE FILEPATH "Compiler of the code generator host tools")
set(P6_HOST_CXX /usr/bin/clang++ CACHE FILEPATH "Compiler of the code generator host tools")
