# Pruebas: preparacion incremental y puertas completas

La optimizacion T48 no cambia el motor, las ROMs, los goldens, los sanitizers ni
los conteos de unidades. Reutiliza artefactos verificados, no maquinas emuladas.
Una ejecucion focalizada no sustituye los checks de cierre de una tarea.

## Generados: verificar antes de reutilizar

`python3 tools/generate_cpu.py` sigue verificando el snapshot contra su lock y
manifiesto, los bytes de cada parche, la cadena de hashes y las salidas. Si la
materializacion ya se aplico exactamente con esos inputs, no vuelve a copiar
upstream ni a aplicar los parches. El recibo lateral es privado de build; la
lista y los hashes esperados se derivan del manifiesto y de la serie, no de el.

Los generadores HOST se reutilizan solo con la misma identidad de compiladores,
receta, implementacion, soporte, fuente seleccionada y entorno relevante, y con
los archivos de salida completos y verificados. No se acepta simplemente que
exista `build/generated`. Un archivo alterado, extra o ausente falla sin repararlo
silenciosamente. CMake conserva su comprobacion al construir; la comprobacion
ahora puede terminar sin ejecutar de nuevo los generadores.

Comandos:

```sh
# Ruta incremental con verificacion de contenido.
python3 tools/generate_cpu.py

# Materializar y generar desde cero en temporales; comparar sin sobrescribir.
python3 tools/generate_cpu.py --force

# Dos generaciones limpias independientes: GEN01, no una prueba de hits de cache.
python3 tools/generate_cpu.py --check

# Solo cuando cambia la herramienta/receta y se quiere verificar la misma salida:
python3 tools/generate_cpu.py --refresh-receipt
```

`--refresh-receipt` no permite cambiar los archivos generados: ejecuta todo el
recorrido limpio y solo actualiza metadatos si los bytes coinciden con la salida
anterior, cuyo inventario tambien se comprueba. Si los bytes difieren, falla.
No usarlo para ocultar una divergencia. Los recibos incluyen la identidad de las
herramientas; su cambio puede cambiar una identidad de build aunque el codigo
emulado no haya cambiado. No implica compatibilidad de estados entre builds.

## Compilar una vez; ejecutar en procesos independientes

`tests/build_support.py` prepara targets explicitos por contenido de fuentes,
configuracion y directorio. En un mismo proceso, pedir otra vez el mismo probe
no repite configure/build. Pedir otro target de esa configuracion solo construye
lo que falta. Se verifican tambien CMakeCache, el recibo de codegen y el binario.
Los errores no se recuerdan como exitos. Debug y sanitizers siguen separados;
no se ha pasado la suite a Release.

Las preparaciones que usan este helper se excluyen con un lock, porque los builds
nativos comparten `build/upstream` y `build/generated`. El lock no protege un
CMake manual ni otros scripts que no usen el helper: no ejecutar clean/builds
manuales en paralelo con una suite, ni editar fuentes mientras esta corre.

El runner host/state prepara los targets compartidos antes de lanzar workers. Les da
un recibo temporal con identidad de fuentes/configuracion y hashes de binarios;
un recibo desactualizado o incompleto falla, no autoriza otra compilacion desde
el worker. `P6_TEST_BUILD_RECEIPT` es interno del runner, no un interruptor para
omitir verificaciones. El runner retira el recibo cuando terminan los workers.

El resumen muestra pruebas omitidas y fallos esperados.
Un modulo sin tests devuelve un error.
Un resultado con pruebas omitidas no cierra su puerta completa.

`--suite state` ejecuta un proceso por modulo de `tests/state`. Prepara
`p6_state_probe` y, si se selecciona `test_envelope_native`, los probes de
envelope y archivo. Los modulos Python puros no requieren un probe.
Cada proceso conserva sus ROMs temporales y cada prueba conserva sus casos.
La seleccion acepta nombres cortos o nombres completos de la suite elegida.
Un nombre desconocido falla antes de compilar. Los nombres repetidos se ejecutan una vez.

