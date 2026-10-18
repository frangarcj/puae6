# T35b: LTO y PGO sobre el motor completo

Estado: experimento en curso. No hay una decision de adopcion ni cambia ningun
default del producto. T35b permanece `in_progress` hasta terminar las cuatro puertas.

## Configuracion y alcance

Fila nativa declarada `macos-arm64-appleclang`: Apple Clang 21.0.0
(`clang-2100.1.1.101`), C y C++ de `/usr/bin`, host tools fijadas por
`cmake/toolchains/macos-appleclang.cmake`. La version se compara con
`plan/toolchains.json`, y el experimento registra el hash del compilador, OS/ISA,
comandos, identidad de fuentes, tiempos de build y hashes/tamanos de los ejecutables.

Referencia Release `-O2` con assertions; LTO mediante `CheckIPOSupported` de CMake;
PGO instrumental de Clang, generado y consumido en builds separados. Se mantienen
`-fno-fast-math -ffp-contract=off`, sin cambiar precision ni ISA. No se combinan
LTO y PGO: se quiere atribuir el efecto de cada experimento por separado. No se
evalua Waterbox aqui ni se extrapolan resultados a Linux/GCC.

`cmake/Optimization.cmake` deja ambas opciones desactivadas. Una solicitud de IPO
no soportada, un modo PGO desconocido, compiladores C/C++ incompatibles, o un
perfil inexistente/vacio producen un error explicito. PGO USE convierte en error
los avisos de funciones sin perfil y perfiles desactualizados. No hay fallback
silencioso a otra optimizacion. GCC requiere su propia receta de PGO; esta opcion
experimental exige Clang y no simula soporte para GCC.

## Corpus y verificacion previa

Se reutilizan los 28 escenarios publicos del corpus diferencial T32, los medios
sinteticos de `tests/reference/corpus_run.py`, y el parser canonico de T25:
hardware y A/V separados, con hashes por unidad. No se usa Amiga Soccer, Kickstart
ni otro asset privado en entrenamiento o medidas.

Cada candidato compara todos los casos contra la referencia sin flags nuevos.
Ademas, todos los puntos y modos de restauracion de cada entrada se ejecutan
mediante el harness DET02 existente con las operaciones publicas `p6_state_*`,
incluidos procesos nuevos, rewinds, eventos de entrada, cambio de disco y escritura.
Los digests, las muestras PCM y la imagen despues de cargar deben coincidir con
la ejecucion original del mismo build. No se comparan snapshots crudos entre builds.

El entrenamiento ejecuta esos escenarios y restauraciones en el build instrumental
y los cinco workloads publicos de T33 en `p6_bench_probe` (300 unidades de warmup,
300 de entrenamiento y 20 rondas save/load). El `llvm-profdata` de la misma Xcode
fusiona solo los fragmentos producidos por esa verificacion, identificados en el
recibo. Perfiles y ejecutables permanecen bajo `build/optimization`, fuera de Git.

LTO y PGO tienen cada uno una variante con ASan/UBSan que repite todo el corpus y
sus restauraciones. Apple Clang ARM64 no ofrece LSan; `detect_leaks=0` es la
limitacion ya declarada en T25, no una supresion de ASan/UBSan.

## Protocolo de medidas

Solo despues de equivalencia: workloads nativos publicos de T33 (OCS ocupado,
Copper NTSC OCS y ECS, AGA de ocho planos y Paula de cuatro canales). Cinco
repeticiones intercaladas por workload, rotando el orden de referencia/LTO/PGO;
300 unidades de warmup, 10000 medidas en el principal y 3000 en los demas, mas
20 rondas save/load. Se conservan todas las muestras wall/CPU, estado, memoria y
condiciones de carga/thermal/swap; mediana, p95 y dispersion con el resumen de T33.

Las medidas son orientativas en el Mac de uso diario, sin runner dedicado. La
campana termina las compilaciones y sanitizers antes de medir para no mezclarlos
con las muestras. Una regresion o ruido no se elimina de los datos.

## Reproduccion y continuacion

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

