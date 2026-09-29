# Managed WaterboxHost smoke test

This .NET 8 ARM64 harness loads `libwaterboxhost.dylib` through BizHawk's
`DynamicLibraryImportResolver`, generates the `WaterboxHostNative` proxy with
`BizInvoker`, and runs the AArch64 `smoke.wbx` guest. It verifies a guest export
and a save/load cycle without loading BizHawk.Emulation.Cores, Snes9x, EmuHawk,
SDL, or any UI.

From this directory:

```sh
dotnet run
```

The build invokes the native smoke Makefile and copies the dylib and guest next
to the managed executable.

The Darwin AArch64 ABI and memory-model invariants exercised by this harness
are documented in [../DARWIN_ARM64.md](../DARWIN_ARM64.md).
