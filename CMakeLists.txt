cmake_minimum_required(VERSION 3.21)
project(PUAE6 LANGUAGES C CXX)

# This bootstrap exposes the real engine compilation graph. It does not yet
# provide a headless emulator: T06b/T06c close compilation and linking.
if(CMAKE_CROSSCOMPILING OR CMAKE_OSX_ARCHITECTURES)
    message(FATAL_ERROR "Only the native host architecture is supported by this bootstrap")
endif()
if(NOT CMAKE_C_COMPILER_ID MATCHES "^(Clang|AppleClang|GNU)$" OR
   NOT CMAKE_CXX_COMPILER_ID MATCHES "^(Clang|AppleClang|GNU)$")
    message(FATAL_ERROR "The native bootstrap requires Clang or GCC")
endif()
file(READ "${CMAKE_CURRENT_SOURCE_DIR}/manifests/source-manifest.json" _manifest)
set_property(DIRECTORY APPEND PROPERTY CMAKE_CONFIGURE_DEPENDS
    "${CMAKE_CURRENT_SOURCE_DIR}/manifests/source-manifest.json")
string(JSON _profile GET "${_manifest}" profile)
set(P6_PROFILE "${_profile}" CACHE STRING "Fixed engine profile")
if(NOT P6_PROFILE STREQUAL _profile)
    message(FATAL_ERROR "Unsupported P6_PROFILE: ${P6_PROFILE}; expected ${_profile}")
endif()
string(JSON _count LENGTH "${_manifest}" selection features_off)
math(EXPR _last "${_count} - 1")
foreach(_index RANGE 0 ${_last})
    string(JSON _feature GET "${_manifest}" selection features_off ${_index})
    if(DEFINED ${_feature})
        message(FATAL_ERROR "Excluded feature requested: ${_feature}; use the fixed profile")
    endif()
endforeach()

set(CMAKE_C_STANDARD 11)
set(CMAKE_C_STANDARD_REQUIRED ON)
set(CMAKE_C_EXTENSIONS OFF)
set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)
set(CMAKE_EXPORT_COMPILE_COMMANDS ON)
include(cmake/Sanitizers.cmake)
option(P6_UNIT_TRACE "Test-only: engine reports and can swallow video boundaries (STEP01)" OFF)
# No fused multiply-add: Paula's RC filter and the sample interval are float math, and a contracted build differs from
# an uncontracted one by one least significant bit in a fraction of the samples (measured). Contraction is a compiler and ISA
# choice that must not reach the machine.
add_compile_options(-fno-fast-math -ffp-contract=off)

include(cmake/HostTools.cmake)
p6_add_host_codegen(p6_codegen)
add_library(p6_engine OBJECT ${P6_UPSTREAM_SOURCES} ${P6_GENERATED_SOURCES})
add_library(p6_portable STATIC
    ports/portable/atomic.cpp
    ports/portable/text_services.cpp
    ports/portable/input_devices.cpp
    ports/portable/video_present.cpp
    ports/portable/blob_io.c
    ports/portable/media/sha256.c
    ports/portable/civil_time.c
    ports/portable/rtc_time.cpp
    ports/portable/audio_math.cpp
    ports/portable/pcm_output.cpp
    ports/portable/sound_lifecycle.cpp
    ports/portable/diagnostics.cpp
    ports/portable/gui.cpp)
if(P6_UNIT_TRACE)
    target_compile_definitions(p6_engine PRIVATE P6_UNIT_TRACE)
endif()
foreach(_target IN ITEMS p6_engine p6_portable)
    add_dependencies(${_target} p6_codegen)
    target_include_directories(${_target} PRIVATE
        "${CMAKE_CURRENT_SOURCE_DIR}/ports/portable" ${P6_INCLUDE_DIRS})
endforeach()

enable_testing()
add_executable(p6_portable_probe
    tests/host/portable_services_probe.cpp
    "${P6_UPSTREAM_DIR}/zfile.cpp"
    "${P6_UPSTREAM_DIR}/crc32.cpp")
add_dependencies(p6_portable_probe p6_codegen)
target_include_directories(p6_portable_probe PRIVATE
    "${CMAKE_CURRENT_SOURCE_DIR}/ports/portable" ${P6_INCLUDE_DIRS})
target_link_libraries(p6_portable_probe PRIVATE p6_portable)
# The probe uses assertions for operations as well as checks, including Release.
target_compile_options(p6_portable_probe PRIVATE -UNDEBUG)
add_test(NAME portable-services COMMAND p6_portable_probe)
set_tests_properties(portable-services PROPERTIES LABELS "host;portable" TIMEOUT 30)

