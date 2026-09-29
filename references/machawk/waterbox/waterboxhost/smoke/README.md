# WaterboxHost macOS ARM64 smoke test

This builds an AArch64 guest against Masterjun's Waterbox musl at commit
`22440a465d6f7d3241e73bdf990c2b8026022625`, plus a native Darwin ARM64 C
harness. Dirty-page detection is enabled: a write fault on one 16 KiB Darwin
host page snapshots and dirties its four 4 KiB Waterbox guest pages.

The smoke linker script pads every ELF load segment to a 16 KiB boundary. This
does not change Waterbox's 4 KiB guest page model; it gives each Darwin host
page a single guest protection. WaterboxHost reports and stops on mixed guest
protections within one host page instead of widening native permissions.

Requirements: Apple Silicon macOS, Rust nightly (or a Rust compiler invoked
with `RUSTC_BOOTSTRAP=1`), Apple Clang, and Docker. Docker is used only to
build the Linux/musl guest; WaterboxHost and the harness run natively on macOS.

```sh
make
cd ../target/debug
./waterbox-smoke smoke.wbx
```
