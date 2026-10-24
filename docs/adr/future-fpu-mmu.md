# T44: estudio de MMU 68030/040/060

## Decision y alcance

La MMU es una extension opcional posterior al bloque CPU T55. Este ADR y
`plan/extensions/fpu-mmu.json` definen su matriz y subbacklog; no habilitan
opciones, fuentes, tablas, registros ni traduccion. Los identificadores MMU-A..H
son propuestas locales de este estudio, no tareas activas ni comandos existentes.
El coordinador debera crear fichas acotadas antes de implementar cada vertical.
T55c12/CU06 denormal conserva su extra independiente posterior a T41 (ADR0016).

Autoridad de implementacion: WinUAE6.0.3/tag6030,
commit `857f48fcb09399d4d95527e29a2fdeaedb41f827`, snapshot local
`upstream/winuae` verificado por `manifests/upstream-files.json` y
`plan/upstream.lock.json`. Las referencias de abajo son rutas y simbolos de ese
snapshot, no atribuciones de exactitud a hardware. Antes de aceptar traduccion
haran falta resultados esperados independientes segun el manual de cada CPU,
con edicion/secciones fijadas en la ficha del probe; un escritor/lector del mismo
emulador no basta como oraculo. No se necesita adquirir firmware para este estudio.

## Matriz

Cada fila se desdobla en dos entradas explicitas en el JSON. Los numeros CPU/FPU/
MMU nombran modelos semanticos; no son una ampliacion del ABI actual.

| CPU / maquina | FPU | MMU actual | MMU por estudiar | Camino upstream Normal |
| --- | --- | --- | --- | --- |
| 68030 / A4030 | ninguna | 0 | 68030 | cpuemu_32 / m68k_run_mmu030 |
| 68030 / A4030 | 68881 | 0 | 68030 | cpuemu_32 / m68k_run_mmu030 |
| 68030 / A4030 | 68882 | 0 | 68030 | cpuemu_32 / m68k_run_mmu030 |
| 68040 / A4040 | ninguna | 0 | 68040 | cpuemu_31 / m68k_run_mmu040 |
| 68040 / A4040 | 68040 interna | 0 | 68040 | cpuemu_31 / m68k_run_mmu040 |
| 68060 / A4040 | ninguna | 0 | 68060 | cpuemu_33 / m68k_run_mmu060 |
| 68060 / A4040 | 68060 interna | 0 | 68060 | cpuemu_33 / m68k_run_mmu060 |

Solo las siete entradas MMU0 son perfiles publicos actuales: Normal, sin caches
de instrucciones/datos ofrecidas para 040/060 y con SoftFloat entero si hay FPU.
FPU ninguna es una configuracion del port, no una promesa de reproducir todas las
variantes fisicas EC/LC. Se rechazan FPU interna de otro modelo, FPU externa en
A4040. La matriz futura descarta parejas CPU/MMU diferentes; el ABI actual no
ofrece selector MMU. Compatible/Exact 030/040/060, 68020+68851, JIT,
aceleradoras y floats host no se incorporan a esta matriz. Una futura MMU no
autoriza ofrecer esos otros niveles ni cambiar el defecto.

## Fronteras actuales que deben preservarse

- `bridge/config.cpp:build_preferences` fija `currprefs.mmu_model=0` en A4000;
  `validate_config` exige Normal y pareja FPU valida. El ABI no ofrece selector
  MMU. `ports/portable/sysconfig.h` rechaza MMUEMU solicitado desde fuera y no
  activa CPUEMU31/32/33; los dos modulos MMU no estan seleccionados para enlace.
- `manifests/codegen.json` usa `--no-mmu`. Los parches0346/0349 y
  `newcpu_common.cpp:movec_illg` rechazan PMMU y controles de traduccion, sin
  fingir escrituras exitosas. El handler ilegal conserva el PC y no lee extension
  ni operando. No retirar esa politica de los perfiles MMU0.
- `bridge/state_cpu.cpp:visit_030_normal` solo conserva controles de CPU030;
  `visit_late` exige explicitamente no-MMU. No son codecs MMU. El
  `newcpu.cpp:save_mmu` upstream escribe modelo/flags, no un inventario completo
  de ATC/reintentos: copiarlo no acredita restauracion determinista.
- `include/mmu_common.h` usa excepciones C++ en C++ y setjmp/longjmp en la rama C.
  Revisar la clausura real de fallos antes de enlazar; no cruzar C++ con longjmp
  ni usar una excepcion para sustituir el corte por frame. Los tres bucles MMU
  de `newcpu.cpp` necesitan su propio probe del retorno comun `p6_run_frame`.

## Subbacklog y oraculos

El JSON contiene dependencias y salidas comprobables. Primero MMU-A fija contrato,
identidad de reinicio y modos; MMU-B demuestra tablas/imports/continuacion con
probes pequenos sin exponer la opcion. MMU-C/D/E separan los protocolos030/040/
060. MMU-F decide campo por campo que se guarda y que puede reconstruirse;
MMU-G demuestra DET02 y rechazo transaccional. MMU-H es la puerta publica de las
filas completas, incluida ejecucion Waterbox real. Ninguna fila futura pasa por
compilar, por arrancar firmware ni por un probe nativo en lugar de Waterbox.

ATC con entradas obsoletas puede ser observable hasta un flush: no vaciarlo al
restaurar por llamarlo cache derivada. Inventariar tambien reemplazo/MRU,
descriptores used/modified en RAM, acceso supervisor/usuario, funcion de bus,
prefetch, accesos parciales, writeback y reintentos. Una reconstruccion requiere
prueba de equivalencia del futuro y ticks; los punteros a bancos/funciones host se
reconstruyen por sus identidades, nunca se serializan. MMU no traduce el DMA del
chipset como si fuese acceso CPU; incluir un probe que distinga ambos caminos.

Las paginas Amiga de traduccion y las paginas host Waterbox4/16KiB son contratos
distintos. Probar ambas sin alinear ni omitir bytes del estado para esconder una
diferencia. El nuevo modo integra CPU/FPU/MMU/precision en identidad y esquema
comun; un estado ajeno falla antes de mutar memoria, tablas o callbacks. No se
promete compatibilidad de estados entre builds.

## Comprobaciones existentes y puerta futura

GEN01 literal es `python3 tools/generate_cpu.py --check`: dos generaciones HOST
limpias. `tests.codegen.test_gencpu_models.GencpuModelTests` comprueba ademas
tablas actuales y rechazo no-MMU; no demuestra tablas MMU31/32/33. Los checks
GEN01/DET02 de T55c/T55 siguen registrados en `tools/check.py`; entre sus
selectores estan `tests.state.test_a4040.A4040Tests`, `tests.state.test_z3`,
`tests.host.test_cpu_late_state.CpuLateStateTests` y el probe FRESTORE admitido.
`tests.state.test_cpu_modes.Cpu020NoMmuTests` conserva el negativo PMOVE con
futuro restaurado; `tests.state.test_a4030` cubre controles/lineaF030.

Estos checks conservan los perfiles MMU0. No son resultados de la matriz futura.
Para cada nueva fila haran falta GEN01 de sus tablas, resultados independientes
de traduccion/fallos/RTE, estados en el mismo proceso y uno nuevo, nueva instancia
Waterbox, paridad de frontends, matriz ISA/compilador y presupuestos medidos. La
FPU opcional debe repetirse en los accesos MMU, incluido FMOVEM reiniciable; no
usar CU06 pendiente como supuesto resuelto. El estudio solo valida coherencia
del documento/JSON; no modifica ni vuelve a ejecutar esas pruebas del producto.
