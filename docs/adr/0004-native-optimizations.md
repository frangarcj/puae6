# 0004. Optimizaciones nativas sin cambiar la maquina emulada

Estado: seleccion RGB565 de T35c integrada y medida en el producto; cierre T35 pendiente. LTO/PGO siguen desactivados por defecto.

## Contexto

T33 situa el coste principal en el chipset ciclo a ciclo. T34 ya conserva una
copia vectorial XRGB->ARGB en el host gestionado de BizHawk, con fallback escalar.
Libretro entrega directamente XRGB8888 por defecto; RGB565 es una conversion de
presentacion real y opcional (`adapters/libretro/present.h::to_rgb565`). Se compara
ese codigo real, sin sustituir CPU, chipset, temporizacion o audio por otro algoritmo.

## Decision SIMD por plataforma

Mantener el bucle portable en macOS y x86_64. La microprueba repetida con las
fuentes anteriores a la integracion da -5,48 % en Apple Clang 21 (0/5 parejas),
+55,95 % en Linux ARM64 Clang 18 y +34,70 % en GCC 13 (5/5 en ambos).
Son tiempos de conversion aislada con copia por filas, no ganancias de frame.

El kernel comun esta en `ports/portable/simd/rgb565.h`. El producto libretro
selecciona NEON en Linux AArch64 little-endian con Clang 18 o GCC 13. Las demas
combinaciones conservan el bucle portable, que el compilador puede vectorizar.
`P6_SIMD_NO_NEON=1` fuerza el fallback; una actualizacion de compilador exige
repetir la medida antes de ampliar la seleccion. No cambia la ISA base ni hay
consultas host por pixel. La conversion solo actua al pedir RGB565.

La referencia por canales, el codigo actual y el candidato coinciden en las
cinco filas declaradas, en modo optimizado, sin SIMD/autovectorizacion y bajo
ASan/UBSan. La seleccion NEON depende de la ABI AArch64; el fallback se fuerza
tambien con `P6_SIMD_NO_NEON=1`. No se presupone SVE/AVX ni otra ISA. x86_64 se
ha ejecutado traducido en Docker, nunca se presenta como hardware x86_64.

## Decision de flags del motor (T35b)

Retener LTO y PGO como opciones explicitas para el experimento nativo medido,
desactivadas por defecto. Apple Clang 21 ARM64, misma Release -O2 con assertions,
sin fast-math, contraccion ni cambio de ISA: LTO reduce la mediana CPU por frame
un 8,02-11,53 %, PGO un 19,34-22,03 %. Cada opcion mejora los 25 pares de la
campana (cinco workloads publicos de T33, cinco repeticiones intercaladas,
330000 unidades). No se descarta una ganancia real por mantener el default portable.

Referencia, LTO y PGO igualan hardware/A/V por separado en los 28 escenarios
publicos T32 y sus 109 restauraciones por la ABI publica. Las dos variantes con
ASan/UBSan repiten la misma cobertura sin avisos. Estado y contadores coinciden
en las 75 medidas; el p95 de save/load no tiene una regresion mayor del 3,27 %.
LTO baja p95 load de AGA un 12,62 %. PGO reduce el ejecutable de benchmark de
11941968 a 7091264 bytes; LTO a 11440320 bytes. No son medidas de RSS.

El coste queda en `cmake/Optimization.cmake` y el runner: sin fork del motor.
PGO entrena solo programas/medios publicos, con Clang/llvm-profdata coherentes,
y rechaza perfiles ausentes/desactualizados. Dos funciones de diskutil eliminadas
del enlace instrumental se retienen solo en GENERATE para registrar contadores
cero (comprobados); no se inventa cobertura ni se desactiva el error de perfil.
El test negativo sigue rechazando un cambio de flujo tras entrenar. Los perfiles
no se publican y son especificos del conjunto de ejecutables entrenado.

Estas opciones no se activan globalmente ni se anuncian para otros compiladores,
frontends o Waterbox: esta fila mide las sondas nativas del motor. Un perfil de
estas sondas no se presenta como valido para libretro. El Mac es de uso diario,
sin runner dedicado; se conservan deriva, outliers y condiciones. El coste del
build inicial LTO+san fue 220,1 s; los tiempos incrementales no comparan builds
limpios. Las medidas pueden repetirse con `tests/simd/engine_flags.py`; los
resultados temporales se eliminan al cerrar la comprobacion.

## Alternativas y limites

- Intrinsecos NEON manuales: adoptados solo en Linux ARM64 con Clang 18/GCC 13 tras medir el frontend completo.
- Mantener C++ portable y autovectorizacion: ruta de macOS, x86_64 y los demas compiladores.
- LTO/PGO (T35b): opciones nativas medidas y reproducibles; no default universal ni perfil
  de produccion. La decision procede del motor completo, no de la microprueba RGB565.
- `-Ofast`, `-ffast-math`, contraccion FP y `-march=native`: excluidos del perfil
  determinista/distribuible; no se cambia precision ni baseline de ISA para ganar tiempo.

Las medidas son orientativas, en el Mac en uso, sin runner dedicado. La microprueba
solo mide conversion; T35c mide retro_run con callbacks minimos y sin vsync. No se
extrapolan fuera de las filas ejecutadas ni prueban exactitud de hardware.

