# Baseline T34-before

Estado: **ORIENTATIVE**. ORIENTATIVE: the Mac is used by someone while the benchmark runs; no dedicated runner exists (plan/runners.json). Nothing here is a hard threshold.

- Commit `bbf1fcde2695` (con cambios sin commitear), Apple M1 Pro (6P+2E, 16 GiB), macOS 26.7.1 25G241, Apple clang version 21.0.0 (clang-2100.1.1.101).
- Native: Release `-O2`; guest Waterbox `-O2`; perfil `deterministic-a500`; 143 parches, serie `9a8127bce151`.

## a500-ocs-busy-pal

A500 OCS: every DMA channel on, a blit in flight under contention, the disk motor running (corpus ocs-blitter-dma-pal)

Unidad de vídeo: 20.000 ms (cadencia del motor; nominal 20.00 ms). Mismo tick maestro al final de la ventana en todos los runners: **True**.

| Runner | reps | unidades | p50 ms | p95 ms | p99 ms | p95/unidad | margen p95 | dispersion entre runs | ruidosos |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| waterbox-core-release | 5 | 10000 | 4.499 | 4.854 | 5.170 | 24.3 % | 4.12x | 1.16 % | 0/5 |

Guardar/cargar (mediana de p50 de cada run):

| Runner | bytes | save p50 ms | load p50 ms | 1.ª unidad tras load ms |
| --- | --- | --- | --- | --- |
| waterbox-core-release | 9204169 | 9.03 | 7.77 | 4.729 |

Desglose de una unidad (medianas de runs de procesos distintos: las diferencias son estimaciones):

| Concepto | ms |
| --- | --- |
| unit_ms | 20.000 |
| waterbox_core_release_ms | 4.499 |

Coste principal por runner: native: engine; libretro: engine; waterbox-core-release: engine (p6w_frame_advance inside the sandbox).

## a600-ecs-copper-ntsc

A600 ECS NTSC: the Copper rewrites BPLCON3/DIWHIGH too (corpus ecs-ring-copper-ntsc)

Unidad de vídeo: 16.667 ms (cadencia del motor; nominal 16.67 ms). Mismo tick maestro al final de la ventana en todos los runners: **True**.

| Runner | reps | unidades | p50 ms | p95 ms | p99 ms | p95/unidad | margen p95 | dispersion entre runs | ruidosos |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| waterbox-core-release | 5 | 3000 | 4.317 | 4.618 | 4.823 | 27.7 % | 3.61x | 0.59 % | 0/5 |

Guardar/cargar (mediana de p50 de cada run):

| Runner | bytes | save p50 ms | load p50 ms | 1.ª unidad tras load ms |
| --- | --- | --- | --- | --- |
| waterbox-core-release | 8516041 | 8.54 | 7.67 | 4.401 |

Desglose de una unidad (medianas de runs de procesos distintos: las diferencias son estimaciones):

| Concepto | ms |
| --- | --- |
| unit_ms | 16.667 |
| waterbox_core_release_ms | 4.317 |

Coste principal por runner: native: engine; libretro: engine; waterbox-core-release: engine (p6w_frame_advance inside the sandbox).

## a1200-aga-planes8-pal

A1200 AGA (68EC020 cycle exact): eight bitplanes fetched 64 bits at a time (corpus aga-planes8-fmode3-pal)

Unidad de vídeo: 20.000 ms (cadencia del motor; nominal 20.00 ms). Mismo tick maestro al final de la ventana en todos los runners: **True**.

| Runner | reps | unidades | p50 ms | p95 ms | p99 ms | p95/unidad | margen p95 | dispersion entre runs | ruidosos |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| waterbox-core-release | 5 | 3000 | 5.681 | 6.006 | 6.216 | 30.0 % | 3.33x | 0.60 % | 0/5 |

Guardar/cargar (mediana de p50 de cada run):

| Runner | bytes | save p50 ms | load p50 ms | 1.ª unidad tras load ms |
| --- | --- | --- | --- | --- |
| waterbox-core-release | 10236361 | 10.00 | 8.48 | 5.758 |

Desglose de una unidad (medianas de runs de procesos distintos: las diferencias son estimaciones):

| Concepto | ms |
| --- | --- |
| unit_ms | 20.000 |
| waterbox_core_release_ms | 5.681 |

Coste principal por runner: native: engine; libretro: engine; waterbox-core-release: engine (p6w_frame_advance inside the sandbox).

