# ADR 0007: entrega de audio del bridge

## Contexto

`p6_run_frame` debe devolver el PCM que Paula produjo durante la unidad sin que el numero de muestras dependa del
redondeo por frame, de donde estaba la CPU al cruzar el borde ni de si alguien pide el audio. El puerto ya trae una cola
acotada de PCM (`ports/portable/audio/pcm_output.cpp`, 16384 palabras int16) y un ciclo de vida de sonido que fija la
frecuencia; el motor genera una muestra cada `scaled_sample_evtime = clk*512/rate` ticks. Mediciones y referencias de
codigo: informe de investigacion de T11 (resumen en T11).

## Decision

1. **Cola drenada siempre.** Al final de cada unidad el bridge vacia la cola del puerto en un buffer propio
   (`bridge/pcm_out.cpp`, `int16_t out[8192 * 2]`). Lo que cambia con `P6_RENDER_AUDIO` es solo si `frame.audio` /
   `audio_frames` se rellenan; el drenado, el contador de sesion `audio_frames_total` y todo lo que hace Paula ocurren
   igual (DET05). El puntero es valido hasta la siguiente llamada mutante y `p6_state_save` no lo invalida. Una unidad
   nunca produce mas de `max_audio_frames` (8192) frames; desbordar la cola es un fallo pegajoso del puerto y el bridge
   pasa a `Faulted` con `P6_ERROR_UNSUPPORTED`, nunca descarta muestras en silencio.
2. **Frames, no int16.** `audio_frames` son pares stereo; el buffer es `[L0,R0,L1,R1,...]`. Izquierda = canales de Paula
   0 y 3, derecha = 1 y 2, mezcla por defecto del motor (±10400 propio, ±2400 del otro par), filtro A500 activo. Nunca
   se entregan pares parciales: el puerto anade dos palabras por llamada del manejador.
3. **Volcado de Paula en el borde.** Paula integra de forma perezosa (hasta el ultimo acceso a registro o linea), asi que
   sin volcado las muestras del final de una unidad caerian en la siguiente y el conteo dependeria de la instruccion que
   cruza el borde (hasta 2560 ticks, 32768 con MOVEM/DIVU). `update_audio()` en el borde deja `lag_max` en 0. Es la misma
   operacion que ya ocurre en cada escritura de registro a instantes arbitrarios y no llama a `schedule_audio`: no cambia
   la maquina. El observador de STEP01 hace el mismo volcado en los limites que se traga, por eso N llamadas y avance
   continuo dan el mismo flujo (test `test_n_calls_and_continuous_advance_flush_paula_alike`).
4. **Conteo exacto por ticks.** La muestra k se emite en el tick `floor(k*m/256 + 1/2) + 2048` (los valores son
   multiplos de 1/256 con mantisa de 24 bits, la aritmetica float es exacta). Frames acumulados en el tick T del borde:
   `count(T) = floor((256*(T-2048)+127)/m)`, `m = (2*clk*131072 + rate)//(2*rate)`. No hay redondeo por frame ni cadencia
   de video en la cuenta; un test compara el acumulado con la identidad en cada una de 400 unidades por combinacion (PAL/NTSC, 44100/48000, `vsync` y `long_ops`).
5. **Deriva residual medida, no oculta.** La unica deriva es la cuantizacion float32 constante de `I = m/256`:
   PAL 44100 −2.21 frames/h, PAL 48000 −0.95, NTSC 44100 −4.66, NTSC 48000 +3.77. Mediciones de investigacion (no son tests): el error `acumulado − ideal` queda en
   (−1.09, +0.004) en 2000 unidades y en −2.34 tras 72000 unidades NTSC (1199 s emulados). Se afirma la identidad exacta con `m`; `E-1..E`
   solo vale para tramos de unos minutos. Es cuantizacion de un `float`, no acumulacion de redondeos por frame.
6. **Configuracion fijada.** Rate 44100 o 48000 por config (`audio_rate_hz`); el preset fija `sound_stereo_separation=7`,
   `sound_mixed_stereo_delay=0`, `sound_volume_paula=0`, `sound_volume_master=0` ademas de filtro A500 e interpolacion,
   de modo que perfil y motor no discrepan (test de perfil PAL/NTSC).
7. **Sin contraccion FMA.** El CMake compila todo con `-fno-fast-math -ffp-contract=off` (DET04): el filtro RC y la mezcla usan
   floats y una contraccion distinta entre hosts cambiaria muestras. Un test comprueba que la linea de compilacion de `audio.cpp` lo lleva.
   La diferencia de floats entre hosts NO esta medida (solo hay un host); queda abierta para DET04 y no se declara bit-exactitud
   entre Darwin ARM64, x86_64 ni Waterbox.

## Inventario de estado que afecta a muestras futuras (entrada de T14)

`audio.cpp` (estaticos salvo que se indique):

