# ADR 0005: frontera de avance por unidad de video

(El numero 0002 de la ficha T07 ya lo ocupa `0002-cpu-owner-interrupts.md`.)

## Contexto

`p6_run_frame` debe avanzar exactamente una unidad de video emulada por llamada y reanudar despues sin
perder eventos ni estado, sin hilos, `longjmp` ni un segundo scheduler. La referencia es el PUAE fijado
(libretro-uae 6536174a, el de `references/puae`): `unlockscr()` pone `libretro_frame_end = true` y
`set_special(1)`; el `m68k_go` modificado retorna cuando `do_specialties` ve esa marca y la siguiente
llamada lo reanuda. Correccion a `docs/00-sources.md`, `01-architecture.md` y `07-machawk.md`: esas
paginas atribuian `SPCFLAG_CHECK` al PUAE fijado. `set_special(SPCFLAG_CHECK)` es del fork MacHawk
(`libretro-glue.c` de TASEmulators); el PUAE fijado usa el bit 0, que nadie limpia salvo un reset.

## Decision

`SPCFLAG_UNIT_END` (bit 0, libre en WinUAE 6.0.3; vale 0 fuera del perfil) se levanta solo en
`vsync_display_render()`, una vez por campo renderizado. `do_specialties` la devuelve como "salir" tras
atender interrupciones, y los dos bucles de espera que viven dentro de ella (CPU detenida, CPU retenida
en reset) la sondean. `m68k_go` pasa a `m68k_go_unit(BEGIN/APPLY/RUN/LEAVE)`; `hardboot` es estatico
y la reanudacion salta la cabecera de reset. El retorno ocurre antes de `custom_end_drawing()`.
`bridge/frame_step.cpp` es el unico llamador de APPLY/RUN; `lifecycle.cpp` llama BEGIN/LEAVE.

Orden dentro de `p6_run_frame`: reset encolado y medios y entrada (aun no implementados: la entrada no vacia
se rechaza con UNSUPPORTED), APPLY (ejecuta la cabecera solo si hay reset pendiente; descarta un borde cruzado
por el retardo aleatorio del arranque), una unidad, comprobaciones de reloj y audio, resultado.
`unit_index` cuenta unidades devueltas desde la carga (1 la primera); `master_ticks` acumula ticks del
scheduler y sigue creciendo aunque un reset reinicie el contador del motor.

## Alternativas

- Levantar la peticion en `unlockscr()` como PUAE: en 6.0.3 se llama desde `custom_end_drawing()` en cada
  reset y salida de `m68k_run`; daria bordes falsos. Descartada.
- Corutina o pila propia (libco/ucontext): en el borde la pila solo contiene `m68k_go_unit`, `m68k_run`,
  `m68k_run_1_ce` y `do_specialties`; el unico estado causal en ella era `hardboot`. Innecesaria; por eso
  WB01 no se añade a T07.
- Hilo del SO o `longjmp`: prohibidos por AGENTS.md.
- Retornar desde `hsync_handler` (manejador de evento) a mitad de instruccion: inseguro, ver riesgos.

## Evidencia

Delta con el PUAE fijado y estado de pila: T07a. STEP01 (tests/step/test_step.py): para cada ROM
de escenario, N llamadas y el mismo motor avanzando N unidades sin volver (semilla de prueba
`P6_UNIT_TRACE`, parche 0062) dan trazas de digest de estado completo identicas en cada borde, con N en
{1,2,3,7} y 19 ROMs (contador, VERTB, STOP, doble fallo con CPU detenida, IRQ del copper con 9 posiciones
horizontales, DIVU/MOVEM con 6 desfases); el digest incluye registros, contadores del chipset, eventos, Chip RAM
y la imagen entregada al final de cada borde. Dos procesos dan salida identica; el manejador corre una vez por
unidad; hay bordes dentro de DIVU y MOVEM y en el manejador de interrupcion. Cinco mutantes del mecanismo
(port mecanico del PUAE con cabecera repetida, retorno tras `custom_end_drawing`, sin sondeo en el bucle de
CPU detenida, peticion sin limpiar, sin puerta de espera) se compilan y todos rompen la comparacion; una copia
sin cambios no. Un `hardboot` local solo aislado no es observable sin un segundo reset (T13): lo cubre la
sonda de `tests/host/test_cpu_rom_guard.py`.

## Costo

103 + 30 lineas de parche (0060, 0062), mas 16 del 0061 (UBSan), 62 parches en la cola. Un sondeo mas en `do_specialties` (una lectura de
`regs.spcflags` cuando ya se iba a devolver 0) y en los dos bucles de espera. La marca se limpia al retornar, a
diferencia del bit 0 de PUAE, que dejaba `do_specialties` activa en cada instruccion.

## Riesgos y limites

- Un reset del guest a mitad de unidad descarta la peticion pendiente: esa unidad cubre "resto + reset + hasta
  el siguiente borde"; determinista pero no "un borde exacto".
- El IRQ VERTB del fotograma ya entregado se toma ~4 lineas antes del retorno (VERTB en la linea 0, render en
  la 4): "IRQ pendiente" no significa "siguiente unidad".
- El bucle de retardo de reset (`m68k_reset_delay`) no sondea: se excluye con `reset_delay == 0` y
  `crash_auto_reset == 0` (manifests/config-invariants.json), que T13 debe imponer.
- Un snapshot Waterbox solo es valido con la marca limpia (tras el retorno). Estado nuevo a inventariar para T15:
  `hardboot` y `suspended` de `m68k_go_unit`, `in_m68k_go`, `prevvpos` de `haltloop`.
- Ctrl-A-A retenido (bucle CPUINRESET) queda sin escenario hasta que exista entrada (T10).
- No hay comparacion ciclo a ciclo con el PUAE viejo: los motores difieren en el raster (linea 3 frente a 4) y
  en la primera unidad tras reset duro. Eso es REF01, no T07.

## Validacion

`python3 -m unittest tests.step.test_step` (arbol `build/step-trace`); mutantes con `P6_TEST_MUTANTS=1`.
`tests/host/test_cpu_rom_guard.py` ejecuta el cuerpo real de `m68k_go_unit` contra fakes. STEP01 solo esta
aprobado en el adaptador headless: T16 lo amplia a libretro y T19 a Waterbox.

## Adenda T13a: reset de API

`p6_reset` solo levanta una marca; `p6_run_frame` la convierte en `quit_program = UAE_RESET_HARD` justo antes de `m68k_go_unit(APPLY)`, de modo que la
cabecera del reset es la misma que la del arranque y consume su retardo aleatorio dentro de la llamada. Antes se descartan la tabla de teclas del motor
y el estado de la secuencia de reset del teclado (parche 0066) y `cycles_seen` vuelve a cero, porque el contador del motor empieza de nuevo y todo lo
que cuenta desde ahi es nuevo para el llamante. El borde que cruza la cabecera no es una unidad (se descarta `SPCFLAG_UNIT_END`); la unidad acaba en el
primer borde tras el reset, siempre a la misma distancia del reset (un cuadro mas 4 lineas), asi que su longitud no depende del retardo.

