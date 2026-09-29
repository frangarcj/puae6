# T06b2aa — salida PCM acotada

ports/portable/sounddep/sound.h y pcm_output.cpp conectan el escritor Paula a
una cola stereo int16 de 8192 frames, staging de 512 y consumo parcial por pull.
No hay dispositivo, worker, reloj host, asignacion dinamica ni feedback de llenado.
Overflow/alineacion invalidan la salida hasta reset explicito; los writes pueden
seguir sin OOB, pero las lecturas fallan sin publicar muestras truncadas. Errores
de argumentos dejan el contenido intacto. La capacidad es del transporte, no un
limite por frame demostrado para cualquier sample rate: validar tasa y propagar
el error por p6_run_frame siguen pendientes de lifecycle/bridge.

0019 guarda solo el exporter sampleripper, WAV host y sus dos callers. No cambia
DMA, sintetizador, filtros ni scheduler. ADR0004 revisa audio.cpp como archivo
upstream distinto 17. La cola queda en 19 parches, 17 archivos y 499 lineas de delta.
El header portable sigue los nombres/constantes del contrato WinUAE 6030; no importa
el backend Windows. Declaraciones init/setup/close/reset/
pause/restart siguen sin implementacion: no son stubs que reporten exito.

Pruebas PASS O0/O2 ASan/UBSan: mezclador sample16s_handler real con valores de
canales controlados positivos, 1025 frames y mezclas esperadas 0+3/1+2; mismos
bytes con drenaje batch, parcial y durante produccion; transporte de -32768/32767;
capacidad exacta, error al excederla, writes posteriores seguros, argumentos,
lectura vacia y reset. Los extremos signed se inyectan en el transporte, no se
presentan como prueba del mezclador negativo (ver defecto siguiente). Luna
escribio el probe inicial; el agente principal amplio limites y consumo, reviso
el codigo y ejecuto checks. El enlace selectivo es solo para el microtest.

audio.cpp entero y p6_portable compilan Debug/Release. Depfile selecciona el
header portable sin threaddep/Win32 sounddep; nm no importa ripper/zfile_fopen.
test_pcm: 1 PASS; devices/CIA teclado/plan: 37 PASS. CTest portable-services
Debug/Release PASS. GEN01: 39 outputs identicos en dos builds HOST. HOST01:
191 archivos, 766 includes, 332 identificadores. Build global medido: 12 unidades
aun fallan, en build/t06b2aa-engine-failures.log (antes 13).

## Defecto confirmado para la siguiente vertical

FINISH_DATA hace `data <<= shift` con un int negativo. El caso minimo fija
audio_channel[0].data.current_sample=-1 y ejecuta sample16s_handler: UBSan aborta
con "left shift of negative value -1". Fuente y logs conservados en
build/t06b2aa-negative-probe.cpp, t06b2aa-negative-build.log y
t06b2aa-negative-run.log. Compilar ese fuente con includes portable/generated/
upstream, pcm_output.cpp, -O0 -ffunction-sections -fdata-sections -Wl,-dead_strip
-fsanitize=undefined -fno-sanitize-recover=all reproduce el fallo. No se modifica
el resultado esperado para esconderlo ni se declara sintesis completa correcta.
T06b2ab exige corregirlo primero y ampliar pruebas negativas.

Tambien hay avisos de conversion PERIOD_MAX=ULONG_MAX a per:int en reset/restore.
Son evidencia de un ancho dependiente del host, no prueba por si solos de una
divergencia: comparaciones de valores negativos pueden promoverse al mismo
sentinel unsigned. Su revision requiere reproducer; no cambiar flags al azar.
Siguen pendientes softfloat/filtros, lifecycle, tasa emulada, public config,
enlace completo, DMA y checks AV02/DET05 por frame/restore/Waterbox.

Caches previas: build/{upstream,generated}-before-t06b2aa.
