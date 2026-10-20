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
al cambiar la opcion. La prueba equivalente del wrapper ya se ha ejecutado con
el guest integrado: snapshot y continuacion identica en los tres niveles de las
tres familias, y rechazo de un estado de otro nivel antes de mutar el sandbox.

La puerta de rendimiento consume una unica candidata fija con
`tools/bench.py run --id t53-cpu --compare-cpu-levels`: tres familias, tres
niveles y tres capas (nativo, libretro y WaterboxCore Release), cinco repeticiones
intercaladas y veinte operaciones de estado por ventana. La identidad vincula
fuentes, ejecutables, guest, runtime y micro-ROMs; solo admite reanudar un prefijo
cronologico completo. Los ticks se comparan entre capas del mismo nivel. Las
medidas son orientativas en el Mac compartido; no justifican cambiar el defecto
exact ni prometer que compatible o normal sean siempre mas rapidos. PERF01
exige las 135 ventanas medidas completas y actuales para acreditar esta puerta.

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
usan el mismo ABI. T55b verifica el arranque con Kickstart A4000 autorizado y la
restauracion del futuro en nativo, libretro y Waterbox, incluido proceso nuevo.

T55c1 prepara tambien las tablas normales 040/060 en la receta sin MMU. Las
opciones publicas siguen rechazando ambos modelos hasta completar sus controles,
FPU y estado. Los opcodes PMMU se rechazan antes de leer extensiones; MOVEC de
controles de traduccion tambien es ilegal, sin fingir que una escritura activa
una traduccion. CINVA/CPUSH conservan las funciones upstream sobre las caches
reales; solo se excluye mantenimiento MMU, con pagina fisica de 4 KiB al no
admitirse TC. No se habilitan los bucles compatibles/CE de 040/060.

T55c6 conecta al visitor P6CP los controles de 040/060 Normal y la captura
privada de FPU de T55c4. Guarda VBR/MSP/CACR/SFC/DFC e instruction_cnt;
060 añade BUSCR/PCR y permite el estado LPSTOP. Las mascaras siguen MOVEC de
upstream y la parte inmutable de PCR debe coincidir con modelo/revision/FPU.
El bloque interno de FPU es semantico, campo a campo, con todos los operandos
FSAVE y estados pendientes; Validate comprueba el contexto sin tocar el owner
y Apply reconstruye el modo SoftFloat. El probe enlaza el visitor real y compara
MOVEC, FSAVE y la siguiente operacion aritmetica tras reinicializar y alterar
todo el estado privado. Es una puerta de componente: configuracion publica,
ejecucion por frames, FRESTORE040 y aceptacion de A4040 siguen en T55c.

T55c7 corrige la rama UNIMP de FRESTORE040 que solo saltaba el cuerpo:
recupera campos definidos de los frames $28/$30 mediante el bus real y publica
la excepcion aritmetica indicada por FPSR/FPCR y E1/E3. E3 se selecciona antes
que E1 y solo corresponde a overflow/underflow/inexact. Sin excepcion activa
la FPU queda idle. NULL/IDLE descartan pendientes del contexto anterior; NULL
reinicializa tambien el modo SoftFloat. El frame no restaura los registros del
programador, excepto los efectos de NULL que upstream ya define.
El oraculo construye frames por offsets independientes del writer y verifica
lecturas, campos, FSAVE, postincremento, formato erroneo y aritmetica posterior.
Fuentes: [MC68040UM, 9.8 y figura9-10](https://www.nxp.com/docs/en/reference-manual/MC68040UM.pdf)
y [Motorola FPSP gen_except 3.7](https://github.com/torvalds/linux/blob/1da177e4c3f41524e886b7f1b8a0c1fc7321cac2/arch/m68k/fpsp040/gen_except.S).
La reanudacion completa de BUSY (CU_SAVEPC/writeback), los casos no aritmeticos
y la habilitacion publica de A4040 siguen pendientes en T55c.

T55c8 conserva los campos definidos del frame BUSY040 de100bytes: CU_SAVEPC,
mascara dirty, WBTEMP, FPSR_SHADOW/FPIARCU, comandos1/2/3, NMNEXC/NMCEXC,
exponentes extendidos, tags y S/X/U/T. Se leen todas las palabras por el bus
antes de publicar; las reservadas no forman parte del codec. FSAVE y el estado
privado/P6CP retienen esos campos. La excepcion aritmetica E3/E1 se publica antes
de la reanudacion CU existente, sin ejecutar la operacion joven ni cambiar FPn.
El probe construye los offsets independientemente del writer, restaura otro
owner y comprueba tambien el vector real de la siguiente instruccion.
Offsets y prioridad: [Motorola FPSP fpsp.h](https://github.com/torvalds/linux/blob/1da177e4c3f41524e886b7f1b8a0c1fc7321cac2/arch/m68k/fpsp040/fpsp.h)
y gen_except.S de la misma revision. Preservar esos campos no acredita la semantica
completa de writeback, dirty, S/X ni CU_SAVEPC06. No copiar WBTEMP a FPn por
CU_SAVEPC=FE: FPSP puede haber escrito el resultado corregido y limpiado su
bit dirty antes de FRESTORE. Esa reanudacion completa sigue pendiente en T55c.

La continuacion FE que genera una excepcion aritmetica nueva utiliza FPIARCU
como direccion causante (MC68040UM9.2.4/9.8); el chequeo recibe la copia local
del frame antes de reinicializar fsave_data. No se altera FPIAR para una E3
ya pendiente ni se inventa la EA/opword original. El probe normaliza denorms
extendidos de mantisas1 y3 segun [FPSP round.S:nrm_set](https://github.com/torvalds/linux/blob/1da177e4c3f41524e886b7f1b8a0c1fc7321cac2/arch/m68k/fpsp040/round.S)
y res_func.S:wrap_div/fix_stk: exponentes-63/-62 y bitsE15. La inversa conserva
exactamente esas mantisas; FDIV da1/3 e INEX2, con FPIAR causante separado del
PC de reintento del frameCPU0 y preservado por FSAVE. No acredita exponentes
FE arbitrarios ni completa writeback/SX/SAVEPC06 o los perfiles publicos040/060.

CU_SAVEPC06 se reanuda para la reconstruccion extended de revision40 que define
[Motorola FPSP bugfix.S:op0_xi/op0_sete15](https://github.com/torvalds/linux/blob/1da177e4c3f41524e886b7f1b8a0c1fc7321cac2/arch/m68k/fpsp040/bugfix.S).
Ese frame recien vaciado carece de FPTEMP: toma FPn como destino y conserva
el registro fuente IEEE extended en ETEMP, con ETE15=!ETE14. La conversion FE
no sirve para esa etapa: para1.0 ETE15 vale1. Se reutilizan la aritmetica y la
prioridad de pendientes existentes. El oraculo usa resultados matematicos,
FCMP/FTST sin escritura y el protocolo FPSP; no es una captura de hardware.
La revision41, el formato double de op2_xi, otros micro-PC y la semantica general
de dirty/SX/writeback siguen en la puerta pendiente de T55c.
