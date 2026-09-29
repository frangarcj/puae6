# T06b2am — Reloj fisico custom

Parche0025 guarda la negociacion de monitor/refresco en compute_framesync y las
bases de pacing host de compute_vsynctime fuera de P6. Paula recibe ahora
CHIPSET_CLOCK_PAL/NTSC y syncadjust=1. El selector de tasas short/long/interlace,
los ajustes half-line/LOF y el resto del cuerpo raster permanecen. No se fuerza
50/60Hz, no se inventan bases de tiempo constantes ni se introduce otro scheduler.

Reproducer antes del cambio: build/t06b2am-clock-before.cpp/executable/log extrae
compute_vsynctime previo. Con PAL y refresco75, notifica5311800Hz frente al reloj
fisico3546895Hz y falla la asercion. El backend real rechazaria esa notificacion
como P6_SOUND_CLOCK. El repro observa el argumento, no ejecuta audio completo.

Prueba nueva extrae los cuerpos completos de compute_vsynctime/compute_framesync
sin cortar ni reescribir ramas; usa headers/tipos reales. Dibujo y notificaciones
de dispositivos se observan mediante callbacks solo del test. El probe comprueba
reloj, selector de tasas nominales, svpos, hblank_hz, dimensiones y llamadas
retenidas; PAL/NTSC, interlace/LOF/half-line, refresco0/75/120, turbo, limites0/8,
syncbase0/1e9 y sonido0..3. No afirma un waveform CIA/TOD ni dibujado real, y no
simula dispositivos exitosos en produccion. La configuracion publica sigue siendo
responsabilidad del bridge pendiente.

La compilacion completa sigue en T06b2an. El primer fallo real custom es un
include threaddep sin usos de threads. Una copia diagnostica sin ese include
(build/t06b2am-custom-probe.cpp y -diagnostics.log) revela _vsntprintf, referencias
color_regs_aga pese a AGA excluido, COLOR_WRITE guardado con AGA aunque llamado
en OCS, y callers serial_rbf_change/specialmonitor_store_fmode sin sus features.
No se habilitan esas features para aprobar. Los consumidores de vsynctimebase,
cputimebase/fake_vblank_hz en framewait/hsync/vsync siguen abiertos; el parche
actual no hace que custom completo sea ejecutable. No tocar el snapshot pristine.

Validacion: microtest de ambos cuerpos O0/O2 ASan/UBSan PASS (1536
combinaciones por build); nm sin imports de monitor y depfiles sin od-win32.
Regresiones audio config, CPU halt/audio y PCM/lifecycle PASS; tests del plan
PASS. Total39 tests unicos, 35 documentales. La primera invocacion global tenia
un nombre de modulo PCM inexistente; se ejecuto despues tests.host.test_pcm
correcto, PASS. No se oculto un fallo del motor.

CMake p6_portable_probe y CTest portable-services Debug/Release PASS. GEN01:
39 salidas identicas entre dos builds HOST limpios. HOST01:196 archivos,
776 includes,332 identificadores; inventario83 rutas. SRC01:1134 archivos
pristine WinUAE verificados. Cola25 parches/20 archivos/545 lineas de delta.
ADR0004 revisa el archivo20; no cambia la alarma de1500. Luna preparo/reviso
el runner de microtest. git diff --check y validate PASS.

T06b2am DONE; T06b2an desbloqueada. Enlace completo, ejecucion de frames y
Waterbox mantienen sus puertas, sin afirmaciones de compatibilidad nuevas.