- **Fase y reloj**: `next_sample_evtime`, `last_cycles`, `scaled_sample_evtime` (derivado de clk/rate), `audio_work_to_do`.
  Anclas en `set_audio`, `audio_reset` y `audio_event_reset` (siempre "next = scaled, last = now"). Un reset re-ancla y descarta
  la fase fraccionaria y la cola sin volcar.
- **Filtros**: `sound_filter_state[4]` (usados [0],[1]), constantes derivadas `a500e_filter1_a0`, `a500e_filter2_a0`,
  `filter_a0` (se reconstruyen en `set_audio`), `led_filter_on`, `led_filter_forced`, `sound_use_filter*`. Entrada del LED:
  `gui_data.powerled/powerled_brightness` alimentados por los acumuladores de `cia.cpp` (`led_cycles_on/off`, `led_cycle`,
  `led_old_brightness`).
- **Mezclador**: `right_word_saved[1024]`, `left_word_saved`, `right2_word_saved`, `left2_word_saved`, `saved_ptr`,
  `saved_ptr2` (historial de retardo; sin efecto con delay 0, pero estado), `mixed_on/mixed_stereo_size/mixed_mul1/mixed_mul2`.
- **Por canal** (`audio_channel[4]`): `evtime, dmaenstore, intreq2, irqcheck, dr, dsr, pbufldl, drhpos, dat_written, lc, pt,
  state, per, len, wlen, volcnt, dat, dat2, ptx, ptx_written, ptx_tofetch, dmaofftime_*, minperloop, volcntbufcnt,
  volcntbuf[4096]` y `.data`: `current_sample, last_sample, new_sample, sample_accum, sample_accum_time, sinc_output_state,
  sinc_queue[256]{time,output}, sinc_queue_time, sinc_queue_head, audvol, mixvol, adk_mask`. `mixvol` se captura al recargar
  el periodo: escribir AUDxVOL en un canal parado no cambia la salida retenida (medido).
- **Punteros a funcion** (reenlazar tras restaurar, no copiar): `sample_handler, sample_prehandler, extra_sample_prehandler,
  audio_data[]`. Los flujos extra estan inactivos en el perfil.
- **Derivado de config, igual al restaurar**: `sound_freq`, `ntscmode`, `sound_interpol/filter/filter_type/stereo_separation/
  mixed_stereo_delay/volume_paula`, `sound_paula_volume[2]`, `firmem[1025]` (solo con `sound_volcnt`).
- **Fuera de audio.cpp**: `dmacon, adkcon, intena, intreq` (custom.cpp), eventos pendientes `eventtab[ev_audio]` y `eventtab2`
  con `event_setdsr` / `event_audxdat_func`, y la IRQ de audio retrasada 1 CCK.
- **Presentacion, no causal con la cola vacia en el borde**: `paula_sndbuffer, paula_sndbufpt, pcm, queued_frames, output_error,
  delivery_enabled` del puerto y `opened, paused, buffer_paused, ntsc, rate, lifecycle_error` del ciclo de vida, mas el `out[]` y
  el contador del bridge. `save_audio/restore_audio` de upstream NO sirve como estado de T14/T15: pierde fase, filtros,
  acumuladores, `mixvol`/`dat2`/`current_sample` y el historial del mezclador.

Gracias al volcado del borde, la posicion perezosa de Paula (`last_cycles`) es siempre `get_cycles()` en un borde, lo que
simplifica el snapshot.

## Limites y hallazgos abiertos

- **Reset del invitado** (instruccion RESET): el reset de audio descarta el PCM producido en esa unidad; conteo y fase se
  re-anclan. Es una limitacion documentada de la semantica de reset, no una perdida silenciosa entre unidades.
- **Segunda carga en el mismo proceso** no es identica bit a bit (estado de disquetera `floppy[].cyl` y similares); T13 la trata.
- Sin Kickstart real no se ha oido audio de programas reales; la evidencia es con micro-ROMs que programan Paula.
- Sin cola de presentacion del frontend: un adaptador que acepte menos muestras conserva su propia cola y ese retorno no se
  alimenta a la velocidad de CPU (docs/02-contracts.md).
- Solo se ha ejercitado la interpolacion y el filtro que fija el perfil A500; `anti`/`sinc` y `sound_volcnt` no tienen prueba.

## Alternativas descartadas

- Redondear frames por unidad (`rate/cadencia`) o acumular un resto en el bridge: duplica la fase que ya vive en Paula y se
  desincroniza en cuanto el invitado cambia el numero de lineas.
- Usar `save_audio` de upstream para la fase: no la contiene.
- Dejar de drenar la cola con la entrega desactivada: haria que el desbordamiento (y por tanto la ruta de fallo) dependiera de la
  presentacion.
- `-ffast-math` o `-O3` con contraccion para abaratar el filtro: prohibido por DET04.
