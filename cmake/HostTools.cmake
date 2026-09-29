include_guard(GLOBAL)
set(_P6_HOSTTOOLS_MODULE_DIR "${CMAKE_CURRENT_LIST_DIR}")

function(p6_add_host_codegen TARGET_NAME)
    if(TARGET "${TARGET_NAME}")
        message(FATAL_ERROR "Host codegen target already exists: ${TARGET_NAME}")
    endif()

    find_package(Python3 REQUIRED COMPONENTS Interpreter)
    find_program(P6_HOST_CC NAMES clang gcc NO_CMAKE_FIND_ROOT_PATH)
    find_program(P6_HOST_CXX NAMES clang++ g++ NO_CMAKE_FIND_ROOT_PATH)
    if(NOT P6_HOST_CC OR NOT P6_HOST_CXX)
        message(FATAL_ERROR "Host C/C++ compilers not found; set P6_HOST_CC and P6_HOST_CXX")
    endif()

    set(_root "${_P6_HOSTTOOLS_MODULE_DIR}/..")
    get_filename_component(_root "${_root}" ABSOLUTE)
    set(_manifest "${_root}/manifests/source-manifest.json")
    file(READ "${_manifest}" _manifest_json)
    set(_all_units)
    foreach(_kind IN ITEMS upstream_units generated_units)
        string(JSON _count LENGTH "${_manifest_json}" selection "${_kind}")
        set(_sources)
        if(_count GREATER 0)
            math(EXPR _last "${_count} - 1")
            foreach(_index RANGE 0 ${_last})
                string(JSON _unit GET "${_manifest_json}" selection "${_kind}" ${_index})
                if(IS_ABSOLUTE "${_unit}" OR "${_unit}" MATCHES "(^|/)\.\.(/|$)"
                        OR "${_unit}" MATCHES "\\\\")
                    message(FATAL_ERROR "Unsafe relative path in ${_kind}: ${_unit}")
                endif()
                if(_unit IN_LIST _all_units)
                    message(FATAL_ERROR "Duplicate source path across manifest selections: ${_unit}")
                endif()
                list(APPEND _all_units "${_unit}")
                if(_kind STREQUAL "upstream_units")
                    list(APPEND _sources "${_root}/build/upstream/${_unit}")
                else()
                    list(APPEND _sources "${_root}/build/generated/${_unit}")
                endif()
            endforeach()
        endif()
        if(_kind STREQUAL "upstream_units")
            set(P6_UPSTREAM_SOURCES "${_sources}")
        else()
            set(P6_GENERATED_SOURCES "${_sources}")
        endif()
    endforeach()
    set_source_files_properties(${P6_UPSTREAM_SOURCES} ${P6_GENERATED_SOURCES}
        PROPERTIES GENERATED TRUE)

    set(P6_GENERATED_DIR "${_root}/build/generated")
    set(P6_UPSTREAM_DIR "${_root}/build/upstream")
    set(P6_INCLUDE_DIRS
        "${P6_GENERATED_DIR}"
        "${P6_UPSTREAM_DIR}/include"
        "${P6_UPSTREAM_DIR}")
    set(P6_GENERATED_DIR "${P6_GENERATED_DIR}" PARENT_SCOPE)
    set(P6_UPSTREAM_DIR "${P6_UPSTREAM_DIR}" PARENT_SCOPE)
    set(P6_INCLUDE_DIRS "${P6_INCLUDE_DIRS}" PARENT_SCOPE)
    set(P6_UPSTREAM_SOURCES "${P6_UPSTREAM_SOURCES}" PARENT_SCOPE)
    set(P6_GENERATED_SOURCES "${P6_GENERATED_SOURCES}" PARENT_SCOPE)

    # Ninja removes generated source files on clean. Remove the whole derived
    # trees too: the hash-closed publisher must never accept a partial cache.
    # Native presets share this cache; build/clean them sequentially.
    set_property(DIRECTORY APPEND PROPERTY ADDITIONAL_CLEAN_FILES
        "${P6_UPSTREAM_DIR}" "${P6_GENERATED_DIR}")

    # Callers order their compilation after this target with add_dependencies.
    # Do not declare individual BYPRODUCTS: Ninja would create empty destination
    # directories before the atomic publisher, which correctly rejects them.
    add_custom_target("${TARGET_NAME}"
        COMMAND "${Python3_EXECUTABLE}" "${_root}/tools/generate_cpu.py"
                --output build/generated --cc "${P6_HOST_CC}" --cxx "${P6_HOST_CXX}"
        WORKING_DIRECTORY "${_root}"
        COMMENT "Generate deterministic PUAE host sources"
        VERBATIM)
endfunction()
