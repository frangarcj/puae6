# Darwin AArch64 invariants

These are design constraints for the native WaterboxHost port, not transient
details of the smoke harnesses:

- `x28` carries the active Waterbox context and guest TLS state.
- `x18` remains reserved for Darwin. Guest code must not use it as a general
  register, and the fault handler must not try to reconstruct it.
- `TPIDR_EL0` is maintained only as a compatibility mirror for existing guest
  code. New host code must not depend on it.
- Waterbox's logical page size and file format remain 4 KiB.
- Darwin memory protection operates at the physical host page size (16 KiB on
  current Apple Silicon), grouping four logical Waterbox pages.
- ELF and emulibc mappings are laid out in host-page-compatible 16 KiB groups;
  incompatible guest protections within a group are an error.
- The emulated syscall surface supports `openat(AT_FDCWD, ...)`.

## Runtime and packaging contract

- `WaterboxHostPlatform.NativeLibraryName` selects
  `libwaterboxhost.dylib` on macOS. The dynamic library stays beside the
  managed executable; no fallback search path is used.
- `WaterboxCore.PreInit` resolves guests from `PathUtils.DllDirectoryPath`.
  Production packages therefore place `hatari.wbx` and other Waterbox guests
  in the normal `dll` directory.
- Executable thunks use `MAP_JIT`, alternate write/execute access with
  `pthread_jit_write_protect_np`, and invalidate the AArch64 instruction cache
  after emission. Write protection is re-enabled before execution; this path
  does not rely on an ordinary RWX allocation.
- Dirty tracking handles the first Darwin write fault at host-page granularity.
  A fault snapshots and marks all four 4 KiB Waterbox pages covered by the
  16 KiB host page before restoring write access.

## Hatari guest inputs

The reproducible guest build is pinned to:

- Hatari `a4d6b320626ef2f5e5564576683b1b74b7e2f712` from the official
  Framagit repository;
- Waterbox musl `22440a465d6f7d3241e73bdf990c2b8026022625`;
- libretro headers `09b55b683764d49ae1e640d7db5069931d6d2d40`;
- LLVM 18.1.8 for the C++ Waterbox runtime used by Snes9x regression tests.

The test firmware is EmuTOS 1.4 1024K. Its image is registered as
`AtariST+TOS` with SHA-1
`5C82272B04B9D0E85FB53B09EF28724649DAF01E`. The Invaders floppy fixture is
public domain. Download scripts pin the source archives by SHA-256 and keep all
downloaded or generated content under the ignored `waterboxhost/target` tree.

## Reproduction

Prerequisites are an Apple Silicon Mac, Xcode command-line tools, Docker,
Rust/Cargo, the .NET 8 runtime and an SDK capable of building `net8.0` and
`net48`. The first run needs network access for pinned source archives, NuGet
packages and the Ubuntu 24.04 build container.

From `waterbox/waterboxhost`:

```sh
./run-macos-smoke.sh
```

Set `DOTNET` to select a particular SDK executable. The suite first compiles
the production `BizHawk.Client.Common` graph without retargeting it, then runs
the native and managed WaterboxHost, Snes9x, Hatari, deterministic-state and
visual fixtures. The reviewed Hatari framebuffer hashes are stored in
`eto-hatari-boot-smoke/visual-fixtures/visual-fixtures.sha256`; actual PPMs are
always emitted under the ignored app output for inspection.

For the native host unit tests:

```sh
RUSTC_BOOTSTRAP=1 cargo test
```

## Upstream review boundaries

The production changes are deliberately separable from the spike harnesses:

- `waterboxhost/src`, AArch64 guest toolchain patches and link scripts provide
  the Darwin/16 KiB Waterbox substrate;
- `waterbox/hatari` plus the four Hatari C# files provide the core;
- the small Common/Bizware changes expose OS-neutral display configuration,
  external OpenGL contexts, native keyboard input, firmware and RomLoader
  discovery;
- directories ending in `-smoke`, `hatari-assets` and
  `run-macos-smoke.sh` are reproducible validation infrastructure.

The minimal Eto frontend intentionally has no WinForms, SDL, audio playback,
notarization or full Client.Common application lifecycle. Those are frontend
product concerns; the Hatari core itself exposes video, audio, input, media,
memory domains, registers, tracing, firmware loading and deterministic
savestates through standard BizHawk interfaces.
