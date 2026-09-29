# T06b2bq — expansión del perfil A500

Parche0040 compila expansion.cpp completo: include threaddep sin uso excluido,
sufijo de nombre con temporal numérico16, catálogo de periféricos vacío y opción0
sin aceleradora conservada con dummy_sub. Helpers/settings de dispositivos fuera
de A500 y hooks FILESYS quedan guardados; parser/ordenación/AutoConfig RAM y
memoryboards permanecen. No hay nuevos init que finjan éxito ni hardware habilitado.
40 parches/25 archivos/1132 líneas de delta (+42); ADR0004 acepta expansion/cfgfile.

El primer diagnóstico de Luna no descubrió toda la unidad; al compilarla realmente
aparecieron39 errores adicionales de catálogos/FILESYS/red. Se conservan en
build/t06b2bq-expansion-all-errors.log y el primer log Debug/Release. La ficha se
amplió explícitamente, sin retirar la puerta de compilación completa. La segunda
auditoría Luna comprobó que Chip/Slow no proceden del catálogo y que [0][0] de
cpuboards debe existir. Los accesos públicos a otros índices aún deben rechazarse.

El catálogo reducido reveló índices directos de controladora: get_unit_expansion_rom
ahora termina al final del catálogo real; write_filesys_config P6 no exporta ni
indexa mounts, y avisa explícitamente ante mountitems!=0. Es diagnóstico legacy,
no status causal público. Se prueba también lookup con catálogo sintético de dos
entradas para demostrar resolución válida y límites, no retorno NULL indiscriminado.

Validación:40 tests únicos PASS en test_expansion_source (2), test_expansion_cards,
test_main_source, test_rommgr_source y test_plan. Compilan objetos expansion/rommgr
CMake Debug/Release y O0/O2 sin linker-GC ocultando imports. Depfiles sin threaddep,
ni Windows salvo CCR autorizado. Ausentes formatter TCHAR y registro/red excluidos.
El cuerpo real del exporter y catálogos/lookups reales se ejecutan O0/O2 ASan/UBSan:
sentinelas/dummy correctos, rangos SCSI/IDE/custom y valores extremos seguros;
mountitems0 no avisa, valores positivos/negativos no soportados avisan sin escribir.
No se afirma compilación completa de cfgfile, todavía bloqueada por commpipe.

Probe Luna ejecuta expansion_parse_cards completo extraído contra pristine en
binarios separados, O0/O2 ASan/UBSan: prioridad CPU/ROM/label/bank/default, devnum
0/1/255, límite127 bytes, callbacks initnum/initrc, fallo, child, base/size/highram.
Raíz refuerza buffers autoconfig inicializados aFF y comparación de hash de nombres,
metadatos y llamadas observadas. Autoconfig profundo y logging inesperados abortan;
no simula un dispositivo real ni prueba bus/boot. Capacidad legacy final<=127 bytes
sigue siendo precondición; el temporal no vuelve segura la API para nombres largos.

GEN01:39 outputs iguales en dos builds HOST limpios. HOST01:83 rutas y196 archivos/
776 includes/332 ids. SRC01:1134 pristine verificados. Upstream/MacHawk intactos.
Build global --target p6_engine -- -k 0 baja7->6 unidades fallidas:
disk,inputdevice,cfgfile,calc,ini,savestate (build/t06b2bq-engine-failures.log).
Cuatro warnings expansion anteriores permanecen: arrays comparados con NULL,
NULL convertido a número y argumento de log sobrante; revisión causal en T06b2br.
Otros imports host y validación de prefs siguen abiertos; no hay core enlazado,
frame, configuración pública validada ni snapshot Waterbox nuevo certificados.

T06b2bq DONE; T06b2br desbloqueada. validate y diff --check PASS.
