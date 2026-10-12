# Baseline T33-2026-10-12-b

Estado: **ORIENTATIVE**. ORIENTATIVE: the Mac is used by someone while the benchmark runs; no dedicated runner exists (plan/runners.json). Nothing here is a hard threshold.

- Commit `bcbd7a2b05e8` (con cambios sin commitear), Apple M1 Pro (6P+2E, 16 GiB), macOS 26.7.1 25G241, Apple clang version 21.0.0 (clang-2100.1.1.101).
- Native: Release `-O2`; guest Waterbox `-O2`; perfil `deterministic-a500`; 142 parches, serie `23c5ed9f8a54`.

## a500-ocs-busy-pal

A500 OCS: every DMA channel on, a blit in flight under contention, the disk motor running (corpus ocs-blitter-dma-pal)

Unidad de vídeo: 19.968 ms (cadencia del motor; nominal 20.00 ms). Mismo tick maestro al final de la ventana en todos los runners: **True**.

| Runner | reps | unidades | p50 ms | p95 ms | p99 ms | p95/unidad | margen p95 | dispersion entre runs | ruidosos |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| libretro | 5 | 10000 | 4.012 | 4.281 | 4.734 | 21.4 % | 4.66x | 0.44 % | 0/5 |
| native | 5 | 10000 | 4.021 | 4.244 | 4.392 | 21.3 % | 4.70x | 0.39 % | 0/5 |
| native-norender | 5 | 10000 | 4.008 | 4.254 | 4.406 | 21.3 % | 4.69x | 0.28 % | 0/5 |
| waterbox-core-debug | 3 | 10000 | 9.327 | 10.068 | 10.560 | 50.4 % | 1.98x | 0.42 % | 0/3 |
| waterbox-core-release | 5 | 10000 | 4.496 | 4.829 | 5.044 | 24.2 % | 4.14x | 0.55 % | 0/5 |
| waterbox-guest-debug-runtime | 3 | 10000 | 9.304 | 10.555 | 16.682 | 52.9 % | 1.89x | 2.16 % | 0/3 |
| waterbox-guest-release-runtime | 5 | 10000 | 5.950 | 6.303 | 6.523 | 31.6 % | 3.17x | 0.39 % | 0/5 |
| waterbox-guest-release-runtime-evict | 3 | 10000 | 8.616 | 9.267 | 9.813 | 46.4 % | 2.15x | 0.56 % | 0/3 |

Guardar/cargar (mediana de p50 de cada run):

| Runner | bytes | save p50 ms | load p50 ms | 1.ª unidad tras load ms |
| --- | --- | --- | --- | --- |
| libretro | 3674381 | 5.63 | 6.25 | 4.115 |
| native | 1069032 | 5.61 | 5.36 | 4.092 |
| waterbox-core-debug | 9204169 | 12.51 | 12.18 | 9.650 |
| waterbox-core-release | 9204169 | 9.21 | 7.87 | 4.701 |
| waterbox-guest-debug-runtime | 9203892 | 6.44 | 7.04 | 9.430 |
| waterbox-guest-release-runtime | 9203892 | 3.25 | 3.14 | 6.023 |
| waterbox-guest-release-runtime-evict | 9203892 | 5.67 | 5.29 | 8.703 |

Desglose de una unidad (medianas de runs de procesos distintos: las diferencias son estimaciones):

| Concepto | ms |
| --- | --- |
| unit_ms | 19.968 |
| engine_ms (native, render 0) | 4.008 |
| output_production_ms (native render 3 - render 0) | 0.013 |
| libretro_adapter_ms (libretro - native) | -0.009 |
| waterbox-guest-release-runtime: Activate / p6w_frame_advance / copia (harness Debug) / Deactivate | 0.000 / 4.343 / 1.600 / 0.000 |
| waterbox-guest-debug-runtime: Activate / p6w_frame_advance / copia (harness Debug) / Deactivate | 2.799 / 4.628 / 1.592 / 0.263 |
| waterbox_core_release_ms | 4.496 |
| core_release_over_native_ms | 0.475 |
| wrapper_ms (core release - guest call, release runtime) | 0.152 |

Coste principal por runner: native: engine; libretro: engine; waterbox-core-release: engine (p6w_frame_advance inside the sandbox); waterbox-guest-debug-runtime: engine (p6w_frame_advance inside the sandbox); waterbox-guest-release-runtime: engine (p6w_frame_advance inside the sandbox).

## a1200-aga-planes8-pal

A1200 AGA (68EC020 cycle exact): eight bitplanes fetched 64 bits at a time (corpus aga-planes8-fmode3-pal)

Unidad de vídeo: 19.968 ms (cadencia del motor; nominal 20.00 ms). Mismo tick maestro al final de la ventana en todos los runners: **True**.

| Runner | reps | unidades | p50 ms | p95 ms | p99 ms | p95/unidad | margen p95 | dispersion entre runs | ruidosos |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| libretro | 5 | 3000 | 5.052 | 7.198 | 11.794 | 36.0 % | 2.77x | 2.96 % | 1/5 |
| native | 5 | 3000 | 5.062 | 5.537 | 10.626 | 27.7 % | 3.61x | 2.35 % | 1/5 |
| native-norender | 5 | 3000 | 5.043 | 5.723 | 9.410 | 28.7 % | 3.49x | 2.52 % | 0/5 |
| waterbox-core-release | 5 | 3000 | 5.653 | 5.964 | 6.207 | 29.9 % | 3.35x | 1.18 % | 0/5 |
| waterbox-guest-debug-runtime | 3 | 3000 | 10.455 | 13.444 | 18.919 | 67.3 % | 1.49x | 3.42 % | 0/3 |
| waterbox-guest-release-runtime | 5 | 3000 | 7.074 | 7.432 | 7.621 | 37.2 % | 2.69x | 0.81 % | 0/5 |

Guardar/cargar (mediana de p50 de cada run):

| Runner | bytes | save p50 ms | load p50 ms | 1.ª unidad tras load ms |
| --- | --- | --- | --- | --- |
| libretro | 5956584 | 18.06 | 20.64 | 5.128 |
| native | 3185481 | 18.01 | 19.64 | 5.136 |
| waterbox-core-release | 10236361 | 10.01 | 8.53 | 5.742 |
| waterbox-guest-debug-runtime | 10236084 | 6.75 | 7.16 | 10.585 |
| waterbox-guest-release-runtime | 10236084 | 3.19 | 3.15 | 7.194 |

Desglose de una unidad (medianas de runs de procesos distintos: las diferencias son estimaciones):

| Concepto | ms |
| --- | --- |
| unit_ms | 19.968 |
| engine_ms (native, render 0) | 5.043 |
| output_production_ms (native render 3 - render 0) | 0.019 |
| libretro_adapter_ms (libretro - native) | -0.009 |
| waterbox-guest-release-runtime: Activate / p6w_frame_advance / copia (harness Debug) / Deactivate | 0.000 / 5.470 / 1.600 / 0.000 |
| waterbox-guest-debug-runtime: Activate / p6w_frame_advance / copia (harness Debug) / Deactivate | 2.795 / 5.783 / 1.592 / 0.253 |
| waterbox_core_release_ms | 5.653 |
| core_release_over_native_ms | 0.591 |
| wrapper_ms (core release - guest call, release runtime) | 0.183 |

Coste principal por runner: native: engine; libretro: engine; waterbox-core-release: engine (p6w_frame_advance inside the sandbox); waterbox-guest-debug-runtime: engine (p6w_frame_advance inside the sandbox); waterbox-guest-release-runtime: engine (p6w_frame_advance inside the sandbox).