## Lectura de la campana de frontend T35c

Antes de disponer de los tiempos de la campana completa se fija su lectura:
por plataforma y escenario, usar la mediana de las cinco medianas de CPU, junto con los cinco
pares actual/candidato, la dispersion entre repeticiones y las distribuciones
de pared/CPU. No mezclar workloads en una sola media que oculte un resultado
negativo. Solo se descartan las 300 unidades de calentamiento previstas; los
outliers medidos se conservan.

La decision de adopcion se apoya en una mejora de frame que se repita entre
pares, escenarios y orden de ejecucion, ademas de equivalencia y coste de
mantenimiento. Se conservan los resultados negativos de la conversion aislada.
Las ganancias de una microprueba no sustituyen esta medida. Un resultado pequeno
o mixto exige explicar su relacion con la dispersion; no se declara una mejora
estable a partir de una sola mediana. Los presupuestos existentes siguen
vigentes: investigar/repetir una regresion sostenida de tiempo superior al 5 %
o de coste/tamano de estado superior al 10 %, sin cambiar precision ni umbrales.

## Validacion y continuacion

`tests/simd/test_rgb565.py`: los 2^24 RGB, X variable, alineamientos naturales sin
alineamiento vectorial, colas, canarios, paginas protegidas a ambos lados, longitud
cero, fallback forzado y defecto sembrado que la comparacion debe detectar.
La matriz se ejecuta con `tests/simd/matrix.py`; las variantes con ASan/UBSan
comprueban el mismo codigo. Los pins y flags se conservan en el runner.

Las cuatro puertas scoped de T35a cubren el experimento, su matriz, seguridad y
decision; T35b ejecuta el motor/corpus y sus cuatro puertas. T35c añade el frontend
real y la seleccion de producto. T35 requiere cerrar la union de estos alcances.

## Decision de frontend T35c

La campana final de producto usa Apple M1 Pro (8 nucleos, 16 GiB), macOS 26.7.1,
alimentacion de red y bateria al 100 %, y Docker Linux ARM64 (8 vCPU, 7,75 GiB); maquina de uso diario,
sin runner dedicado. Compiladores fijados: Apple Clang 21, Clang 18.1.3 y GCC
13.3.0. Release -O2 con assertions, sin LTO/PGO, fast-math ni contraccion FP.

| Fila | Reduccion de CPU por frame, segun escenario | Parejas favorables |
| --- | --- | --- |
| macos-arm64-appleclang | -1,164 a +2,252 % | 9/25; resultado mixto |
| linux-arm64-clang | +7,809 a +11,840 % | 25/25 |
| linux-arm64-gcc | +4,078 a +6,614 % | 24/25 |

Son cinco cargas publicas T33 (OCS Copper/blitter, OCS NTSC, ECS, AGA y Paula),
cinco repeticiones intercaladas de ambas rutas: 300 unidades de calentamiento,
10000 medidas en la principal y 3000 en las otras, con 20 save/load por ejecucion.
La dispersion entre medianas de repeticiones es 0,79-1,72 % CV en macOS y
0,47-4,37 % en Linux. GCC gana cuatro de las cinco parejas AGA: la restante pierde
0,83 %. Las medianas de CPU mejoran en los cinco escenarios y la primera campana tambien
mejoraba en las cinco cargas Linux de ambos compiladores. No se mezclan cargas
para esconder un resultado negativo. El p95 de pared baja en las diez cargas
Linux (2,65-11,87 %); el mayor aumento de p95 save/load es +4,22/+4,05 %, bajo el
10 % existente. Estados finales, tamano y contadores coinciden en cada conjunto
de diez ejecuciones. La comparacion completa de pixeles/PCM/ticks se hace fuera
del benchmark, en 27 casos por fila y con restore en el mismo proceso y otro nuevo.

En macOS, el candidato NEON contrafactual sube el p95 save/load de OCS NTSC
23,79/20,64 %, concentrado en la tercera repeticion y valores extremos posteriores.
Se repite esa carga aisladamente con el mismo protocolo, cinco parejas y los
mismos estados finales: save/load cambia -0,28/-2,70 %, sin reproducir el aumento.
La CPU de esa repeticion aislada gana 3,81 % (4/5 parejas), pero las dos campanas
completas no dan una mejora estable en el conjunto de cargas macOS. Se conserva
su ruta portable; los resultados originales y sus outliers no se sustituyen.

La decision es activar las dos filas Linux ARM64 y conservar macOS portable.
El cambio mantiene una sola implementacion, con la cola escalar original del
prototipo y el bucle portable anterior. No bifurca CPU/chipset ni cambia el formato
por defecto XRGB8888. Los porcentajes describen este frontend minimo sin vsync y
este equipo, no todos los juegos, frontends o CPU ARM64.

La biblioteca estandar es siempre el producto. El target opt-in
`puae6_libretro_rgb565_alternate` selecciona la ruta opuesta con los mismos objetos
del motor: portable de referencia en Linux ARM64, NEON contrafactual en macOS.
La campana final valida y mide estos binarios de producto: 150 ejecuciones mas la
repeticion aislada de NTSC. `python3 -m tests.simd.frontend_statistics` calcula los
pares y la dispersion desde los datos temporales; los checks comprueban los
ejecutables, corpus y protocolo exactos. No se conservan informes historicos.
