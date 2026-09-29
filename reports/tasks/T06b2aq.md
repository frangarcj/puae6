# T06b2aq — IRQ/TOD custom sin BSDSOCKET/CD32

Parche0028 guarda el callback y variable BSDSOCKET en rethink_uae_int y ambos
brazos CD32 de cia_hsync_do con las macros de sus features. El perfil A500 no
las compila ni enlaza proveedores falsos para ellas. Orden IRQ6/IRQ2, bits
observados, procesamiento CIA previo y brazos A500 de TOD quedan intactos.
Las preferencias publicas incompatibles siguen necesitando rechazo en bridge;
estas funciones internas asumen configuracion valida del perfil.

El probe incluye cia.cpp real, extrae cia_hsync_do/rethink_uae_int completos y
enlaza events, keybuf, atomic y diagnostics reales. Aporta posiciones de linea
y avanza con do_cycles_normal; no ejecuta el raster custom ni instrucciones CPU.
Entrega IRQ se observa al salir hacia safe_interrupt_set, cuya implementacion
tiene sus pruebas propias. Las fronteras no ejercitadas abortan. La referencia
extrae las mismas dos funciones pristine; BSDSOCKET/CD32 permanecen desactivados
en sus preferencias y sus hooks solo del test abortan si fueran alcanzados.

Prueba 65536 combinaciones de pending IRQ: numero de solicitudes, nivel,
orden IRQ6 antes de IRQ2 y flags pendientes sin consumir. Prueba60 casos de
TOD: PAL/NTSC, LOF corto/largo, TOD normal/50/60 y cinco señales de sincronismo
(normal, programado, HSync invalido, externo ausente, genlock). Con CIA/events
reales observa TOD, offset/estado diferido, ICR y ciclos de IRQ; compara el digest de esa
traza con pristine, sin golden redefinible. Aserciones independientes prueban
cuatro ticks CIA-A en cuatro campos normales, un tick CIA-B por linea, alarmas
reales en ambos y ausencia de pulsos con HSync invalido/sync externo ausente.
El programa con LOF largo usa hcenter210 para probar cruce a la linea siguiente.
El probe no prueba dispositivos CD32/genlock completos ni temporizacion de un
frame emitido por el motor: solo sus entradas de sincronismo controladas.

Nuevo probe O0/O2 ASan/UBSan PASS. Objeto custom completo O0/O2 y CMake
Debug/Release PASS; nm ya no importa bsdsock_fake_int_handler, bsd_int_requested
ni akiko_ntscmode. No se ocultan los restantes imports de reloj/UI/render.
GEN01:39 salidas identicas; HOST01:196 archivos/776 includes/332 identificadores,
inventario83 rutas. SRC01:1134 archivos pristine verificados. Cola28 parches,
20 archivos,613 lineas de delta. Regresiones CIA, teclado CIA, IRQ CPU,
custom timing y plan:39 tests PASS; con el nuevo probe son40 unicos
(35 documentales). git diff --check y validate PASS. Luna reviso parche y
pruebas sin encontrar defectos. Los casos programados/genlock comparan digest
con pristine; los conteos independientes se limitan a los casos mencionados.

## Continuacion de pacing (auditoria Luna, no implementacion)

El avance real sigue en do_cycles_cck/do_cck/custom_trigger_start: conservar
ese unico propietario del beam, DMA y posiciones; un frontend no debe llamar
hsync_handler ni crear otro scheduler de lineas. La futura primitive ejecutara
CPU/ciclos hasta el limite producido por esa ruta.

framewait, rpt_vsync y fpscounter mezclan espera/medicion host con presentacion.
vsync_handler_render requiere separar esas esperas de housekeeping y entrega.
vsync_display_render debe conservar draw_denise_vsync_queue, flush,
end_draw_denise y start_draw_denise para el siguiente buffer. Retirar espera
no autoriza omitir ni duplicar ese drenaje de dibujo.

hsync_handler_post llama primero cia_hsync_do y termina con inputdevice_hsync,
rethink_uae_int/compute_spcflag_copper y checks; el bloque intermedio de throttle
host debe separarse sin quitar el handler. vsync_handler_post conserva DISK_vsync,
devices_vsync_post y housekeeping basado en get_cycles. reset_frame_rate_hack
sigue leyendo reloj host fuera de framewait. Las rutas linesync_beam estan
ligadas a WITH_BEAMRACER; no rescatarlas para implementar p6_run_frame.

Estos son puntos para leer y contrastar en T06b2ar, no prueba del frame completo.
El ultimo build global (T06b2ao) conserva11 unidades sin compilar; no se cierra
la puerta de enlace ni la de Waterbox al retirar estos tres imports.

T06b2aq DONE; T06b2ar desbloqueada.
