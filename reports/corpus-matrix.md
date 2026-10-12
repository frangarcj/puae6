# Corpus diferencial y diagnostico de desync (T32)

Estado: CERRADO. T32a (referencia, comparador, REF01 y DET01 sobre los micro-ROM existentes), T32b (el corpus de 28 escenarios, DET02/DET03), T15n (las dos brechas del estado que hallo el corpus) y
T32c (matriz DET04/DET06, sanitizers SAFE01, fuzz FUZZ01) cerradas; T32 es el cierre que completa este documento: matriz final por escenario, `OpenDivergenceGate` en verde y toda diferencia explicada.

Lo que dice y lo que no: igualar la referencia prueba fidelidad al WinUAE 6.0.3 elegido bajo el puerto comun (puente, puerto portable, probe, reloj
emulado). No es exactitud de una Amiga fisica ni ve un error del puerto, y la referencia no es una compilacion independiente de WinUAE para Windows.

## T32a: la referencia

`python3 tools/build_reference.py` aplica al snapshot verificado (`upstream/winuae`, intacto) los parches de `patches/winuae/series.json` que
`tests/reference/patch-classes.json` no marca como `ablate`, en `build/reference/upstream`, y deja un recibo con las identidades (serie, clasificacion,
herramientas) y el hash del arbol; un arbol que no es lo que dice su recibo no se reutiliza. Se compila con `-DP6_UPSTREAM_TREE=reference` (mismos fuentes
generados de CPU: ningun parche quitado toca una entrada de los generadores ni una cabecera). 142 parches: 74 `required`, 50 `instrumentation`, 18 `behavior`
(todos quitados en la referencia: 124 aplicados, 107 hunks con offset, ninguno con fuzz).

Parches quitados y lo que se espera de su ausencia (REF01 lo comprueba caso a caso):

| Parche | Efecto | Casos | Respaldo del parche |
| --- | --- | --- | --- |
| 0020 audio-signed-scaling | neutral | 5 de audio | tests.host.test_pcm |
| 0024 cpu-halt-preserve-paula | differs: scheduler, bridge, pcm | a500-halt-pal | tests.host.test_cpu_halt_audio |
| 0041 preset-clear-all-ram-boards | inert | | tests.host.test_cfgfile_preset |
| 0061 drawing-null-buffer-arithmetic | neutral | 5 de video | tests.host.test_drawing_source |
| 0262 denise-sprite-mask-unsigned-shift (T32c) | neutral | ocs-ring-copper-ntsc, ecs-ring-copper-ntsc | tests/reference/test_corpus_sanitizers.py |
| 0068 0072 (sesion) | inert | | tests.lifecycle.test_reload / test_cycles |
| 0069 input-pot-state-reset | differs: input | a500-vsync-reset3-pal | tests.lifecycle.test_reload |
| 0090 0091 0093 0096 0102 (aplicar un estado) | inert | | tests.state.test_state_upstream, FUZZ01 |
| 0111 fast-ram-access-mask | inert | | tests/state/test_state_fast.py |
| 0114 expansion-clear-keeps-clean-pages | neutral | a1200-vsync-fast8m | tests/waterbox-smoke/test_puae6.py |
| 0144 0145 (FPU) | inert / neutral | | tests/host/test_cpu_fpu.py |
| 0190 filesys-rdb-checksum-bounded | inert | | tests.fuzz.test_fuzz.HardFileFuzzTests |

Los `inert` no los alcanza el corpus de micro-ROM (actuan tras una carga, en un proceso usado o con entradas que el corpus no tiene): REF01 solo dice que
el corpus no los nota; la evidencia del parche es su test, y el corpus de T32b y los DET02 son donde se ejercitan con estados.

### REF01 sobre los 89 casos de `tests/portability/corpus.py` y un caso de reset

