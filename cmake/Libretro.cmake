# Libretro core and its reference frontend (T16).
#
# puae6_libretro is the libretro adapter over the native ABI: the engine objects, the portable services and the bridge in one shared
# library that exports only the retro_* functions (adapters/libretro/exports.txt, exports.map). p6_libretro_host is the minimal
# frontend of tests/libretro-host: it dlopens the core and enforces the contract on every retro_run. Neither depends on a window,
# an audio device or any frontend library.
set(_libretro_include "${CMAKE_CURRENT_SOURCE_DIR}/references/libretro-api/include")
string(SUBSTRING "${_upstream_commit}" 0 12 _upstream_short)
set(_libretro_version "0.1.0-winuae-${_upstream_short}-p${_patch_count}")

add_library(puae6_libretro SHARED adapters/libretro/libretro_core.cpp $<TARGET_OBJECTS:p6_engine>)
add_dependencies(puae6_libretro p6_codegen)
# The portable SHA-256 is included as "sha256.h", like the bridge does; options_generated.h (tools/generate_options.py) sits next to the sources.
target_include_directories(puae6_libretro PRIVATE "${_libretro_include}" "${CMAKE_CURRENT_SOURCE_DIR}/ports/portable/media")
# p6_state is what makes the image announce P6_CAP_STATE (T15, T18): the core links it, offers states, and falls back to serialize_size 0 without it.
target_link_libraries(puae6_libretro PRIVATE p6_state p6_bridge p6_portable)
target_compile_definitions(puae6_libretro PRIVATE P6_LIBRETRO_VERSION="${_libretro_version}")
set_target_properties(puae6_libretro PROPERTIES PREFIX "" CXX_VISIBILITY_PRESET hidden VISIBILITY_INLINES_HIDDEN ON)
if(APPLE)
    target_link_options(puae6_libretro PRIVATE "-Wl,-exported_symbols_list,${CMAKE_CURRENT_SOURCE_DIR}/adapters/libretro/exports.txt" "-Wl,-dead_strip")
else()
    # A shared object needs position independent objects; the static libraries and the engine objects are built without them.
    set_target_properties(p6_engine p6_bridge p6_portable p6_state p6_state_io PROPERTIES POSITION_INDEPENDENT_CODE ON)
    target_link_options(puae6_libretro PRIVATE "-Wl,--version-script=${CMAKE_CURRENT_SOURCE_DIR}/adapters/libretro/exports.map")
endif()

add_executable(p6_libretro_host tests/libretro-host/host.cpp)
add_dependencies(p6_libretro_host puae6_libretro)
target_include_directories(p6_libretro_host PRIVATE "${_libretro_include}")
target_link_libraries(p6_libretro_host PRIVATE ${CMAKE_DL_LIBS})
