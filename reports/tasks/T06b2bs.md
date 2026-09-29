# T06b2bs — limpieza completa de bancos del preset

Parche0041 corrige dos indices en buildin_default_prefs: cada vuelta limpia su
ramboard Fast/Z3 completo, en vez de repetir slot0. Es correccion de estado,
candidata a upstream; no se ha enviado. 41 parches/25 archivos/1136 lineas de delta.
El contrato y su limite se registran en ADR0001 y host-surface.configuration.

La prueba usa los cuerpos reales de buildin_default_host_prefs,
buildin_default_prefs, set_68000_compa, bip_a500, has_expansion_with_rtc y
built_in_chipset_prefs. target_default_options, cfgfile_compatibility_romtype y
configure_rom son callbacks observados de prueba; no servicios implementados.
No ejecuta built_in_prefs completo, carga de ROM, cfgfile completo ni una maquina.

Regresiones cercanas: 40 tests PASS (main_source, main_seed, expansion_source,
rommgr_source y test_plan). GEN01:39 salidas iguales entre dos builds HOST limpios.
HOST01:83 rutas,196 archivos,776 includes,332 identificadores; SRC01:1134 archivos
pristine verificados. Los cuatro warnings expansion ya documentados permanecen.
Probe especifico PASS en O0/O2 con ASan/UBSan: referencia pristine falla ante
slots Fast/Z3 poblados; referencia limpia y P6 limpia/poblada dan la misma huella
de campos estables. Comprueba cada byte de todos los ramboard, reaplicacion con
estado identico, OCS CE68000, Chip/Slow512K, PAL/NTSC y semilla conservados.
Config3 sin Slow no hereda RTC; configure_rom recibe IDs6/32/-1 y propaga0/1.
Total41 tests unicos PASS incluyendo este probe. El storage de prefs es static;
usar asignacion aggregate generaba un temporal demasiado grande en el test,
corregido con memset sin cambios al motor.

Luna audito cfgfile en copia temporal quitando solo el include IDE y obtuvo224
errors completos; log y resumen en build/t06b2br-cfgfile-probe. ide.h introduce
commpipe/threaddep, aunque cfgfile solo usa sus dos APIs ATA para geometria.
El grueso restante es formatting/parsing TCHAR, mas conflicto static/extern,
separador FSDB y ATA. Presets no disparan errores en ese diagnostico. No se ha
quitado el include en produccion ni ocultado errores con stubs. La continuacion
T06b2bt debe resolver callers custom/inputdevice al separar configuracion host.
Las seis unidades fallidas del ultimo build global siguen pendientes; no hay
nuevo core enlazado, boot, frame ni snapshot Waterbox certificados.

Caso previo preservado en build/t06b2bs-before/: header pristine extraido, copia
del probe, ejecutable ASan O0 y repro.log con comando y salida. dirty termina
por SIGABRT (-6) con Fast/Z3 RAM slot remains populated, no por sanitizer.
T06b2bs DONE; T06b2bt desbloqueada. validate y diff --check PASS.
