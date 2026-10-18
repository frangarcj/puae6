# T35b: LTO y PGO sobre el motor completo

Estado: T35b done; DET04, SAFE01, PERF01 y ADR01 PASS. T35 sigue pendiente de T35c y su cierre completo.

## Decision

Retener LTO y PGO como opciones explicitas del experimento nativo, con las dos desactivadas por defecto. En la fila Apple Clang 21 ARM64, LTO reduce la mediana de tiempo CPU por frame un 8,02-11,53 % y PGO un 19,34-22,03 %. Cada opcion mejora los 25 pares (cinco escenarios por cinco repeticiones). No se descarta una mejora real ni se cambia el perfil para obtenerla.

La evidencia corresponde al motor completo por la ABI publica en `p6_state_probe` y `p6_bench_probe`. No es una medida del frontend libretro ni del runtime Waterbox, ni de otros compiladores/ISA. No se activa un flag global a partir de una sola fila; LTO puede solicitarse mediante `P6_NATIVE_IPO=ON`. PGO exige entrenamiento de los ejecutables concretos que van a consumirlo: este perfil de las sondas no se anuncia valido para libretro u otros targets. No se distribuye un perfil ni se usa entrenamiento privado.

El coste de mantenimiento queda en un modulo CMake opcional y el runner: no hay cambios en CPU/chipset, generadores, precision ni baseline ISA. Los errores de toolchain/perfil son explicitos. Las opciones y el entrenamiento quedan reproducibles para la fila medida; la evaluacion de presentacion por plataforma pertenece a T35c.

## Medidas terminadas

75 ejecuciones intercaladas, 330000 unidades medidas. Calentamiento de 300 unidades; 10000 medidas en OCS ocupado y 3000 en los demas; cinco repeticiones rotando el orden referencia/LTO/PGO. Cada ejecucion incluye 20 rondas de guardar/cargar y conserva todas las muestras. El tiempo de CPU siguiente es la mediana de las cinco medianas por ejecucion, en ms por frame; delta respecto de -O2.

| Escenario | -O2 CPU ms | LTO CPU ms | Delta LTO | PGO CPU ms | Delta PGO |
| --- | ---: | ---: | ---: | ---: | ---: |
| a500-ocs-busy-pal | 3.8927 | 3.5287 | -9.35 % | 3.0447 | -21.78 % |
| a500-ocs-copper-ntsc | 3.5733 | 3.1612 | -11.53 % | 2.8451 | -20.38 % |
| a600-ecs-copper-ntsc | 3.6905 | 3.2708 | -11.37 % | 2.9770 | -19.34 % |
| a1200-aga-planes8-pal | 4.8738 | 4.4827 | -8.02 % | 3.8003 | -22.03 % |
| a500-ocs-tone4-pal | 3.4374 | 3.0548 | -11.13 % | 2.7321 | -20.52 % |

p95 de tiempo de pared agregado, ms por frame (todos los samples; no se eliminan outliers):

| Escenario | -O2 | LTO | PGO |
| --- | ---: | ---: | ---: |
| a500-ocs-busy-pal | 4.3079 | 3.8659 | 3.3085 |
| a500-ocs-copper-ntsc | 3.7402 | 3.3398 | 2.9756 |
| a600-ecs-copper-ntsc | 3.9000 | 3.4131 | 3.1300 |
| a1200-aga-planes8-pal | 5.1206 | 4.8148 | 4.0325 |
| a500-ocs-tone4-pal | 3.6117 | 3.3286 | 2.9141 |

Los contadores de maquina, muestras de audio, cadencia, dimensiones y estado coinciden entre variantes en las 75 ejecuciones. Las pequenas subidas del p95 de save/load se conservan: el maximo es +3,27 % (PGO load, Paula); no hay regresion sostenida superior al umbral orientativo del plan. LTO reduce el p95 de load de AGA un 12,62 %. Dispersión (MAD/mediana), p99, min/max, todas las muestras y condiciones de cada run estan en los datos.

La mayor relacion wall/CPU media por ejecucion es 1,0102. Hay deriva entre runs del OCS ocupado (por ejemplo la referencia baja de 4,1468 a 3,8718 ms); no se oculta. Aun asi cada par LTO/PGO mejora su referencia de la misma repeticion. Mac de uso diario, no runner dedicado: resultados orientativos, sin ratificar presupuestos ni extrapolar a otros hosts.

Tamanos de los ejecutables Mach-O en bytes; no son medidas de memoria:

| Variante | p6_state_probe | p6_bench_probe |
| --- | ---: | ---: |
| base | 12012480 | 11941968 |
| lto | 11625968 | 11440320 |
| pgo | 7150144 | 7091264 |

## Toolchain, corpus y seguridad

Fila declarada `macos-arm64-appleclang`: Apple Clang 21.0.0 (`clang-2100.1.1.101`), C/C++ y host tools de `/usr/bin` fijados por `cmake/toolchains/macos-appleclang.cmake`. El runner comprueba la version de `plan/toolchains.json` y registra el hash del compilador, OS/ISA, comandos, fuentes y ejecutables. Reference Release -O2 con assertions; CMake `CheckIPOSupported` para LTO; PGO instrumental de Clang y `llvm-profdata` de la misma Xcode. No se combinan LTO y PGO. Se mantienen `-fno-fast-math -ffp-contract=off`, sin -march=native.

