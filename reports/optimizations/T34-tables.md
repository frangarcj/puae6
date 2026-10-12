## T34f
| scenario | before p50 | after p50 | delta p50 | before p95 (median of 5) | after p95 (median of 5) | after < before | alloc B/unit before -> after | GC collections in the 5 runs, before -> after | same end tick |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| a500-ocs-busy-pal | 4.573 ms | 4.400 ms | -0.173 ms (-3.8 %) | 4.843 | 4.683 | 5/5 | 4453 -> 53 | 52 -> 15 | yes |
| a600-ecs-copper-ntsc | 4.340 ms | 4.254 ms | -0.086 ms (-2.0 %) | 4.617 | 4.530 | 5/5 | 4446 -> 44 | 30 -> 0 | yes |
| a1200-aga-planes8-pal | 5.680 ms | 5.532 ms | -0.148 ms (-2.6 %) | 6.047 | 5.893 | 4/5 | 4446 -> 44 | 30 -> 0 | yes |

load average (1 min) at the start of the 30 runs: min 2.9, median 4.9, max 42.1

## T34
| scenario | before p50 | after p50 | delta p50 | before p95 (median of 5) | after p95 (median of 5) | after < before | alloc B/unit before -> after | GC collections in the 5 runs, before -> after | same end tick |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| a500-ocs-busy-pal | 4.499 ms | 4.378 ms | -0.120 ms (-2.7 %) | 4.851 | 4.688 | 4/5 | 4453 -> 53 | 55 -> 15 | yes |
| a600-ecs-copper-ntsc | 4.317 ms | 4.188 ms | -0.129 ms (-3.0 %) | 4.616 | 4.486 | 4/5 | 4446 -> 44 | 30 -> 0 | yes |
| a1200-aga-planes8-pal | 5.681 ms | 5.507 ms | -0.174 ms (-3.1 %) | 6.023 | 5.881 | 5/5 | 4446 -> 44 | 30 -> 0 | yes |

load average (1 min) at the start of the 30 runs: min 2.9, median 4.8, max 10.0