88 iguales y 2 con la diferencia declarada. `a500-halt-pal` difiere desde la unidad 1 en scheduler, bridge y pcm (Paula sigue corriendo con la CPU parada en el
producto; el upstream la desactiva). `a500-vsync-reset3-pal` (p6_reset tras la unidad 3) difiere desde la unidad 4 solo en el digest de entrada (el producto pone a cero el estado del
puerto de pot al resetear; el upstream conserva lo que dejo la sesion). Atribucion medida en los dos: reaplicando solo ese parche (0024, 0069) al arbol de referencia y recompilando, el caso queda igual.
Grupos: video-a600 22, video-a1200 19, cpu-a500 17, audio 10, video-a500 8, memory 4, cpu-a600 3, cpu-a1200 3, input 3.

### Diagnostico de una divergencia

`tools/compare_runs.py A B [--probe-args "..." | --inputs escenario.json]` da el primer boundary distinto, los subsistemas que difieren alli (en orden
causal: es donde mirar primero, no la causa), las claves que se separan despues, la ultima frontera igual y las entradas/eventos aplicados hasta ella.
Ejemplo medido (`vsync`, `--poke-at 4 beam:10:10:20`): primer boundary 5, ultima igual 4, claves `chip.beam`, `chip.counters`, CPU, scheduler, RAM, RTC,
CIA, bridge y pcm; entradas: `unit 4: poke-at beam:10:10:20`.

### DET01

Dos procesos nuevos a la vez, mismas entradas, 10000 unidades, digest de cada subsistema cada 5 unidades y PCM/imagen de cada unidad, salidas identicas byte
a byte: `python3 tools/check.py --check DET01 --task T32a` PASS (~17 min). Casos: a500-vsync-pal, audio-tone_all-48000,
a500-flip-lace-pal, input-scripted (LCG con semilla 32: raton y joystick cada 37 unidades, teclas cada 301) y a1200-copper-irq-pal.

## T32b: el corpus de 28 escenarios

`tests/scenarios/corpus/corpus.py` (escenarios estrictos generados, `index.json`, procedencia en `manifests/fixtures.json`; las categorias de `docs/04-validation.md` las exige
`tests/reference/test_corpus.py` y comprueba que cada una este respaldada por el programa y los argumentos). Cada escenario pasa REF01 contra la referencia de T32a, DET02 en los modos de
`tests/state/test_det02.py` y DET03 por la ABI publica.

