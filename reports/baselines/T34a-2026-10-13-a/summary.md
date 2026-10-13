# Baseline T34a-2026-10-13-a

Estado: **ORIENTATIVE**. ORIENTATIVE: the Mac is used by someone while the benchmark runs; no dedicated runner exists (plan/runners.json). Nothing here is a hard threshold.

- Commit `dd4ae28ec2f0`, Apple M1 Pro (6P+2E, 16 GiB), macOS 26.7.1 25G241, Apple clang version 21.0.0 (clang-2100.1.1.101).
- Native: Release `-O2`; guest Waterbox `-O2`; perfil `deterministic-a500`; 143 parches, serie `9a8127bce151`.

## a500-ocs-busy-pal

A500 OCS: every DMA channel on, a blit in flight under contention, the disk motor running (corpus ocs-blitter-dma-pal)

Unidad de vídeo: 19.968 ms (cadencia del motor; nominal 20.00 ms). Mismo tick maestro al final de la ventana en todos los runners: **True**.

| Runner | reps | unidades | p50 ms | p95 ms | p99 ms | p95/unidad | margen p95 | dispersion entre runs | ruidosos |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| native | 3 | 10000 | 3.986 | 4.243 | 4.420 | 21.2 % | 4.71x | 0.80 % | 0/3 |
| native-norender | 3 | 10000 | 3.992 | 4.238 | 4.427 | 21.2 % | 4.71x | 0.42 % | 0/3 |
| waterbox-core-debug | 3 | 10000 | 8.604 | 13.658 | 19.461 | 68.4 % | 1.46x | 3.07 % | 1/3 |
| waterbox-core-release | 3 | 10000 | 4.364 | 4.654 | 4.870 | 23.3 % | 4.29x | 0.11 % | 0/3 |
| waterbox-guest-debug-runtime | 3 | 10000 | 9.247 | 9.873 | 10.318 | 49.4 % | 2.02x | 0.96 % | 0/3 |
| waterbox-guest-release-runtime | 3 | 10000 | 5.911 | 10.080 | 16.147 | 50.5 % | 1.98x | 2.62 % | 1/3 |
| waterbox-guest-release-runtime-evict | 3 | 10000 | 8.653 | 9.221 | 9.925 | 46.2 % | 2.17x | 1.03 % | 0/3 |

Guardar/cargar (mediana de p50 de cada run):

| Runner | bytes | save p50 ms | load p50 ms | 1.ª unidad tras load ms |
| --- | --- | --- | --- | --- |
| native | 1069032 | 5.64 | 5.32 | 4.023 |
| waterbox-core-debug | 9204169 | 12.91 | 12.48 | 8.606 |
| waterbox-core-release | 9204169 | 10.76 | 8.22 | 4.462 |
| waterbox-guest-debug-runtime | 9203892 | 6.08 | 7.08 | 9.467 |
| waterbox-guest-release-runtime | 9203892 | 3.71 | 3.24 | 6.032 |
| waterbox-guest-release-runtime-evict | 9203892 | 5.47 | 5.36 | 8.693 |

Desglose de una unidad (medianas de runs de procesos distintos: las diferencias son estimaciones):

| Concepto | ms |
| --- | --- |
| unit_ms | 19.968 |
| engine_ms (native, render 0) | 3.992 |
| output_production_ms (native render 3 - render 0) | -0.005 |
| waterbox-guest-release-runtime: Activate / p6w_frame_advance / copia (harness Debug) / Deactivate | 0.000 / 4.324 / 1.584 / 0.000 |
| waterbox-guest-debug-runtime: Activate / p6w_frame_advance / copia (harness Debug) / Deactivate | 2.800 / 4.591 / 1.586 / 0.256 |
| waterbox_core_release_ms | 4.364 |
| core_release_over_native_ms | 0.378 |
| wrapper_ms (core release - guest call, release runtime) | 0.039 |

Coste principal por runner: native: engine; libretro: engine; waterbox-core-release: engine (p6w_frame_advance inside the sandbox); waterbox-guest-debug-runtime: engine (p6w_frame_advance inside the sandbox); waterbox-guest-release-runtime: engine (p6w_frame_advance inside the sandbox).

## a600-ecs-copper-ntsc

A600 ECS NTSC: the Copper rewrites BPLCON3/DIWHIGH too (corpus ecs-ring-copper-ntsc)

