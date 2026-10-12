# T38: conflictos de la cola de parches contra 6100b17

Candidato `8132fd219c808d950028de2b50f76e142460da50` (WinUAE 6.1.0 beta 17, 2026-10-12), pin `857f48fcb09399d4d95527e29a2fdeaedb41f827` (6030).
La cola tiene 143 parches. Metodo: `tools/update_upstream.py` clasifica (informe `upd01-first-pass-report.md`); la reescritura se hizo con
`rebase_series.py` (un `git rebase --onto` de 3 vias sobre un repositorio disperso de los 43 archivos que toca la serie; los conflictos
se resolvieron a mano, parche a parche, leyendo los dos lados; nada se resolvio con "ours/theirs"). Resultado verificable: la serie
candidata (`candidate-series/`) aplica EXACTA (sin offset ni fuzz) con el mismo `git apply` de `materialize_upstream` y
`update_upstream.py --old NEW --new NEW` da 148 parches clean sobre el candidato.

## Cuenta

| | parches |
|---|---|
| clean tal cual (UPD01) | 6 |
| rebased (UPD01: aplica exacto, hay que re-registrar hashes) | 3 |
| shifted (UPD01: contexto intacto en otra linea) | 8 |
| semantic-conflict (UPD01) | 10 |
| blocked por un fichero con conflicto (UPD01) | 116 |
| Git 3-way sin marcadores, cuerpo identico salvo contexto | 122 |
| Git 3-way sin marcadores, pero el cuerpo del diff cambio (revisados a mano: 0030, 0036, 0037, 0160) | 4 |
| conflicto con marcadores resuelto a mano | 21 |
| absorbidos por upstream o con la premisa cambiada, se retiran de la cola | 4 (0061, 0096, 0143, 0145) |
| parches nuevos que exige la actualizacion | 10 (0900..0909) |
| cola resultante (143 - 4 + 10) | 149 |

La herramienta es conservadora a proposito (exige aplicacion exacta): 116 parches quedan `blocked` porque comparten fichero con uno
conflictivo, y varios "semantic-conflict" (0007, 0160, 0163, ...) se fusionan sin marcadores en 3 vias. Eso no es un fallo: lo que la
herramienta no decide, lo decide una persona con el diff delante, y el 3-way solo propone. Cada fusion automatica se reviso comparando el cuerpo
del parche viejo y del nuevo (`candidate-series/rebase-report.json`, campo `body_unchanged`).

## Resolucion parche a parche (21 con marcadores)

