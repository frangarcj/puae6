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

El generador sigue siendo la autoridad: los modelos genericos 3/4/5 comparten
cpuemu_0, los compatibles 11/12 cpuemu_11 y el compatible 68020 cpuemu_20.
Los handlers compatibles que devuelven
ciclos tienen una tabla del tipo correcto separada de los handlers CE void del
parche 0311. Los fuentes de handlers CE siguen identicos byte a byte. Los parches
required 0343 y 0344 seleccionan accesos, tablas y bucles existentes de upstream.

El parche 0346 acota el archivo generico a las CPUs ofrecidas (68020/010/000):
no se generan tablas ni handlers 030/040/060 que importen modulos excluidos.
El enlace nativo descarta esos caminos, mientras el guest Waterbox enlaza los
objetos completos; el perfil debe ser correcto en ambos. Los PMMU no entran en
las tablas generadas; la tabla por defecto los rechaza con la excepcion de linea F
mediante `op_illg`, y sus helpers MMU y
MMU ficticia no se compilan en el perfil portable. No se cambian la recoleccion
de secciones, las exportaciones del guest ni los interpretes CE. La prueba
dirigida ejecuta un PMOVE sin 68851, comprueba el vector 11, su continuacion y
su restauracion por la ABI publica en los tres niveles del 68020.

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

T55b incorpora el A4000/030 real como maquina 11 (9/10 reservados para CD32/CDTV),
con mapa de 32 bits, AGA, Fat Gary revision 0, Ramsey 15, Ricoh y el IDE fisico
incluso sin disco. La primera vertical solo acepta CPU normal y declara sin MMU:
compatible y exact fallan al cargar hasta cubrir sus caches. El generador recibe
`--no-mmu` en la receta fijada y genera PMMU mediante `op_illg`, sin leer la
extension ni simular registros MMU. La generacion sin argumentos conserva upstream.
El nuevo cuerpo CPU guarda controles 030 e instruction_cnt; P6RM añade los cuatro
bytes Gary/Ramsey y P6DK conserva tambien el controlador IDE vacio. La RAM Fast
cero sigue significando la del preset (8 MiB Zorro II en A4030), con NONE explicito.
El esquema comun expone el modelo y el preset a4030_normal; los tres frontends
usan el mismo ABI. La aceptacion de arranque/restauracion real Waterbox sigue
pendiente en T55b.

T55c1 prepara tambien las tablas normales 040/060 en la receta sin MMU. Las
opciones publicas siguen rechazando ambos modelos hasta completar sus controles,
FPU y estado. Los opcodes PMMU se rechazan antes de leer extensiones; MOVEC de
controles de traduccion tambien es ilegal, sin fingir que una escritura activa
una traduccion. CINVA/CPUSH conservan las funciones upstream sobre las caches
reales; solo se excluye mantenimiento MMU, con pagina fisica de 4 KiB al no
admitirse TC. No se habilitan los bucles compatibles/CE de 040/060.
