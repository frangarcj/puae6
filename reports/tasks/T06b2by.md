# T06b2by — soporte compartido de estados

Parche0044 compila savestate.cpp conservando globals y helpers. Versionheader
usa snprintf con temporal100 (tres int siempre caben); P6 excluye solo el loop
export CD y savestate_quick desktop. No proporciona un guardar/restore ficticio.
Parche0045 corrige cuatro logs: size_t %zu, uae_u32 %u, ptrdiff_t %td. Candidato
upstream sin envio; no modifica bytes de estado ni algoritmos de hardware.
Cola45 parches/26 archivos/1203 lineas de delta (+16), ADR0004 acepta archivo26.

Objetos completos CMake Debug/Release PASS sin warnings de esa unidad;44 tests
cercanos PASS (devices, cfgfile_source/defaults/preset, native_cmake y plan).
GEN01:39 salidas iguales en dos builds HOST limpios; HOST01:83 rutas,196 archivos,
776 includes,332 ids; SRC01:1134 pristine verificados. Dos pruebas propias PASS,
total46 tests unicos. Unidad entera O0/O2 -Werror=format, globals B/D/S definidos
(Mach-O usa S, no U), writers/readers/free/save_state T; sin _stprintf, save_cd ni
savestate_quick, depfiles sin threaddep/Windows salvo CCR autorizado.

Prefijo REAL de save_state_internal hasta antes de save_cycles y writers reales
save_u32/save_string comparados contra pristine, O0/O2 ASan/UBSan. Referencia usa
sprintf solo para el formatter decimal fijado; aparece su warning de deprecacion
macOS, no en produccion. Prueba bytesbig-endian, version6.0.3, descripcion normal/
vacia, longitudes, avance y centinelas. save_chunk/getname/log/conversion UTF8 son
fronteras observadas. No ejecuta ni certifica framing completo, resto del
serializer ni backends. El probe fue revisado para detectar overwrite del byte
inmediato tras la cadena y exigir definiciones reales en nm, no solo nombres.

Build global --target p6_engine -- -k 0 baja3->2 unidades fallidas:disk e
inputdevice. Log en build/t06b2by-engine-failures.log. No acredita enlace.
La cabecera de ASF conserva buffer1000 legacy sin validar descripciones
arbitrarias; esa validacion sigue en T15 antes de exponer serializer publico.
Ningun resultado de esta tarea demuestra estados nativos, rewind o Waterbox.

Auditoria de imports de unidad completa conservada en
build/t06b2bx-savestate-audit/ (comando, objeto y nm, sin warnings). Permanecen
restore_cd/restore_cdtv_final, save/restore_gayle(_ide), inprec_*, save_screenshot,
save/restore_configuration, fullpath y zfile. Los dos callers de savestate_quick
en inputdevice (acciones save/restore) necesitan su exclusion de escritorio.
No se resuelven estas referencias mediante proveedores falsos; T06b2bz/T06c y
T15 conservan las puertas de enlace y estado causal/publico.

T06b2by DONE; T06b2bz desbloqueada. validate y diff --check PASS.
