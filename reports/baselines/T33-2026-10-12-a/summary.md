# Baseline T33-2026-10-12-a

Estado: **ORIENTATIVE**. ORIENTATIVE: the Mac is used by someone while the benchmark runs; no dedicated runner exists (plan/runners.json). Nothing here is a hard threshold.

- Commit `bcbd7a2b05e8` (con cambios sin commitear), Apple M1 Pro (6P+2E, 16 GiB), macOS 26.7.1 25G241, Apple clang version 21.0.0 (clang-2100.1.1.101).
- Native: Release `-O2`; guest Waterbox `-O2`; perfil `deterministic-a500`; 142 parches, serie `23c5ed9f8a54`.

## a500-ocs-busy-pal

A500 OCS: every DMA channel on, a blit in flight under contention, the disk motor running (corpus ocs-blitter-dma-pal)

Unidad de vídeo: 19.968 ms (cadencia del motor; nominal 20.00 ms). Mismo tick maestro al final de la ventana en todos los runners: **True**.

| Runner | reps | unidades | p50 ms | p95 ms | p99 ms | p95/unidad | margen p95 | dispersion entre runs | ruidosos |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| libretro | 5 | 10000 | 4.006 | 4.206 | 4.352 | 21.1 % | 4.75x | 0.18 % | 0/5 |
| native | 5 | 10000 | 3.999 | 4.220 | 4.371 | 21.1 % | 4.73x | 0.25 % | 0/5 |
| native-norender | 5 | 10000 | 4.001 | 4.221 | 4.383 | 21.1 % | 4.73x | 0.23 % | 0/5 |
| waterbox-core-debug | 3 | 10000 | 9.293 | 9.855 | 10.914 | 49.4 % | 2.03x | 0.97 % | 0/3 |
| waterbox-core-release | 5 | 10000 | 4.501 | 4.777 | 5.065 | 23.9 % | 4.18x | 0.39 % | 0/5 |
| waterbox-guest-debug-runtime | 3 | 10000 | 9.302 | 10.111 | 11.275 | 50.6 % | 1.97x | 0.66 % | 0/3 |
| waterbox-guest-release-runtime | 5 | 10000 | 5.932 | 6.256 | 6.797 | 31.3 % | 3.19x | 0.36 % | 1/5 |
| waterbox-guest-release-runtime-evict | 3 | 10000 | 8.576 | 9.186 | 10.653 | 46.0 % | 2.17x | 1.13 % | 0/3 |

Guardar/cargar (mediana de p50 de cada run):

| Runner | bytes | save p50 ms | load p50 ms | 1.ª unidad tras load ms |
| --- | --- | --- | --- | --- |
| libretro | 3674381 | 5.64 | 6.17 | 4.047 |
| native | 1069032 | 5.57 | 5.31 | 4.023 |
| waterbox-core-debug | 9204169 | 12.19 | 12.23 | 9.525 |
| waterbox-core-release | 9204169 | 8.83 | 7.81 | 4.675 |
| waterbox-guest-debug-runtime | 9203892 | 5.95 | 7.08 | 9.460 |
| waterbox-guest-release-runtime | 9203892 | 3.60 | 3.15 | 5.989 |
| waterbox-guest-release-runtime-evict | 9203892 | 5.93 | 5.43 | 8.749 |

Desglose de una unidad (medianas de runs de procesos distintos: las diferencias son estimaciones):

| Concepto | ms |
| --- | --- |
| unit_ms | 19.968 |
| engine_ms (native, render 0) | 4.001 |
| output_production_ms (native render 3 - render 0) | -0.002 |
| libretro_adapter_ms (libretro - native) | 0.007 |
| waterbox-guest-release-runtime: Activate / p6w_frame_advance / copia (harness Debug) / Deactivate | 0.000 / 4.339 / 1.587 / 0.000 |
| waterbox-guest-debug-runtime: Activate / p6w_frame_advance / copia (harness Debug) / Deactivate | 2.806 / 4.630 / 1.584 / 0.256 |
| waterbox_core_release_ms | 4.501 |
| core_release_over_native_ms | 0.502 |
| wrapper_ms (core release - guest call, release runtime) | 0.162 |

