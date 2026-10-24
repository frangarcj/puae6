# Verified offline dependency, shared by standalone probes and native CD owners.
include_guard(GLOBAL)
find_package(Python3 REQUIRED COMPONENTS Interpreter)
get_filename_component(_chd_root "${CMAKE_CURRENT_LIST_DIR}/.." ABSOLUTE)
execute_process(COMMAND "${Python3_EXECUTABLE}" "${_chd_root}/tools/import_chd.py"
    RESULT_VARIABLE _chd_verified OUTPUT_VARIABLE _chd_output ERROR_VARIABLE _chd_error)
if(NOT _chd_verified EQUAL 0)
    message(FATAL_ERROR "CHD source verification failed: ${_chd_output}${_chd_error}")
endif()
set(_chd "${CMAKE_CURRENT_BINARY_DIR}/p6-chd")
execute_process(COMMAND "${Python3_EXECUTABLE}" "${_chd_root}/tools/import_chd.py"
    --materialize "${_chd}" RESULT_VARIABLE _chd_materialized ERROR_VARIABLE _chd_error)
if(NOT _chd_materialized EQUAL 0)
    message(FATAL_ERROR "CHD derivation failed: ${_chd_error}")
endif()
add_custom_target(p6_chd_verify COMMAND "${Python3_EXECUTABLE}" "${_chd_root}/tools/import_chd.py" --materialize "${_chd}")
add_library(p6_chd STATIC EXCLUDE_FROM_ALL
    "${_chd}/src/libchdr_bitstream.c" "${_chd}/src/libchdr_cdrom.c"
    "${_chd}/src/libchdr_chd.c" "${_chd}/src/libchdr_flac.c" "${_chd}/src/libchdr_huffman.c"
    "${_chd}/deps/lzma/LzmaDec.c" "${_chd}/deps/lzma/LzmaEnc.c" "${_chd}/deps/lzma/LzFind.c"
    "${_chd}/deps/zlib/adler32.c" "${_chd}/deps/zlib/crc32.c" "${_chd}/deps/zlib/inflate.c"
    "${_chd}/deps/zlib/inffast.c" "${_chd}/deps/zlib/inftrees.c" "${_chd}/deps/zlib/zutil.c")
target_include_directories(p6_chd PUBLIC "${_chd}/include"
    PRIVATE "${_chd}/deps/lzma" "${_chd}/deps/zlib")
target_compile_definitions(p6_chd PRIVATE _7ZIP_ST DR_FLAC_NO_STDIO DR_FLAC_NO_OGG)
set_target_properties(p6_chd PROPERTIES C_STANDARD 99 POSITION_INDEPENDENT_CODE ON
    C_VISIBILITY_PRESET hidden)
add_dependencies(p6_chd p6_chd_verify)
if(CMAKE_CXX_COMPILER_LOADED)
    add_library(p6_chd_reader STATIC EXCLUDE_FROM_ALL "${_chd_root}/ports/portable/media/chd_reader.cpp"
        "${_chd_root}/ports/portable/media/cd_chd_metadata.cpp")
    target_link_libraries(p6_chd_reader PUBLIC p6_chd)
    target_include_directories(p6_chd_reader PUBLIC "${_chd_root}/ports/portable/media")
    target_compile_features(p6_chd_reader PUBLIC cxx_std_17)
    set_target_properties(p6_chd_reader PROPERTIES POSITION_INDEPENDENT_CODE ON CXX_VISIBILITY_PRESET hidden)
endif()
