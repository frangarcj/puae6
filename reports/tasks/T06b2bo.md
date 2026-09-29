# T06b2bo — main como soporte de biblioteca

Parche0039 separa CLI/launcher desktop de globals y helpers del motor. P6 no
compila main/real_main/real_main2, parser/swapsheet, start_program/do_start_program
ni leave_program. No se copian ni simulan sus servicios de inicialización. La
unidad retiene prefs, PRNG, helpers y uae_reset/quit/restart sin cambios de cuerpo.
El include threaddep no tenía consumidores; se excluye en P6.

uaerandomizeseed pasa currprefs.seed directamente a uaesetrandseed, cuyo cero->1
es el contrato T05e. No existe import getlocaltime ni cambia xorshift/consumo.
El cleanup sin AUTOCONFIG referenciaba tres miembros inexistentes en WinUAE6030;
usa arrays actuales fastmem/z3fastmem/rtgboards. Prueba con prefs reales confirma
limpieza de todos sus tamaños, idempotencia y conservación del resto, Chip/Slow
incluidas. Es fixup legacy, no el rechazo de configuración de la API pública.

39 parches/23 archivos/1090 líneas de delta (+19); ADR0004 acepta main como
archivo nuevo. No se alteran pristine, generados ni MacHawk.

Validación:
- Objeto main completo en CMake Debug/Release y clang O0/O2: PASS.
- test_main_source: objeto sin GC, símbolos/imports/depfiles; no main ni llamadas
  m68k_go, UI/startup/CLI o threaddep; reset/quit/restart iguales a pristine.
  Prueba de cleanup real con ASan/UBSan O0/O2: PASS.
- test_main_seed (Luna): bloques PRNG reales en ejecutables referencia/P6 separados,
  0/1/2/0x80000000/max/0x12345678, secuencias conocidas, seed frente a estado vivo,
  continuación restaurando ambos globals y lazy init. Referencia recibe semilla
  normalizada y aborta si llega al fallback host. O0/O2 ASan/UBSan y tres TZ: PASS.
- test_civil_time:2 PASS; se marca maybe_unused su fallback abortable antiguo.
- Regresiones test_rtc/test_cia/test_cpu_rom_guard/test_plan:38 PASS.
  Total42 pruebas únicas; no se cuenta repetir como cobertura adicional.
- GEN01:39 salidas iguales en dos builds HOST limpios; HOST01:83 rutas,
  196 archivos/776 includes/332 ids; SRC01:1134 archivos pristine verificados.

Revisión raíz refuerza que uaerandgetseed no reemplaza el estado vivo tras cinco
muestras. Un fallo inicial de test confundía start_program con restart_program
al buscar subcadenas nm: ahora inspecciona nombres desmanglados de funciones.
El probe de prefs usa almacenamiento estático: uae_prefs incluye configuración
extensa y no pertenece a la pila del test. No fueron fallos de producción.

Build global --target p6_engine -- -k 0 conserva siete fallos (antes ocho) en
build/t06b2bo-engine-failures.log: disk,inputdevice,cfgfile,expansion,calc,ini,savestate.
main aún importa target_quit/restart/fixup, consolehook_shutdown y consultas de
cpuboard/gfxboard/blkdev: no se sustituyen por éxito ficticio; se cierran en
T06b2bp/T06c. cfgfile conserva un caller CLI de usage que debe excluirse con su
parser. No hay core enlazado, bootstrap p6_*, frame ni snapshot real certificados.
El campo público de semilla64 requiere validación frente al PRNG32 en lifecycle.

Auditoría Luna identifica siguiente frontera en expansion: threaddep no usado y
un formato de sufijo activo en expansion_parse_cards; conservar enumeración y
nombres autoconfig, sin borrar hardware para ocultar el formatter. Los otros
formatos quedan bajo #if0/SANA2. Esa corrección aún no está implementada.

T06b2bo DONE; T06b2bp desbloqueada. validate y diff --check PASS.
