# T43: contratos del cartucho FMV CD32 antes del port

## Decision y alcance

Estudiar el cartucho **CL450 video + L64111 audio** del WinUAE fijado, con
salida software y el CD cooperativo existente. Este ADR y `plan/extensions/cd.json`
son un subplan: no habilitan firmware, fuentes, perfiles, decoder ni opciones.
FMV-A..F son propuestas que requieren fichas propias. CD32/CD32FR, EEPROM,
medios/cambio y peliculas ya tienen sus puertas T66; no se reinterpretan como FMV.
No incluye otros cartuchos ni una nueva aceleradora/CPU/MMU.

Autoridad: WinUAE6.0.3/tag6030, commit
`857f48fcb09399d4d95527e29a2fdeaedb41f827`, tagobj
`88e3c2c6eac57d6dfc2ed688455a35e2e3a86a59`, snapshot `upstream/winuae`
verificado por `manifests/upstream-files.json` y `plan/upstream.lock.json`.
Las lineas siguientes pertenecen a ese snapshot, salvo referencia explicita.

| Fuente | Observacion que condiciona el port |
| --- | --- |
| `cd32_fmv.cpp:35..41,202..217` | Apertura de 1 MiB en 0x200000, ROM 256 KiB, RAM 512 KiB en 0x280000, IO/L64111/CL450 separados. No confundirla con Fast 8 MiB de CD32FR. |
| `cd32_fmv.cpp:464..478` | El parser de audio exige MPEG-1, LayerII; no admite MPEG-2 audio por compartir nombre con kjmp2. |
| `cd32_fmv.cpp:792..866`, `cd32_fmv_genlock.cpp:32..54` | libmpeg2 decodifica, convierte y copia a un anillo de ocho imagenes de 352x288x4; el limite del compositor posterior no protege la copia previa de dimensiones maliciosas. |
| `cd32_fmv.cpp:1362..1505` | Hsync gobierna SCR, PTS, entrega de imagen y PCM. Hay floats (`cl450_scr`, ajuste/resto de sincronizacion), callbacks y semaforo; no es solo decodificar un fichero. |
| `cd32_fmv.cpp:1537..1589`, `expansion.cpp:1006..1014` | ROM se abre al inicializar la placa, tras Autoconfig; lectura de 128 bytes del header sucede antes. El init no valida por si solo toda la disponibilidad de firmware. |
| `cd32_fmv_genlock.cpp:22..29,67..174` | Composicion por umbral/escala/borde, comentada upstream como aproximacion. No constituye un oraculo independiente de genlock fisico. |
| `archivers/mp2/kjmp2.cpp:216..250,334..338` | kjmp2 contiene globals mutables y genera tabla N mediante `cos` host; su salida no queda acreditada cross-ISA por llamarse fixed-point. |

El objetivo inicial propuesto es **MPEG-1/VideoCD**, 352x288 PAL y 352x240 NTSC,
con audio Layer II a 44100 Hz estereo. Son limites del futuro perfil, no una certificacion
del CL450 fisico. `references/machawk-uae/deps/libmpeg2/README:5` declara soporte
generico MPEG-1 y MPEG-2; eso no prueba que el cartucho emulado, su ROM o el pipeline
soporten MPEG-2. Se excluyen MPEG-2/SVCD/DVD, otras resoluciones/frecuencias y
streaming host hasta contar con contrato y oraculos propios. No inferir MPEG-2
del antiguo titulo T43 ni del nombre `WITH_MPEG2`.

## Reutilizacion de MacHawk, firmware y procedencia

Referencia capturada: `references/machawk-uae`, commit
`ccecb1ead642c1bbe391308b88a7ffa9478b918d`, con overlays fijados en
`references/machawk-uae.manifest.json`; el archivo `sources/src/cd32_fmv.c`
modificado tiene SHA256 `b6981e1765a11688e45efbf0ea0d587098bb325ff1418b0a7412abc115b2fe3f`.
El delta `references/machawk/waterbox/uae/uae-waterbox.patch:253..328` precarga
la ROM antes de seal (`cd32_fmv_preload_rom`) y copia al banco al hacer Autoconfig.
Reutilizar esa idea en un parche pequeño contra WinUAE6; no copiar el motor PUAE5.
Evitar heredar su cache `valid` sin identidad entre sesiones: cambiar firmware o
fallar una carga no puede reutilizar bytes de la carga anterior. El cierre de
sesion y la ROM precargada deben tener propietario/vida util explicitos.

