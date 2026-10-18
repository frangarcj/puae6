# 0004. Optimizaciones nativas sin cambiar la maquina emulada

Estado: decision SIMD T35a (2026-10-18); cierre T35 pendiente de T35b/T35c. No se activa ninguna ruta ni flag nuevo.

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

## Alternativas y limites

- Intrinsecos NEON manuales: mejora local en Linux ARM64 y regresion en el Mac; requieren la decision medida de T35c.
- Mantener C++ portable y autovectorizacion: resultado actual, con cola escalar del compilador.
- LTO/PGO (T35b): evaluacion pendiente con toolchain fijada y corpus representativo; este
  microbenchmark de conversion no basta para adoptarlos en el motor completo.
- `-Ofast`, `-ffast-math`, contraccion FP y `-march=native`: excluidos del perfil
  determinista/distribuible; no se cambia precision ni baseline de ISA para ganar tiempo.

Las medidas son orientativas, en el Mac en uso, sin runner dedicado. No se
extrapolan fuera de las filas ejecutadas ni al tiempo completo de un frame. Tampoco
se consideran una prueba de exactitud de hardware.

## Validacion y continuacion

`tests/simd/test_rgb565.py`: los 2^24 RGB, X variable, alineamientos naturales sin
alineamiento vectorial, colas, canarios, paginas protegidas a ambos lados, longitud
cero, fallback forzado y defecto sembrado que la comparacion debe detectar.
ASan/UBSan no detectan errores. Receta, pins, flags y datos en
`reports/optimizations/T35-rgb565.md` y sus JSON.

Las cuatro puertas scoped de T35a cubren el experimento, su matriz, seguridad y
decision; T35 sigue pendiente de T35b (flags/LTO/PGO con motor/corpus) y T35c
(medida e integracion del frontend si conviene), ademas de su cierre completo.
