# T35c: RGB565 en el frontend libretro real

Estado: T35c in_progress tras T35d, sin decision de activacion. No se reutiliza la
ganancia de la microprueba como ganancia de frame.

## Vertical ejecutable

`P6_RGB565_EXPERIMENT=ON` añade una segunda biblioteca libretro de prueba. Se
compilan los mismos fuentes del adaptador con las mismas propiedades de include,
enlace, exportacion y visibilidad que el target real, y **los mismos objetos** de
`p6_engine` y bibliotecas de bridge/estado/servicios. Solo el target candidato
define `P6_TEST_RGB565_CANDIDATE`, que selecciona el prototipo ya probado por T35a
en `to_rgb565`. La otra biblioteca conserva la conversion actual. La opcion
experimental esta desactivada por defecto; el producto no activa el candidato.

El host es `p6_libretro_host`: carga la biblioteca real con dlopen, exige el
contrato libretro y avanza por retro_run/p6_run_frame. No es un modelo del motor.
Se mide retro_run completo, incluida la presentacion del core y los callbacks
minimos del frontend; no es una medida de GPU ni de la UI de RetroArch.

## Verificacion y filas

Nueve programas publicos (los cinco workloads T33, mas SuperHires ECS, HAM8,
sprite AGA de 64 bits y entrelazado OCS), en lores/hires/superhires: 27 casos por
fila. Las bibliotecas deben entregar el mismo RGB565, PCM, ticks y estado guardado
completo. Cada caso guarda en 6, carga en 10 y repite 7..10; despues otro proceso
carga ese estado y repite tambien 7..10. Se exige que los saves/loads sean aceptados
y que el formato realmente anunciado sea RGB565. No se comparan snapshots crudos
entre compiladores/ISA; entre filas se comparan las salidas de cada unidad.

Filas: las cinco declaradas de T25 (Mac ARM64 Apple Clang 21; Linux ARM64 y x86_64
Clang 18/GCC 13). Linux usa los IDs locales congelados de T35a, sin red ni pull,
repo solo lectura y una raiz build independiente por fila para codegen. x86_64
esta traducido en Docker Desktop sobre ARM64; no se anuncia como hardware x86_64.
Las mismas filas ejecutan ambas bibliotecas con ASan/UBSan. Se mantienen los tests
de colores, alineamientos, tails y paginas protegidas de T35a.

Las cinco pasadas optimizadas anteriores a T35d terminaron: 27 casos y sus
restauraciones en cada fila, OK. Sus observaciones de RGB565/PCM/ticks coinciden
tambien entre filas (no sus snapshots crudos). Son evidencia anterior al arreglo
del centinela; deben renovarse con las fuentes corregidas. Un
smoke de benchmark (10 unidades, no medida de rendimiento) confirma las dos rutas
RGB565, el parseo de ticks y el estado final igual. Se repite la verificacion al
terminar el runner y sus registros scoped, con el input fingerprint vigente.

Auditoria de compile_commands en las tres filas ARM64: una sola compilacion por
cada uno de los 47 fuentes del motor. Los dos adaptadores tienen las mismas
opciones, salvo el selector candidato, su include de pruebas, las macros EXPORTS
propias de cada shared target y las rutas de objetos. No se compara una variante
con otro perfil de CPU, precision, LTO o PGO.

## Medidas previstas, sin decision anticipada

Mac y ambas filas Linux ARM64: cinco workloads publicos T33, cinco repeticiones
alternando el orden de las dos bibliotecas, 300 unidades de warmup, 10000 medidas
en OCS ocupado y 3000 en los demas; 20 rondas save/load. Se guardan todas las
muestras de pared/CPU, contadores de salida, ultimo tick/unidad, estado final,
build/flags/OS/ISA y carga observada. Ningun perfil de LTO/PGO entra en esta
comparacion. Las filas x86_64 verifican equivalencia/fallback y seguridad; no se
interpreta su traduccion como una medida del rendimiento de hardware x86_64.

El orquestador termina todas las compilaciones y sanitizers antes de medir. Las
medidas son orientativas: Mac de uso diario y Linux en VM, sin runner dedicado;
el contenedor ve su carga, no ofrece sensores de energia/temperatura del host.
No hay asset privado en programas ni medidas.

Entorno observado durante la compilacion de la campana renovada: Apple M1 Pro,
8 nucleos y 16 GiB de RAM, macOS 26.7.1; Docker Desktop ARM64 ofrece 8 vCPU y
8319770624 bytes de RAM a sus contenedores. El Mac estaba conectado a corriente,
con bateria al 100 %. Registro: `T35c-host-conditions.json`. Esta lectura no
equivale a muestrear energia o temperatura durante cada run; las medidas guardan
la carga antes y despues de cada uno y siguen siendo orientativas.

## Reproduccion y continuacion