# Headless runner: engine objects + portable services + the bridge. It proves the A500
# profile links without windowing, audio or plugin libraries and reports the build identity
# (upstream commit, patch series hash, profile); it does not boot a machine yet.
string(JSON _upstream_commit GET "${_manifest}" commit)
set(_series_file "${CMAKE_CURRENT_SOURCE_DIR}/patches/winuae/series.json")
set_property(DIRECTORY APPEND PROPERTY CMAKE_CONFIGURE_DEPENDS "${_series_file}")
file(READ "${_series_file}" _series)
string(JSON _series_patches GET "${_series}" patches)
string(JSON _patch_count LENGTH "${_series_patches}")
file(SHA256 "${_series_file}" _series_sha256)
add_library(p6_bridge STATIC bridge/caps.cpp bridge/config.cpp bridge/lifecycle.cpp bridge/frame_step.cpp bridge/video.cpp bridge/input.cpp bridge/pcm_out.cpp bridge/media.cpp bridge/memory.cpp)
add_dependencies(p6_bridge p6_codegen)
target_include_directories(p6_bridge PUBLIC bridge/include)
target_include_directories(p6_bridge PRIVATE
    "${CMAKE_CURRENT_SOURCE_DIR}/ports/portable" "${CMAKE_CURRENT_SOURCE_DIR}/ports/portable/media" ${P6_INCLUDE_DIRS})
add_executable(p6_headless adapters/headless/main.cpp $<TARGET_OBJECTS:p6_engine>)
target_link_libraries(p6_headless PRIVATE p6_bridge p6_portable)
target_compile_definitions(p6_headless PRIVATE
    P6_PROFILE="${P6_PROFILE}"
    P6_UPSTREAM_COMMIT="${_upstream_commit}"
    P6_PATCH_COUNT="${_patch_count}"
    P6_PATCH_SERIES_SHA256="${_series_sha256}")
# Native state (T15a). The container (ports/portable/state_io) is pure and has no engine in it; p6_state adds the chunk registry and the
# p6_state_* entry points over the bridge. It is a library apart from p6_bridge on purpose: a Waterbox guest takes its sources from
# p6_bridge, and a Waterbox snapshot is not this format (docs/adr/0009-state-format.md, point 7), so the guest does not carry it.
add_library(p6_state_io STATIC ports/portable/state_io/envelope.cpp)
target_include_directories(p6_state_io PUBLIC "${CMAKE_CURRENT_SOURCE_DIR}/ports/portable/state_io"
    PRIVATE "${CMAKE_CURRENT_SOURCE_DIR}/ports/portable/media" "${CMAKE_CURRENT_SOURCE_DIR}/bridge/include")
add_executable(p6_envelope_probe tests/state/envelope_probe.cpp)
target_link_libraries(p6_envelope_probe PRIVATE p6_state_io p6_portable)
add_executable(p6_archive_probe tests/state/archive_probe.cpp)
target_include_directories(p6_archive_probe PRIVATE "${CMAKE_CURRENT_SOURCE_DIR}/ports/portable/state_io")
target_compile_options(p6_archive_probe PRIVATE -UNDEBUG)
# The identity of a build, as the state envelope binds it (core_build_id): the upstream commit, the patch series, the recipe of the
# generated sources (manifests/codegen.json: GEN01 makes their bytes a function of it, the upstream files and the patches) and the
# bridge and portable sources. The compiler and its flags are not part of it until a determinism test across compilers needs them.
set(_codegen_file "${CMAKE_CURRENT_SOURCE_DIR}/manifests/codegen.json")
set_property(DIRECTORY APPEND PROPERTY CMAKE_CONFIGURE_DEPENDS "${_codegen_file}")
file(SHA256 "${_codegen_file}" _codegen_sha256)
file(GLOB_RECURSE _core_files CONFIGURE_DEPENDS
    "${CMAKE_CURRENT_SOURCE_DIR}/bridge/*.cpp" "${CMAKE_CURRENT_SOURCE_DIR}/bridge/*.h"
    "${CMAKE_CURRENT_SOURCE_DIR}/ports/portable/*.c" "${CMAKE_CURRENT_SOURCE_DIR}/ports/portable/*.cpp"
    "${CMAKE_CURRENT_SOURCE_DIR}/ports/portable/*.h" "${CMAKE_CURRENT_SOURCE_DIR}/ports/portable/*.inc")