Se reutilizan los 28 escenarios publicos T32: OCS/ECS/AGA, PAL/NTSC, audio/DMA, entradas, reset/swap, disquete y HDF con escritura. Cada variante compara hardware y A/V separados, por unidad, contra -O2. Ademas se ejecutan los 109 puntos/modos de restauracion por `p6_state_*`, con procesos nuevos, rewind y medios, usando el harness DET02 existente. No se comparan snapshots crudos entre builds ni se promete su compatibilidad.

Referencia, LTO, instrumental PGO, PGO, LTO+ASan/UBSan y PGO+ASan/UBSan completan todos los casos y restauraciones. Las dos variantes sanitizadas no emiten avisos y coinciden con los hashes canonicos de referencia. Apple Clang ARM64 no ofrece LSan: detect_leaks=0 es el limite ya declarado en T25; ASan/UBSan conservan no-recover. El build inicial LTO+san tardo 220,1 s. Los build_seconds de otras invocaciones pueden ser incrementales: no se presentan como una comparacion de builds limpios.

El entrenamiento incluye el corpus completo y los cinco workloads publicos de T33 (300 de warmup, 300 de entrenamiento, 20 rondas save/load). No usa Amiga Soccer, Kickstart ni otro asset privado. Se fusionan solo los 190 fragmentos producidos por esa verificacion. Perfiles y binarios quedan en build/optimization, fuera de Git.

## Fallo inicial y correccion del entrenamiento

El primer build PGO fallo en diskutil.cpp con -Werror=profile-instr-unprofiled. nm confirma que isamigatrack/ispctrack existen en su objeto instrumental pero desaparecen de ambas sondas con -dead_strip; por ello sus contadores no llegaron al perfil. Log: build/optimization/pgo-first-failure.log.

El caso minimo build/optimization/pgo-unlinked-repro (main retorna cero y una segunda unidad define una funcion nunca referenciada) reproduce el error con el mismo Clang y -O2 -Wl,-dead_strip. No se quita el diagnostico: un cambio de flujo tambien puede aparecer como profile-instr-unprofiled, de modo que conservar solo profile-instr-out-of-date seria insuficiente. Un primer test que esperaba este ultimo aviso fallo y ayudo a comprobar esa distincion.

La correccion retiene los dos puntos de entrada de diskutil solamente en el enlace GENERATE (P6_PGO_TRAIN_ROOTS, simbolos verificados con nm). No se ejecutan; llvm-profdata confirma que sus contadores y bloques son cero. USE conserva el dead stripping normal y ambos diagnosticos fatales. No se omite ningun escenario, comparacion, sanitizer, precision ni golden. El test permanente genera un perfil con una funcion retenida pero no llamada, lo consume correctamente y despues exige el rechazo al cambiar el flujo de main.

## Evidencia y reproduccion

`reports/optimizations/T35b-engine-flags.json` guarda build/verification receipts, hashes, entrenamiento, decision y resumen. `T35b-engine-measurements.json.gz` guarda los 12930651 bytes originales de medidas, comprimidos sin timestamp; se ha descomprimido y comparado byte a byte con el original y su SHA-256 esta en el JSON. Los seis recibos del corpus son publicos: no incluyen firmware/juegos ni perfiles privados.

El runner no instala herramientas ni modifica MacHawk:

```
python3 -m tests.simd.engine_flags build --variant base
python3 -m tests.simd.engine_flags verify --variant base
python3 -m tests.simd.engine_flags build --variant lto
python3 -m tests.simd.engine_flags verify --variant lto
python3 -m tests.simd.engine_flags build --variant instrument
python3 -m tests.simd.engine_flags verify --variant instrument
python3 -m tests.simd.engine_flags merge
python3 -m tests.simd.engine_flags build --variant pgo
python3 -m tests.simd.engine_flags verify --variant pgo
python3 -m tests.simd.engine_flags build --variant lto-san
python3 -m tests.simd.engine_flags verify --variant lto-san
python3 -m tests.simd.engine_flags build --variant pgo-san
python3 -m tests.simd.engine_flags verify --variant pgo-san
python3 -m tests.simd.engine_flags measure
```

Los recibos estan en build/optimization/<variante>. La campana final y todos sus procesos terminaron en cero (build/optimization/experiment-second.log). Las compilaciones, sanitizers y tests de configuracion no se ejecutaron durante las medidas.

Checks scoped de T35b (sin P6_TEST_ENGINE_FLAGS=1, las omisiones son BLOCKED):

```
P6_TEST_ENGINE_FLAGS=1 python3 tools/check.py --check DET04 --task T35b
P6_TEST_ENGINE_FLAGS=1 python3 tools/check.py --check SAFE01 --task T35b
P6_TEST_ENGINE_FLAGS=1 python3 tools/check.py --check PERF01 --task T35b
P6_TEST_ENGINE_FLAGS=1 python3 tools/check.py --check ADR01 --task T35b
```

DET04, SAFE01, PERF01 y ADR01: PASS, sin skips. SAFE01 incluye las dos variantes sanitizadas y los dos tests de configuracion/perfil. ADR01 comprueba la identidad de entrenamiento/medidas y la decision guardada. T35 conserva las puertas completas; la siguiente vertical es T35c (frontend RGB565 por plataforma).
