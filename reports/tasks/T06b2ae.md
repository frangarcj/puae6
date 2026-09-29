# T06b2ae — lifecycle del backend PCM y reloj fisico

sound_lifecycle.cpp implementa setup/init/close/reset/pause/resume y pausa/restart
del buffer. Setup declara disponible el transporte estatico real; init valida
stereo, 44100/48000 Hz, produce_sound 2/3, sound_auto apagado, sin swap/volumen
master de dispositivo, y volumen Paula 0..100. No negocia frecuencia/canales ni
modifica prefs para fingir exito. El handler base es sample16s_handler, igual
que la seleccion inicial upstream; set_audio todavia debe seleccionar y validar
interpolacion/filtros. Este init NO es validacion publica completa de p6_config.

scaled_sample_evtime usa CHIPSET_CLOCK_PAL/NTSC por CYCLE_UNIT dividido entre
la tasa acordada. update_sound acepta solo ese reloj fisico para la region
cargada, rechazando clock host-ajustado, cambio de tasa/region, NaN e infinito.
Repetir init/update con la misma config conserva fase y PCM pendiente. Errores
de config/clock permanecen hasta close/init y se consultan por p6_sound_status;
el bridge pendiente debe comprobarlos antes de avanzar y traducirlos al ABI.
Reset del transporte no borra errores de configuracion ni cambia el reloj.

p6_pcm_set_delivery descarta solo datos de presentacion y conserva errores
sticky. Pausa global y pausa del buffer son independientes. Con entrega apagada
el mezclador sigue ejecutandose, pero las escrituras no llenan la cola. Estos
hooks backend no tocan next_sample_evtime, sample_handler ni el scheduler.
El audio_reset upstream si reinicia fase/canales y llama al reset del proveedor,
como corresponde a un reset explicito. Close es idempotente.

El probe ejecuta init_audio/audio_reset/audio_activate/update_audio reales y
events.cpp real, con canales idle de valores controlados; boundaries IRQ/DMA no
ejercitados abortan si se alcanzan. Verifica PAL/NTSC a ambas tasas, conteo de
100 muestras tras 100.25 intervalos, PCM negativo esperado, dos tandas con igual
fase/ciclos con o sin pausa de entrega, pausas independientes, reset y reinit.
Comprueba rechazo sin modificar prefs de tasas/formatos no admitidos, sonido
sin sintesis, sound_auto, swap/master gain y volumen Paula invalido; cambio en
caliente y clocks invalidos conservan error visible. No ejecuta CPU ni DMA.

O0/O2 ASan/UBSan PASS. PCM (3 probes), devices, CIA teclado y plan: 38 tests PASS,
35 documentales. CMake audio.cpp entero/p6_portable y CTest portable-services
Debug/Release PASS. nm del lifecycle completo solo importa globals audio/prefs,
sample16s_handler y tres funciones PCM; no reloj host, workers ni dispositivo.
GEN01: 39 salidas identicas; HOST01: 191 archivos/766 includes/332 identificadores.
No hay nuevo parche upstream ni cambio de caches: cola0020 permanece intacta.

## Puerta restante

Luna audito la secuencia real y reviso el proveedor. set_audio llama SoftFloat
incluso con filtro off: fpp_native.cpp define SOFTFLOAT_CONVERSIONS=1 sin depender
de WITH_SOFTFLOAT. No se sustituye por tan/tanl ni se desactivan defaults A500
para aprobar esta vertical. Filtros/interpoladores/volcnt requieren su cierre.

La pausa comprobada es la del backend. audio_deactivate, llamado tambien desde
newcpu.cpp al halt de CPU, hace audio_event_reset y pone audio_work_to_do=0.
Su efecto sobre hardware/fase requiere revision separada; no confundirlo con
pause_sound. custom.cpp aun calcula clk desde valores de refresco/host sync:
su caller debe pasar reloj emulado fisico coherente, incluidos modos de video,
antes de dar por cerrado el perfil. El rechazo actual no certifica esos modos.

T06b2af conserva estos puntos, errores a traves del bridge, validacion publica,
enlace completo y 12 unidades pendientes del ultimo build global T06b2aa.
No hay prueba de audio de maquina completa, filtro cross-ISA ni Waterbox.
