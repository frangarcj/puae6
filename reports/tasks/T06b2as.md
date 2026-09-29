# T06b2as — HSync sin esperas host

Parche0029 guarda fuera de P6 el bloque de throttling de hsync_handler_post:
cpu_thread/pull audio, beamracer, sleeps de CPU, is_syncline y polling de reloj,
vsync o nivel de audio. CIA se sigue ejecutando primero; inputdevice_hsync(false),
rethink_uae_int, compute_spcflag_copper y conversion de solicitudes de reset en
flags siguen en su orden original. input_read_done permanece false en P6, donde
WITH_BEAMRACER esta excluido. No se reemplaza el handler ni se añade scheduler.

Luna reviso la frontera: las escrituras a is_syncline son de pacing, y su consumo
en events.cpp ya esta excluido en P6. Audio pull pertenece a entrega host, no a
Paula. Las operaciones causales de CIA/input/IRQ/copper/reset quedan fuera de la
guarda. reset_frame_rate_hack aun conserva reloj host y se deja explicitamente
para T06b2at con framewait/vsync, no se declara custom libre de reloj de pared.

La prueba existente CIA/custom se amplia: el control ejecuta cia_hsync_do
pristine; P6 ejecuta hsync_handler_post entero, con cuerpos reales extraidos de
cia_hsync_do, rethink_uae_int, is_copper_dma, compute_spcflag_copper y
rtg_vsynccheck, y enum/struct copper reales. CIA/events/keybuf/atomic/diagnostics
siguen siendo reales. El callback inputdevice_hsync es una frontera observada
solo de prueba; verifica que CIA ya avanzo su toggle y solicita IRQ2/6 para
comprobar entrega en esa misma linea antes de copper. No se ejecuta inputdevice
completo, que aun tiene fallos de compilacion propios.

Se conservan las comparaciones de digest TOD frente a control y aserciones de
conteos/alarmas (60 casos PAL/NTSC/LOF/TOD/sync) y 65536 mascaras IRQ. Se añaden
aserciones de orden, ausencia de avance extra de ciclos, estados copper
STOP/WAITFOREVER/BLTWAIT/READ1/WAIT1 con comparacion vertical, DMA desactivado,
ciclo de cambio DMA y strobe pendiente. Reset normal/hard/keyboard invierte la
solicitud y añade BRK|MODE_CHANGE preservando CHECK; quit normal no se procesa
como reset. Preferencias host adversarias no introducen waits. No se habilitan
CPU threaded ni velocidad negativa como perfiles publicos.

O0/O2 ASan/UBSan PASS. El ejecutable no necesita uae_time/sleep/isvsync/audio
pull ni target_get_display_scanline. Objeto custom completo O0/O2 compila;
conserva imports host de otras funciones, no se confunde microtest con enlace.
El primer intento de extraccion encontro el prototipo compute_spcflag_copper
antes que la definicion; ahora la firma incluye salto de linea y llave de
apertura, y se incluyen constantes UAE desde el header real. Diagnostico en
build/t06b2as-hsync-test.log. No se cambio codigo del motor para resolverlo.

Verificacion: nuevo probe PASS; regresiones CIA, teclado CIA, IRQ CPU,
custom timing y plan:39 tests PASS, total40 unicos (35 documentales).
CMake custom Debug/Release PASS. GEN01:39 salidas identicas entre dos builds
HOST limpios. HOST01:196 archivos/776 includes/332 identificadores, inventario83
rutas. SRC01:1134 archivos pristine verificados. Cola29 parches/20 archivos/617
lineas de delta; dentro de revision ADR0004 existente. git diff --check y
validate PASS.

Limites: posiciones y limites de linea son suministrados por el probe; no se
prueba raster, instrucciones copper, frame entregado, reset completo, dibujo o
Waterbox. La ruta real do_cck/custom_trigger_start sigue siendo dueña del beam.
La puerta global mantiene11 unidades con errores (ultimo build T06b2ao), ademas
de enlace/servicios pendientes. T06b2at debe cerrar framewait, vsync y el resto
sin omitir drenaje de dibujo ni callbacks de disco/CIA.

T06b2as DONE; T06b2at desbloqueada.