Unidad de vídeo: 16.652 ms (cadencia del motor; nominal 16.67 ms). Mismo tick maestro al final de la ventana en todos los runners: **True**.

| Runner | reps | unidades | p50 ms | p95 ms | p99 ms | p95/unidad | margen p95 | dispersion entre runs | ruidosos |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| native | 3 | 3000 | 3.818 | 4.108 | 4.493 | 24.7 % | 4.05x | 1.66 % | 0/3 |
| native-norender | 3 | 3000 | 3.821 | 4.064 | 4.739 | 24.4 % | 4.10x | 0.29 % | 0/3 |
| waterbox-core-release | 3 | 3000 | 4.183 | 4.431 | 4.628 | 26.6 % | 3.76x | 0.75 % | 0/3 |
| waterbox-guest-debug-runtime | 3 | 3000 | 8.886 | 9.652 | 10.305 | 58.0 % | 1.73x | 0.74 % | 0/3 |
| waterbox-guest-release-runtime | 3 | 3000 | 5.487 | 5.761 | 5.953 | 34.6 % | 2.89x | 0.83 % | 0/3 |

Guardar/cargar (mediana de p50 de cada run):

| Runner | bytes | save p50 ms | load p50 ms | 1.ª unidad tras load ms |
| --- | --- | --- | --- | --- |
| native | 1094923 | 5.64 | 5.45 | 3.847 |
| waterbox-core-release | 8516041 | 8.46 | 7.61 | 4.258 |
| waterbox-guest-debug-runtime | 8515764 | 5.92 | 7.06 | 8.911 |
| waterbox-guest-release-runtime | 8515764 | 3.00 | 3.00 | 5.527 |

Desglose de una unidad (medianas de runs de procesos distintos: las diferencias son estimaciones):

| Concepto | ms |
| --- | --- |
| unit_ms | 16.652 |
| engine_ms (native, render 0) | 3.821 |
| output_production_ms (native render 3 - render 0) | -0.003 |
| waterbox-guest-release-runtime: Activate / p6w_frame_advance / copia (harness Debug) / Deactivate | 0.000 / 4.150 / 1.335 / 0.000 |
| waterbox-guest-debug-runtime: Activate / p6w_frame_advance / copia (harness Debug) / Deactivate | 2.812 / 4.458 / 1.343 / 0.249 |
| waterbox_core_release_ms | 4.183 |
| core_release_over_native_ms | 0.365 |
| wrapper_ms (core release - guest call, release runtime) | 0.033 |

Coste principal por runner: native: engine; libretro: engine; waterbox-core-release: engine (p6w_frame_advance inside the sandbox); waterbox-guest-debug-runtime: engine (p6w_frame_advance inside the sandbox); waterbox-guest-release-runtime: engine (p6w_frame_advance inside the sandbox).

## a1200-aga-planes8-pal

A1200 AGA (68EC020 cycle exact): eight bitplanes fetched 64 bits at a time (corpus aga-planes8-fmode3-pal)

Unidad de vídeo: 19.968 ms (cadencia del motor; nominal 20.00 ms). Mismo tick maestro al final de la ventana en todos los runners: **True**.

| Runner | reps | unidades | p50 ms | p95 ms | p99 ms | p95/unidad | margen p95 | dispersion entre runs | ruidosos |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| native | 3 | 3000 | 5.029 | 5.343 | 5.885 | 26.8 % | 3.74x | 0.99 % | 0/3 |
| native-norender | 3 | 3000 | 5.025 | 5.323 | 5.514 | 26.7 % | 3.75x | 0.50 % | 0/3 |
| waterbox-core-release | 3 | 3000 | 5.566 | 6.070 | 6.386 | 30.4 % | 3.29x | 1.06 % | 0/3 |
| waterbox-guest-debug-runtime | 3 | 3000 | 10.397 | 11.109 | 11.459 | 55.6 % | 1.80x | 0.40 % | 0/3 |
| waterbox-guest-release-runtime | 3 | 3000 | 7.062 | 7.501 | 8.032 | 37.6 % | 2.66x | 0.88 % | 0/3 |

Guardar/cargar (mediana de p50 de cada run):

| Runner | bytes | save p50 ms | load p50 ms | 1.ª unidad tras load ms |
| --- | --- | --- | --- | --- |
| native | 3185481 | 18.04 | 19.71 | 5.084 |
| waterbox-core-release | 10236361 | 10.40 | 8.78 | 5.675 |
| waterbox-guest-debug-runtime | 10236084 | 6.99 | 7.29 | 10.559 |
| waterbox-guest-release-runtime | 10236084 | 3.40 | 3.18 | 7.155 |