Coste principal por runner: native: engine; libretro: engine; waterbox-core-release: engine (p6w_frame_advance inside the sandbox); waterbox-guest-debug-runtime: engine (p6w_frame_advance inside the sandbox); waterbox-guest-release-runtime: engine (p6w_frame_advance inside the sandbox).

## a500-ocs-copper-ntsc

A500 OCS NTSC: the Copper rewrites the display registers every line (corpus ocs-ring-copper-ntsc)

Unidad de vídeo: 16.652 ms (cadencia del motor; nominal 16.67 ms). Mismo tick maestro al final de la ventana en todos los runners: **True**.

| Runner | reps | unidades | p50 ms | p95 ms | p99 ms | p95/unidad | margen p95 | dispersion entre runs | ruidosos |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| libretro | 5 | 3000 | 3.706 | 4.068 | 8.053 | 24.4 % | 4.09x | 1.83 % | 1/5 |
| native | 5 | 3000 | 3.702 | 3.892 | 4.022 | 23.4 % | 4.28x | 0.28 % | 0/5 |
| native-norender | 5 | 3000 | 3.699 | 3.923 | 5.695 | 23.6 % | 4.24x | 0.80 % | 0/5 |

Guardar/cargar (mediana de p50 de cada run):

| Runner | bytes | save p50 ms | load p50 ms | 1.ª unidad tras load ms |
| --- | --- | --- | --- | --- |
| libretro | 3674381 | 5.58 | 6.22 | 3.859 |
| native | 1068038 | 5.50 | 5.28 | 3.749 |

Desglose de una unidad (medianas de runs de procesos distintos: las diferencias son estimaciones):

| Concepto | ms |
| --- | --- |
| unit_ms | 16.652 |
| engine_ms (native, render 0) | 3.699 |
| output_production_ms (native render 3 - render 0) | 0.003 |
| libretro_adapter_ms (libretro - native) | 0.004 |

Coste principal por runner: native: engine; libretro: engine.

## a600-ecs-copper-ntsc

A600 ECS NTSC: the Copper rewrites BPLCON3/DIWHIGH too (corpus ecs-ring-copper-ntsc)

Unidad de vídeo: 16.652 ms (cadencia del motor; nominal 16.67 ms). Mismo tick maestro al final de la ventana en todos los runners: **True**.

| Runner | reps | unidades | p50 ms | p95 ms | p99 ms | p95/unidad | margen p95 | dispersion entre runs | ruidosos |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| libretro | 5 | 3000 | 3.820 | 4.060 | 4.191 | 24.4 % | 4.10x | 1.27 % | 0/5 |
| native | 5 | 3000 | 3.818 | 4.205 | 6.272 | 25.3 % | 3.96x | 1.62 % | 0/5 |
| native-norender | 5 | 3000 | 3.819 | 4.099 | 4.345 | 24.6 % | 4.06x | 1.61 % | 0/5 |
| waterbox-core-release | 5 | 3000 | 4.339 | 4.601 | 4.794 | 27.6 % | 3.62x | 1.20 % | 0/5 |
| waterbox-guest-debug-runtime | 3 | 3000 | 8.818 | 9.454 | 10.481 | 56.8 % | 1.76x | 1.23 % | 0/3 |
| waterbox-guest-release-runtime | 5 | 3000 | 5.540 | 5.933 | 6.250 | 35.6 % | 2.81x | 1.21 % | 0/5 |

Guardar/cargar (mediana de p50 de cada run):

| Runner | bytes | save p50 ms | load p50 ms | 1.ª unidad tras load ms |
| --- | --- | --- | --- | --- |
| libretro | 3674350 | 5.67 | 6.33 | 3.855 |
| native | 1094923 | 5.65 | 5.49 | 3.854 |
| waterbox-core-release | 8516041 | 8.90 | 7.64 | 4.421 |
| waterbox-guest-debug-runtime | 8515764 | 6.11 | 7.09 | 8.950 |
| waterbox-guest-release-runtime | 8515764 | 3.16 | 3.07 | 5.575 |

Desglose de una unidad (medianas de runs de procesos distintos: las diferencias son estimaciones):

