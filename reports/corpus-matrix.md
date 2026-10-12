# Corpus diferencial y diagnostico de desync (T32)

Estado: EN CURSO. T32a cerrada (referencia, comparador, REF01 y DET01 sobre los micro-ROM existentes); T32b (corpus de 20 escenarios, DET02/DET03) y T32c
(repeticiones, entorno, sanitizers, fuzz) pendientes; T32 es el cierre que completa este documento.

Lo que dice y lo que no: igualar la referencia prueba fidelidad al WinUAE 6.0.3 elegido bajo el puerto comun (puente, puerto portable, probe, reloj
emulado). No es exactitud de una Amiga fisica ni ve un error del puerto, y la referencia no es una compilacion independiente de WinUAE para Windows.

## T32a: la referencia

`python3 tools/build_reference.py` aplica al snapshot verificado (`upstream/winuae`, intacto) los parches de `patches/winuae/series.json` que
`tests/reference/patch-classes.json` no marca como `ablate`, en `build/reference/upstream`, y deja un recibo con las identidades (serie, clasificacion,
herramientas) y el hash del arbol; un arbol que no es lo que dice su recibo no se reutiliza. Se compila con `-DP6_UPSTREAM_TREE=reference` (mismos fuentes
generados de CPU: ningun parche quitado toca una entrada de los generadores ni una cabecera). 137 parches: 74 `required`, 46 `instrumentation`, 17 `behavior`
(todos quitados en la referencia: 120 aplicados, 99 hunks con offset, ninguno con fuzz).

Parches quitados y lo que se espera de su ausencia (REF01 lo comprueba caso a caso):

| Parche | Efecto | Casos | Respaldo del parche |
| --- | --- | --- | --- |
| 0020 audio-signed-scaling | neutral | 5 de audio | tests.host.test_pcm |
| 0024 cpu-halt-preserve-paula | differs: scheduler, bridge, pcm | a500-halt-pal | tests.host.test_cpu_halt_audio |
| 0041 preset-clear-all-ram-boards | inert | | tests.host.test_cfgfile_preset |
| 0061 drawing-null-buffer-arithmetic | neutral | 5 de video | tests.host.test_drawing_source |
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
a byte: ver el resultado de `python3 tools/check.py --check DET01 --task T32a` en el commit de cierre de T32a. Casos: a500-vsync-pal, audio-tone_all-48000,
a500-flip-lace-pal, input-scripted (LCG con semilla 32: raton y joystick cada 37 unidades, teclas cada 301) y a1200-copper-irq-pal.

## Pendiente

- T32b: 20 escenarios (4 OCS, 4 ECS, 4 AGA, PAL/NTSC, teclado/raton/joystick, 2 audio/DMA, 2 reset/swap, 2 escritura/restauracion), anillo `rga_denise` en
  OCS/ECS con DET02, `manifests/fixtures.json`.
- T32c: procesos/instancia nueva/entorno distinto/sanitizers (DET04, DET06 con Docker al final, SAFE01) y fuzz de todos los parsers (FUZZ01).
- T32: matriz final por escenario, toda diferencia explicada y respaldada por un test o bloqueando la release.