list(FILTER _core_files EXCLUDE REGEX "/bridge/state_probe\\.cpp$")      # a test tool that lives in the bridge directory
list(SORT _core_files)
set_property(DIRECTORY APPEND PROPERTY CMAKE_CONFIGURE_DEPENDS ${_core_files})
set(_core_lines "")
foreach(_file IN LISTS _core_files)
    file(RELATIVE_PATH _relative "${CMAKE_CURRENT_SOURCE_DIR}" "${_file}")
    file(SHA256 "${_file}" _file_sha256)
    string(APPEND _core_lines "${_relative}:${_file_sha256}\n")
endforeach()
string(SHA256 _sources_sha256 "${_core_lines}")
add_library(p6_state STATIC bridge/state.cpp bridge/build_identity.cpp bridge/state_cpu.cpp)
add_dependencies(p6_state p6_codegen)
target_include_directories(p6_state PUBLIC bridge)
target_include_directories(p6_state PRIVATE
    "${CMAKE_CURRENT_SOURCE_DIR}/ports/portable" "${CMAKE_CURRENT_SOURCE_DIR}/ports/portable/media" ${P6_INCLUDE_DIRS})
target_link_libraries(p6_state PUBLIC p6_state_io p6_bridge)
set_source_files_properties(bridge/build_identity.cpp PROPERTIES COMPILE_DEFINITIONS
    "P6_PROFILE=\"${P6_PROFILE}\";P6_UPSTREAM_COMMIT=\"${_upstream_commit}\";P6_PATCH_SERIES_SHA256=\"${_series_sha256}\";P6_CODEGEN_SHA256=\"${_codegen_sha256}\";P6_SOURCES_SHA256=\"${_sources_sha256}\"")
# Libretro core and the minimal frontend that tests its contract (T16).
include(cmake/Libretro.cmake)
# Engine-level probe of the bridge lifecycle (tests/host/test_bridge_lifecycle.py runs it with a ROM).
add_executable(p6_lifecycle_probe tests/host/bridge_lifecycle_probe.cpp $<TARGET_OBJECTS:p6_engine>)
add_dependencies(p6_lifecycle_probe p6_codegen)
target_include_directories(p6_lifecycle_probe PRIVATE
    "${CMAKE_CURRENT_SOURCE_DIR}/ports/portable" ${P6_INCLUDE_DIRS})
target_compile_options(p6_lifecycle_probe PRIVATE -UNDEBUG)
target_link_libraries(p6_lifecycle_probe PRIVATE p6_bridge p6_portable)
# Identity, validation and registration of the media of p6_load (tests/media/test_media.py runs it with a ROM).
add_executable(p6_media_probe tests/media/media_probe.cpp $<TARGET_OBJECTS:p6_engine>)
add_dependencies(p6_media_probe p6_codegen)
target_include_directories(p6_media_probe PRIVATE
    "${CMAKE_CURRENT_SOURCE_DIR}/ports/portable" "${CMAKE_CURRENT_SOURCE_DIR}/ports/portable/media"
    "${CMAKE_CURRENT_SOURCE_DIR}/bridge" ${P6_INCLUDE_DIRS})
target_compile_options(p6_media_probe PRIVATE -UNDEBUG)
target_link_libraries(p6_media_probe PRIVATE p6_bridge p6_portable)
# p6_memory_regions and the drive light of a unit (tests/host/test_bridge_memory.py runs it with two ROMs and a disk).
add_executable(p6_memory_probe tests/host/bridge_memory_probe.cpp $<TARGET_OBJECTS:p6_engine>)
add_dependencies(p6_memory_probe p6_codegen)
target_include_directories(p6_memory_probe PRIVATE
    "${CMAKE_CURRENT_SOURCE_DIR}/ports/portable" "${CMAKE_CURRENT_SOURCE_DIR}/ports/portable/media" ${P6_INCLUDE_DIRS})
target_compile_options(p6_memory_probe PRIVATE -UNDEBUG)
target_link_libraries(p6_memory_probe PRIVATE p6_bridge p6_portable)
# MED01: the guest reads, sees the swap of and cannot write to a disk (tests/media/test_med01.py).
add_executable(p6_med01_probe tests/media/med01_probe.cpp $<TARGET_OBJECTS:p6_engine>)
add_dependencies(p6_med01_probe p6_codegen)
target_include_directories(p6_med01_probe PRIVATE
    "${CMAKE_CURRENT_SOURCE_DIR}/ports/portable" "${CMAKE_CURRENT_SOURCE_DIR}/ports/portable/media"
    "${CMAKE_CURRENT_SOURCE_DIR}/bridge" ${P6_INCLUDE_DIRS})
