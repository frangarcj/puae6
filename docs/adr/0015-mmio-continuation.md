# ADR 0015: continuacion semantica de CPU/MMIO y esperas del blitter

## Estado y alcance

Propuesta pendiente de decision causal; T77b1 no esta completada. Perfil privado:
A1200, Normal020, sin FPU. `waiting_blits` publico conserva el defecto 0. No se
registra el prototipo 0393 ni se habilita otra politica Copper mediante esta ADR.

## Contexto

`waitingblits()` de WinUAE fijado ejecuta `x_do_cycles(4 * CYCLE_UNIT)` dentro de
una escritura MMIO. Volver desde esa espera exige conservar los locales vivos
del handler generado, las subpalabras del acceso compuesto y los llamadores
CPU/Copper/evento. Pending debe propagarse antes de usar un resultado provisional,
repetir un efecto, completar ciclos de instruccion o entregar una IRQ.

Los marcos tipados generados permiten conservar esos valores sin copiar pila C,
return addresses o punteros host. Sus descriptores declaran anchura, campos,
opcode y fases; el codec debera validar dominios y relaciones antes de aplicar.
Un roundtrip de bytes o un componente con bus de laboratorio no prueba la
restauracion del motor ni sustituye un snapshot Waterbox real.

## Problema causal

`COPPER_CHAIN`, en `tests/state/test_waiting_blits.py`, inicia un blit D grande
por VBI. Copper hace WAIT de linea, MOVE a BLTCON0 y COPJMP1; la CPU ejecuta un
bucle ordinario. La ruta real es:

`waitingblits -> do_cycles_slow -> do_cycles_cck -> do_cck -> handle_rga_out ->
process_copper -> custom_wput_copper -> custom_wput_1 -> maybe_blit -> waitingblits`.

La escritura Copper anterior sigue pendiente mientras otro MOVE vuelve a entrar.
El reloj y el slot RGA avanzan: no es una repeticion del mismo evento con reloj
congelado. Cuatro esperas simultaneas ya necesitan mas de 32 marcos si cada
llamador conserva su causalidad. Una arena mayor por si sola no resuelve la
reentrada: reconstruir todas las llamadas C++ vuelve a consumir pila host.

Tampoco hay aun una cota global demostrada a partir del tamaño del blit:

- El guard admite hasta 1M palabras; en Normal se agenda palabras por
  `blit_cyclecount` (2..4), ajustado por `makebliteventtime`.
- `blitter_handler` con DMA deshabilitado rearma el evento diez CCK despues;
  en el perfil sin `immediate_blits` no hay limite de reintentos por esa rama.
- El timeout de `waitingblits` cuenta cambios de vpos hasta `5 * maxvpos`, pero
  solo actualiza el contador despues de retornar `x_do_cycles`. No es un deadline
  absoluto compartido: una espera anidada puede aplazar esa actualizacion.
- No puede suponerse que una unidad entregada vacie las continuaciones.

Fuentes primarias: `blitter.cpp: waitingblits, do_blitter, blitter_handler` y
`custom.cpp: process_copper, handle_rga_out, do_cck, shift_rga` del snapshot
WinUAE 6.0.3 fijado por `plan/upstream.lock.json`.

## Decision propuesta

Esperar la atribucion de una cota y de la politica temporal antes de integrar el
grafo completo. No aumentar 32 a otro numero arbitrario, reservar gigabytes,
excluir Copper, adelantar un acceso o cambiar silenciosamente el timeout.

Si se demuestra una cota global compatible con el contrato de estado, usar un
dispatcher iterativo de fases y marcos tipados: solicitar un hijo y volver al
dispatcher, consumir su resultado una vez y continuar el padre. La reentrada no
reconstruira la profundidad completa en pila C. El maximo se fijara al cargar y
entrara en `state_bound`, que no puede crecer entre load y unload (ADR0009).
La ruta 0 debera conservar su orden y medirse sin callbacks host ni otro scheduler.

Si la recursion upstream exige corregir causalidad/deadline, esa correccion sera
una decision separada con reproducer y oraculo independiente. Impedir que Copper
procese otro MOVE mientras espera una escritura, o convertir el timeout en un
deadline absoluto, cambia comportamiento; no se deduce de poder guardar locales.

## Hipotesis verificables pendientes

1. Con DMA continuamente activo, el maximo de palabras, la latencia del evento y
   el throttle del perfil pueden dar una cota finita de anidacion. Contrastar el
   deadline del evento y profundidad frente al maximo permitido; incluir el
   timeout externo sin suponer que progresa durante un hijo.
2. Un programa Copper puede deshabilitar DMA en los vencimientos y habilitarlo
   antes de otra espera, aplazando el blit y renovando esperas antes de actualizar
   los contadores exteriores. Contrastar DMACON, rearmes de `blitter_handler`,
   reloj y profundidad con parada controlada. La rama de rearmado existe, pero
   no demuestra por si sola crecimiento ilimitado: el `while` de `waitingblits`
   tambien termina si observa DMA deshabilitado despues de los cuatro CCK.
   `COPPER_DMA_PULSE` conserva como control la secuencia ordinaria
   BLTCON0, DMACON off, DMACON on, COPJMP1: las esperas pendientes retornan con
   off antes del siguiente on. El test comprueba que esa secuencia entrega una
   unidad PAL por llamada incluso con la espera privada activada. No prueba una
   cota para otras secuencias de strobe, cambios del haz o arbitraje DMA.

Con DMA continuamente activo, `COPPER_CHAIN` mantiene esperas anidadas mientras
el reloj avanza hacia el deadline fijo del blit. El timeout de los padres no
progresa hasta que retorna cada hijo. Un limite de observacion del debugger solo
interrumpe el experimento: no limita esa causalidad ni fija `state_bound`.
Por ello siguen siendo necesarios el dispatcher iterativo y una cota global
atribuida a todos los caminos, no una arena ajustada a la profundidad observada.

Hasta resolverlas, las pruebas de componente son avance del prototipo; DET02,
STATE01, STEP01 y WB03 de T77b1 siguen pendientes.