| Escenario | Maquina | Norma | Categorias | REF01 | DET02 (unidades) | DET03 |
| --- | --- | --- | --- | --- | --- | --- |
| ocs-vsync-irq-pal | a500 | pal | ocs, pal, irq, cpu | igual | igual (2,3) | igual |
| ocs-copper-irq-ntsc | a500 | ntsc | ocs, ntsc, copper, irq | igual | igual (2) | igual |
| ocs-lores-bars-pal | a500 | pal | ocs, pal | igual | igual (2,4) | igual |
| ocs-hires-lace-ntsc | a500 | ntsc | ocs, ntsc, interlace | igual | igual (2,3,4) | igual |
| ocs-blitter-dma-pal | a500 | pal | ocs, pal, dma, blitter | igual | igual (2,5) | igual |
| ocs-halt-pal | a500 | pal | ocs, pal, cpu | declarada (0024: scheduler, bridge, pcm) | igual (2) | igual |
| ocs-ring-cpu-pal | a500 | pal | ocs, pal, ring | igual | igual (2,4,6) | igual |
| ocs-ring-copper-ntsc | a500 | ntsc | ocs, ntsc, ring, copper | igual | igual (2,4,6) | igual |
| ecs-beam-var-wide-pal | a600 | pal | ecs, pal | igual | fijada, T15n (2,4) | fijada, T15n |
| ecs-shres-ntsc | a600 | ntsc | ecs, ntsc | igual | igual (2,4) | igual |
| ecs-diwhigh-v-pal | a600 | pal | ecs, pal | igual | igual (2,4) | igual |
| ecs-ring-cpu-pal | a600 | pal | ecs, pal, ring | igual | fijada, T15n (2,4,6) | igual |
| ecs-ring-copper-ntsc | a600 | ntsc | ecs, ntsc, ring, copper | igual | igual (2,4,6) | igual |
| aga-planes8-fmode3-pal | a1200 | pal | aga, pal | igual | igual (2) | igual |
| aga-ham8-ntsc | a1200 | ntsc | aga, ntsc | igual | igual (2) | igual |
| aga-sprite64-pal | a1200 | pal | aga, pal | igual | igual (2) | igual |
| aga-bars24-ntsc | a1200 | ntsc | aga, ntsc, copper | igual | igual (2) | igual |
| audio-tone4-pal | a500 | pal | ocs, pal, audio, dma | igual | igual (3) | igual |
| audio-volume-ntsc | a500 | ntsc | ocs, ntsc, audio, irq | igual | igual (5,6) | igual |
| audio-modulated-pal | a500 | pal | ocs, pal, audio, dma | igual | igual (3,4) | igual |
| input-mouse-joystick-pal | a500 | pal | ocs, pal, mouse, joystick | igual | igual (4) | igual |
| input-keyboard-pal | a500 | pal | ocs, pal, keyboard | igual | igual (3,6) | igual |
| input-pot-pal | a500 | pal | ocs, pal, joystick | igual | igual (6) | igual |
| input-types-pal | a500 | pal | ocs, pal, mouse, joystick | igual | igual (4) | igual |
| reset-mid-run-pal | a500 | pal | ocs, pal, reset, restore | declarada (0069: input) | igual (5) | igual |
| swap-disk-pal | a500 | pal | ocs, pal, swap, dma | igual | igual (12) | igual |
| floppy-write-pal | a500 | pal | ocs, pal, write, restore | igual | igual (105) | igual |
| hdf-write-pal | a500 | pal | ocs, pal, write, restore, dma | igual | igual (3,5) | igual |

Resultado (`python3 tools/check.py --check REF01|DET02|DET03 --task T32b`, los tres PASS): REF01 igual en 26 escenarios y con la diferencia declarada y atribuida en 2; DET02 109 pruebas y DET03 28,
todas igualan (T32b las cerro con 8 DET02 y 1 DET03 como divergencia fijada; T15n las resolvio y `DIVERGENCES` esta vacia). `OpenDivergenceGate` (`P6_RELEASE_GATE=1`) falla mientras esa tabla no
este vacia y es parte del cierre de T32.

### Hallazgos del corpus, cerrados por T15n

El punto 5 de la ficha (programas que mantienen lleno el anillo `rga_denise` en OCS/ECS) y un haz programado en un proceso nuevo encontraron dos brechas del estado nativo en el A600 que
ningun escenario anterior tocaba:

1. `ecs-ring-cpu-pal` (la CPU reescribe BPLCON0/1/2, BPLCON3, DIWHIGH y los colores sin parar): DET02 divergia en las unidades 2, 4 y 6 con la misma imagen y con la imagen reiniciada, no con un
   proceso nuevo. Atribucion medida con un trazador de instrucciones en una copia de desarrollo del motor (nada de ello esta en el repositorio): las dos maquinas ejecutan las mismas instrucciones
   hasta la 937 de la primera unidad (linea 44, la primera visible); ahi la original se detiene 148 ciclos de color en una escritura de COLOR03 (la captura de bitplanes en hires de 4 planos
   se lleva el bus) y la cargada no, porque `bprun` pasa a 0 un ciclo despues de empezar. Las variables del secuenciador de bitplanes de Agnus (`ddf_stopping` 0 contra 1, `bprun_cycle` 16
   contra 36...) ya difieren en la primera instruccion tras la carga: son estaticos del motor que ningun cuerpo nombra y que `custom_reset` deja en reposo, pero un programa que reescribe
   DIWHIGH sin parar abre y cierra la ventana vertical en mitad de una captura y los deja en cualquier sitio en la frontera. Arreglo: BEAM lleva `bprun`, `bprun_cycle`, `ddf_stopping`,
   `ddf_enable_on`, `ddf_limit_in`, `ddf_limit`, `ddfstrt_match`, `hwi_old` y `ddfstrt_val` (parche 0261; BEAM de 80 a 89 palabras). Lo que quedo descartado: el anillo `rga_denise` y sus
   cursores (copiarlos del original no basta), `diwhigh_saved` y `pfunc` (la escritura retardada de BPLCON0 estaba vacia en todas las fronteras medidas).