| Concepto | ms |
| --- | --- |
| unit_ms | 16.652 |
| engine_ms (native, render 0) | 3.819 |
| output_production_ms (native render 3 - render 0) | -0.001 |
| libretro_adapter_ms (libretro - native) | 0.002 |
| waterbox-guest-release-runtime: Activate / p6w_frame_advance / copia (harness Debug) / Deactivate | 0.000 / 4.193 / 1.337 / 0.000 |
| waterbox-guest-debug-runtime: Activate / p6w_frame_advance / copia (harness Debug) / Deactivate | 2.784 / 4.442 / 1.335 / 0.241 |
| waterbox_core_release_ms | 4.339 |
| core_release_over_native_ms | 0.522 |
| wrapper_ms (core release - guest call, release runtime) | 0.146 |

Coste principal por runner: native: engine; libretro: engine; waterbox-core-release: engine (p6w_frame_advance inside the sandbox); waterbox-guest-debug-runtime: engine (p6w_frame_advance inside the sandbox); waterbox-guest-release-runtime: engine (p6w_frame_advance inside the sandbox).

## a1200-aga-planes8-pal

A1200 AGA (68EC020 cycle exact): eight bitplanes fetched 64 bits at a time (corpus aga-planes8-fmode3-pal)

Unidad de vídeo: 19.968 ms (cadencia del motor; nominal 20.00 ms). Mismo tick maestro al final de la ventana en todos los runners: **True**.

| Runner | reps | unidades | p50 ms | p95 ms | p99 ms | p95/unidad | margen p95 | dispersion entre runs | ruidosos |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| libretro | 5 | 3000 | 5.058 | 5.463 | 6.439 | 27.4 % | 3.66x | 2.04 % | 0/5 |
| native | 5 | 3000 | 5.056 | 5.329 | 5.631 | 26.7 % | 3.75x | 1.21 % | 0/5 |
| native-norender | 5 | 3000 | 5.048 | 5.384 | 5.990 | 27.0 % | 3.71x | 1.43 % | 0/5 |
| waterbox-core-release | 5 | 3000 | 5.654 | 6.101 | 6.739 | 30.6 % | 3.27x | 1.98 % | 0/5 |
| waterbox-guest-debug-runtime | 3 | 3000 | 10.486 | 10.961 | 11.989 | 54.9 % | 1.82x | 0.35 % | 0/3 |
| waterbox-guest-release-runtime | 5 | 3000 | 7.085 | 7.527 | 8.484 | 37.7 % | 2.65x | 1.45 % | 0/5 |

Guardar/cargar (mediana de p50 de cada run):

| Runner | bytes | save p50 ms | load p50 ms | 1.ª unidad tras load ms |
| --- | --- | --- | --- | --- |
| libretro | 5956584 | 18.21 | 20.74 | 5.201 |
| native | 3185481 | 18.07 | 19.74 | 5.132 |
| waterbox-core-release | 10236361 | 10.54 | 8.79 | 5.717 |
| waterbox-guest-debug-runtime | 10236084 | 6.97 | 7.14 | 10.615 |
| waterbox-guest-release-runtime | 10236084 | 2.98 | 3.21 | 7.108 |

Desglose de una unidad (medianas de runs de procesos distintos: las diferencias son estimaciones):

| Concepto | ms |
| --- | --- |
| unit_ms | 19.968 |
| engine_ms (native, render 0) | 5.048 |
| output_production_ms (native render 3 - render 0) | 0.008 |
| libretro_adapter_ms (libretro - native) | 0.002 |
| waterbox-guest-release-runtime: Activate / p6w_frame_advance / copia (harness Debug) / Deactivate | 0.000 / 5.492 / 1.587 / 0.000 |
| waterbox-guest-debug-runtime: Activate / p6w_frame_advance / copia (harness Debug) / Deactivate | 2.786 / 5.843 / 1.582 / 0.255 |
| waterbox_core_release_ms | 5.654 |
| core_release_over_native_ms | 0.597 |
| wrapper_ms (core release - guest call, release runtime) | 0.162 |

Coste principal por runner: native: engine; libretro: engine; waterbox-core-release: engine (p6w_frame_advance inside the sandbox); waterbox-guest-debug-runtime: engine (p6w_frame_advance inside the sandbox); waterbox-guest-release-runtime: engine (p6w_frame_advance inside the sandbox).

