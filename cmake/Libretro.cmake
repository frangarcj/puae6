# Libretro core and its reference frontend (T16).
#
# puae6_libretro is the libretro adapter over the native ABI: the engine objects, the portable services and the bridge in one shared
# library that exports only the retro_* functions (adapters/libretro/exports.txt, exports.map). p6_libretro_host is the minimal
# frontend of tests/libretro-host: it dlopens the core and enforces the contract on every retro_run. Neither depends on a window,
# an audio device or any frontend library.
set(_libretro_include "${CMAKE_CURRENT_SOURCE_DIR}/references/libretro-api/include")
string(SUBSTRING "${_upstream_commit}" 0 12 _upstream_short)
set(_libretro_version "0.1.0-winuae-${_upstream_short}-p${_patch_count}")

add_library(puae6_libretro SHARED adapters/libretro/libretro_core.cpp adapters/libretro/config_import.cpp adapters/libretro/cd_session.cpp $<TARGET_OBJECTS:p6_engine>)
add_dependencies(puae6_libretro p6_codegen)
set_source_files_properties(adapters/libretro/cd_session.cpp PROPERTIES INCLUDE_DIRECTORIES
    "${CMAKE_CURRENT_SOURCE_DIR}/ports/portable;${CMAKE_CURRENT_SOURCE_DIR}/bridge;${P6_INCLUDE_DIRS}")
# The portable SHA-256 is included as "sha256.h", like the bridge does; options_generated.h (tools/generate_options.py) sits next to the sources.
target_include_directories(puae6_libretro PRIVATE "${_libretro_include}" "${CMAKE_CURRENT_SOURCE_DIR}/ports/portable/media")
# p6_state is what makes the image announce P6_CAP_STATE (T15, T18): the core links it, offers states, and falls back to serialize_size 0 without it.
target_link_libraries(puae6_libretro PRIVATE p6_state p6_bridge p6_portable p6_cd_images)
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

# T66o1: native CD owners are prepared independently of the announced formats.
# The same derived TU is compiled four times with target-local parser flags.
set(_cd_metadata "${P6_UPSTREAM_DIR}/blkdev_cdimage.cpp")
set_source_files_properties("${_cd_metadata}" PROPERTIES GENERATED TRUE)
foreach(_kind IN ITEMS cue ccd nrg mds)
    string(TOUPPER "${_kind}" _upper)
    add_library(p6_cd_metadata_${_kind} STATIC EXCLUDE_FROM_ALL "${_cd_metadata}")
    add_dependencies(p6_cd_metadata_${_kind} p6_codegen)
    target_include_directories(p6_cd_metadata_${_kind} PRIVATE "${CMAKE_CURRENT_SOURCE_DIR}/ports/portable/media")
    target_compile_definitions(p6_cd_metadata_${_kind} PRIVATE P6_${_upper}_METADATA)
    set_target_properties(p6_cd_metadata_${_kind} PROPERTIES POSITION_INDEPENDENT_CODE ON CXX_VISIBILITY_PRESET hidden)
endforeach()
add_library(p6_cd_images STATIC EXCLUDE_FROM_ALL adapters/libretro/cd_image.cpp
    adapters/libretro/cd_cue.cpp adapters/libretro/cd_ccd.cpp adapters/libretro/cd_nrg.cpp adapters/libretro/cd_mds.cpp ports/portable/media/cd_wave.cpp)
target_include_directories(p6_cd_images PUBLIC "${CMAKE_CURRENT_SOURCE_DIR}/adapters/libretro" "${CMAKE_CURRENT_SOURCE_DIR}/ports/portable/media"
    PRIVATE "${_libretro_include}" "${CMAKE_CURRENT_SOURCE_DIR}/ports/portable" ${P6_INCLUDE_DIRS})
target_link_libraries(p6_cd_images PRIVATE p6_cd_metadata_cue p6_cd_metadata_ccd p6_cd_metadata_nrg p6_cd_metadata_mds p6_portable)
set_target_properties(p6_cd_images PROPERTIES POSITION_INDEPENDENT_CODE ON CXX_VISIBILITY_PRESET hidden)
