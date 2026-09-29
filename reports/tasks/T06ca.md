# T06ca — solo las tablas de CPU que el perfil compila

Causa de los 11859 simbolos `op_*` de la puerta: gencpu escribe cpustbl.cpp y un
`cpuemu_N.cpp` por modelo en un bucle fijo. La tabla de cada modelo esta guardada
por `#ifdef CPUEMU_N` solo para la primera variante; las demas (1-5, 20-25, 31-35,
41-46, 51-55...) por `#ifndef CPUEMU_68000_ONLY`, que el perfil no define. Con solo
CPUEMU_13 definido se compilaban de todas formas y nombraban manejadores que nadie
enlaza.

## Cambio

Parche 0054: `main()` de gencpu acepta modelos por argumento; sin argumentos hace lo
de upstream. `manifests/codegen.json` y source-manifest piden `13 14` y sus salidas
pasan de 17 a 3 archivos (25 salidas generadas en total en lugar de 39).

## Pruebas (tests/codegen/test_gencpu_models.py)

- Sin argumentos: las 17 salidas del gencpu parcheado son identicas byte a byte a las
  del gencpu original de upstream (compilados ambos).
- `13 14`: salida identica al arbol `build/generated` que compila el motor;
  `cpuemu_13.cpp` identico al de la generacion completa; solo se definen las tablas
  13 y 14.
- Argumentos invalidos (`abc`, `56`, `-1`, `13x`, cadena vacia): codigo 1, mensaje y
  ningun archivo creado.
- Comprobado tambien a mano: tablas 13/14 con 1574/1541 entradas identicas a las
  anteriores.

## Resultado

Puerta de imports: 12018 -> 159 simbolos (T06c 44, T15 79, T10 20, T08 15). Host 55
modulos/88 tests, codegen 24: verdes. La tabla en ejecucion no cambia.

## Limites

- Para M2 (68020/AGA) el receta debe añadir modelos; no se prueba aqui.
- `CPUEMU_68000_ONLY` no es utilizable en este arbol (ADR 0004).
