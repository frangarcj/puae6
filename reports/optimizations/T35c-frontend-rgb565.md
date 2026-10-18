# T35c: RGB565 en el frontend libretro real

Estado: experimento en curso, sin decision de activacion. T35c sigue
`in_progress`; no se reutiliza la ganancia de la microprueba como ganancia de frame.

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

Las pasadas Mac ARM64 Apple Clang, Linux ARM64 Clang/GCC y Linux x86_64 Clang
han terminado: 27 casos y sus restauraciones en cada fila, OK. Sus observaciones
de RGB565/PCM/ticks coinciden tambien entre las cuatro filas (no sus snapshots
crudos). GCC x86_64 sigue compilando. Un
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
subdirectorio `experiment`). Log de la campana viva: `build/T35c-campaign.log`.

Los cuatro checks scoped de T35c se ejecutaran con
`P6_TEST_RGB565_FRONTEND=1`; sin matriz/medidas se declaran BLOCKED, nunca PASS.
Quedan pendientes los resultados Linux, sanitizers, medidas, decision cuantitativa
y, si procede, integracion del selector por plataforma con su validacion. No
cerrar T35c ni el padre T35 por el resultado preliminar del Mac.
