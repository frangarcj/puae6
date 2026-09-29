# T06b2cd — inputdevice.cpp compila; frontera de texto acotada

Con el parche 0048 compila inputdevice.cpp y, por primera vez, **los 33 objetos
del motor** (`cmake --build --preset native-debug --target p6_engine`, 0 errores,
log `build/t06b2cd-engine-build.log`). No hay emulador: no se ha enlazado nada.

## Decision: cerrar la frontera de texto en el port, no en los parches

El bloque de errores inicial (69) era casi todo `_stprintf` (34), `_tstol` (23) y
`_tcstol`/`_stscanf`. Dos caminos: guardar cada sitio con parches locales, o
resolver la frontera que T06b2aw dejo abierta a proposito. Se hizo lo segundo, de
forma explicita y acotada (ports/portable/tchar.h):

- `_stprintf` solo compila si el destino es un array (capacidad por tipo); siempre
  termina la cadena; la truncacion se ve en el retorno (>= N); se elimina una
  secuencia UTF-8 incompleta al final. Un destino puntero es error de compilacion.
- `_tcstol/_tstol/_tstoi`: ASCII, sin locale, saturacion definida con ERANGE,
  errno intacto en los envoltorios atol/atoi. Diferencial contra strtol de libc en
  locale C sobre 40000 cadenas fijas: iguales en valor, fin y ERANGE.

No se anaden scanf, %f/%g/%e ni `_tstof` (locale numerico). Esto reduce el parche
en un archivo caliente y sirve a las demas unidades, a costa de mantener 2
funciones nuestras; el riesgo es que alguien use `%f`, documentado en el header.

## Lo excluido y por que (parche 0048, 19 regiones)

Configuracion textual (escritor y lector), macros custom (incluye ShellExecute y
el fallback al parser cfgfile ya excluido), scripts jport custom, RTG, test del
depurador, volumen/mute de escritorio, disk_prevnext (de 0047) y el autoswitch
con reloj de pared. `custom` solo lo rellenaba el lector excluido, asi que en P6
es siempre NULL y los sitios de llamada se guardan sin cambiar el resto.

## Hallazgo: el compilador no ve los callers de funciones declaradas en cabeceras

Excluir una definicion solo produce errores para las funciones `static`. Las
publicas (`handle_custom_event`, `inputdevice_parse_jport_custom`,
`inputdevice_jportcustom_fixup`, `disk_prevnext`) siguen referenciadas y solo
salen al enlazar. Se detecto con un enlace de prueba (dylib,
`-undefined error`) y se cerro; despues ninguna funcion excluida sigue
referenciada. Vale la pena convertirlo en check de T06c.

Ese mismo enlace da el estado real: 12084 simbolos sin resolver en el conjunto
(11859 son manejadores `op_` de otros modelos de CPU que la tabla generada aun
referencia; 225 son servicios host, `save_*/restore_*` con SAVESTATE apagado,
etc.). `inputdevice.o` deja 52, clasificados en `manifests/host-surface.json`
(`input_logical_devices.pending_link`). Logs en `build/t06b2cd-link-*`.

## Comportamiento cambiado

Solo `gethidvals2` (sscanf %X -> parseo acotado). Mismo id para valores reales;
negativos o > INT_MAX ahora dan "desconocido" (-1). Probado contra sscanf.

## Limites

- No asigna puertos ni mapea teclas del bridge (T10) ni mide lag. Las funciones
  joyport-por-nombre siguen compiladas; la config publica debe rechazar ids
  JSEM_CUSTOM y `input_autoswitchleftright`.
- Las acciones AKS de escritorio (screenshot, toggle_*, gui_display...) siguen
  compiladas y sin resolver (T06c). Los backends fisicos (`inputdevicefunc_*`,
  `input_get_default_*`) tampoco existen: son la parte de T10.
- Aviso upstream sin tocar: `if (dst[i].custom)` prueba la direccion de un array.
- La rama de fallo de vsnprintf en `_stprintf` no tiene test.
- SAVESTATE sigue apagado (T15). Nada de esto toca Waterbox.

## Validacion

tests.host.test_tchar_numeric (probe C++ O0/O2 ASan/UBSan, header valido como C,
destino puntero rechazado, formato comprobado), tests.host.test_input_source
(objeto O0/O2 con nm, imports exactos, depfile, HID contra sscanf).

## Suite de host: de ~14 min a ~2 min

La regresion tardaba porque 40 de los 54 modulos ejecutaban `tools/generate_cpu.py`
en su `setUpClass` (~15 s cada vez: rehace materialize y regeneracion para
verificar lo mismo). Ahora `tests/host/__init__.py` ofrece `ensure_generated()`,
memoizado por proceso, y `tests/host/run_parallel.py` corre un proceso por modulo
con `-j` = nucleos - 2. Resultado: 54 modulos, 85 tests, 0 fallos en 136 s de
reloj (suma de modulos 561 s). `python3 -m unittest discover -s tests/host -t .`
sigue valido y ahora tarda menos. Los workers heredan `P6_TESTS_GENERATED=1` y no
repiten la generacion, que hace el runner una vez.

Al lanzarlo salio una regresion mia: los tests de referencia compilan el cuerpo
original con `-D_stprintf=sprintf` y `tchar.h` lo pisaba. Las cuatro macros de
texto respetan ahora una definicion previa (`#ifndef`), con test.

Limite: el paralelismo asume que los modulos no comparten estado mas alla de
build/upstream y build/generated (solo lectura tras la generacion); cada uno usa
directorios temporales. Los tests de tests/codegen (CMake/Ninja) no forman parte
del runner: se lanzan aparte.
