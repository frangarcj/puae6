# 0014. La precision de CPU es configuracion de reinicio

T53 incorpora los interpretes normal y compatible con prefetch del WinUAE fijado
junto al interprete ciclo exacto. Exact conserva el valor cero y sigue siendo el
defecto. El nivel cambia el timing emulado de instrucciones y bus: es parte de la
maquina, nunca presentacion ni respuesta automatica a la velocidad del host.

`p6_config.cpu_compatibility` consume el byte reservado del offset 66 (0 exact,
1 compatible, 2 normal); el struct sigue midiendo 72 bytes. `m68k_speed` queda a
cero: los niveles avanzan por ciclos emulados mediante el mismo scheduler y
`p6_run_frame`. Upstream exige memoria CPU CE para el blitter CE, asi que los
niveles no exact usan el scheduler normal del blitter.

La cache opcional de lineas de pantalla es independiente del nivel CPU y queda
desactivada (`DISPLAY_OPTIMIZATIONS_NONE`) en los niveles no exact: activa otro
evento sync e historia de caches ausente del inventario de estados CE. Se
conserva el camino completo del chipset; habilitar esa optimizacion exige cubrir
su estado y comparar su rendimiento. Se prioriza correccion sobre ese ahorro.

Los tres niveles se ofrecen para las CPUs soportadas 68000, 68010 y 68EC020 en
sus maquinas permitidas. Son opciones de reinicio del esquema comun: libretro
`puae6_cpu_compatibility`, headless `--cpu-compatibility` y el sync setting
`CpuCompatibility` de BizHawk. Un cambio durante la ejecucion requiere recargar
el contenido. No se anuncian netplay ni runahead nuevos.

El generador sigue siendo la autoridad: los modelos genericos 0..5 comparten
cpuemu_0, los compatibles 11/12 cpuemu_11 y el compatible 68020 cpuemu_20.
Los handlers compatibles que devuelven
ciclos tienen una tabla del tipo correcto separada de los handlers CE void del
parche 0311. Los fuentes de handlers CE siguen identicos byte a byte. Los parches
required 0343 y 0344 seleccionan accesos, tablas y bucles existentes de upstream.

Fuera de exact, la identidad del estado deriva el hash actual con el byte del
nivel y el dominio `p6/config/cpu-compatibility/v1`. Exact conserva su hash. Los
estados cruzados fallan antes de mutar la maquina. El helper upstream
`m68k_setpc_normal` ya reconstruye el PC directo de normal o el indirecto de
compatible/exact sin rellenar el prefetch guardado. El cuerpo CPU no exact lleva
ademas `lastipl` (un byte 0..7), causal para detectar el flanco de la interrupcion
de nivel 7; CE exige cero y conserva su cuerpo anterior.

La prueba dirigida es `tests/state/test_cpu_modes.py`: restauracion por ABI
publica en el mismo proceso y uno nuevo con VERTB, timers CIA y DMA, modo bucle y
VBR del 68010, cache y audio del 68020, cota estable, rechazo transaccional entre
niveles y ejecucion exact por defecto sin cambios. La prueba dirigida de libretro
compara los tres niveles de las tres familias CPU con headless y exige reinicio
al cambiar la opcion. La prueba equivalente del wrapper esta preparada; su
ejecucion requiere reconstruir el guest integrado. La aceptacion completa de T53,
incluidos Waterbox y rendimiento, sigue pendiente en la candidata integrada.
