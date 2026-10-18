# 0004. Optimizaciones nativas sin cambiar la maquina emulada

Estado: decisiones SIMD T35a y flags T35b (2026-10-18); cierre T35 pendiente de T35c. No cambia ningun default del producto.

## Contexto

T33 situa el coste principal en el chipset ciclo a ciclo. T34 ya conserva una
copia vectorial XRGB->ARGB en el host gestionado de BizHawk, con fallback escalar.
Libretro entrega directamente XRGB8888 por defecto; RGB565 es una conversion de
presentacion real y opcional (`adapters/libretro/present.h::to_rgb565`). Se compara
ese codigo real, sin sustituir CPU, chipset, temporizacion o audio por otro algoritmo.

## Decision SIMD por plataforma

No activar el candidato globalmente. Con Apple Clang 21 pierde un 6,08 % en la
medida por filas; con Homebrew Clang 22 perdia un 4,5-6,4 %. La conversion actual
ya se autovectoriza en el Mac. **No descartar Linux ARM64**: con Clang 18 y GCC 13
el candidato gana un 55,29 % y un 35,76 % en la microprueba, respectivamente.

El candidato queda en `tests/simd/`, fuera de los targets del producto. T35c es
obligatoria antes de cerrar T35: medir el frontend completo con RGB565 y decidir
una seleccion por plataforma, manteniendo la ruta portable donde convenga. Una
microprueba no demuestra ganancia de frame ni justifica una bifurcacion global.

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
limpios. Datos completos y limites en `reports/optimizations/T35b-engine-flags.md`,
su JSON y `T35b-engine-measurements.json.gz`.

## Alternativas y limites

- Intrinsecos NEON manuales: mejora local en Linux ARM64 y regresion en el Mac; requieren la decision medida de T35c.
- Mantener C++ portable y autovectorizacion: resultado actual, con cola escalar del compilador.
- LTO/PGO (T35b): opciones nativas medidas y reproducibles; no default universal ni perfil
  de produccion. La decision procede del motor completo, no de la microprueba RGB565.
- `-Ofast`, `-ffast-math`, contraccion FP y `-march=native`: excluidos del perfil
  determinista/distribuible; no se cambia precision ni baseline de ISA para ganar tiempo.

Las medidas son orientativas, en el Mac en uso, sin runner dedicado. No se
extrapolan fuera de las filas ejecutadas ni al tiempo completo de un frame. Tampoco
se consideran una prueba de exactitud de hardware.

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
ASan/UBSan no detectan errores. Receta, pins, flags y datos en
`reports/optimizations/T35-rgb565.md` y sus JSON.

Las cuatro puertas scoped de T35a cubren el experimento, su matriz, seguridad y
decision; T35b conserva su evidencia del motor/corpus y sus cuatro puertas. T35
sigue pendiente de T35c (medida e integracion del frontend si conviene), ademas
de su cierre completo.