2. `ecs-beam-var-wide-pal` (BEAMCON0 y HTOTAL programados, linea mas ancha) cargado en un proceso nuevo: el grupo `timing` del digest del chipset diferia nada mas cargar. restore_custom deja
   `new_beamcon0` y no `beamcon0` ni lo que `init_beamcon0` deriva de el (maxhpos/maxvpos, vblank_hz, hsstop_detect, minfirstline, las tablas de hsync...). Arreglo: `p6_custom_beam_set`
   ejecuta `init_beamcon0` en un Agnus ECS o AGA antes de poner los contadores (parche 0260).

Las cotas del estado crecen 36 bytes en las tres maquinas (BEAM). Pruebas: `tests/reference/test_corpus_det02.py` y `test_corpus_det03.py` (los dos escenarios, ahora sin divergencia), los rechazos de
BEAM y el tamano del cuerpo en `tests/state/test_state_upstream.py` y `test_state_container.py`, y `A600RewoundStateTests`, que obligo a llevar tambien `ddfstrt_val`.

## T32c: repeticiones, entorno distinto, sanitizers y fuzz

Cuatro puertas, todas con `python3 tools/check.py --check ID --task T32c`:

- **DET04 / DET06** (`tests/portability/test_portability.py`: `DifferentialMatrixTests` y `DifferentialRowExecutionTests`). El mismo corpus (28 programas, medios y argumentos `@corpus/...`, digest
  `6dc0d803...`) corre en las cinco filas de T25 (macOS arm64 AppleClang nativo; Linux arm64 clang y gcc, Linux x86_64 clang y gcc en Docker, x86_64 traducido) con `P6_MATRIX_SET=differential`
  (`tests/portability/run_matrix.py`; la evidencia de T25 y `reports/platform-matrix.md` no se tocan). Cada fila corre en O0 y O2 (linea base y variante `host`: otro locale, otra zona, reloj
  adelantado ~25 anos por una biblioteca precargada, otro directorio de trabajo y otra ruta de ROM) y en `san` (ASan/UBSan, linea base). Resultado, `tests/portability/evidence-differential.json`:
  las 15 combinaciones fila/optimizacion dan 28/28 hashes de hardware y 28/28 de A/V iguales a la fila de referencia, sin errores ni diferencias, y las 10 variantes `host` son iguales a su linea base
  (28/28 y 28/28). Los hashes distinguen los escenarios entre si (la prueba lo exige: un hash constante no demostraria nada). Con Docker abierto una sola vez al final, las dos puertas pasan
  re-ejecutando las cinco filas en O0 (`P6_TEST_PORTABILITY=1`); sin Docker las filas Linux se saltan y el check es BLOCKED, nunca PASS.
- **SAFE01** (`tests/reference/test_corpus_sanitizers.py`, mas las cinco suites de T24). Los 28 escenarios corren bajo ASan/UBSan y un estado guardado en el primer punto de cada uno se carga bajo
  los sanitizers, sin un solo aviso. **Hallazgo**: `ocs-ring-copper-ntsc` y `ecs-ring-copper-ntsc` (el Copper escribe BPLCON2 con PF1P=5) disparaban UBSan en `expand_bplcon2`
  (`drawing.cpp`, `0x0000FFFF << (4 * plf1pri)`, un `int` desplazado hasta 20 posiciones). Parche 0262: la constante pasa a `unsigned` y la mascara se recorta a 16 bits como antes; mismo valor.
  Clase `behavior`, efecto `neutral` en `tests/reference/patch-classes.json`: REF01 lo prueba en esos dos escenarios (producto y referencia iguales). El guest de Waterbox cambia
  (`manifests/waterbox-memory.json` vuelto a medir; humo, memoria, contrato, hdf, wb y unidades de Waterbox pasan).
