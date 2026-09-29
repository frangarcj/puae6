# T06b2au — VSync custom sin reloj host

Parche0030 separa VSync del reloj y la presentacion del host. Guarda fuera de
P6 framewait, fpscounter, audio pull, bucle de pausa/UI y helpers de beam racing;
reset_frame_rate_hack ya no negocia tiempo host. fpscounter_reset conserva sus
contadores sin leer el reloj. vsync_event_done usa events_reset_syncline real.
No se añade scheduler ni se sustituye hardware por proveedores ficticios.

Se conserva cola VSync Denise, flush, end_draw_denise, vsync_handle_redraw y
start_draw_denise, en ese orden. Tambien permanecen el bookkeeping STOP/idle,
DISK_vsync, devices_vsync_post, config_check_vsync, vsync_handle_check y las
comprobaciones de señal/ciclos. render_screen/show_screen son presentacion GPU
host, no el trabajo del buffer que realiza drawing.cpp/Denise.

display_reset pierde su consumidor crender_screen: ahora se retira una solicitud
despues del dibujo por frame completo, conservando la condicion de entrelazado
y LOF del helper original. No hay reintentos GPU que puedan retirarla dos veces.
La prueba compara contra el cuerpo original con resultado GPU true y false,
sin usar ese callback de prueba como proveedor del motor.

drawlines pasa true expresamente: P6 dibuja al buffer independientemente del
modo de VSync del monitor. isvsync_chipset original devuelve 0 sin VSync, 1 en
modo tradicional y valores negativos en modo host low-latency (incluido -1 con
m68k_speed>=0). No se conserva esa politica host de suprimir finish_drawing_frame.
Luna reviso esta frontera, las pruebas y el contador de limpieza. custom_disabled
solo se establece desde picasso_on; RTG esta excluido del perfil. La matriz usa
el estado A500 coherente (ambos false); no certifica la ruta RTG ni A2024.

Prueba: custom.cpp entero incluido; count_frame, check_prefs_picasso,
vsync_handle_redraw y config_check_vsync se extraen sin reescribir sus cuerpos.
events.cpp y diagnostics.cpp son proveedores reales. Los callbacks de buffer,
disco/dispositivos y reconfiguracion son observaciones exclusivas del probe:
no se afirma ejecucion de esos subsistemas completos ni pixels correctos.
576 combinaciones initial/interlace/LOF/scanlines/resolucion/frameskip/inhibit/
clear, mas secuencias de campos cortos, llamadas duplicadas, STOP/idle,
config ageing, falta de señal y flags temporales. Los ciclos no avanzan por
ejecutar el cierre VSync. Se inspecciona tambien el objeto custom entero O0/O2,
sin dead stripping, para no esconder imports host en funciones no ejercitadas.

El primer build detecto helpers de slices que aun referenciaban crender_screen;
se guardaron junto con el resto de beam racing (build/t06b2au-custom-build.log).
El primer probe dejo LOF activo al pasar de la matriz a los campos cortos:
se inicializa explicitamente esa entrada (build/t06b2au-vsync-test.log). No se
cambio el motor para satisfacer la asercion. cycles_do_special en perfil sin
JIT pone pissoff=0, como establece el header upstream real.

Verificacion:45 tests PASS (35 del plan y10 de host), incluyendo VSync, CIA,
OCS, timing, teclado y diagnosticos. Probe O0/O2 con ASan/UBSan PASS; objeto
custom completo O0/O2 sin imports uae_time, cpu_sleep_millis, render_screen,
show_screen, handle_events ni audio pull. CMake custom Debug/Release PASS, con
avisos upstream ya existentes. GEN01:39 salidas iguales entre dos builds HOST
limpios. HOST01:83 rutas,196 archivos/776 includes/332 identificadores. SRC01:
1134 archivos pristine verificados. Cola30 parches/20 archivos/670 lineas de
delta (53 de este parche), dentro del presupuesto revisado en ADR0004.

T06b2au DONE; T06b2av desbloqueada.

Limites: no hay aun frame API ni renderer completo portable validado. Quedan
servicios host custom (configuracion/entrada/buffer) y unidades con errores;
T06b2av conserva la puerta global de compilacion y T06c/T07 las de enlace y
avance real. Waterbox sigue requiriendo ejecucion dentro de su sandbox.