## a500-ocs-tone4-pal

A500 Paula + DMA: four audio channels on DMA (corpus audio-tone4-pal)

Unidad de vídeo: 19.968 ms (cadencia del motor; nominal 20.00 ms). Mismo tick maestro al final de la ventana en todos los runners: **True**.

| Runner | reps | unidades | p50 ms | p95 ms | p99 ms | p95/unidad | margen p95 | dispersion entre runs | ruidosos |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| libretro | 5 | 3000 | 3.554 | 3.803 | 3.962 | 19.0 % | 5.25x | 1.51 % | 0/5 |
| native | 5 | 3000 | 3.559 | 4.114 | 6.201 | 20.6 % | 4.85x | 2.31 % | 1/5 |
| native-norender | 5 | 3000 | 3.557 | 4.184 | 6.908 | 21.0 % | 4.77x | 2.10 % | 1/5 |

Guardar/cargar (mediana de p50 de cada run):

| Runner | bytes | save p50 ms | load p50 ms | 1.ª unidad tras load ms |
| --- | --- | --- | --- | --- |
| libretro | 3674381 | 5.58 | 6.11 | 3.586 |
| native | 1063332 | 5.54 | 5.28 | 3.596 |

Desglose de una unidad (medianas de runs de procesos distintos: las diferencias son estimaciones):

| Concepto | ms |
| --- | --- |
| unit_ms | 19.968 |
| engine_ms (native, render 0) | 3.557 |
| output_production_ms (native render 3 - render 0) | 0.002 |
| libretro_adapter_ms (libretro - native) | -0.005 |

Coste principal por runner: native: engine; libretro: engine.

## a500-ks13-game-pal

OPT-IN, A500 Kickstart 1.3 + a game disk, read in place from P6_KICKSTART_ROM and P6_GAME_ADF (not copied, not hashed)

Unidad de vídeo: 20.032 ms (cadencia del motor; nominal 20.00 ms). Mismo tick maestro al final de la ventana en todos los runners: **True**.

| Runner | reps | unidades | p50 ms | p95 ms | p99 ms | p95/unidad | margen p95 | dispersion entre runs | ruidosos |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| libretro | 5 | 3000 | 4.166 | 4.446 | 4.614 | 22.2 % | 4.51x | 1.40 % | 0/5 |
| native | 5 | 3000 | 4.172 | 4.657 | 18.638 | 23.2 % | 4.30x | 1.78 % | 1/5 |
| native-norender | 5 | 3000 | 4.155 | 4.440 | 4.577 | 22.2 % | 4.51x | 1.44 % | 0/5 |
| waterbox-core-release | 5 | 3000 | 4.735 | 5.073 | 5.264 | 25.3 % | 3.95x | 1.47 % | 0/5 |
| waterbox-guest-debug-runtime | 3 | 3000 | 10.014 | 13.644 | 17.783 | 68.1 % | 1.47x | 2.95 % | 0/3 |
| waterbox-guest-release-runtime | 5 | 3000 | 6.171 | 6.541 | 6.788 | 32.7 % | 3.06x | 1.75 % | 0/5 |

Guardar/cargar (mediana de p50 de cada run):

| Runner | bytes | save p50 ms | load p50 ms | 1.ª unidad tras load ms |
| --- | --- | --- | --- | --- |
| libretro | 3674381 | 7.81 | 8.26 | 4.224 |
| native | 1323918 | 7.82 | 7.51 | 4.212 |
| waterbox-core-release | 9236937 | 9.47 | 8.18 | 4.839 |
| waterbox-guest-debug-runtime | 9236660 | 6.17 | 7.22 | 10.105 |
| waterbox-guest-release-runtime | 9236660 | 3.70 | 3.19 | 6.218 |

Desglose de una unidad (medianas de runs de procesos distintos: las diferencias son estimaciones):