target_compile_options(p6_med01_probe PRIVATE -UNDEBUG)
target_link_libraries(p6_med01_probe PRIVATE p6_bridge p6_portable)
# LIFE01: many init/load/run/unload/shutdown cycles in one process (tests/lifecycle/test_cycles.py).
add_executable(p6_cycles_probe tests/lifecycle/cycles_probe.cpp $<TARGET_OBJECTS:p6_engine>)
add_dependencies(p6_cycles_probe p6_codegen)
target_include_directories(p6_cycles_probe PRIVATE
    "${CMAKE_CURRENT_SOURCE_DIR}/ports/portable" "${CMAKE_CURRENT_SOURCE_DIR}/ports/portable/media" ${P6_INCLUDE_DIRS})
target_compile_options(p6_cycles_probe PRIVATE -UNDEBUG)
target_link_libraries(p6_cycles_probe PRIVATE p6_bridge p6_portable)
# Deterministic mutation fuzzing of config, firmware and the logical-name store (tests/fuzz/test_fuzz.py).
add_executable(p6_fuzz_probe tests/fuzz/fuzz_probe.cpp $<TARGET_OBJECTS:p6_engine>)
add_dependencies(p6_fuzz_probe p6_codegen)
target_include_directories(p6_fuzz_probe PRIVATE "${CMAKE_CURRENT_SOURCE_DIR}/ports/portable" "${CMAKE_CURRENT_SOURCE_DIR}/ports/portable/media" ${P6_INCLUDE_DIRS})
target_compile_options(p6_fuzz_probe PRIVATE -UNDEBUG)
target_link_libraries(p6_fuzz_probe PRIVATE p6_bridge p6_portable)
# Reports how the bridge's A500 preferences differ from upstream's own (tests/host/test_a500_profile.py).
add_executable(p6_profile_probe tests/host/a500_profile_probe.cpp $<TARGET_OBJECTS:p6_engine>)
add_dependencies(p6_profile_probe p6_codegen)
target_include_directories(p6_profile_probe PRIVATE
    "${CMAKE_CURRENT_SOURCE_DIR}/ports/portable" "${CMAKE_CURRENT_SOURCE_DIR}/bridge" ${P6_INCLUDE_DIRS})
target_compile_options(p6_profile_probe PRIVATE -UNDEBUG)
target_link_libraries(p6_profile_probe PRIVATE p6_bridge p6_portable)
# State probe (T14b): one canonical digest per subsystem (tests/state/test_probe.py).
add_executable(p6_state_probe bridge/state_probe.cpp $<TARGET_OBJECTS:p6_engine>)
add_dependencies(p6_state_probe p6_codegen)
target_include_directories(p6_state_probe PRIVATE
    "${CMAKE_CURRENT_SOURCE_DIR}/ports/portable" "${CMAKE_CURRENT_SOURCE_DIR}/ports/portable/media" "${CMAKE_CURRENT_SOURCE_DIR}/bridge" ${P6_INCLUDE_DIRS})
target_compile_options(p6_state_probe PRIVATE -UNDEBUG)
target_link_libraries(p6_state_probe PRIVATE p6_state p6_bridge p6_portable)
if(P6_UNIT_TRACE)
    # STEP01 harness (tests/step/test_step.py drives it). Only in trace builds: the profile never has the hook.
    add_executable(p6_step_probe tests/step/step_probe.cpp $<TARGET_OBJECTS:p6_engine>)
    add_dependencies(p6_step_probe p6_codegen)
    target_include_directories(p6_step_probe PRIVATE
        "${CMAKE_CURRENT_SOURCE_DIR}/ports/portable" "${CMAKE_CURRENT_SOURCE_DIR}/bridge" ${P6_INCLUDE_DIRS})
    target_compile_options(p6_step_probe PRIVATE -UNDEBUG)
    target_link_libraries(p6_step_probe PRIVATE p6_bridge p6_portable)
endif()
add_test(NAME headless-identity COMMAND p6_headless --identity)
set_tests_properties(headless-identity PROPERTIES LABELS "headless;link" TIMEOUT 30
    PASS_REGULAR_EXPRESSION "core: puae6-headless.*profile: ${P6_PROFILE}.*machines: A500.*abi: 1.*boot: micro-ROM programs only")
add_test(NAME headless-rejects-arguments COMMAND p6_headless --boot)
set_tests_properties(headless-rejects-arguments PROPERTIES LABELS "headless;link" TIMEOUT 30 WILL_FAIL FALSE
    PASS_REGULAR_EXPRESSION "usage:")
