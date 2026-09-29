# T06b2ba — Fallos de ROM sin fallback AROS

Parche0032 excluye del perfil P6 la ROM AROS embebida y su fallback. :AROS
devuelve UNSUPPORTED; falta de banco, apertura fallida y fallo detectado por el
lector producen NO_MEMORY, OPEN_ERROR y READ_ERROR. La consulta interna
p6_memory_rom_status usa almacenamiento ordinario por imagen/sandbox. Una nueva
carga reemplaza el resultado; init/cleanup dejan UNLOADED. La consulta de version
y los diagnosticos no lo borran. LOAD_OK sigue siendo completion del lector
legacy, no validacion de firmware.

restore_roms retorna en fallo antes de patch_kick y vuelve a proteger ROM.
m68k_go rechaza estados distintos de LOAD_OK antes de su preambulo; si el reset
produce un error, sale antes de m68k_reset2 y la lectura de vectores, ejecutando
el teardown normal. Productor y consumidor se entregan atomicamente; los dos
probes independientes justifican el numero de archivos y las fronteras de test.
La decision y sus limites quedan en ADR0002. No hay cambios de ABI publica.

El probe de memoria extrae sin editar cuerpos completos de loader, restore,
consulta de version, reload, init y cleanup. Enlaza zfile/blob/diagnosticos reales.
read_rom_name abre el blob crudo como frontera observada; bancos/dispositivos,
metadata, parcheo y proteccion son fixtures observados o abortan si se invocan
fuera del escenario. No son proveedores de produccion. Prueba los cuatro errores,
80 repeticiones sin fuga de streams, retorno antes de patch_kick, recuperacion,
init/cleanup y persistencia tras version/reload sin solicitud/limpieza de logs.
El payload de512 KiB con ceros solo prueba finalizacion del cargador legacy.

Luna implemento el probe del cuerpo real m68k_go: todos los estados no-OK rechazan
antes del preambulo; fallo inyectado en custom_reset impide reset-vector fetch y
ejecucion CPU, y verifica argumentos/conteo de cleanup e in_m68k_go restaurado.
Enlace estricto, sin permitir simbolos indefinidos. Revision Luna del parche y
ciclo de vida no encontro defectos dentro del alcance.

Limite de cobertura: no se ejecuta memory_reset ni la cadena completa
devices_reset/custom_reset. Se comprueba que memory_reset no escribe el estado;
la revision verifica que su ruta memory_init tambien fuerza restore_roms. El
probe CPU inyecta el fallo en la frontera custom_reset. Validacion de tamaños,
checksum, transforms/aliases, firmware y propagacion p6_load/reset/run siguen
obligatorias en T06b2bb/T06c. No se afirma arranque ni enlace del motor completo.

Verificacion:53 tests pasan (incluyen los dos probes,35 documentales y regresiones
ROM/zfile/CPU/halt/audio/diagnosticos). Ambos probes O0/O2 ASan/UBSan pasan.
memory.cpp/newcpu.cpp
completos compilan en CMake Debug/Release. GEN01:39 outputs iguales en dos builds
HOST limpios. HOST01:83 rutas,196 archivos/776 includes/332 identificadores.
SRC01:1134 archivos pristine verificados. Cola32 parches/20 archivos/730 lineas
de delta (0032 añade55 de delta, sin ampliar la superficie de archivos).
nm del objeto memory completo O0/O2 confirma ausencia de AROS/seriallog/gunzip,
ademas de las rutas host ya excluidas. Sin nuevos fallos; permanecen warnings
upstream de conversion ULONG_MAX a int en audio. validate y diff --check pasan.
No se modifican upstream pristine ni MacHawk. Los logs de ejecuciones correctas
son temporales; no se repite el build global cuya ultima evidencia esta en T06b2ay.

T06b2ba DONE; T06b2bb queda desbloqueada con validacion de firmware como primer paso.
