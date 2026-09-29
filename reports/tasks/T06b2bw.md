# T06b2bw — residuos desktop

Parche0043 guarda cfgfile_addcfgparam(0) en devices_free y
cfgfile_createconfigstore en main.fixup_prefs. La lista privada temp_lines y
todos sus producers/consumers se excluyeron con0042; no existe estado desktop
que liberar enP6. Los fixups y cleanups de hardware permanecen intactos.
43 parches/25 archivos/1187 lineas de delta (+4).

Auditoria Luna y comprobacion raiz:33 unidades preprocesadas completas (28
manuales+5 generadas), cero usos de28 APIs calc/ini fuera de sus declaraciones.
Objetos disponibles Debug30/Release20 tampoco las definen ni importan; faltaban
objetos disk/inputdevice/savestate, pero sus preprocesados si se comprobaron.
Evidencia en build/t06b2bv-selection-audit. calc solo alimentaba formulas desktop;
ini, geometria de hardfile/FILESYS. Ambas salen de selection.upstream_units y
entran en excluded_units con motivo; audit_sources/pristine permanecen.

Se amplia test_native_cmake para preprocesar toda la seleccion y detectar usos
de esas APIs, incluso en headers, retirando solo declaraciones (no llamadas ni
address-taking). El check existente de grafo verifica ambas configuraciones
contra el manifiesto. Se ajusta test_devices a imports/teardown reales, sin
provider de cfgfile_addcfgparam en el probe. test_main_source exige ausencia de
cache/parametros desktop en el objeto completo. No se agrega test que copie
las cuatro llamadas de fixup ni se simula un parser exitoso.

Objetos main/devices CMake Debug/Release PASS, sin warnings.46 tests unicos PASS:
devices, main_source, cfgfile_source/defaults/preset, codegen.native_cmake/cmake y
plan. GEN01:39 salidas iguales en dos builds HOST limpios; HOST01:83 rutas,
196 archivos,776 includes,332 identificadores; SRC01:1134 pristine verificados.

El build global --target p6_engine -- -k 0 conserva tres fallos reales:disk,
inputdevice y savestate, registrados en build/t06b2bw-engine-failures.log.
La reduccion5->3 procede de seleccion correcta de dependencias desktop, no de
portar calc/ini ni de resolver sus funciones con stubs.
Rutas de medios en cia/disk, comandos/exporters de input y callers de savestate
siguen pendientes. No declara enlace ni configuracion publica/boot/frame.

T06b2bw DONE; T06b2bx desbloqueada. validate y diff --check PASS.