`references/machawk/waterbox/uae/Makefile:243..254` selecciona kjmp2 y nueve fuentes
C de libmpeg2; headers identifican libmpeg2 **0.4.1**, capturada, no una descarga
latest. `decode.c` conserva GPL-2.0-or-later y kjmp2 su aviso tipo zlib. Fijar esa
closure/avisos, configuracion C escalar, allocator y hashes antes de importarla
como dependencia de producto. La referencia no demuestra build actual PUAE6.

El ROM reconocido 40.30 de 256 KiB figura en `rommgr.cpp:334..337`, SHA1
`03ca81c7a7b259cf64bc9582863eca0f6529f435` (40.22 es otra identidad). El usuario
ya aporta una copia local autorizada de 512 KiB con mitades duplicadas; aqui no se
lee ni distribuye. El futuro proveedor debe validar ambas mitades iguales y el
payload completo/identidad antes de conservar la mitad canonica; tambien calcular
SHA256 para el sobre. Preload anterior a seal, sin abrir paths durante p6_run_frame.
Firmware y presencia de cartucho pertenecen a sync config/estados/peliculas;
FMV0 conserva perfiles e identidades actuales. No se incluye ROM en el paquete.

## Medios, video y PCM

Mantener propietario del `p6_cd::Disc`, catalogo y geometria existentes durante
la sesion. Akiko/blkdev obtienen sectores por el lector canonico cooperativo;
el programa Amiga alimenta los puertos CL450/L64111 desde RAM. El cartucho
upstream no es otro lector de unidades: no añadir una ruta directa de MPEG desde
fichero/drive fisico ni un hilo host. Documentar en FMV-A la vista real requerida
por el demux/firmware (2048/2352/2448), Mode2 y payload; si el lector actual no la
soporta, abrir vertical con fixture propio, sin reinterpretar bytes para aprobar.
Cambio/eject/error debe seguir el contrato CD32 y preservar FIFO/pausa/IRQ segun
el dispositivo; no vaciar decoder o reiniciar controller para fingir continuidad.

Hardware, colas y decoder viven dentro del motor y avanzan por `p6_run_frame`.
Reutilizar hsync/eventos ya emulados; no medir la cadencia con reloj del host.
Antes de reservar o copiar, validar dimensiones/stride/formato y bytes disponibles,
longitudes de packet/ring indices y muestras. Los puertos reciben datos parciales:
un corte puede estar dentro de una cabecera, audio frame o imagen predictiva.

Video: conservar hasta ocho imagenes propias de 405504 bytes, dimensiones y cursor
read/write/count; el compositor mantiene otra imagen y estado de borde/seleccion.
La conversion y el genlock escriben en buffers acotados distintos de la entrada;
probar mult 1/2/4, bordes, ultimo bloque y altura no divisible sin OOB. Hoy
`ports/portable/video_present.cpp:190..193` rechaza cs_cd32fmv/auxbuffer y0362
excluye el hook. No retirar esos guards hasta que la composicion preserve tanto
AGA/chipset como FMV. Libretro/Waterbox reciben un solo frame compuesto con el
layout/capacidades acordados; no exponer un puntero temporal de libmpeg2 al frontend.

PCM: upstream reserva 128 frames LayerII de 1152 muestras estereo signed16 y encola
bloques de 4 frames (4608 muestras) en dos buffers (`cd32_fmv.cpp:1404..1447`).
`ports/portable/audio_cda_state.h:7..34` limita el codec a 588 frames y un stream
slot 0 estereo. Hace falta un contrato real de ownership/multiplexacion y codec
para FMV/CDDA, sin sustituir el dueño ni subir kMaxFrames a ciegas. Preservar
cola, ready flags, ultimo sample, mute/volumen, fase Paula, callbacks de agotamiento
y eventos; normalizar endian explicitamente al exportar PCM. No confundir permiso
de reproducir CDDA con soporte del L64111 ni omitir AV02 por ser T43 AV01/DET02.

