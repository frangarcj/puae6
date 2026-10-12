# Corpus diferencial y diagnostico de desync (T32)

Estado: EN CURSO. T32a (referencia, comparador, REF01 y DET01 sobre los micro-ROM existentes) y T32b (el corpus de 28 escenarios, DET02/DET03) cerradas; T32c
(repeticiones, entorno, sanitizers, fuzz) y T15n (las brechas del estado que halla el corpus) pendientes; T32 es el cierre que completa este documento.

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

Resultado (`python3 tools/check.py --check REF01|DET02|DET03 --task T32b`, los tres PASS): REF01 igual en 26 escenarios y con la diferencia declarada y atribuida en 2; DET02 109 pruebas,
101 igualan y 8 son una divergencia FIJADA; DET03 28 pruebas, 27 igualan y 1 fijada. Un PASS de T32b significa "cada escenario iguala o su divergencia esta fijada donde la tabla dice",
no "no hay divergencias": `OpenDivergenceGate` (`P6_RELEASE_GATE=1`) falla mientras `DIVERGENCES` no este vacia y es parte del cierre de T32.

### Hallazgos del corpus (tarea T15n)

El punto 5 de la ficha (programas que mantienen lleno el anillo `rga_denise` en OCS/ECS) y un haz programado en un proceso nuevo han encontrado dos brechas del estado nativo en el A600 que
ningun escenario anterior tocaba:

1. `ecs-ring-cpu-pal` (la CPU reescribe BPLCON0/1/2, BPLCON3, DIWHIGH y los colores sin parar): DET02 diverge en las unidades 2, 4 y 6 con la misma imagen y con la imagen reiniciada, no con
   un proceso nuevo; desde la unidad 1 tras la carga la frontera cae seis ciclos de color despues, con CPU, scheduler, video, bridge, chipset.beam e imagen distintos y todos los digest iguales en
   la unidad 0. Igualan: el mismo programa sin BPLCON3 ni DIWHIGH, el A500 con el mismo bucle y el Copper en ambas maquinas. Medido, no atribuido: copiar al cargado el anillo `rga_denise` y sus
   cursores del original no basta; los estaticos que cambian en la carga son sobre todo de Denise (`lts`, `denise_res`, `bplcon1_shift_mask`, `bpl1dat_trigger`, `hbstop_denise_reg`...) y
   `diwhigh_saved` (restore_custom lo deja sin el bit 15 que escribe DIWHIGH).
2. `ecs-beam-var-wide-pal` (BEAMCON0 y HTOTAL programados, linea mas ancha) cargado en un proceso nuevo: el grupo `timing` del digest del chipset difiere nada mas cargar. restore_custom
   deja `new_beamcon0` y no `beamcon0` ni lo que `init_beamcon0` deriva de el (maxhpos/maxvpos, vblank_hz, hsstop_detect, minfirstline...); una misma imagen que programo el haz ella misma
   iguala. Candidato a arreglo: llamar a `init_beamcon0` en `p6_custom_rebuild_derived` cuando `beamcon0 != new_beamcon0`.

Los dos estan en `DIVERGENCES` de `tests/reference/test_corpus_det02.py` con su dueno (T15n), y T32 no se cierra hasta que T15n los resuelva.

## Pendiente

- T15n: las dos brechas de arriba (dueno de las 8 pruebas DET02 y la DET03 fijadas).
- T32c: procesos/instancia nueva/entorno distinto/sanitizers (DET04, DET06 con Docker al final, SAFE01) y fuzz de todos los parsers (FUZZ01).
- T32: matriz final por escenario, `OpenDivergenceGate` en verde, toda diferencia explicada y respaldada por un test o bloqueando la release.