La suite state usa dos workers y un limite de 7200 segundos por modulo.
La suite host conserva su limite de 900 segundos y su numero de workers.
`-j` y `--timeout` permiten cambiar estos valores. Un timeout devuelve un error
y detiene tambien los probes hijos del worker. La limpieza del grupo se prueba
con procesos reales en POSIX. La ruta Windows usa `taskkill /T /F`; requiere
validacion en Windows. Si falla esa orden, el runner intenta terminar el worker
y devuelve un error que indica que sus hijos pueden seguir activos.

```sh
# Regresiones rapidas de la infraestructura (sin emulador ni Waterbox).
python3 -m unittest tests.test_parallel_runner tests.test_build_support tests.test_movie_setup tests.codegen.test_cache tests.test_plan

# Subconjunto host con un unico build previo y tres workers.
python3 tests/host/run_parallel.py -j 3 test_bridge_lifecycle test_bridge_memory test_a500_profile

# Suite host completa; el paralelismo se puede ajustar con -j.
python3 tests/host/run_parallel.py -j 4

# La misma seleccion de estado, primero en serie y luego en paralelo.
python3 tests/host/run_parallel.py --suite state -j 1 test_state_rtc test_state_cia test_probe
python3 tests/host/run_parallel.py --suite state -j 3 test_state_rtc test_state_cia test_probe

# Suite state completa. Conserva los SKIP y los fallos esperados visibles.
python3 tests/host/run_parallel.py --suite state

# Ejemplos de seleccion focalizada del motor: conservan sus casos y assertions.
python3 -m unittest tests.lifecycle.test_reload.ReloadClockTests
python3 -m unittest tests.state.test_state_rtc
```

La reutilizacion es del ejecutable, nunca del estado de un proceso emulado. Se
conservan los tests que necesitan un proceso limpio, varias recargas, otra
instancia o un sandbox nuevo.

## Peliculas: seleccionar corto no prepara largo

`MoviePlaybackTests` conserva los cinco casos de peliculas de 720 unidades.
`LongMoviePlaybackTests` conserva el caso de 10000 unidades. Ambos tienen su
propia preparacion y limpian el harness incluso si falla el setup. La suite
completa sigue descubriendo seis pruebas; sus cuerpos no se han cambiado.

```sh
# Solo los casos cortos: no graba antes la pelicula de 10000 unidades.
python3 -m unittest discover -s tests/bizhawk-movies -p test_movie_playback.py -k '*.MoviePlaybackTests.*'

# Solo el caso largo, sin preparar los dos fixtures cortos.
python3 -m unittest discover -s tests/bizhawk-movies -p test_movie_playback.py -k '*.LongMoviePlaybackTests.*'

# Puerta de peliculas completa, incluido formato y playback largo.
python3 -m unittest discover -s tests/bizhawk-movies
```

El setup corto pasa de preparar 11440 a 1440 unidades. Eso no es una medida del
tiempo de toda la suite. No se han reducido LONG_UNITS, SHORT_UNITS, resets,
seeks, seeds ni comparaciones. La ejecucion completa sigue siendo obligatoria
cuando la ficha o la regresion que se investiga la requieren.

## Cierres y medidas

Mantener los checks de cada ficha. Los escenarios largos de Waterbox y peliculas,
los sanitizers y la regeneracion limpia no se sustituyen por un subconjunto.
Un SKIP por ausencia de runtime, Docker o ROM autorizada no es PASS. No fijar
`P6_TEST_WB_UNITS` a un numero menor para anunciar WB02 completo.

```sh
P6_TEST_SANITIZERS=1 python3 tests/host/run_parallel.py -j 4
python3 -m unittest discover -s tests/waterbox-smoke
python3 -m unittest discover -s tests/waterbox-state
python3 -m unittest discover -s tests/bizhawk-movies
python3 -m unittest discover -s tests/libretro-host
```

Medidas y limites: `reports/tasks/T48a.md`, `reports/tasks/T48b.md`,
`reports/tasks/T48c.md` y `reports/tasks/T48d.md`.
Separar siempre preparacion, compilacion y ejecucion.
Una cache caliente no mide el coste de un build limpio.
Una suite focalizada no comprueba el emulador completo.