Cada build y verificacion escribe su recibo bajo `build/optimization/<variante>`.
Los checks scoped de DET04/SAFE01 comprueban recibos, identidad actual de fuentes,
ejecutable, harness y flags, cobertura completa y equivalencia por subsistema.
PERF01 comprueba las 75 ejecuciones completas y sus contadores de maquina.
ADR01 requiere la decision final y la identidad de medidas/entrenamiento. Sin
`P6_TEST_ENGINE_FLAGS=1` la ausencia de la campana queda BLOCKED, nunca PASS.

Checks al terminar la campana (no ejecutados aun):

```
P6_TEST_ENGINE_FLAGS=1 python3 tools/check.py --check DET04 --task T35b
P6_TEST_ENGINE_FLAGS=1 python3 tools/check.py --check SAFE01 --task T35b
P6_TEST_ENGINE_FLAGS=1 python3 tools/check.py --check PERF01 --task T35b
P6_TEST_ENGINE_FLAGS=1 python3 tools/check.py --check ADR01 --task T35b
```

Pruebas ya terminadas: configuracion/fallback y plan (36 tests, OK), y
configuracion/dispatcher (7 tests, OK); builds reales de referencia y LTO. Los
dos completan el corpus de 28 escenarios y sus 109 restauraciones con recibos
de inputs vigentes. Hardware y A/V de LTO coinciden con la referencia en cada
unidad. Tamano `p6_bench_probe`: referencia 11941968 B, LTO 11440320 B; tamano
`p6_state_probe`: referencia 12012480 B, LTO 11625968 B. Esto no demuestra una
ganancia de tiempo por frame. La campana sigue con el build instrumental de PGO.
Log inicial: `build/optimization/experiment-first.log`; continuacion actual:
`build/optimization/experiment-second.log`. No marcar done por
estas comprobaciones preliminares; faltan candidatos, sanitizers, medidas y decision.

## Primer build PGO: diagnostico de codigo no enlazado

El primer intento se detuvo por `-Werror=profile-instr-unprofiled` en
`diskutil.cpp`: `isamigatrack`/`ispctrack` existen en su objeto instrumental pero
no en `p6_state_probe` ni en `p6_bench_probe` despues de `-dead_strip`. No pueden
generar contadores en ese ejecutable. La falta no se corrige repitiendo el
entrenamiento. Log conservado: `build/optimization/pgo-first-failure.log`.

Caso minimo `build/optimization/pgo-unlinked-repro`: main retorna cero y una
segunda unidad define una funcion nunca referenciada. Con el mismo Clang y
`-O2 -Wl,-dead_strip`, instrumentar/ejecutar/fusionar y compilar USE reproduce
el error para esa segunda unidad, aunque su codigo nunca podra ejecutarse.
Sin elevar ese aviso a error, USE compila (aviso visible) y ejecuta correctamente.

No se desactiva ese diagnostico: el experimento minimo mostro tambien que un
cambio de flujo puede aparecer como `profile-instr-unprofiled`, por lo que
conservar solo `profile-instr-out-of-date` seria insuficiente. El test inicial
que esperaba este ultimo aviso fallo y ayudo a identificar esa distincion.

El segundo intento conserva los dos puntos de entrada de diskutil solamente
en el enlace instrumental (`P6_PGO_TRAIN_ROOTS`, simbolos comprobados con nm).
No se ejecutan: se guardan sus contadores a cero, sin inventar actividad del
corpus. USE mantiene el dead stripping normal y ambos diagnosticos fatales.
El test permanente retiene una funcion no llamada, genera/fusiona su perfil
cero y comprueba USE; despues cambia el flujo de main y exige el rechazo.
No se omite ningun escenario, comparacion, sanitizer, precision ni golden.

Segundo build PGO: compila completo con ambos diagnosticos fatales. El perfil
fusionado contiene 14867 funciones; `llvm-profdata show --counts --function=...`
confirma que los contadores de `isamigatrack` e `ispctrack` y todos sus bloques
son cero. No se simula haber ejecutado esas funciones. Quedan la verificacion
del candidato PGO, sanitizers y medidas antes de cerrar la decision.
