"""Declared portability rows, execution methods and excluded platforms.

Actual gates execute the corpus; golden files contain only expected regression hashes.
"""

DATE = "2026-10-10"

# Optimization / instrumentation variants of a build. O0 is the configuration of the test suites (Debug), O2 the -O2 -g build with the
# assertions kept (the flags of tests/build_support.py OPTIMIZED_FLAGS), san the ASan+UBSan build of SAFE01 (P6_SANITIZERS=ON, Debug).
OPTS = {
    "O0": {"build_type": "Debug", "flags": "", "sanitizers": False, "label": "-O0 (Debug)"},
    "O2": {"build_type": "RelWithDebInfo", "flags": "-O2 -g", "sanitizers": False, "label": "-O2 -g, assertions on"},
    "san": {"build_type": "Debug", "flags": "", "sanitizers": True, "label": "-O0 + ASan/UBSan"},
    # Audit builds (not part of the closing matrix): the same -O2 build with the two flags that change what the compiler may assume about
    # aliasing and signed overflow, to see whether the corpus depends on either; and a Debug build with the warnings of the audit.
    "O2nsa": {"build_type": "RelWithDebInfo", "flags": "-O2 -g -fno-strict-aliasing", "sanitizers": False, "label": "-O2 -g -fno-strict-aliasing"},
    "O2wrap": {"build_type": "RelWithDebInfo", "flags": "-O2 -g -fwrapv", "sanitizers": False, "label": "-O2 -g -fwrapv"},
    "warn": {"build_type": "Debug", "sanitizers": False, "label": "-O0 + audit warnings",
             "flags": "-Wall -Wextra -Wshift-count-negative -Wshift-count-overflow -Wshift-negative-value -Wcast-align -Wstrict-aliasing "
                      "-Wundef -Wno-unused-parameter -Wno-sign-compare"},
    "ubsan": {"build_type": "Debug", "flags": "-fsanitize=undefined -fno-sanitize-recover=all -fno-omit-frame-pointer", "sanitizers": False,
              "label": "-O0 + UBSan (no ASan)"},
}

# Executable rows. `runner` is the id of the runner in plan/runners.json. `native_hardware` says whether the ISA is the one of the machine
# that executes it: the x86_64 rows are translated (Docker Desktop linux/amd64 on an Apple silicon Mac) and are NOT x86_64 hardware.
ROWS = {
    "macos-arm64-appleclang": {
        "kind": "native", "runner": "native-macos-arm64", "os": "macOS 26.7", "isa": "arm64", "compiler": "Apple Clang",
        "toolchain": "cmake/toolchains/macos-appleclang.cmake", "native_hardware": True,
        "execution": "native on the build machine (Apple silicon, 16 KiB pages)",
    },
    "linux-arm64-clang": {
        "kind": "docker", "runner": "linux-arm64", "os": "Ubuntu 24.04 (container)", "isa": "arm64", "compiler": "Clang",
        "toolchain": "cmake/toolchains/linux-clang.cmake", "platform": "linux/arm64", "image": "puae6-portability:arm64",
        "native_hardware": True, "execution": "container on the Docker Desktop arm64 VM of a macOS arm64 host (arm64 code on arm64 CPU)",
    },
    "linux-arm64-gcc": {
        "kind": "docker", "runner": "linux-arm64", "os": "Ubuntu 24.04 (container)", "isa": "arm64", "compiler": "GCC",
        "toolchain": "cmake/toolchains/linux-gcc.cmake", "platform": "linux/arm64", "image": "puae6-portability:arm64",
        "native_hardware": True, "execution": "container on the Docker Desktop arm64 VM of a macOS arm64 host (arm64 code on arm64 CPU)",
    },
    "linux-x86_64-clang": {
        "kind": "docker", "runner": "linux-x86_64", "os": "Ubuntu 24.04 (container)", "isa": "x86_64", "compiler": "Clang",
        "toolchain": "cmake/toolchains/linux-clang.cmake", "platform": "linux/amd64", "image": "puae6-portability:amd64",
        "native_hardware": False, "execution": "x86_64 translated in Docker Desktop on macOS arm64 (not x86_64 hardware)",
    },
    "linux-x86_64-gcc": {
        "kind": "docker", "runner": "linux-x86_64", "os": "Ubuntu 24.04 (container)", "isa": "x86_64", "compiler": "GCC",
        "toolchain": "cmake/toolchains/linux-gcc.cmake", "platform": "linux/amd64", "image": "puae6-portability:amd64",
        "native_hardware": False, "execution": "x86_64 translated in Docker Desktop on macOS arm64 (not x86_64 hardware)",
    },
}

# The row that every other row is compared with (the machine the project is developed on).
REFERENCE = ("macos-arm64-appleclang", "O0")

# Rows OUT OF THE DECLARED SCOPE. Scope change decided by the user on 2026-10-10: the only hardware available is a Mac, so the declared matrix is
# macOS ARM64 (native) plus Linux ARM64 and Linux x86_64 in Docker (the latter with --platform linux/amd64, translated), each with Clang and GCC,
# plus the ARM64 Waterbox guest on MacHawk Darwin. These rows are not "pending a runner": they are not announced, have no evidence and
# no support is claimed. Re-adding one needs a new scope decision and a runner that executes it.
SCOPE_DECISION = ("2026-10-10, user decision: the declared matrix is the Mac (native) and Docker Linux ARM64/x86_64 with Clang and GCC; "
                  "there is no Windows hardware or runner")
OUT_OF_SCOPE = {
    "windows-x86_64": {"runner": "windows-x86_64", "os": "Windows", "isa": "x86_64", "kind": "native build + run",
                       "reason": "no Windows hardware or runner; out of the declared scope by user decision"},
    "waterbox-linux-x86_64": {"runner": "linux-x86_64", "os": "Linux", "isa": "x86_64", "kind": "Waterbox runtime + x86_64 guest",
                              "reason": "outside the declared scope by user decision (no x86_64 Waterbox runtime or guest toolchain is announced; "
                                        "the ARM64 guest of the Darwin row does not run on it)"},
    "waterbox-windows-x86_64": {"runner": "windows-x86_64", "os": "Windows", "isa": "x86_64", "kind": "Waterbox runtime + x86_64 guest",
                                "reason": "no Windows hardware or runner; out of the declared scope by user decision"},
    "macos-x86_64": {"runner": "macos-x86_64", "os": "macOS", "isa": "x86_64", "kind": "native build + run",
                     "reason": "never part of the declared matrix (no Intel machine; Rosetta was not used as a runner)"},
}

# Waterbox execution belongs to tests/waterbox-smoke.
WATERBOX_ROW = "waterbox-darwin-arm64"

# The (row, opt) pairs of the closing run, and the ones that carry the sanitizers.
OPT_ORDER = ("O0", "O2")
SANITIZER_OPT = {"macos-arm64-appleclang": "san", "linux-arm64-clang": "san", "linux-arm64-gcc": "san",
                 "linux-x86_64-clang": "san", "linux-x86_64-gcc": "san"}
