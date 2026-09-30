# Sanitizer instrumentation of the native build (P6_SANITIZERS).
#
# ASan + UBSan on every native target, with -fno-sanitize-recover=all so that a finding stops the process
# instead of scrolling by. Nothing is silenced: there is no -w, no suppression list and no per-family opt-out.
# Findings of the engine are inventoried in reports/sanitizer-baseline.md and fixed by patches or documented.
#
# Leak detection (LSan) belongs to ASan where the platform supports it (Linux): it is switched on at run time with
# ASAN_OPTIONS=detect_leaks=1 by the tests. macOS on arm64 has no LSan, and the report says so instead of hiding it.
option(P6_SANITIZERS "Instrument native code with ASan and UBSan" OFF)

if(P6_SANITIZERS)
    add_compile_options(-fsanitize=address,undefined -fno-sanitize-recover=all -fno-omit-frame-pointer)
    add_link_options(-fsanitize=address,undefined -fno-sanitize-recover=all)
endif()
