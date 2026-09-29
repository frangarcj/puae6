# T06b2bu — configuracion de biblioteca

Parche0042 (47 lineas delta) separa parser/exporter/CLI desktop de los presets y
helpers reales de cfgfile. Mantiene clearmountitems/discard_prefs, addbcromtype,
copy/default/builtin, compatibilidad, configchange y lista de errores. P6 no crea
la cache default_file del exporter ni migra nombres de adaptadores de red.
No hay stubs que finjan parsear. Cola:42 parches/25 archivos/1183 lineas de delta.

error_log adapta el formatter al contrato C99: vsnprintf devuelve longitud
requerida, va_copy separa la primera pasada y se reserva capacidad exacta si el
mensaje supera256 bytes. No hay alias global TCHAR; esto no altera errores
causales ni los convierte en un status de exito del motor.

Compila cfgfile.cpp completo en CMake Debug/Release, sin warnings de esa unidad.
40 regresiones cercanas PASS (cfgfile_preset, custom_vsync, audio_config,
expansion_source y test_plan). Warnings ya registrados custom/expansion siguen.
GEN01:39 salidas iguales en dos builds HOST limpios. HOST01:83 rutas,196 archivos,
776 includes,332 identificadores. SRC01:1134 pristine verificados offline.
Tres pruebas nuevas PASS; total43 tests unicos. cfgfile_source compila objetos
completos O0/O2 y verifica definiciones T de los helpers conservados, ausencia de
parser/exporter/CLI y de imports red/zfile/files/reloj/numeric parsing; depfiles
sin IDE/thread ni Windows salvo CCR autorizado. No usa linker-GC para esa auditoria.
error_log ejecuta cuerpos reales con captura observacional de write_log: mas de1KB,
argumentos multiples, lista LIFO, vacio y limpieza/null, O0/O2 ASan/UBSan.

cfgfile_defaults compara el cuerpo completo default_prefs y clearmountitems
pristine frente a P6 en binarios separados O0/O2 ASan/UBSan. La huella de todos
los bytes de prefs (inicializados por memset real, sin punteros externos en la
prueba) coincide con reset0/1; orden/argumentos de callbacks y efecto target se
conservan. Solo faltan los cuatro callbacks de cache desktop previstos. ROM,
input/blkdev/target y cache son fronteras observadas, no backends implementados.
La prueba no depende de snapshots build antiguos: lee pristine fijado.

Build global --target p6_engine -- -k 0 reduce6->5 unidades fallidas:disk,
inputdevice,ini,savestate,calc. Log en build/t06b2bu-engine-failures.log.
No declara enlace. Diagnostico adicional -fsyntax-only -ferror-limit=0 con los
mismos flags, guardado en build/t06b2bu-remaining/: disk15, inputdevice69, ini21,
calc5 y savestate3 errores. Esto evita disenar el siguiente delta desde los
primeros20 errores que limita el build normal.

Auditoria Luna de objetos y callers: cia/disk necesitan resolver rutas de medios;
devices_free llama cfgfile_addcfgparam(NULL) para limpiar parametros; main.fixup
llama cfgfile_createconfigstore (export legacy), y main/expansion usan compat.
inputdevice_uaelib tiene fallback a cfgfile_parse_line y su serializer usa write*;
custom ya excluye parse_lines en P6. Esas fronteras quedan en T06b2bv: resolver
medios VFS requeridos y guardar callers de features excluidas, sin retornos OK
inventados. No retirar calc/ini de seleccion sin comprobar todos sus callers.

default_prefs/built_in_prefs no tienen caller de inicializacion seleccionado aun;
se conservan para el lifecycle comun pendiente. Sus imports inputdevice/blkdev,
target, ROM y helpers de modelos aun no acreditan servicios reales ni validacion
publica del perfil. No hay boot/frame/snapshot Waterbox nuevo certificado.

T06b2bu DONE; T06b2bv desbloqueada. validate y diff --check PASS.