```
python3 -m tests.simd.frontend_matrix prepare
python3 -m tests.simd.frontend_matrix prepare --sanitizers
python3 -m tests.simd.frontend_matrix verify
python3 -m tests.simd.frontend_matrix verify --sanitizers
python3 -m tests.simd.frontend_matrix measure
```

El runner de una fila es `tests/simd/frontend_rgb565.py`. Los resultados estan en
`build/simd/frontend/<fila>` (los contenedores guardan el experimento en su
subdirectorio `experiment`). Log de la campana detenida por el fallo de
sanitizer: `build/T35c-campaign.log`.

Los cuatro checks scoped de T35c se ejecutaran con
`P6_TEST_RGB565_FRONTEND=1`; sin matriz/medidas se declaran BLOCKED, nunca PASS.
Quedan pendientes la matriz renovada, sanitizers, medidas, decision cuantitativa
y, si procede, integracion del selector por plataforma con su validacion. No
cerrar T35c ni el padre T35 por el resultado preliminar del Mac.

## Fallo real de sanitizer y tarea duena T35d

Las cinco filas optimizadas completan los 27 casos y coinciden por RGB565/PCM/ticks.
La primera fila sanitizada, Mac ARM64, falla en AGA planes8/fmode3 con SuperHires:
`drawing.cpp:2626`, left shift de 2147483645 por 2, proceso -6. El fallo aparece
en la biblioteca **de referencia**, no solo en el candidato. Una sola unidad y
XRGB8888 tambien lo reproducen. Logs: `build/T35c-campaign.log` y
`build/T35d-repro/{24bit,16bit}.log`; esta ejecucion no es PASS ni se reinicia
como si solo hubiera caducado la observacion.

El centinela de `spr_nearest` se corrige y valida en T35d con un reproducer
aritmetico y el motor completo. T35c pasa a pending con esa dependencia, conserva
sus cuatro puertas y todos los escenarios. Tras T35d deben renovarse las fuentes
derivadas de las filas y sus recibos antes de volver a medir; los resultados del
guest previo no sustituyen la comprobacion del guest corregido.

T35d termina con sus seis puertas en PASS, incluido el caso A1200 SuperHires
en Waterbox real y las 16 medidas de memoria coherentes (commit `ef2b90c`). Las
dos variantes nativas opt/san ya verifican el arreglo con los inputs finales.
Al reanudar T35c se conservan las fuentes derivadas y recibos Linux anteriores
en `build/simd/frontend/<fila>/pre-T35d-20261018/`. La nueva campana los
materializa mediante las herramientas normales, repite microprueba, matriz y
sanitizers antes de medir. Log: `build/T35c-campaign-after-T35d.log`.

## Microprueba renovada tras T35d

Las cinco filas vuelven a completar los 2^24 colores, colas, alineamientos y
paginas protegidas en opt/fallback/san, con el mismo digest. Datos completos:
`reports/optimizations/T35c-rgb565-micro.json`; no se sobrescribe la evidencia
historica T35a.

La mediana de las cinco muestras mantiene la diferencia entre compiladores:
el candidato empeora 6,48 % en Apple Clang 21 (0/5 pares ganados), y reduce el
coste 56,65 % en Linux ARM64 Clang 18 y 34,69 % en GCC 13 (5/5 en ambos).
Son tiempos de conversion/copia de filas, no de frame. La decision de T35c
sigue pendiente de las medidas de `retro_run` completo y de sus puertas.

## Matriz renovada: primer punto comprobado

Las cinco filas optimizadas completan de nuevo sus 27 casos con 0342 y el
manifiesto final. Se comprueba que
sus recibos son nuevos (no los conservados antes de T35d), que cada verificacion
identifica su build y que RGB565/PCM/ticks coinciden entre las cinco filas. Pasan
las restauraciones en el mismo proceso y en otro nuevo, y la igualdad del
snapshot completo entre las dos bibliotecas de cada fila.

La auditoria renovada de compile_commands confirma los mismos flags del
adaptador en las tres filas salvo selector, include experimental, EXPORTS y
objeto de salida; GCC x86_64 tambien conserva esa equivalencia. Cada una de las
cinco filas tiene una sola compilacion de
los 47 fuentes del motor; sus comandos no introducen fast-math, ISA nativa,
LTO ni PGO. Las tres filas ARM64 sanitizadas tambien completan los 27 casos
con los inputs actuales y conservan las salidas de las cinco optimizadas. Se
verifican recibos, hashes de las bibliotecas/host y los flags ASan/UBSan de los
47 fuentes del motor y los dos adaptadores. Pasan las restauraciones en el mismo
proceso y en otro nuevo. Las dos filas x86_64 sanitizadas y las mediciones
siguen pendientes: este punto no es PASS de DET04/SAFE01 de T35c. La campana
completa todas las compilaciones y puertas de equivalencia y seguridad antes de
iniciar los tiempos.