| Concepto | ms |
| --- | --- |
| unit_ms | 20.032 |
| engine_ms (native, render 0) | 4.155 |
| output_production_ms (native render 3 - render 0) | 0.017 |
| libretro_adapter_ms (libretro - native) | -0.006 |
| waterbox-guest-release-runtime: Activate / p6w_frame_advance / copia (harness Debug) / Deactivate | 0.000 / 4.573 / 1.593 / 0.000 |
| waterbox-guest-debug-runtime: Activate / p6w_frame_advance / copia (harness Debug) / Deactivate | 2.876 / 5.184 / 1.628 / 0.298 |
| waterbox_core_release_ms | 4.735 |
| core_release_over_native_ms | 0.563 |
| wrapper_ms (core release - guest call, release runtime) | 0.162 |

Coste principal por runner: native: engine; libretro: engine; waterbox-core-release: engine (p6w_frame_advance inside the sandbox); waterbox-guest-debug-runtime: engine (p6w_frame_advance inside the sandbox); waterbox-guest-release-runtime: engine (p6w_frame_advance inside the sandbox).

## a1200-ks31-demo-pal

OPT-IN, A1200 Kickstart 3.1 + an AGA demo disk, read in place from P6_KICKSTART_A1200_ROM and P6_AGA_ADF (not copied, not hashed)

Unidad de vídeo: 20.032 ms (cadencia del motor; nominal 20.00 ms). Mismo tick maestro al final de la ventana en todos los runners: **True**.

| Runner | reps | unidades | p50 ms | p95 ms | p99 ms | p95/unidad | margen p95 | dispersion entre runs | ruidosos |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| libretro | 5 | 3000 | 6.411 | 7.365 | 7.710 | 36.8 % | 2.72x | 1.37 % | 0/5 |
| native | 5 | 3000 | 6.406 | 7.361 | 7.659 | 36.7 % | 2.72x | 1.30 % | 0/5 |
| native-norender | 5 | 3000 | 6.425 | 7.394 | 7.739 | 36.9 % | 2.71x | 1.21 % | 0/5 |
| waterbox-core-release | 5 | 3000 | 7.052 | 8.120 | 8.584 | 40.5 % | 2.47x | 1.27 % | 0/5 |
| waterbox-guest-debug-runtime | 3 | 3000 | 12.187 | 20.538 | 38.157 | 102.5 % | 0.98x | 1.98 % | 1/3 |
| waterbox-guest-release-runtime | 5 | 3000 | 8.509 | 9.712 | 10.381 | 48.5 % | 2.06x | 1.97 % | 0/5 |

Guardar/cargar (mediana de p50 de cada run):

| Runner | bytes | save p50 ms | load p50 ms | 1.ª unidad tras load ms |
| --- | --- | --- | --- | --- |
| libretro | 5956584 | 11.72 | 13.00 | 6.190 |
| native | 2288543 | 11.67 | 11.79 | 6.185 |
| waterbox-core-release | 10301897 | 10.27 | 8.79 | 6.748 |
| waterbox-guest-debug-runtime | 10301620 | 7.48 | 7.46 | 12.171 |
| waterbox-guest-release-runtime | 10301620 | 3.79 | 3.27 | 8.228 |

Desglose de una unidad (medianas de runs de procesos distintos: las diferencias son estimaciones):

| Concepto | ms |
| --- | --- |
| unit_ms | 20.032 |
| engine_ms (native, render 0) | 6.425 |
| output_production_ms (native render 3 - render 0) | -0.019 |
| libretro_adapter_ms (libretro - native) | 0.005 |
| waterbox-guest-release-runtime: Activate / p6w_frame_advance / copia (harness Debug) / Deactivate | 0.000 / 6.910 / 1.597 / 0.000 |
| waterbox-guest-debug-runtime: Activate / p6w_frame_advance / copia (harness Debug) / Deactivate | 2.803 / 7.452 / 1.608 / 0.290 |
| waterbox_core_release_ms | 7.052 |
| core_release_over_native_ms | 0.647 |
| wrapper_ms (core release - guest call, release runtime) | 0.143 |

Coste principal por runner: native: engine; libretro: engine; waterbox-core-release: engine (p6w_frame_advance inside the sandbox); waterbox-guest-debug-runtime: engine (p6w_frame_advance inside the sandbox); waterbox-guest-release-runtime: engine (p6w_frame_advance inside the sandbox).

