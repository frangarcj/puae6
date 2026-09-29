# T06b2ao — Compilacion custom OCS

custom.cpp entero compila por primera vez para el perfil A500. Parche0026
expone COLOR_WRITE fuera de AGA, guarda solo su rama/arrays AGA y mantiene la
escritura OCS/ECS. update_mirrors fuerza aga_mode=false cuando AGA no existe;
get_custom_color_reg/cursorsprite/draw_line_fast conservan la rama OCS y los
cuatro accesos a color0 usan color_reg_get, helper upstream ya condicionado.
No se amplian arrays ni se activa AGA para satisfacer al compilador.

El reset sigue consumiendo los 510 draws de uaerand del bucle de inicializacion
AGA aunque se excluyan sus stores: quitar el bucle entero desplazaria la
secuencia aleatoria del resto del hardware. El test extrae ese bucle exacto de
ambas versiones y verifica el numero de draws y el siguiente valor usando un
PRNG observado de prueba. No ejecuta custom_reset completo ni valida su estado.
SAVESTATE esta desactivado; sus referencias AGA dormidas siguen pendientes antes
de habilitar serializacion nativa. Luna audito estas fronteras en solo lectura.

Parche0027 excluye el include threaddep sin uso en P6 y guarda los callers
serial_rbf_change/specialmonitor_store_fmode con las mismas macros que sus
headers y features. uae_abort usa vsnprintf acotado al buffer real con TCHAR
estrecho; gui_message recibe "%s", buffer, evitando interpretar el diagnostico
formateado como un segundo formato. No se crean aliases Win32 ni providers vacios.

La prueba portable incluye custom.cpp completo; el linker del microtest elimina
codigo no ejercitado, sin crear proveedores de maquina ficticios. La referencia
extrae COLOR_WRITE/get_custom_color_reg pristine y compila AGA en otro ejecutable
con aga_mode=false, preservando su layout original sin violar ODR entre layouts.
Se comparan resultados y aserciones para los 65536 valores en los 32 registros,
con Denise OCS/ECS, lectura RGB, genlock, tabla nativa e invalid indices. Esto
no anuncia ECS soportado como perfil publico ni prueba salida de video. Prueba
uae_abort contra el proveedor diagnostics real: porcentajes literales y texto
largo terminado/truncado, sin reinterpretar %n. Todo O0/O2 ASan/UBSan PASS.

El objeto completo tambien compila O0/O2 sin dead-strip y CMake Debug/Release.
Sus imports no contienen COLOR_WRITE sin definir, serial_rbf_change,
specialmonitor_store_fmode, _vsntprintf ni funciones threaddep. Depfiles permiten
unicamente od-win32/machdep/m68k.h, reutilizacion pura CCR ya inventariada, sin
otros headers Win32/threaddep. El primer intento del test rechazo ese header
por una asercion demasiado amplia; se corrigio a la allowlist existente tras
leer el wrapper, fuente y manifiestos. Evidencia: build/t06b2ao-ocs-test.log.

Regresiones custom timing, diagnostics y plan:40 tests PASS; nuevo custom OCS:1,
total41 unicos (35 documentales). GEN01:39 salidas identicas; HOST01:196 archivos,
776 includes,332 identificadores e inventario83 rutas. SRC01:1134 archivos
pristine verificados. Cola27 parches/20 archivos/601 lineas de delta, dentro
de la revision ADR0004 existente. git diff --check/validate PASS.

Build global revalidado en build/t06b2ao-engine-failures.log: 24/35 objetos
compilan, fallan 11: memory, inputdevice, disk, expansion, cfgfile, rommgr,
main, calc, ini, savestate y drawing. custom ya no esta entre ellos. No se
marca cerrada la puerta global. Sus imports restantes quedan visibles en
build/t06b2ao-custom-imports.txt: pacing/uae_time/vsync, BSDSOCKET/akiko, UI,
y servicios requeridos del motor y renderer, entre otros. Compilar no prueba
enlace. T06b2ap conserva esa puerta junto a las unidades restantes.

T06b2ao DONE; T06b2ap desbloqueada. update_mirrors no se ejecuta en el
probe: su seleccion sin AGA se revisa en codigo/compilacion. Reset completo,
renderer, frame y sandbox Waterbox mantienen sus pruebas pendientes.
