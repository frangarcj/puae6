# Baseline de sanitizers y fuzzing (T26)

Estado a fecha de T26. Este informe enumera lo que se ejecuta, lo hallado, lo que no se puede ejecutar aqui y las superficies que aun no existen.

## Instrumentacion que existe

- `cmake/Sanitizers.cmake`, opcion `P6_SANITIZERS`: ASan + UBSan en todos los objetivos nativos, `-fno-sanitize-recover=all` (un hallazgo detiene el proceso),
  sin `-w`, sin lista de supresiones y sin excluir familias de UB. El preset `native-debug` la activa; `native-release` no.
- `tests/canaries` (autoprueba **separada** del motor): diez defectos deliberados (desbordamiento de heap, pila y global, uso tras liberar, desbordamiento
  con signo, desplazamiento de negativo, aritmetica de puntero nulo, acceso desalineado, vector) que deben abortar con el informe del sanitizer, y un control
  que debe callar. Un sanitizer que no detecta un OOB conocido no dice nada de las ejecuciones limpias del motor.
- Ejecuciones del motor bajo ASan+UBSan: ciclo de vida del bridge (`tests/host/test_bridge_lifecycle`, opt-in `P6_TEST_SANITIZERS=1`), invitados adversarios y
  de imagen de video (`tests.step.test_step.VideoGuestSanitizerTests`, arbol `build/step-trace-san`), objetivos de fuzzing (arbol `build/native-debug`).
- `tools/check.py --check SAFE01 --task T26` y `--check FUZZ01 --task T26` ejecutan esas suites. `--require state` da NOT_IMPLEMENTED (ver abajo).

## Fuzzing (`tests/fuzz`)

Mutacion determinista con semilla fija (no hay libFuzzer en el clang de Apple): la misma semilla y numero de iteraciones es la misma ejecucion, asi que una
semilla es un reproductor. `tests/fuzz/corpus/regressions.json` guarda las semillas reproducidas en cada ejecucion y recibira una entrada por cada bug
corregido.
- `config`: `p6_config` mutado (bits, campos a valores frontera, `struct_size`, version, error opcional/inservible) contra un oraculo escrito aparte con las
  reglas del ABI; 20000 iteraciones x 2 semillas, el oraculo coincide en todas.
- `firmware`: imagenes mutadas (bytes, truncado, ampliado, cabecera, longitud, checksum recompuesto) por `p6_load`; las aceptadas corren dos unidades y se
  descargan; cada 25 iteraciones la imagen intacta sigue cargando.
- `blob`: operaciones aleatorias sobre el almacen de nombres logicos (ids, kinds, handles obsoletos, lecturas y seeks) con codigos dentro del conjunto documentado.
- `scenario`: JSON de escenarios mutado (estructura y escalares) contra `validate_scenario`: solo `ValueError`; y JSON roto en texto.
- Autoprueba del arnes: con `P6_FUZZ_SELFTEST=1` el oraculo se invierte y la ejecucion tiene que fallar.

Resultado: ningun defecto nuevo. Los defectos que los sanitizers si hallaron antes de este informe estan abajo.

## Hallazgos heredados del motor

| Donde | Hallazgo | Estado |
| --- | --- | --- |
| drawing.cpp `expand_drga` (4 sitios) | suma/resta de un desplazamiento a un puntero de linea NULL antes del primer bloqueo de buffer (UBSan: applying non-zero offset to null pointer) | corregido, parche 0061 (candidato a upstream) |
| drawing.cpp `get_line` | desplazamiento a la izquierda del centinela negativo de pixeles | corregido, parche 0061 |

Encontrados por una pasada de UBSan en modo recover sobre el arranque completo (T07b); ese modo no se conserva (el arbol de trabajo se movio a `build/stale` y no
hay una segunda pasada automatica): las pruebas actuales usan `no-recover`.

## No se puede o no se ha ejecutado

- **LeakSanitizer**: no existe en macOS arm64. En Linux se activaria con `ASAN_OPTIONS=detect_leaks=1`; no esta verificado. Fugas del motor entre cargas: sin medir.
- **MSan** (memoria sin inicializar): no disponible en macOS. **TSan**: sin hilos en el perfil. **Valgrind**: no instalado.
- **Release (-O2) con sanitizers** y **GCC**: no ejecutado; la matriz de compiladores (`P6_TEST_COMPILERS=1`) compila pero no corre sanitizers.
- **Sandbox Waterbox**: los sanitizers no pueden correr dentro de Waterbox; solo hay cobertura nativa. Las pruebas del guest estan bloqueadas (T09b sin runner).
- La cobertura de fuzzing es por semilla fija; no se mide cobertura de codigo del motor.

## Superficies que aun no existen (no declaradas como cubiertas)

| Superficie | Tarea |
| --- | --- |
| estado serializado (`p6_state_*`): objetivo `state` de fuzzing, NOT_IMPLEMENTED en `tools/check.py` | T15 |
| medios: ADF y eventos de montaje (`p6_load` con media, `p6_media_event_enqueue`) | T12 |
| entrada de teclado, raton y joystick, y su lag | T10 |
| entrega de audio | T11 |
| importador de configuracion `.uae`, playlists, rutas logicas del frontend | T13, T16 |
| frontera libretro y Waterbox (FFI, layouts) | T16-T22 |