## Estado causal y bloqueo previo al port

No hay save/restore FMV en `cd32_fmv.cpp` ni `cd32_fmv_genlock.cpp`. Guardar ROM,
RAM e imagen visible no basta: inventariar registros/IO, IMEM/TMEM/HMEM, FIFO/audio
parcial, NewPacket 32/longitudes/PTS, offsets de bitstream, SCR/ajustes/restos,
IRQs, ocho imagenes y fases de presentacion, MP2 V/Voffs y globals de algoritmo.
Clasificar cada campo, incluidos mpeg_info, convert context y sample callbacks.

libmpeg2 expone un contexto opaco con referencias I/P/B, YUV, quantizers, parser,
chunk_buffer y buf_start/end; contiene funciones `action`/convert y punteros
internos (`deps/libmpeg2/src/mpeg2_internal.h:153..210`). **No hay codec nativo
acordado**: memcpy del struct, reset del decoder o replay ilimitado desde CD no
son soluciones. Dos hipotesis verificables antes de implementar:

1. Codec por campos/IDs de funcion y offsets sobre buffers propios acotados puede
   reconstruir el contexto completo. Probar corte parcial/I/P/B y relocacion real,
   con futuro/IRQ/ticks/PCM/pixeles exactos, no solo la primera imagen.
2. Un checkpoint en GOP cerrado y replay desde bytes ya capturados podria
   reconstruirlo con una cota explicita. Medir/validar independencia de referencias
   anteriores y cota de entrada; no adoptarlo si requiere I/O, replay sin limite
   o altera ciclos/estado observable. No se ha probado esta alternativa.

Waterbox puede capturar allocations/punteros internos del mismo guest, pero debe
hacerlo antes de seal o bajo allocator/snapshot auditados y rebindear solo bindings
host. No usar memoria invisible ni reset en restore. El codec nativo, el stream
PCM y los floats/tablas host son bloqueos de implementacion, no supuestos resueltos.
Mantenerla cerrada hasta probes de estado y determinismo, sin cambiar tolerancias.

Memoria conocida de payload mutable, sin padding: RAM 524288 + audioram 262144 +
video 8*405504 + compositor 405504 + PCM 128*4608 + doble salida 36864 = **5062656 bytes**.
Aparte libmpeg2 reserva al menos chunk_buffer 1194*1024+4 = 1222660 bytes, mas
referencias YUV/convert/contextos, MP2, registros y metadatos. ROM/cache 256 KiB
cada una y buffer chipset/auxbuffer se contabilizan por separado. Esto es un
inventario parcial, **no un state_bound total**. FMV-B debe fijar allocator/cotas
por secuencia antes de asignar y medir pico/snapshot/dirty grupos 4/16 KiB; fuente
malformada no puede reservar memoria por dimensiones arbitrarias.

## Puertas y limite de esta entrega

FMV-A..F en el JSON ordenan source/licencias y medios, decoder/determinismo,
audio, codec, composicion y exposicion. Fixtures propios con fuente/pin/licencia:
colores/movimiento I/P/B y PCM conocidos, PTS/pause/still/singlestep/underflow,
secuencia truncada, cambio/eject/reset y puntos de restore a ambos lados de FIFO,
prediccion, callback y cambio de imagen. Fijar oraculos independientes del decoder
portado (patrones analiticos y vectores de referencia con procedencia); igualdad
contra el mismo decoder solo acredita repeticion. Genlock fisico requiere otra
fuente primaria o experimento autorizado: no anunciar paridad por el comentario
aproximado upstream.

AV01/DET02 de T43 conservan regresiones actuales AGA/CD32; sus PASS acumulados
no acreditan FMV. Las futuras puertas añaden AV02/STATE01/STATE02, LR01 y WB03/04
reales en proceso/sandbox nuevo, render/audio off, movie replay/seek, memoria y
matriz portable/sanitizers. No hay build, decode, snapshot FMV ni compatibilidad
anunciada por este estudio; un asset/runner/oraculo faltante conserva BLOCKED.