Desglose de una unidad (medianas de runs de procesos distintos: las diferencias son estimaciones):

| Concepto | ms |
| --- | --- |
| unit_ms | 19.968 |
| engine_ms (native, render 0) | 5.025 |
| output_production_ms (native render 3 - render 0) | 0.004 |
| waterbox-guest-release-runtime: Activate / p6w_frame_advance / copia (harness Debug) / Deactivate | 0.000 / 5.474 / 1.585 / 0.000 |
| waterbox-guest-debug-runtime: Activate / p6w_frame_advance / copia (harness Debug) / Deactivate | 2.799 / 5.749 / 1.585 / 0.249 |
| waterbox_core_release_ms | 5.566 |
| core_release_over_native_ms | 0.537 |
| wrapper_ms (core release - guest call, release runtime) | 0.093 |

Coste principal por runner: native: engine; libretro: engine; waterbox-core-release: engine (p6w_frame_advance inside the sandbox); waterbox-guest-debug-runtime: engine (p6w_frame_advance inside the sandbox); waterbox-guest-release-runtime: engine (p6w_frame_advance inside the sandbox).

## a1200-ks31-demo-pal

OPT-IN, A1200 Kickstart 3.1 + an AGA demo disk, read in place from P6_KICKSTART_A1200_ROM and P6_AGA_ADF (not copied, not hashed)

Unidad de vídeo: 20.032 ms (cadencia del motor; nominal 20.00 ms). Mismo tick maestro al final de la ventana en todos los runners: **True**.

| Runner | reps | unidades | p50 ms | p95 ms | p99 ms | p95/unidad | margen p95 | dispersion entre runs | ruidosos |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| native | 3 | 3000 | 6.499 | 10.761 | 16.490 | 53.7 % | 1.86x | 4.04 % | 1/3 |
| native-norender | 3 | 3000 | 6.384 | 7.296 | 7.637 | 36.4 % | 2.75x | 0.24 % | 0/3 |
| waterbox-core-release | 3 | 3000 | 6.932 | 10.730 | 15.878 | 53.6 % | 1.87x | 4.93 % | 1/3 |
| waterbox-guest-debug-runtime | 3 | 3000 | 12.006 | 13.186 | 13.766 | 65.8 % | 1.52x | 0.81 % | 0/3 |
| waterbox-guest-release-runtime | 3 | 3000 | 8.467 | 9.520 | 10.140 | 47.5 % | 2.10x | 0.37 % | 0/3 |

Guardar/cargar (mediana de p50 de cada run):

| Runner | bytes | save p50 ms | load p50 ms | 1.ª unidad tras load ms |
| --- | --- | --- | --- | --- |
| native | 2288543 | 11.99 | 12.20 | 6.432 |
| waterbox-core-release | 10301897 | 11.13 | 9.18 | 6.607 |
| waterbox-guest-debug-runtime | 10301620 | 6.73 | 7.15 | 11.631 |
| waterbox-guest-release-runtime | 10301620 | 3.77 | 3.22 | 8.088 |

Desglose de una unidad (medianas de runs de procesos distintos: las diferencias son estimaciones):

| Concepto | ms |
| --- | --- |
| unit_ms | 20.032 |
| engine_ms (native, render 0) | 6.384 |
| output_production_ms (native render 3 - render 0) | 0.116 |
| waterbox-guest-release-runtime: Activate / p6w_frame_advance / copia (harness Debug) / Deactivate | 0.000 / 6.874 / 1.591 / 0.000 |
| waterbox-guest-debug-runtime: Activate / p6w_frame_advance / copia (harness Debug) / Deactivate | 2.783 / 7.328 / 1.592 / 0.263 |
| waterbox_core_release_ms | 6.932 |
| core_release_over_native_ms | 0.433 |
| wrapper_ms (core release - guest call, release runtime) | 0.058 |

Coste principal por runner: native: engine; libretro: engine; waterbox-core-release: engine (p6w_frame_advance inside the sandbox); waterbox-guest-debug-runtime: engine (p6w_frame_advance inside the sandbox); waterbox-guest-release-runtime: engine (p6w_frame_advance inside the sandbox).

