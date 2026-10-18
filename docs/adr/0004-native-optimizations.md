# 0004. Optimizaciones nativas sin cambiar la maquina emulada

Estado: evaluacion T35 en curso (2026-10-18). No se activa ninguna ruta ni flag nuevo.

## Contexto

T33 situa el coste principal en el chipset ciclo a ciclo. T34 ya conserva una
copia vectorial XRGB->ARGB en el host gestionado de BizHawk, con fallback escalar.
Libretro entrega directamente XRGB8888 por defecto; RGB565 es una conversion de
presentacion real y opcional (`adapters/libretro/present.h::to_rgb565`). Se compara
ese codigo real, sin sustituir CPU, chipset, temporizacion o audio por otro algoritmo.

## Decision provisional

Conservar el RGB565 actual en Darwin ARM64. El prototipo NEON de cuatro pixeles
es bit-exacto, pero pierde 4,5-6,4 % en dos ejecuciones consecutivas de cinco pares.
Clang ya vectoriza el bucle actual de ocho pixeles en ocho. El candidato queda en
`tests/simd/`, fuera de los targets del producto, como experimento reproducible.
No se justifica mantener otra implementacion en produccion por estas medidas.

Se conserva una referencia C++ con aritmetica por canal y seleccion explicita del
fallback. La compilacion `P6_SIMD_NO_NEON=1 -fno-vectorize -fno-slp-vectorize`
prueba tambien la ruta sin intrinsecos ni autovectorizacion. La seleccion NEON del
experimento se limita a la capacidad de la ABI AArch64; no supone soporte de SVE,
AVX ni nuevas ISA. No es aun evidencia de la matriz completa de plataformas.

## Alternativas y limites

- Intrinsecos NEON manuales: mas codigo que mantener, sin ganancia en el caso medido.
- Mantener C++ portable y autovectorizacion: resultado actual, con cola escalar del compilador.
- LTO/PGO: evaluacion pendiente con toolchain fijada y corpus representativo; este
  microbenchmark de conversion no basta para adoptarlos en el motor completo.
- `-Ofast`, `-ffast-math`, contraccion FP y `-march=native`: excluidos del perfil
  determinista/distribuible; no se cambia precision ni baseline de ISA para ganar tiempo.

Las medidas son orientativas, en el Mac en uso, sin runner dedicado. No se
extrapolan a otros compiladores/ISA ni al tiempo completo de un frame. Tampoco
se consideran una prueba de exactitud de hardware.

## Validacion y continuacion

`tests/simd/test_rgb565.py`: los 2^24 RGB, X variable, alineamientos naturales sin
alineamiento vectorial, colas, canarios, paginas protegidas a ambos lados, longitud
cero, fallback forzado y defecto sembrado que la comparacion debe detectar.
ASan/UBSan no detectan errores. Receta, pins, flags y datos en
`reports/optimizations/T35-rgb565.md` y sus JSON.

Antes de cerrar T35 faltan la equivalencia en las plataformas anunciadas, la
evaluacion de flags/LTO/PGO y las cuatro puertas de la ficha (PERF01, DET04,
SAFE01, ADR01) con cobertura de su aceptacion completa.
