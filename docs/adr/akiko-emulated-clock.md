# Reloj del servicio Akiko (T66h2)

El reproducer llama `akiko_reset(1)` real, avanza17lineas y vuelve a resetear.
Con0358, el primer pulso llega en linea1 al arrancar y en linea190 despues del
segundo reset: los dos `static float` locales conservaban la fase anterior.

0360 usa `get_cycles()` y el reloj fijo PAL/NTSC de la configuracion,
`CHIPSET_CLOCK_* * CYCLE_UNIT`, como el anclaje RTC. No usa `maxvpos`,
`vblank_hz`, tiempo de pared ni cadencia del host. El primer hsync con tiempo
transcurrido conserva el pulso inicial. Despues, los acumuladores conservan
restos exactos: sectores/TOC75×velocidad Hz, Q2Hz y presencia de medio0,5Hz.
Una llamada sin ciclos nuevos no ejecuta servicio, DMA ni temporizadores.
Un intervalo retrogrado o mayor que1/150s falla explicitamente: este callback
no debe saltar periodos y perder sectores en silencio.

El subcodigo se programa cinco hsync antes del siguiente vencimiento sectorial,
calculado desde su resto y la duracion de la ultima linea observada. El adelanto
no acorta el oscilador ni se acumula en cada vuelta. Cambiar1x/2x conserva el
resto y recalcula ese plazo. Es exacto con lineas de
longitud constante; cambios de haz entre la prediccion y el vencimiento necesitan
validacion de integracion, y no son una afirmacion de precision del hardware.

`turbo_emulation` y `cd_speed=0` no fuerzan transferencias por linea: el reloj
sectorial sigue a75/150Hz. La politica de seek del comando upstream sigue
separada; no se publica una nueva opcion de velocidad de CD ni un preset CD32.
Reset ancla la fase a los ciclos actuales y descarta comandos/mute pendientes.
La continuacion conserva sus acumuladores y el contador de subcodigo; el test
reproduce esa fase privada, no un formato de estado nativo ni una prueba WB.

El probe usa el reset Akiko y cleanup EEPROM/zfile reales. ROM persistente,
conversion MSF/BCD e intercalado no ejercidos son fixtures que abortan si se
alcanzan. Estado completo, TOC/subQ, DMA SUBCODE con disco y arranque CD32
siguen pendientes en T66.
