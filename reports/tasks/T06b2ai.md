# T06b2ai — configuracion de Paula

Parche0022 mantiene el cuerpo de set_audio, filtros, makefir, mezcladores y
scheduler. La frontera P6 valida changed_prefs antes de aritmetica/aplicacion,
sale si init_audio falla sin negociar produce_sound, y no llama al ajuste de
pacing compute_vsynctime: init_sound ya establece el intervalo desde el reloj
fisico PAL/NTSC. Cambiar preferencias de audio no cambia geometria de video.
check_prefs_changed_audio tampoco reactiva canales despues de un error.

p6_sound_validate_config admite el transporte ya probado (stereo,44100/48000,
produce2/3, sin sound_auto ni swap/master gain), interpoladores0..4, modos y
tipos de filtro0..2, separacion0..10 y delay0..10 limitado por el buffer1024.
Paula volumen0..100; CD<=100, con negativos tratados por el clamp upstream.
El booleano volcnt conserva su algoritmo. Este es un contrato interno de audio,
no validacion publica del modelo de maquina: el bridge sigue pendiente.
Un error invalida la entrega PCM y queda visible en p6_sound_status; no modifica
preferencias activas ni fase antes de la aplicacion. close/init o set_audio
valido constituyen reconfiguracion explicita y reinician el transporte.
Si la apertura falla despues de aplicar preferencias, currprefs ya contiene
la nueva configuracion y el handler queda cerrado: no hay rollback atomico.
El caller/bridge debe observar p6_sound_status y detener su avance; el cambio
solo garantiza que no negocia ni sigue configurando como si hubiera funcionado.

Luna audito la separacion: compute_vsynctime usa syncbase/refresco/turbo y
mezcla ese pacing con notificaciones de audio. Su caller de custom sigue
pendiente; no se eliminan ni reinterpretan interlace/half-line/LOF aqui.
Tambien detecto que audio_deactivate del halt reinicia canales con
audio_event_reset; no equivale a pause_sound_buffer y queda en T06b2aj.

El probe enlaza audio.cpp/events.cpp/SoftFloat y PCM/lifecycle/GUI/diagnosticos
reales. Extrae el cuerpo exacto set_config_changed de cfgfile, no simula una
notificacion exitosa; cfgfile completo sigue pendiente. Las fronteras IRQ/DMA
no ejercitadas abortan. Usa canales idle con samples signed8 y volumen64.
Defaults produce3/interpol1/sep7/filtro EMUL A500 conservados. Compara 80 frames
PCM, fase y ciclos por lotes frente a fragmentos para PAL/NTSC y ambas tasas;
el LED cambia efectivamente el PCM. Comprueba seleccion de los cinco handlers,
tres modos/tipos de filtro, delay0..10, volcnt y limpieza de reconfiguracion.
El FIR de volcnt tiene coeficientes finitos/simetricos y produce PCM no cero;
no afirma exactitud de su temporizado frente a un oraculo independiente.
Rechazos incluidos freq0, interpol5, delay11/INT_MAX, sep11/INT_MAX, filtro/tipo
invalidos, volumen101/CD INT_MAX. Comprueba tambien que el caller de cambio de
prefs no reactiva audio despues del fallo, y apertura sin sound_available no
negocia produce_sound ni llega a set_config_changed.

Checks: audio_config, audio_math, PCM y plan (38 tests distintos,35 del plan),
con probes O0/O2 ASan/UBSan. CMake audio.cpp/p6_portable/probe Debug y Release,
CTest portable-services en ambos presets. HOST01:196 archivos/776 includes/332
identificadores; inventario83 rutas. GEN01:39 salidas identicas entre builds
HOST limpios. SRC01:1134 archivos pristine verificados. Cola22 parches sobre19
archivos,524 lineas de delta. No nuevos archivos upstream parcheados.

Los fallos iniciales del probe eran del harness: options.h antes de sysdeps y
copias multiMB de uae_prefs en la pila. Se corrigieron includes y almacenamiento
estatico, sin cambiar flags ni el algoritmo para aprobar sanitizadores.

Limites adicionales: makefir usa sinf/cosf/atanf del runtime fijado, sin
fast-math; no se afirma equivalencia cross-ISA. El contador de volumen tiene
su propia sensibilidad potencial al particionado sub-CCK que requiere prueba
con hardware real antes de anunciar exactitud. No hay DMA/CPU/maquina completa
en este probe. Los avisos PERIOD_MAX permanecen documentados en T06b2ac.
