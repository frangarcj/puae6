# ADR 0012: 68010 cycle exact y reloj de la CPU como opciones de reinicio

Continua la ADR 0010 (68020 cycle exact). Paridad con `puae_cpu_model` 68010 y `puae_cpu_multiplier` de PUAE5 (inventario en `reports/research/parity-puae5.md`, P04).

## Decisiones (T52)

1. **La 68010 ya estaba generada.** `gencpu 13 14 21` escribe 68010 (`op_*_13`) y 68000 (`op_*_14`) en el mismo `cpuemu_13.cpp`, que el perfil compila desde T04. Lo que faltaba era
   la fila `[1][4]` de `cputbls` (nula desde la ADR 0010: `build_cpufunctbl` abortaba en vez de elegir una CPU que ningun preset anunciaba). El parche 0310 la apunta a `op_smalltbl_13`:
   **GEN01 y todos los fuentes generados quedan identicos**, y las tablas de la 68000 salen byte a byte iguales. No hay nada que regenerar ni editar a mano.
2. **Que maquinas.** La 68010 es la mejora de siempre de una maquina con 68000 en zocalo: se ofrece en el A500, A500 OG, A500+, A1000, A2000 OG, A2000 **y en el A600** (su 68000 es un PLCC
   desmontable; sin otro cambio de preset). El **A1200 no**: su 68EC020 va soldada y es la unica CPU para la que estan hechos su preset AGA, el bus de 24 bits y el Kickstart 3.1 (el parche
   0290 lo rechaza en una 68000/68010 con un mensaje que nombra el requisito). Una 68020 en una maquina de 68000 tampoco: seria otro preset (T55). `p6_config.cpu_model` lo dice con
   `P6_ERROR_UNSUPPORTED` y la razon antes de tocar el motor.
3. **Hallazgo de upstream (parche 0311).** El modo bucle de la 68010 (un DBcc sobre una sola instruccion no vuelve a leer la instruccion ni rellena la cola) estaba **muerto** en el
   cycle-exact de WinUAE 6.0.3: `loop_mode_table[opcode]` se llenaba con `cpufunctbl[opcode]`, que en toda tabla cycle-exact es NULL (solo existe el manejador `noret`), y el DBcc generado
   comprueba `loop_mode_table[regs.ird]` antes de entrar. Un bucle `TST.W (An)+` / `DBRA` costaba lo de una 68000 (18 relojes la pasada). El parche llena la tabla con `cpufunctbl_noret`
   (y cambia el tipo, porque una llamada por un puntero de tipo distinto lo señalaria UBSan): 12 relojes la pasada. Es `upstream_candidate`; no se puede ablacionar en REF01 (toca una cabecera), asi
   que se clasifica `required` y su efecto solo existe donde hay una 68010, que el corpus no tiene.
4. **Estado.** Bloque de 10 bytes al final de `P6CP` en una 68010 (VBR, SFC, DFC, `loop_mode`; el modo bucle deja `regs.loop_mode` puesto entre dos instrucciones y el DBcc lo lee para saber si es la
   primera pasada). `p6_cpu_boundary` deja pasar esos registros en una 68010 (en una 68000 siguen siendo un error). La cota crece 10 bytes. Una prueba con un defecto sembrado (no restaurar
   `loop_mode`) hace fallar DET02: el campo no es decorativo.
5. **Identidad.** La palabra `flags` de `config_hash` esta llena (bits 0-31). La CPU y el reloj entran como el audio (T73): fuera de la CPU y del reloj de la maquina,
   `H("p6/config/cpu/v1" || hash_hasta_aqui || modelo, multiplicador)`, con el modelo y el multiplicador **resueltos** (dos grafias de la maquina de serie, `0` y el numero, dan el hash de
   siempre). Se calcula despues del hash de audio. En el wrapper BizHawk entra en la identidad (`;cpu=...;cpuclock=...`) solo fuera de `Auto`.
6. **Reloj (`cpu_multiplier`).** Es `cpu_clock_multiplier` de upstream (N << 8) con los valores de PUAE5: 1, 2, 4, 8, 10, 12, 16 (3,55 a 56,75 MHz); 0 es el de la maquina (2 en las de 68000, 4 en el
   A1200; es lo que `built_in_prefs` pone). `cpucycleunit = CYCLE_UNIT / N` es **entero** (512 / N): a N = 10 y 12 el ciclo es 51 y 42 en lugar de 51,2 y 42,67 (-0,4 % y -1,6 %), y las pruebas
   derivan los tiempos con esa division. Nada lee el reloj del host ni hay una "velocidad maxima": el motor solo cambia cuantos ciclos de color emulados cuesta cada acceso. Es determinista
   (DET02 con cada multiplicador en el A500, la 68010 y el A1200) y no cambia ningun cuerpo del estado: lo unico que lo distingue es el hash.

## Medido

2000 pasadas de `TST.W (An)+` / `DBRA` desde ROM, en pulsos E de CIA-A (`tests/host/test_cpu_clock.py`), para N = 1, 2, 4, 8, 10, 12, 16:
68000: 7205, 3603, 1802, 902, 719, 592, 452 (derivado del manual: 36004 relojes, `36004 * (512 // N) / 2560`); 68010 en modo bucle: 4807, 2404, 1203, 602, 480, 396, 302; 68EC020: 6102, 3252, 2002,
1002, 813, 743, 552. La 68EC020 nunca va mas lenta con un reloj mas rapido pero no escala del todo: la cache de instrucciones corre al reloj de la CPU y las lecturas de ROM esperan al bus.
Los ticks de cada unidad y los marcos de audio entregados son los de la maquina por defecto salvo lo que mide la instruccion que cierra la unidad (una unidad acaba en la primera
instruccion despues de que el haz vuelva a empezar).

## Limites

- Sin MMU: un acceso a espacio no mapeado devuelve datos (ADR 0010). El error de direccion de la 68010 apila el marco de formato 8 de 29 palabras con el relleno que upstream escribe
  ("partially implemented only", `Exception_normal`): el tamaño, el formato, el vector y la direccion de fallo estan probados, el contenido interno del marco no.
- BKPT, el error de bus de un acceso real y las 68030/040/060 no se ofrecen (T55). La 68010 lleva la misma tabla de bus que la 68000 cycle-exact (`do_cycles_ce`).
- El multiplicador 1 y los altos en la 68EC020 estan medidos y restauran (DET02), pero que un programa real los tolere es asunto del programa (temporizaciones, protecciones): la ayuda de la
  opcion lo dice.