- **FUZZ01** (`CorpusScenarioFuzzTests` y los parsers de T59/T76). Mutaciones sembradas de los 28 escenarios y de los primeros archivos de maquina: los intactos validan, toda mutacion se rechaza solo con
  `ValueError` y se rechaza mas de la mitad. Se suman los demas objetivos ya implementados: nativo, escenarios, estados (`state`, `state_writable`), contenido de libretro (M3U, VFS, archivos
  ZIP/7z/RP9 de T59, el modelo y las etiquetas de nombre de T76), normalizacion de opciones y hard files.

## Matriz final por escenario (T32)

Todos los escenarios, tras T15n y T32c:

| Escenario | REF01 | DET02 | DET03 | DET04 (5 filas x O0/O2/san) | DET06 (host, O0/O2) | SAFE01 | FUZZ01 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 28 escenarios (19 A500 OCS, 5 A600 ECS, 4 A1200 AGA) | igual en 26; declarada en 2 | igual (109 pruebas) | igual (28) | igual (28/28 hardware y A/V en 15 combinaciones) | igual (28/28 en 10 variantes) | limpio (28 y 28 estados) | 28 semillas |

Las dos diferencias frente a la referencia son las unicas del corpus y estan explicadas y respaldadas por un test:

| Escenario | Parche | Subsistemas | Primera unidad | Causa | Test |
| --- | --- | --- | --- | --- | --- |
| ocs-halt-pal (y a500-halt-pal de T32a) | 0024 cpu-halt-preserve-paula | scheduler, bridge, pcm | 1 | con la CPU parada el producto sigue ejecutando Paula (ADR 0002); el upstream la desactiva | tests.host.test_cpu_halt_audio |
| reset-mid-run-pal (y a500-vsync-reset3-pal) | 0069 input-pot-state-reset | input | 4 | p6_reset deja el puerto de pot en cero; el upstream conserva lo de la sesion | tests.lifecycle.test_reload |

Atribucion medida en ambos: reaplicar solo ese parche al arbol de referencia deja el caso igual. No quedan divergencias abiertas: `DIVERGENCES` de `tests/reference/test_corpus_det02.py` esta vacia
y `P6_RELEASE_GATE=1 python3 -m unittest tests.reference.test_corpus_det02.OpenDivergenceGate tests.reference.test_corpus_det03.OpenDivergenceGate` pasa. Los parches `inert` no los nota el corpus
de micro-ROM ni el de escenarios (actuan tras una carga, en un proceso usado o con entradas que no tiene): su evidencia es el test nombrado en `patch-classes.json`.

Cierre: `python3 tools/check.py --check ID --task T32` registra la union de las suites de los hijos (`tools/check.py`, con `P6_RELEASE_GATE=1` para `OpenDivergenceGate`). Ejecutados aqui sobre el arbol
final: REF01 PASS (con las dos `OpenDivergenceGate`) y DET01 PASS (10000 unidades, dos procesos nuevos). DET02, DET03, SAFE01 y FUZZ01 son las mismas suites que T32b/T32c, ejecutadas en PASS sobre este arbol
(DET02 109 pruebas y DET03 28 tras el parche 0262; SAFE01 y FUZZ01 de T32c); DET04 y DET06 necesitan Docker para las filas Linux y corrieron una sola vez, en T32c, con `P6_TEST_PORTABILITY=1`
(sin Docker el check de T32 queda BLOCKED).

Limites: igualar la referencia es fidelidad al WinUAE 6.0.3 elegido bajo el puerto comun, no exactitud de una Amiga fisica; la matriz prueba determinismo entre ISA, compiladores y entorno host sobre
estos 28 escenarios, no sobre cualquier programa; Waterbox/BizHawk se mide por sus propias puertas (WB03, waterbox-state), no por esta matriz.