| parche | archivo | que cambio upstream | resolucion | semantica |
|---|---|---|---|---|
| 0002 generated-includes | drawing.cpp | quita `FMODE64_HACK` y sus `linetoscr_aga_fm0_64/fm1_64` (b10: Lisa siempre 64 bits) | lista nueva de includes con `<>` | ninguna para el perfil (esos dos archivos ya no se generan ni se incluyen) |
| 0003 portable-sysdeps | include/sysdeps.h | upstream ya reconoce `__aarch64__` (misma condicion que el parche) y define `CPU_AARCH64` | se toma upstream (el hunk del parche desaparece) | `CPU_AARCH64` solo activa rutas JIT/NATMEM (newcpu.h, memory.cpp): inerte en el perfil |
| 0005 portable-zfile-boundary | zfile.cpp | inserta `#include "uae.h"` en el mismo sitio | el bloque `P6_PORTABLE` va antes, `uae.h` dentro del `#else` | ninguna |
| 0011 cia-portable-profile | cia.cpp | el manejo del handshake del teclado pasa a `keyboard_handshake_write()` | se toma la llamada de upstream; el cambio `%lld` del log se traslada a la funcion nueva | ninguna (solo log) |
| 0014 cpu-a500-selection | newcpu.cpp | la rama CE de la cache de instruccion del 68020 mira `cpu_memory_cycle_exact` (antes `cpu_cycle_exact`) | condicion nueva dentro del `#ifndef P6_PORTABLE` del parche | el perfil excluye la rama; los presets del puerto ya fijan las dos opciones (config/profiles/*.json, bridge/config.cpp lo exige), no hace falta tarea |
| 0016 cpu-dump-without-debugger | newcpu.cpp | llaves en `if (nextpc)` | `#ifdef DEBUGGER` envuelve el bloque con llaves | ninguna |
| 0017 devices-a500-lifecycle | devices.cpp | callbacks de hilo principal (`device_add_main_thread_callback`, `thread_sema`, `SPCFLAG_CALLBACK`), `rtarea_free()` sube antes de `free_shm()`, `AVIOUTPUT` pasa a `VIDEOGRAB` | el semaforo y las dos funciones solo en `!P6_PORTABLE`; en el perfil el callback se ejecuta en el sitio (un solo hilo); `rtarea_free` sigue excluido en su sitio nuevo | nueva API de hilos sin hilos en el perfil; el orden de liberacion es el de upstream |
| 0018 cia-software-keyboard | cia.cpp | las condiciones del teclado MCU excluyen `CP_VELVET` | condicion de upstream dentro del `#ifndef` del parche | ninguna |
| 0025 custom-physical-clock | custom.cpp | `compute_vsynctime` reordenado: `shpos/svpos` se calculan antes de `cputimebase` y `svpos` ya no suma `lof_display` | la negociacion de refresco y `cputimebase` quedan en `#ifndef P6_PORTABLE`; `shpos/svpos` quedan fuera (los usan el reloj de sonido y `devices_update_sync`) | `svpos` cambia para el perfil (upstream): afecta a `devices_update_sync` (ajuste de sync), no al reloj de sonido del perfil (`CHIPSET_CLOCK_*`); pendiente de clasificar con el corpus |
| 0027 custom-host-build-boundaries | custom.cpp | upstream envuelve `serial_rbf_change` en `SERIAL_PORT` | se toma upstream | ninguna |
| 0028 custom-optional-irq-tod | custom.cpp | el conteo del TOD de los CIA sale de `cia_hsync_do` hacia `ciab_tod_maybe_inc`/`ciaa_tod_maybe_inc` (b17) | la guarda `CD32` se traslada a la funcion nueva; `cs_ciaatod` conserva la suya | CAMBIO DE SEMANTICA de upstream: el TOD de CIA-B cuenta ahora en la deteccion de hsync con `delay` y no en el cierre de linea; afecta a CIA/teclado y al parche 0240 (TOD de red electrica) |
| 0032 rom-load-status-without-aros-fallback | memory.cpp | `is_builtin_aros_rom()` (acepta rutas terminadas en `:AROS`) | condicion nueva + cuerpo del parche | ninguna para ROM de usuario |
| 0038 drawing-ocs-feature-guards | drawing.cpp | 5 hunks: `debug_dma >= 3`, `bpldat_docopy` simplificado (64 bits siempre), declaraciones de `matchsprites2` movidas, `lts_hres_shift`, `getbpl` -> `getbpl_aga` | cada hunk con la forma de upstream + guarda `DEBUGGER`/`AGA` del parche | AGA: el copiado de BPLxDAT es siempre de 64 bits (b10): cambia pixeles AGA con FMODE 0/1 en los bordes; lo ejercita el corpus A1200 |
| 0051 memory-custom-excluded-hooks | memory.cpp | `is_builtin_aros_rom()` | idem 0032 | ninguna |
| 0055 savestate-legacy-uss-excluded | savestate.cpp | `statefile_version` y `get_statefile_version()` (arreglo de estados 6.0.0-6.0.3 en el trazador CPU) | la variable queda fuera de la guarda y la funcion se define en el perfil (parche nuevo 0907); vale 0 | ninguna: este build solo lee estados propios |
| 0056 desktop-host-services-excluded | disk.cpp, inputdevice.cpp | `gui_ask_disk` recibe el tamano del buffer; el contador de la secuencia de reset de teclado (`keyboard_reset_seq`) cuenta siempre que el modo este activo (antes exigia `> 0`) | llamada nueva dentro de la guarda; condicion nueva | el cambio de `keyboard_reset_seq` toca el acorde de reset de teclado (ver parche 0073/0075): revisar con `tests.input` |
| 0060 video-unit-return-m68k-go-unit | include/newcpu.h | los `SPCFLAG_*` pasan a hexadecimal y aparece `SPCFLAG_CALLBACK 0x020000` | `SPCFLAG_UNIT_END` queda en el bit 1 libre | confirmar que `do_specialties` en el perfil no atiende `SPCFLAG_CALLBACK` (no hay hilos que lo pongan) |
| 0070 events-secondary-scheduler-reset | events.cpp | planificador secundario reescrito: `event2idx` desaparece, aparecen `firstvent2idx`, `event2next` (global), `event2total`, `event2max` creciente; `clear_events` ya reinicia los dos primeros | el parche conserva solo lo que sigue haciendo falta (`event2cnt`, `event2restart`, `event2total`) | upstream corrigio el mismo defecto de recarga que el parche arreglaba |
| 0078 events-state-access | events.cpp | idem | accesores adaptados (parche nuevo 0904) | el estado del planificador cambia de forma; `event2max` no viaja |
| 0108 cpu-020-aga-state-access | custom.cpp | `static int extra;` de `do_cycles_ce020` queda dentro de un `#if 0` | se toma el `#endif` de upstream, la variable es la del parche | ninguna |
| 0166 devices-filesys-lifecycle | devices.cpp | `rtarea_free()` se movio (ver 0017) | el bloque `P6_FILESYS` va en la posicion nueva | ninguna |

## Parches absorbidos por upstream (se retiran de la cola)

- 0061 drawing-null-buffer-arithmetic: upstream comprueba `if (buf1)` antes de sumar al puntero de linea y elimino el calculo del centinela `denise_pixtotal_max` que el parche corregia. `buf2` y `buf_d` se guardan solo por `buf1` (se asignan juntos): lo vigila la ejecucion con sanitizers.
- 0096 custom-rga-fourth-slot-state: la nota de b17 "Statefile save didn't always save correct RGA pipeline slots" y el PR #526 de upstream guardan los 3 slots reales en orden de pipeline con `rga_slot_first_offset = 0`. Es el arreglo que 0096 marcaba como `upstream_candidate`; es otra solucion al mismo defecto, no la nuestra: el formato del cuerpo CHSL cambia (el cuarto slot ya no viaja) y los parches 0094/0098 que lo suponian se adaptaron (ver estado).
- 0145 fpu-quotient-pointer-type: upstream aplica el mismo arreglo (`uint64_t quotient = *q; ... *q = quotient;`).
- 0143 softfloat-case-exact-includes: premisa invertida. El parche (y una parte de 0021) escribia `SOFTFLOAT-MACROS.H` para coincidir con el nombre
  en disco de 6.0.3; 6100b17 renombra el archivo a `softfloat-macros.h` y los includes de upstream coinciden. Se retira 0143 y el parche nuevo 0909 deshace
  la parte de 0021. Lo encontro `tests.host.test_audio_math` (`-Werror=nonportable-include-path`); el 3-way lo habia aplicado "limpio".

Los cuatro estaban en `ablate` de REF01 (0143 no): la referencia del candidato ya trae lo de upstream (clases actualizadas en el proyecto de ensayo).

## Parches nuevos que exige la actualizacion (0900..0909)

Codigo nuevo de upstream que no compila en el perfil o accesores de estado que leen variables que ya no existen. Descritos en `new-patches.json`.

- 0900 copper-strobe-else-brace: upstream tiene una llave de `else` dentro de `#ifdef DEBUGGER` (compila porque WinUAE siempre define DEBUGGER). `upstream_candidate`.
- 0901 debugger-and-thread-symbols-of-new-code: `debug_dma` (drawing.cpp, 2 sitios) y `cpu_thread_tid` (newcpu.cpp) usados sin su guarda. `upstream_candidate`.
- 0902 velvet-keymcu-and-aspect-flag: `keymcu_do()` en las escrituras de CIA-B (placa Velvet) y `gfx_keep_aspect_seen`.
- 0903 beam-state-accessor: BEAM (89 palabras) lee `linear_display_vpos`, `linear_vpos_visible`, `current_linear_vpos_visible`, `display_last_hsync/vsync`, `rga_pipe[].conflictaddr` (todo eso ya no existe), `linear_vpos_vsync` (ahora solo con debugger) y el shifter DMAL (ahora partido en mitades Agnus/Paula). Las palabras desaparecidas valen 0 y se ignoran al cargar: ESTADO INCOMPLETO.
- 0904 scheduler-state-accessors: `firstvent2idx`+1 hace de `event2idx`; `event2max`/`event2total` se rederivan de la tabla; `p6_blitter_handler()` nombra el manejador del blitter (ya no ocupa el slot 0). `event2max` no viaja.
- 0905 copper-state-accessor: la maquina de estados del Copper se reescribio (b13/b14): `state`, `state_prev`, `strobe_next`, `strobeip`, `strobetype`, `strobe_cycles` ya no existen. ESTADO INCOMPLETO.
- 0906 frame-geometry-accessor: `struct vidbuffer` pierde `inwidth2/inheight2`.
- 0907 statefile-version-accessor: ver 0055.
- 0908 counter-restart-hook-before-early-set-cycles: el commit upstream 066f6bc0 ("Always clear pending events and set cycle counter when emulation restarts") reinicia el contador al
  principio de la rama de reset de `m68k_go`, antes del gancho del parche 0073. El bridge veia el contador ya a cero y el reloj de la placa (`p6_rtc`) bloqueaba un fallo
  ("the clock service failed during the unit") en la primera unidad tras `p6_reset`. Regresion REAL del puerto, no de upstream: la encontro REF01 con el micro-ROM
  `a500-vsync-reset3-pal`; el 3-way habia fusionado sin marcadores el parche 0073. `upstream_candidate`: no (es del puerto).
- 0909 softfloat-include-follows-upstream-rename: ver 0143.

## Resultado del 3-way que parecia limpio y no lo era

Dos de los diez parches nuevos (0908, 0909) corrigen fusiones automaticas que aplicaban sin marcadores y compilaban. Lo que las delato no fue el diff sino
las pruebas por parche (REF01 sobre un caso de reset, `test_audio_math`, el enlace). El procedimiento de `docs/05-maintenance.md` ("un parche por vez leyendo ambos lados y sus tests")
no se puede sustituir por "aplica sin conflicto".

En el lado del puerto/bridge (no son parches del motor; `port-adaptation.patch`): `ports/portable/hostfs_unsupported.cpp` anade `my_existsfiledir` (nuevo en `filesys.cpp`), `bridge/state_cpu.cpp` y `bridge/state_probe.cpp` pierden `ev2_blitter`/`blitter_handler` y `linear_vpos` ya no esta declarado en `custom.h`.

## Cambios de semantica de upstream que importan al perfil (para clasificar con el corpus)

1. TOD de CIA-A/CIA-B contado en la deteccion de hsync/vsync (b17). Toca el hardware que lee Kickstart.
2. Planificador de eventos secundarios y blitter sin evento fijo (b13); el blitter ya no usa un evento cada 10 CCK al quedarse atascado.
3. Detecccion de blanking vertical (b14) y reloj de sync `svpos` (0025).
4. DMAL de Paula/Agnus cycle-accurate (b16, solo CE 68000): el A500 del perfil es CE.
5. Copper reimplementado (b13/b14).
6. AGA: shifters de 64 bits.
7. `cpu_memory_cycle_exact` como opcion distinta de `cpu_cycle_exact`.
