# 02. Contratos del motor y sus adaptadores

Son especificaciones A IMPLEMENTAR. Los nombres p6_* son propuestos, no funciones
existentes de WinUAE. T04 congela un header y tests antes de escribir el bridge.
Reglas libretro verificadas: S4. Reglas del wrapper Waterbox verificadas: S5-S7.

## ABI C pequena

Operaciones del motor: p6_abi_version, p6_query_caps, p6_init, p6_load,
p6_run_frame, p6_reset, p6_unload y p6_shutdown. Extensiones: p6_state_bound,
p6_state_save, p6_state_load, p6_memory_regions y p6_media_event. La capa publica
no expone uae_prefs, std::string, std::vector, objetos C++, excepciones ni tipos Windows.

Structs con abi_version/struct_size cuando deban evolucionar. Enteros de ancho
fijo, enums con almacenamiento explicito y buffers con capacidad. En FFI no usar
bool C++ ni bitfields; usar uint32_t/int32_t. No transmitir structs con padding
sin especificar. Alinear naturalmente y probar sizeof/alignof/offsetof; NO curarlo
todo con pack(1). Los formatos persistidos se codifican por campos y byte order.

Descriptores A/V nativos pueden llevar punteros de vida limitada a la llamada.
No son un formato portable serializable. La frontera Waterbox usa sus tipos y
calling-convention verificados; no castea a ciegas structs C# a structs del motor.
T20 escribe pruebas de layout en ambos lados y un canario de capacidad.

Errores tipados: BAD_ARGUMENT, BAD_STATE, BUSY, UNSUPPORTED, NEED_FIRMWARE,
WRONG_MEDIA, CAPACITY, IO_ERROR, CORRUPT_STATE, INCOMPATIBLE_STATE, INTERNAL.
Retornar codigo y texto acotado. Nada de abort/exit/MessageBox para una configuracion
invalida. Incluir limites antes de multiplicar dimensiones, offsets o tamanos.

## Vida util y ownership

Estados: UNINITIALIZED -> INITIALIZED -> LOADED -> INITIALIZED -> UNINITIALIZED.
p6_run_frame y estados solo se permiten en LOADED. load fallido limpia parcialmente
lo que asigno y conserva INITIALIZED. unload repetido es seguro. reset no equivale
a unload/load y no elimina medios, configuracion ni overlay de escritura.

La configuracion y las entradas se copian o consumen sin retener punteros prestados.
ROMs/discos se copian a memoria propia o reciben un contrato inmutable de vida util
hasta unload. El caller no puede liberar esos datos antes. Todo callback/config
host tiene politica de rebinding tras restore y no se confunde con estado Amiga.

Reset (T13a): `p6_reset` marca un reset duro que corre en la cabecera del siguiente `p6_run_frame`, antes de los eventos de medios y de la
entrada (es la misma cabecera del arranque, con su retardo aleatorio sembrado). Conserva discos (tambien un cambio pendiente: la cabecera inserta
el disco pedido de inmediato), configuracion y contadores del llamante (`unit_index`, `master_ticks` siguen creciendo aunque el contador del motor
vuelva a cero); el invitado arranca con la RAM limpia y el teclado repite su secuencia de arranque. Las teclas que el anfitrion mantiene se pulsan
una vez tras esa secuencia; las que solto en la unidad del reset no se pulsan. No equivale a unload/load: la cabecera resiembra el generador con la semilla de la configuracion (el retardo de arranque es el del encendido, no uno nuevo) y la cabeza de la
unidad de disco se queda donde estaba, como en una unidad real. El reloj de la placa sigue contando a traves de cualquier reset, tambien el del acorde de teclado (la cabecera de reset avisa al bridge justo antes de
que el contador de ciclos del motor vuelva a cero: reancla el reloj y suma a `master_ticks` lo que corrio antes del reinicio). Dos resets en unidades consecutivas son validos y reinician la maquina cada vez.

Sesion (T13b, T13c): cada `p6_load` empieza una sesion que se comporta como un proceso limpio. Los registros de la CPU, la posicion del haz, la cabeza
del disquete, los potenciometros, el scheduler secundario, el instante de servicio de los CIA, el estado por frame de los CIA (`cia_cycle_accurate`), los contadores y el movimiento pendiente del raton, las teclas y el acorde de reset, los nombres de
dispositivos de entrada y el ancla del reloj vuelven a su valor inicial; manifests/state-inventory.json lista cada uno con su prueba y lo que
sobrevive sin ser causal. Una recarga en el mismo proceso da, unidad a unidad y con el digest completo desde la unidad 1, lo mismo que un proceso
limpio en los diez micro-ROM, los que usan las interrupciones del teclado incluidos. Eso se ha comprobado con esos guests; no con un Kickstart real, cuyo arranque toca mas estado.
Replays, pruebas de determinismo y Waterbox siguen arrancando una instancia nueva por imagen. El heap no crece entre
ciclos: 0 fugas con `leaks` en 30 ciclos y 100 ciclos limpios bajo ASan/UBSan.

Una instancia nativa activa por imagen. create/init concurrente devuelve BUSY.
No API reentrante: sin hilos en el perfil, la unica llamada que puede encontrar otra en curso es la hecha desde dentro de una (un callback o el
observador de fronteras); devuelve BUSY antes de mirar sus argumentos y no toca nada (tests.lifecycle.test_busy). Un segundo `p6_init` o
`p6_load` con la imagen ya presente devuelve BAD_STATE. Todos los callbacks documentan si son solo-init, por-frame o
lecturas puras autorizadas. No invocar libretro fuera de su hilo de entrada.

Adaptador libretro (T16a, T17): `adapters/libretro` exporta solo los `retro_*` (exports.txt / exports.map) sobre la ABI nativa y no incluye nada del motor. El contenido
de `retro_load_game` NO es el firmware: es un disco (`.adf`, `.adz`, `.dms` o un `.zip` con un disco), una playlist `.m3u` de discos o ninguno (SET_SUPPORT_NO_GAME: el
Kickstart arranca solo); `need_fullpath` y `block_extract` son true (el core lee el ZIP en memoria; el frontend no extrae nada) y las extensiones son `adf|adz|dms|fdi|ipf|zip|7z|rp9|lha|lzh|hdf|hdz|m3u|uae|/` (los archivos de varios discos se describen en "Archivos de discos (T59)"; los discos duros `.hdf`/`.hdz` se describen en "Libretro (T63a)" tras el estado de un HDF; `.lha`, un `.zip` de ficheros y un directorio, la `/` de PUAE5, son volumenes: "Volumenes (T64a)"). El Kickstart 1.3 se busca en el directorio de sistema del frontend por una lista fija de nombres conocidos (`kick34005.A500`,
`amiga-os-130.rom` y tres nombres de coleccion); `p6_load` acepta o rechaza el fichero por su contenido (tamaño y SHA-256), no por el nombre, y un fichero presente pero
rechazado no oculta uno bueno bajo el nombre siguiente. El core copia el buffer porque `p6_load` lo toma prestado hasta el unload. Los ficheros se leen enteros a traves del VFS
del frontend (version 1: open, size, read, close) y con stdio POSIX si no lo ofrece, siempre con cota y nunca con el tamaño que el fichero dice tener: playlist 64 KiB, una ruta
4096 bytes, un disco 3 MiB antes de decodificar (T57: el tope de un ADF extendido o un FDI), un Kickstart 512 KiB. Contenedores (T17d, adapters/libretro/container.h, inflate.h, dms.h, cabeceras sin motor): el formato se
reconoce por los bytes, no por el nombre; ADZ (gzip), ZIP (almacenado o deflate; un disco aqui, varios en "Archivos de discos (T59)") y DMS (los modos de xDMS 1.3, dominio publico,
reescrito con limites y comparado con el de WinUAE) se decodifican en memoria, con salida acotada y todos los CRC comprobados, al ADF de 901120 bytes, y la identidad es el SHA-256
de ese ADF: el contenedor no entra en estados ni peliculas. Un ADF extendido (`UAE-1ADF`, `UAE--ADF`) y un FDI (T57) se toman TAL CUAL, tambien dentro de un ADZ, un ZIP o un 7z (`.fdi` es una extension de disco): la identidad
es el SHA-256 del fichero tal como esta, la comprobacion es la del puente (`classify_floppy`, antes del motor: el mensaje de un fichero danado es el suyo) y son de solo lectura (nada se guarda para ellos con `puae6_floppy_save`).
El IPF (T58) se toma tal cual, tambien dentro de un ADZ, un ZIP o un 7z (lo lee el decodificador libre de MAME, no capsimg), y es de solo lectura. Se rechazan con mensaje CT RAW, SCP, RAR (un LHA no es un disco: es un volumen), ZIP cifrado/ZIP64/multivolumen y DMS cifrado, de alta densidad o FMS. Las rutas son bytes UTF-8 que se pasan tal cual y nunca son identidad: los medios se nombran por el SHA-256 de sus
bytes (ADR 0008), y dos discos con los mismos bytes comparten un medio del manifiesto. Una playlist M3U tiene una entrada `fichero|etiqueta` por linea (la etiqueta es opcional y por
defecto es el nombre sin extension), las lineas `#` son comentarios salvo `#SAVEDISK:` (T60, descrito en el parrafo de escritura de libretro de mas abajo), las rutas relativas lo son al directorio de la
playlist (una ruta con raiz, letra de unidad o `esquema://` se usa tal cual) y se aceptan un BOM UTF-8 inicial y los finales de linea CR, LF y CRLF. Como mucho 16 discos
(`P6_MAX_MEDIA`) y cada entrada ha de ser un disco (ADF o uno de los contenedores anteriores); un NUL, una entrada sin nombre, una ruta de mas de 4096 bytes, mas de 16 entradas, una playlist vacia o un disco que no es un
ADF hacen fallar toda la carga con un mensaje que nombra la linea o el disco; nada se carga a medias. Disk Control: la interfaz EXT si el frontend la ofrece y la clasica si no. Las
llamadas del frontend solo registran la peticion (no leen ficheros ni tocan el bridge); al empezar el `retro_run` siguiente se convierten en
`p6_media_event_enqueue` (expulsar y/o montar), asi que un cambio cae siempre en una frontera de unidad y las mismas llamadas en los mismos runs dan los mismos eventos.
`set_initial_image` solo se usa si el contenido tiene esa imagen en ese indice; quitar una imagen (`replace_image_index` con NULL) se admite con la bandeja abierta, pero
`add_image_index` crea un hueco vacio al final de la lista y `replace_image_index` con un fichero (ADF o contenedor, leido con las mismas cotas) lo registra con
`p6_media_append` y enlaza el hueco a su indice del manifiesto, sin seleccionarlo (la seleccion la hace el frontend con `set_image_index`); el registro ocurre en el
callback porque no cambia la maquina, y el montaje sigue siendo el de la bandeja en el siguiente `retro_run`. Un rewind a antes de una adicion devuelve las unidades a como
estaban; el disco añadido sigue en el manifiesto y en la lista y se puede volver a insertar. Un fichero de estado guardado tras una adicion solo carga en un proceso
que haya añadido el mismo contenido en el mismo orden. Las opciones del core se describen en Configuracion. Solo acepta XRGB8888: si el frontend lo rechaza el load falla con un mensaje, sin
fallback. `retro_run` hace un poll de entrada, exactamente una llamada a `p6_run_frame` y entrega un solo cuadro: el raster activo recortado (con `SET_GEOMETRY` si el
frontend lo acepta) o, si la unidad no tiene cuadro nuevo, el anterior (NULL si el frontend puede duplicar, una copia si no). El audio se ofrece en lotes y lo que el frontend
no toma queda en una cola acotada de 16384 frames stereo que se ofrece primero en el run siguiente; al desbordarse se descartan los mas antiguos con un aviso. Una llamada
libretro hecha desde dentro de un callback se ignora. Los estados (`retro_serialize*`) se describen en Estados nativos. Los mandos se asignan joypad B/A/X -> botones 1/2/3 y el raton solo en el puerto 0;
el teclado, los dispositivos por puerto y los formatos ADZ/ZIP/DMS llegan con T17c y T17d.

## Frame, campos y reloj

Unidad de avance: una frontera vertical de video emulado, no una cantidad fija de
instrucciones ni una espera de 20 ms. En entrelazado, distinguir campo y frame
compuesto. run_frame entrega metadatos de field parity y no oculta dos avances
para dibujar una sola imagen. La cadencia frontend se documenta para cada modo.

Cadencia, aspecto y buffer por unidad (T23f). `cadence_num/cadence_den` y `aspect_num/aspect_den`
describen el haz que la unidad realmente ejecuto, no la configuracion: el estandar sale de
BEAMCON0 (bit PAL) en una maquina ECS y de la configuracion en OCS; las lineas por unidad son
maxvpos (+1 con campo largo, +1/2 entrelazado, sin extension con haz programable); el reloj de
linea es maxhpos_short (+1/2 con la alternancia 227/228 del haz NTSC); el reloj del chipset es el
de la configuracion, el mismo al que se ancla el RTC. Todo son enteros del haz, nunca el
`vblank_hz` float ni una medida. Un invitado ECS que pase de PAL a NTSC (o al reves) cambia la
cadencia y el aspecto de las unidades siguientes; un adaptador que anuncie un unico valor al
cargar debe releerlos por unidad (libretro: SET_SYSTEM_AV_INFO una vez por cambio de mas de un 1 %
en la tasa, ver ADR 0006; BizHawk: VsyncNumerator/Denominator por unidad con imagen). El buffer (`video_width/height`, `caps.max_video_*`) es constante
desde el load: 760 x 576, salvo un NTSC con Agnus OCS (A500, A500 OG, A1000, A2000 OG: 760 x 486; no pueden salir de su estandar). Una
configuracion NTSC con Agnus ECS o AGA (A500+, A600, A2000, A1200) reserva las 576 filas para no perder la imagen de un haz PAL; decide el Agnus del preset, no el numero de maquina (T72a).
Es el buffer del perfil (hires, doble linea): la resolucion y el modo de linea (T72, abajo) lo escalan, `p6_query_video(config)` da el de una configuracion y `caps.max_video_*` sigue describiendo el perfil.

p6_run_frame es la unica primitive publica que hace avanzar el scheduler. Los
adaptadores pueden traducir input/eventos antes y presentar A/V despues, pero no
llaman m68k_go ni inventan otro boundary. retro_run hace exactamente una llamada
p6_run_frame; el FrameAdvance Waterbox hace exactamente una llamada p6_run_frame.
La referencia PUAE/MacHawk explica como implementar esa primitive, no una excepcion
para saltarse el bridge.

Guardar master ticks, contador de unidades de video y fases fraccionarias. La
frontera debe estar definida respecto al scheduler; una instruccion que cruza el
borde se completa en el punto seguro definido y su desfase se conserva. run(N)
y N llamadas individuales deben producir el mismo estado bajo el mismo contrato.
No fabricar una cadencia universal exacta de 50/60 Hz: usar timings del motor y
fracciones enteras cuando sea posible, convirtiendo a double solo al informar al host.

RTC determinista: epoch fijo en sync config mas ciclos emulados y resto acumulado.
Propuesta de default: 2000-01-01T00:00:00Z, modificable solo antes de cargar o como
cambio sincronizado. Semillas explicitas y serializadas para cualquier fuente de
aleatoriedad del emulador. No eliminar aleatoriedad real del hardware: modelarla
con el algoritmo upstream y una semilla/configuracion registrada.

sleep, host_vsync, audio-fill feedback, reloj monotono y wall-clock no pueden variar
los ciclos del perfil deterministic. Puede leerse un reloj monotono FUERA del
motor para benchmarking, sin alimentar decisiones del emulador.

## Entrada, eventos y lag

Un paquete de entrada por unidad de video: teclas Amiga, dos puertos, botones,
deltas de raton con signo, eventos de medios y reset. Caps/qualifiers y key up/down
se prueban expresamente. Convertir posiciones absolutas del frontend a deltas en
el adaptador; guardar el estado previo necesario. No usar auto-repeat del SO.

Input poll libretro no es una lectura del hardware Amiga. Lag se deriva de que el
software invitado haya consultado un recurso de entrada relevante; instrumentar
el punto correcto del hardware, no declarar sin lag cada retro_run. Definir en el
corpus que lecturas cuentan (joystick, raton, teclado/CIA) para no dejarlo ambiguo.
Los callbacks de debugger/input son opcionales y no cambian el resultado emulado.

Semantica de p6_input (T10a). El paquete es el estado del host en esa unidad: el bridge guarda lo aplicado y envia
solo las diferencias, porque una pulsacion de tecla no es idempotente en el motor y el movimiento del raton es aditivo.
- Teclas: el bitmap es el estado; la tecla n es el codigo Amiga n (0x00..0x67). 0x68..0x7f son invalidos (BAD_ARGUMENT):
  0x78 inyectaria un aviso de reset y 0x7f se confunde con Ctrl. El bit 0x62 (Caps Lock) es el pestillo: el frontend
  convierte sus eventos en cambios de estado del bit, como el teclado del A500 (make al activar, break al desactivar).
  Orden dentro de un paquete: primero las liberaciones y luego las pulsaciones, cada grupo en orden ascendente de codigo.
  Un cambio que no cabe en el buffer de teclado del invitado (255 codigos) no se pierde: el bridge lo reintenta en las
  unidades siguientes, y si la tecla vuelve a su estado anterior antes de enviarse, el par se cancela.
- Ctrl-Amiga-Amiga reinicia dentro de la propia aplicacion del paquete; la unidad que lo contiene cubre el resto del
  reinicio. Tras el reinicio toda tecla que el host siga manteniendo se pulsa de nuevo, una vez, despues de la secuencia de
  arranque del teclado ($00, $FD, $FE), y su liberacion posterior llega como una liberacion normal.
- Puerto 0: raton (botones izquierdo, derecho y central = P6_BUTTON_1..3; dx/dy relativos de esta unidad) o joystick;
  puerto 1: joystick (tres botones y cuatro direcciones; izquierda+derecha o arriba+abajo dan eje neutro). Un raton en el
  puerto 1 es UNSUPPORTED (haria falta un segundo raton logico). Un puerto NONE no lleva botones ni movimiento; un joystick no
  lleva dx/dy; un raton no lleva bits de direccion (BAD_ARGUMENT). La validacion ocurre antes de tocar la maquina.
- El motor entrega como mucho 120 cuentas por eje y por lectura de JOYxDAT y descarta el resto: dx/dy son exactos para
  |d| <= 120 por unidad. Los joysticks son gamepads, de modo que sus botones 2 y 3 se ven en POTGOR.
- Orden completo dentro de un boundary: reset (cabecera), eventos de medios, entrada (teclas, puerto 0, puerto 1), unidad.
  La entrada va despues de la cabecera del reset para no mezclarse con la secuencia de arranque del teclado.

Entrada del adaptador libretro (T17c). Se lee una vez por retro_run, tras el unico input poll, con `input_state_cb`; no se usa el callback asincrono de
teclado. Reglas (adapters/libretro/input_map.h; tests/libretro-host/test_input.py):
- Teclado: siempre conectado, sea cual sea el dispositivo de los puertos. Cada RETROK_* se mapea por simbolo sin Shift (en un teclado US, la posicion
  fisica) a una tecla Amiga 0x00..0x66 con la tabla de input_map.h, que alcanza todas las teclas que nombra el wrapper BizHawk. Sin gemelo en el PC: Insert
  y Help dan Help, Home y End los parentesis del teclado numerico, Page Up/LMeta/LSuper dan Left Amiga y Page Down/RMeta/RSuper Right Amiga, LCtrl y
  RCtrl dan Control. Los codigos que el Amiga no tiene (F11 en adelante, Pause, Print, NumLock...) o desconocidos se ignoran; nunca se envia 0x68..0x7f.
  Caps Lock es el pestillo del contrato: cada pulsacion de la tecla del host (flanco de arriba a abajo) lo conmuta.
- Raton: RETRO_DEVICE_MOUSE leido en el puerto libretro 0 (el unico raton fisico de RetroArch), con "Joystick + Mouse" o "Mouse only" en ese puerto; botones izquierdo, derecho y central = P6_BUTTON_1..3. Cada
  unidad envia como mucho 120 cuentas por eje; lo que no cabe se arrastra a las unidades siguientes con un tope de 8 unidades (960 cuentas) por eje, sin
  aceleracion ni reloj. El arrastre se descarta si el puerto 0 deja de leer el raton.
- Joystick: B, A, X = botones 1, 2, 3; cruceta y stick izquierdo (umbral fijo 20000 de 32767) = las cuatro direcciones.
- Puertos: el jugador 2 de libretro (puerto 1) es un joystick en el primer puerto Amiga (`ports[0]`, el del raton, donde los juegos de dos jugadores leen su
  segundo mando). El jugador 1 (puerto 0) ofrece, con SET_CONTROLLER_INFO: "Joystick + Mouse" (RETRO_DEVICE_JOYPAD, el valor por defecto), "Joystick only"
  (subclase RETRO_DEVICE_SUBCLASS(JOYPAD, 0) = 257), "Mouse only" (RETRO_DEVICE_MOUSE), "Keyboard only" y None. Con "Joystick + Mouse" el RetroPad
  maneja el segundo puerto Amiga (`ports[1]`) y el raton leido en el puerto libretro 0 el primero (`ports[0]`), a la vez, como PUAE5 (que lee el raton
  fisico ademas del pad) y el wrapper BizHawk (raton en el primer puerto, joystick en el segundo); un raton sin movimiento envia cero cuentas, de modo que
  quien juega solo con mando no pierde nada. RetroArch entrega RETRO_DEVICE_JOYPAD en todo puerto que nadie ha cambiado, por eso el JOYPAD simple del
  puerto 0 es la combinacion; "Joystick only" es lo que se elige para dejar libre `ports[0]` al jugador 2. "Mouse only" solo mueve `ports[0]`;
  "Keyboard only" y None dejan vacios los puertos Amiga que ese jugador manejaria (P6_PORT_NONE). El puerto 1 ofrece Joystick, "Keyboard only" y None
  (el contrato no tiene un segundo raton, ni "Joystick only" ahi). Un tipo no ofrecido se rechaza y el puerto conserva el suyo. Si el jugador 1 tiene el
  raton ("Joystick + Mouse" o "Mouse only") y el jugador 2 es Joystick, ambos pedirian `ports[0]`: gana el raton y el joystick del jugador 2 no se envia;
  un mensaje INFO lo dice al cargar y cuando una llamada a retro_set_controller_port_device lo provoca. La regla depende solo de los dos dispositivos, que son
  configuracion del frontend y no de la linea temporal; un cambio de dispositivo se ve en el siguiente retro_run.
- Doble entrada: el nucleo no puede saber que teclas del host estan ligadas a un RetroPad (RetroArch liga flechas y letras por defecto), asi que esas teclas
  llegan como joystick y como teclas Amiga. La mitigacion es el tipo "Keyboard only" (un puerto sin RetroPad), no una opcion del nucleo.
- Memoria de entrada: el pestillo de Caps Lock, el flanco previo de esa tecla y el arrastre del raton son estado del adaptador, no del bridge. Viajan en la
  seccion del adaptador del estado de libretro (version de marco 2; un estado de la version 1 se rechaza), de modo que un load, un rewind o una rama de
  runahead devuelven la memoria de la linea temporal que restauran. Lo que el frontend mantiene pulsado no se guarda: se vuelve a leer en cada ejecucion.

Lag (T10b). `P6_FRAME_LAG` de una unidad significa que la CPU emulada no leyo ninguno de JOY0DAT, JOY1DAT, POT0DAT,
POT1DAT, POTGOR, CIA-A PRA (botones de fuego; comparte registro con lineas de disco) ni CIA-A SDR (el byte del teclado)
durante ella. Una mascara de escritura unica para el motor (parche 0065) la registra y el bridge la limpia al empezar la
unidad y la lee al terminar; no hay callback desde dentro del motor. Lo que el frontend consulta o envia no cuenta. Un
programa que solo usa el teclado da lag casi siempre (el SDR solo se lee al llegar un byte); uno que lee el disco por PRA
no da lag. La mascara es estado derivado: no forma parte del estado ni cambia ningun hash. Capacidad: `P6_CAP_LAG_FLAG`.

Luz de unidad (T20a). `P6_FRAME_DRIVE_LIGHT` indica que al final de la unidad el LED de algun disquete esta encendido (motor en marcha o
escribiendo, los bits `LED_DF0..DF3` que el motor mantiene en el puerto gui del host). Es una salida derivada del estado de la unidad: no
llega a la maquina ni cambia ningun hash, y sirve a `IDriveLight` del wrapper.

Orden en boundary: aplicar reset/eventos pendientes en orden estable registrado,
fijar input, ejecutar, recoger A/V y devolver. Eventos simultaneos tienen prioridad
documentada. No cambiar el disco desde otro hilo. Entradas subframe quedan fuera
del MVP; reservar versionado para añadir timestamps de ticks en el futuro.

## Video

Render software propio del motor. Baseline: XRGB8888 para libretro si el frontend
lo acepta; probar fallo o fallback explicito si no. Para Waterbox convertir al
layout de IVideoProvider del pin. Reportar ancho, alto, stride en bytes, region
activa, aspecto, modo y paridad. No asumir stride=width*4 ni altura constante.

Capacidades de buffers se fijan por un limite demostrado del perfil. Ante modo
fuera del limite, error controlado antes de escribir, no truncar silenciosamente.
Los hashes de video canonicos se calculan sobre pixeles activos normalizados,
sin padding, overlays, shaders, bytes no inicializados o conversiones de UI.

render=false suprime entrega/conversion externa, NO avance de copper/blitter/DMA,
colisiones ni otro comportamiento invitado. Un framebuffer de entrelazado/weave
puede necesitar historia; no es automaticamente descartable para savestates.

## Audio

Paula conserva su timing interno. Acumular muestras segun ticks y una fase
fraccionaria persistida, no redondear frecuencia/fps independientemente cada frame.
Mantener en estado filtros, resampler, interpoladores, colas y contadores que afecten
muestras futuras. La salida inicial es PCM stereo int16 intercalado; samples_count
significa frames stereo, no numero total de int16.

Sample rate fijo por sesion/config; 48000 Hz propuesto para libretro/headless.
El adaptador Waterbox acuerda con su IVideo/ISoundProvider el rate soportado y lo
incluye en perfil. Comparaciones exactas A/V entre backends usan MISMO rate/filtro;
comparaciones de hardware pueden ignorar solo la presentacion de salida.
No adaptar los clocks Amiga al llenado del buffer. rendersound=false sigue
avanzando filtros/fases y descarta solo la entrega; test alternando true/false.

Implementado (T11, docs/adr/0007-audio-delivery.md): `p6_run_frame` devuelve en cada unidad los pares stereo que
Paula produjo hasta el tick del borde, tras volcar Paula a ese tick. Frames acumulados en el tick T:
`floor((256*(T-2048)+127)/m)`, `m = (2*clk*131072 + rate)//(2*rate)`, sin redondeo por frame. La cola se drena siempre;
`P6_RENDER_AUDIO` solo decide si `audio` / `audio_frames` se rellenan. Puntero valido hasta la siguiente llamada mutante
(`p6_state_save` no lo invalida); como mucho `max_audio_frames` por llamada, y un desbordamiento deja la imagen en fallo, no
descarta muestras. Rates 44100 y 48000. La diferencia de floats entre hosts sigue abierta para DET04.

Audio batch con capacidad verificada y retorno de consumo. Definir explicitamente
la politica de cola si el frontend acepta menos muestras; no alimentar ese retorno
a la velocidad de CPU. Distinguir cola de presentacion de estado de hardware.

## Configuracion

Un esquema genera validadores, opciones libretro y sync settings C#. Campos
normalizados y serializados de forma canonica producen config_hash. Una maquina
A500/A600/A1200 es una config explicita; no detectar CPU a partir de la velocidad
medida del host. Presets iniciales derivan de prefs upstream comprobadas.

Opciones no soportadas se rechazan antes de init. Cambios de CPU/RAM/ROM/regiones
que requieran recargar no se aplican en caliente ni cambian serialize_size.
Config .uae heredada pasa por un importador allowlist con diagnosticos; no da
acceso a rutas arbitrarias, plugins o shell. No se ejecuta un comando contenido
en una playlist/configuracion de juego.

Modelo implementado (T17b): `config/schema/options.json` es el esquema unico de lo que la ABI v1 deja elegir, y `tools/generate_options.py` lo valida, normaliza una configuracion
pedida y genera las tablas del core libretro (`adapters/libretro/options_generated.h` y `config/schema/options_table.generated.json`; `--check` falla si estan obsoletas).
Normalizar da a cada opcion el valor pedido o el defecto del esquema; una clave desconocida, una no soportada (CPU, RAM, ROM, una maquina que no sea A500, A600 ni A1200, `.uae`; con su razon)
o un valor fuera del esquema levanta ConfigError, que nombra la clave y todos los problemas, y nunca devuelve una configuracion parcial. Headless y el wrapper BizHawk no se
generan: `tests/config` comprueba contra el esquema sus defaults, valores y mapeo a `p6_config`, de modo que no hay defaults divergentes. Cada opcion es `reboot` (un campo de
`p6_config`: entra en config_hash y en la identidad del estado) o `presentation` (sin efecto sobre la emulacion). El core libretro lee las opciones antes de cargar y un valor que
no sea exactamente un nombre del esquema (sin recortar, sin cambiar mayusculas y sin sustituirlo por el defecto) hace fallar la carga con un mensaje que nombra la clave y el
valor; semilla y epoca RTC no se ofrecen en libretro y quedan en su defecto. Con la maquina en marcha, un cambio de una opcion `reboot` solo se avisa y se aplica al volver a
cargar el contenido (no cambia el config, la identidad ni el bound de estado), uno de presentacion se aplica en el acto y un valor no valido se avisa y se ignora.
La regla de que un cambio que afecta al tamaño del estado exige recarga se cumple por construccion (todo lo que entra en el config es `reboot`) y T18 la comprueba contra el bound
real: `retro_serialize_size` no se mueve con una opcion de presentacion cambiada en marcha ni con una `reboot` solo avisada, y es el mismo en cada carga del mismo contenido y configuracion.

### Maquinas y capacidades (T23, T24)

`p6_config.machine` elige entre tres presets (T23, T24; T50 anade cinco mas, ver abajo) del MISMO perfil de build `deterministic-a500` (no hay un segundo perfil: mismo motor, mismo scheduler `p6_run_frame`, sin ifdefs
de frontend; el interprete 68000 ciclo exacto `cpuemu_13` y el 68020 ciclo exacto `cpuemu_21` estan compilados y `m68k_run` elige por el modelo de CPU del preset, ADR 0010).
Cada preset sale de `built_in_prefs` del upstream fijado (`config/profiles/a500-ocs.json`, `a600-ecs.json`, `a1200-aga.json`; `tests/host` compara cada perfil con las
preferencias que construye `bridge/config.cpp`, campo a campo, y cada campo que difiere del upstream lleva su razon).

| | A500 | A600 | A1200 |
| --- | --- | --- | --- |
| Chipset | OCS (Agnus + Denise OCS) | ECS (Agnus + Denise ECS: SuperHires, DIWHIGH, BEAMCON0/VARBEAMEN, productividad) | AGA (Alice + Lisa: paleta de 256 entradas de 24 bits, 8 planos, HAM8, BPLCON4, FMODE 0/1/3 con fetch de 16/32/64 bits, sprites de 16/32/64 bits) |
| CPU | 68000 ciclo exacto, 24 bits, sin FPU/MMU | igual | 68EC020 ciclo exacto (`cpuemu_21`, cache de instrucciones de 64 lineas, 24 bits), sin FPU/MMU: la linea F levanta la excepcion 11 |
| Chip RAM | 512 KiB | 1 MiB (sin alias del primer medio MiB) | 2 MiB (sin alias) |
| Slow (Bogo) RAM | 512 KiB | ninguna | ninguna |
| Fast RAM | ninguna de serie; opcional una placa Zorro II autoconfig de 1, 2, 4 u 8 MiB (`p6_config.fast_kib`, T49) | igual | igual |
| RTC | MSM6242B (epoca de la config) | ninguno de serie (el cuerpo `P6RT` del estado va vacio) | ninguno de serie (igual) |
| Gayle / IDE / PCMCIA | no existe | de serie NO: `cs_ide = cs_pcmcia = cs_compatible = 0`; con `p6_config.hdf_controller = IDE` y un HDF, Gayle con su puerto IDE (T56, ver "Gayle con IDE"); sin PCMCIA | igual; sin Gayle, `$DE1000` cae en el espejo de custom |
| Kickstart probado | 1.3 (r34.5) | 2.05 (r37.350), con Workbench 2.05 r37.71 de disco | 3.1 (r40.68), hasta la pantalla de insertar disco y con una demo AGA gratuita (Catabasis) cargada de disco |
| Selector | libretro `puae6_machine` = `A500` (defecto) / `A600` / `A1200` (reinicio; el `A1200` es el A1200OG de PUAE5); headless `--machine`; BizHawk sync setting `Machine` | igual | igual |
| FirmwareID BizHawk | Amiga/A500 | Amiga/A600 | Amiga/A1200 |
| Estados y peliculas | propios del A500 | propios del A600 | propios del A1200: cada maquina rechaza las de las otras dos antes de tocar nada (`config_hash` lleva la maquina; matriz 3 x 3 en las pruebas) |
| Chipset de VPOSR / DENISEID | familia 0 (OCS) | familia 2 en la tabla upstream = 8372A (`$2xxx` PAL, `$3xxx` NTSC), DENISEID `$FFFC` | Alice `$23xx` PAL / `$33xx` NTSC, Lisa `$00F8` |
| Cota de estado (`retro_serialize_size`) | 3 608 621 B | 3 608 590 B | 5 890 824 B (cuerpo del 68020, paleta AGA, anillo de Denise y raster a 3 bytes por pixel; ADR 0009 punto 9) |
| Waterbox, mmap medido (presupuesto 16 MiB) | 5 586 944 B | 5 046 272 B | 6 094 848 B (36 % del presupuesto, sin cambiarlo) |
| Waterbox, snapshot tras 60 unidades | 9 203 892 B | 9 187 508 B | 10 236 084 B |

Probado con firmware autorizado (opt-in por variables de entorno, nunca copiado ni registrado): Kickstart 1.3 en el A500 y Kickstart 2.05 en el A600 arrancan hasta
la pantalla de insertar disco y hasta el shell del Workbench 2.05, y DET02 restaura un estado tomado durante la carga real de un disco (parche 0105). El Kickstart 3.1 r40.68 arranca
en el A1200 sin Gayle hasta la pantalla de insertar disco y, con la demo AGA gratuita Catabasis en DF0, sale de ella y muestra una paleta de 24 bits (mas de 32 colores, canales que
el ECS no produce); DET02 restaura estados del A1200 tomados con el Kickstart 3.1 y en mitad de la carga del disco de la demo, en el mismo proceso y en uno nuevo; el nucleo libretro
corre el A1200 unidad por unidad como headless (con RetroArch real en un contenedor, tambien con el Kickstart 3.1 y la demo) y el nucleo BizHawk dentro de Waterbox lo ejecuta y restaura
estados en un sandbox nuevo y en otro proceso (WB03). Probado sin firmware comercial: micro-ROM de
identificacion, 1 MiB de Chip, SuperHires, DIWHIGH, BEAMCON0/VARBEAMEN y productividad en PAL y NTSC (AV01, AV02); en el A1200, un micro-ROM de instrucciones 68020 con valores
calculados desde la semantica del M68020UM (`tests/host/test_cpu_020.py`, `tests/step/test_a1200.py`), 15 programas AGA con la imagen predicha desde el propio programa en PAL y NTSC
(`tests/scenarios/aga`, `tests/step/test_aga.py`), audio con el modelo de filtro del A1200 (`AudioA1200Tests`), 185 pruebas DET02 (`tests/state/test_det02_a1200.py`), estados nativos y
de Waterbox, libretro con RetroArch real y el nucleo BizHawk (WB03).

NO probado (no afirmar compatibilidad): otros Kickstart que el motor acepta por contenido (2.04 o 3.1 r40.063 en el A600, el Kickstart 3.0 en el A1200; T51 mide el 2.05 en el A500 y otros arranques, ver "Kickstart alternativo y rom.key"); configuraciones del
el Kickstart 3.1 del A600 (r40.063, el del A600 de PUAE5; no hay copia autorizada) y Chip de 1,5 MiB; FPU (T54); la ranura PCMCIA y sus tarjetas (el IDE de Gayle es T56); 68030/040/060 y otros 020 sin el bus de 24 bits (T55); la interfaz de EmuHawk
(el wrapper se probo por FFI y en el arnes, no con la UI); CD32/CDTV. La compatibilidad del A1200 con juegos y demos AGA mas alla de la demo citada no esta medida.

Medido (lo que hace el motor, no una afirmacion de lo que hace un chip real): la tabla upstream identifica el A600 como 8372A (VPOSR `$20`/`$30`) y el A1200 por Alice `$23`/`$33`;
en SuperHires el motor pinta el borde negro en lugar de COLOR00; en NTSC la imagen SHRES de las lineas impares aparece 2 columnas a la izquierda (el propio comentario de upstream llama a
esa alineacion un hack); en el A1200 un sprite aparece un pixel lores a la derecha del pixel de plano de la misma coordenada, y un DDFSTRT que no cae en una unidad de fetch empieza la
imagen en la unidad siguiente (las dos medidas de T24d, fijadas en `MeasuredTests` de `tests/step/test_aga.py`, 15 casos la segunda).

Limites documentados: la unidad en la que el invitado escribe BEAMCON0 se describe entera con el haz nuevo aunque parte corriera con el anterior (un error de una unidad,
determinista; ADR 0006 "Haz programado"); el aviso de cambio de cadencia de libretro (`SET_SYSTEM_AV_INFO`) solo salta cuando la tasa se aleja mas de un 1 % de la anunciada,
de modo que el campo largo y el entrelazado (0,3 %) no lo disparan y un cambio real PAL<->NTSC (19 %) si; los rasters de mas de 576 filas o 760 columnas no se entregan
(`P6_FRAME_NO_VIDEO`); fuera de `active` todo byte vale cero en cada unidad, tambien en la transicion de haz (T23g) y en los modos AGA mas anchos (T24d). En el A1200,
`waiting_blits = 0` (el preset de upstream pone 1, "automatico", para toda CPU desde el 68020: la espera del blitter correria dentro de la escritura que lo arranca, un atajo de emulacion
sin hardware detras y un bucle en mitad de una instruccion; con 0 la CPU ejecuta sus propios bucles de sondeo, como en las maquinas de 68000), de modo que un programa que cuenta con ese
atajo no lo encuentra; sin Gayle (el A600 y el A1200 de serie, y con la opcion IDE pero sin HDF), `$DE1000` y su banco leen el espejo de custom en lugar de un registro de Gayle. El A1200 de serie es el A1200OG de PUAE5; el que lleva 8 MiB de Fast RAM es el preset `a1200_fast8` (T49).

### Presets A500 OG, A500+, A1000, A2000 OG y A2000 (T50)

Cinco presets mas del MISMO perfil `deterministic-a500` (mismo motor, mismo `p6_run_frame`), valores aditivos de `p6_config.machine` (`P6_MACHINE_A500OG` 4, `A500PLUS` 5, `A1000` 6, `A2000OG` 7, `A2000` 8):
los tres anteriores conservan valor, `config_hash`, estados y hashes. `bridge/config.cpp` tiene una tabla (`kMachines`) con el modelo y la configuracion de `built_in_prefs` de cada uno, su
memoria, CPU y chipset; cada perfil esta en `config/profiles/<maquina>.json` y `tests/host/test_machine_profiles.py` lo compara campo a campo con el comparador upstream (fuente de cada campo
`upstream`/`pin`/`override`/`config`; un campo `upstream` o `pin` tiene el valor del comparador, un cambio sin declarar falla). El comparador se construye como WinUAE: primero el estandar de video,
despues `built_in_prefs`.

| | A500 OG | A500+ | A1000 | A2000 OG | A2000 |
| --- | --- | --- | --- | --- | --- |
| Sale de | modelo 0, configuracion 3 | modelo 1 | modelo 3 | modelo 0 + tabla `CP_A2000` | modelo 1 (chipset ECS Agnus) + tabla `CP_A2000` |
| Chipset | OCS | ECS (Agnus y Denise) | 8361 Agnus + 8362 Denise (`CSMASK_A1000`, `cs_agnusmodel`/`cs_denisemodel`) | OCS | ECS Agnus + Denise OCS (el `ecs_agnus` de PUAE5) |
| Chip / Slow | 512 KiB / ninguna | 1 MiB / ninguna | 512 KiB / ninguna (la memoria del preset: otra se rechaza) | 512 KiB / 512 KiB | 1 MiB / ninguna |
| Reloj MSM6242B | no | si | no | si | si |
| TOD de CIA-A | sincronismo vertical | sincronismo vertical | red (50/60 Hz) | red | red |
| Kickstart (libretro, `puae6_machine`) | el 1.3 del A500 | `kick37175.A500` (2.04) | 1.3, 1.2, 1.1 como ROM de 256 KiB | el 1.3 | `kick40063.A600` (3.1 r40.063) |
| FirmwareID BizHawk | Amiga/A500 | Amiga/A600 | Amiga/A500 | Amiga/A500 | Amiga/A600 |
| Estado | el del A500 sin Slow ni reloj (-524 319 B) | igual tamano que el A500 | el del A500 OG + CTOD (+16 B) | el del A500 + CTOD (+16 B) | el del A500+ + CTOD (+16 B) |

Overrides declarados en los perfiles (el resto de diferencias con upstream es la de siempre: media por content id, entrada, video, audio):
DF1 se conserva (la configuracion 3 del A500 y el A1000 de upstream solo tienen una unidad; la ABI da DF0 y DF1 a toda maquina); en el A1000 y los A2000, `cs_compatible = 0` despues de aplicar la
tabla una vez (cada `fixup_prefs` la reaplicaria con el aviso de reset, `cs_resetwarning`, que la frontera de entrada del estado rechaza; el A600 y el A1200 hacen lo mismo por Gayle); el filtro de audio del
A1000 es el del perfil (emulado) y no el "siempre activo" de upstream; el A2000 (ECS Agnus sin Denise ECS) es el de PUAE5 (`chipset ecs_agnus`). El A2000 no tiene modelo upstream: PUAE5 lo hace con
`chipset_compatible = A2000`, que en WinUAE es `cs_compatible = CP_A2000` y `built_in_chipset_prefs`, y asi lo construye el puente (`cs_rtc`, `cs_ciaatod`, `cs_unmapped_space`, `cs_df0idhw` son
overrides con `via: CP_A2000`). `cs_ciaatod` (50 o 60 Hz) depende del estandar, que el puente elige ANTES de `built_in_prefs`, como WinUAE.

Memoria: A500 OG, A2000 OG, A500+ y A2000 siguen las reglas de T49 de su Agnus (OCS: Chip 512 KiB o 1 MiB; ECS: tambien 2 MiB; Slow 512 KiB a 1,75 MiB; una placa Fast Zorro II); el A1000 solo admite la
suya (`P6_ERROR_UNSUPPORTED` "the A1000 has the memory of its preset"). FPU: solo el A1200.

Decision del A1000: el Kickstart llega como imagen ROM de 256 KiB (1.3 probado; 1.2 y 1.1 los busca el nucleo libretro pero NO se probaron). El A1000 real arranca con un ROM de arranque de 8 KiB y el
Kickstart en un disco que se carga en la RAM de solo-escritura (WOM): el cargador portable solo acepta 256 o 512 KiB (y la imagen de disco `KICK`), asi que ese camino no existe aqui y `cs_a1000ram`
queda como bandera sin efecto. Declarado, no emulado.

TOD de red: con `cs_ciaatod` 1 o 2 el TOD cuenta 50 o 60 Hz y no el sincronismo; el estado lleva dos palabras mas (parche 0240: `cia_hsync` y `vblank_hz`, cuerpo CTOD de P6CH, solo en esas tres
maquinas: los estados de las otras cinco no cambian), sin las cuales DET02 divergia (un tick de TOD) con un micro-ROM y para siempre con el Kickstart 1.3 y un juego, en un estado tomado a mitad de la
carga del disco. Medido (no afirmado de un hardware): el periodo del TOD de red es el de 1/50 s o 1/60 s mas una o dos lineas (el contador de `cia_hsync_do` espera un limite de linea; 14 233 relojes E en
PAL frente a 14 187,6 ideales); el registro de decenas de ano del reloj vale 10 en 2000 (`tm_year / 10`, como lo guarda `rtc.cpp`); en NTSC las dos columnas de la izquierda de cada par de filas
(fuera de lo que describe el modelo) son negras en el A1000 donde el A500 muestra el final de la linea anterior.

Probado: micro-ROM en los cinco, PAL y NTSC (identificacion de chips, Chip RAM y su alias, reloj, TOD, imagenes deducidas del programa y sus hashes de repetibilidad en
`tests/scenarios/machines/machines-baselines.json`; `tests/step/test_machines.py`, AV01), DET02 con 439 pruebas y la estructura y matriz de estados (`tests/state/test_det02_machines.py`,
`test_state_machines.py`), headless, nucleo libretro (`tests/libretro-host/test_machines.py`: sesion = `p6_headless --machine`, nombres de Kickstart, familias, memoria, estados) y el nucleo BizHawk dentro de Waterbox
(`tests/waterbox-state/test_wrapper_machines_t50.py`, WB03 y la matriz de identidad entre las ocho maquinas). Con firmware autorizado (opt-in): el Kickstart 1.3 arranca en el A500 OG, el A1000 y el A2000 OG
hasta la pantalla de insertar disco (la misma imagen que el A500) y, con el disco del juego autorizado, hasta su pantalla de carga; DET02 restaura estados con el 1.3 (con y sin el disco) en las tres maquinas
(`tests/step/test_kickstart_ks13.py`, `Det02Kickstart13*`).

NO probado: el Kickstart 2.04 en el A500+ y el 3.1 r40.063 en el A2000 (no hay copia autorizada; esos dos presets solo corren micro-ROM); los Kickstart 1.2 y 1.1 en el A1000; el arranque real del A1000 con
su ROM de arranque; la compatibilidad con juegos o demos mas alla del arranque citado; la interfaz de EmuHawk. Los libretro y BizHawk piden el firmware por familia (la base de BizHawk de MacHawk no se toca):
quien tenga el 2.04 y el 3.1 los elige en el gestor de firmware de la entrada Amiga/A600.

### Memoria configurable (T49)

`p6_config` lleva tres tamanos en KiB (`chip_kib`, `slow_kib`, `fast_kib`; ocupan la mitad de lo que era `reserved`, el struct sigue midiendo 72 bytes): 0 es la memoria propia de la
maquina, `P6_MEMORY_NONE` (`0xFFFFFFFF`) es ninguna (Slow y Fast), y los valores se validan por maquina en `p6_init` (`P6_ERROR_UNSUPPORTED` con la razon): Chip 512 KiB o 1 MiB en
el A500 (Agnus OCS) y ademas 2 MiB en el A600 y el A1200 (no se ofrece 1,5 MiB: el motor lo asigna como 2 MiB con la mitad alta inalcanzable); Slow 512 KiB, 1 MiB o 1,5 MiB (y 1,75 MiB
salvo en el A1200, cuyo AGA mapea 1,5 MiB como maximo); Fast una sola placa Zorro II de 1, 2, 4 u 8 MiB. Zorro III y una segunda placa quedan fuera (T55).

La placa de Fast RAM es una tarjeta autoconfig de verdad: el motor la presenta en `$E80000` al terminar el reset (`expamem_reset`), el Kickstart (1.3, 2.05 y 3.1 probados) la configura
escribiendo su base (`$200000` para 8 MiB) y anade su memoria a la lista del sistema. Sin placa no hay cadena autoconfig ni ventana mapeada: las tres maquinas de serie siguen
siendo, byte a byte, las de antes (los parches 0110 a 0113 solo actuan con `fastmem[0].size != 0`). La placa se asigna al cargar la imagen y es una region mas de `p6_memory_regions`
(`P6_REGION_FAST_RAM`, big endian, el byte n de la region es la direccion `$200000 + n`). Su contenido entra en el estado nativo (P6RM, ver ADR 0009 punto 10): la cota crece en
`4 + fast + 278` bytes, y la memoria configurada forma parte de `config_hash` cuando no es la del preset (un estado o una pelicula de otra memoria es INCOMPATIBLE).

Cotas de estado medidas (micro-ROM, tras T15n: BEAM de 80 a 89 palabras, ADR 0009 punto 14, T62: DSKX de 41 a 42 palabras, +4 bytes en todas, punto 15, T57a: DSKX de 42 a 46 palabras, +16 bytes, punto 16, y T57b: DSKX a 62 palabras, +64 bytes, y la cota de DSDn de 4 a 6 bytes por palabra del buffer, +131 072 bytes, punto 17): A500 3 739 773, A600 3 739 742, A1200 6 021 976; A500 OG 3 215 454, A500+ 3 739 773, A1000 3 215 470, A2000 OG 3 739 789, A2000 3 739 789
(T50: sin Slow ni reloj el A500 OG y el A1000; +16 de CTOD en el A1000 y los A2000); A500 con 2 MiB de Fast 5 837 207; A600 con 2 MiB de Chip y 8 MiB de Fast 13 177 208;
A1200 con 8 MiB de Fast 14 410 866; A1200 con 8 MiB de Fast y 1,5 MiB de Slow 16 508 018; A1200 con FPU 68882 6 022 086.

### FPU 68881/68882 (T54)

`p6_config.fpu_model` (los 4 bytes que eran `reserved32`, offset 52; una union anonima conserva el nombre antiguo y el struct sigue midiendo 72 bytes): 0 sin FPU (por defecto), 68881
o 68882. Solo el A1200 (su 68020 tiene interfaz de coprocesador): en un 68000 o con otro valor `p6_init` falla con `P6_ERROR_UNSUPPORTED` y la razon. La FPU interna del 030/040 llega con
esas CPUs (T55). Se calcula con `fpp.cpp` + `fpp_softfloat.cpp` + `softfloat/*` (nunca `fpp_native`, x87 ni `long double`): los resultados son bit a bit los mismos en cualquier host e
ISA (corpus de `tests/host/fpu_corpus.py` en las cinco filas de la matriz, `tests/host/fpu_matrix.golden.json`). Una maquina sin FPU lanza la excepcion de linea F (vector 11) con
las instrucciones de coprocesador, como siempre.

`config_hash`: el modelo entra en la palabra `flags` del hash en los bits 23 y 24 (1 = 68881, 2 = 68882), entre la memoria (bits 8-22 y 31) y `p6_config.flags` (0-7); sin FPU la palabra es la
de antes y el hash de las series no cambia. Un estado o una pelicula de un modelo es INCOMPATIBLE en los otros dos. La cota crece 110 bytes con FPU (A1200: 5 890 716 -> 5 890 826; con T15l, 5 890 768 -> 5 890 878; con T15n, 5 890 820 -> 5 890 930); ver
ADR 0009 punto 11. Opciones: libretro `puae6_fpu_model` (None, M68881, M68882), headless `--fpu 0|68881|68882` y el sync setting `FpuModel` de BizHawk (en la identidad del estado y de la
pelicula solo cuando no es `None`).

### CPU 68010 y reloj de la CPU (T52)

`p6_config.cpu_model` (un byte, offset 63: el octavo de lo que era `reserved[0]`; `reserved8` desaparece como campo y el struct sigue midiendo 72 bytes): **cero es la CPU de la maquina** (68000 en el A500,
A500 OG, A500+, A600, A1000, A2000 OG y A2000; 68EC020 en el A1200), 1 = 68000, 2 = 68010, 3 = 68020 (`1 + (modelo - 68000) / 10`). Decision: **la 68010 es opcional en las maquinas de 68000**, incluido el A600
(el mismo zocalo; la 68010 es la mejora de siempre de una maquina de 68000) y **el A1200 solo tiene su 68EC020 soldada**: es la unica CPU para la que estan hechos su preset AGA, el Kickstart 3.1 (que el motor
rechaza en una 68000, parche 0290) y el bus de 24 bits, de modo que `cpu_model` 1 o 2 en el A1200, o un 68020 en una maquina de 68000, o un valor mayor de 3, dan `P6_ERROR_UNSUPPORTED` con la razon antes de
tocar el motor. La 68030, 68040 y 68060 no se ofrecen (T55). Es una **opcion de reinicio**: cambia el juego de instrucciones, los marcos de excepcion y los registros.

La 68010 cycle-exact es la fila `[1][4]` de `cputbls` (`op_smalltbl_13`, parche 0310): `cpuemu_13.cpp` la lleva desde siempre (`gencpu 13 14 21` genera 13 y 14 en el mismo archivo), asi que **GEN01 y los
fuentes generados no cambian** y las tablas de la 68000 salen byte a byte iguales. Lo que distingue a la 68010 y esta probado con valores derivados del manual (`tests/host/test_cpu_010.py`): MOVEC con VBR, SFC,
DFC y USP (y `MOVEC CACR` como instruccion ilegal), la tabla de vectores en VBR, el marco de excepcion de cuatro palabras con la palabra formato/offset de vector (la 68000 apila tres), el marco de formato 8
de 29 palabras del error de direccion y su RTE, RTD, MOVES, MOVE from CCR y MOVE from SR como instruccion privilegiada, y el modo bucle. **Hallazgo (parche 0311, defecto de upstream 6.0.3)**: el modo bucle de la 68010
cycle-exact estaba muerto (`loop_mode_table` se llenaba de `cpufunctbl`, cuyas entradas son NULL en todas las tablas cycle-exact, asi que el DBcc nunca lo activaba y un bucle de una instruccion costaba lo que en una
68000); con el parche el bucle `TST.W (An)+` / `DBRA` de 2000 pasadas tarda 2404 pulsos E del CIA frente a los 3600 de la 68000. Sin MMU: un acceso a espacio no mapeado devuelve datos, no un error de bus
(ADR 0010). Estado: bloque de 10 bytes en `P6CP` tras el cuerpo de la 68000 (VBR, SFC, DFC y `loop_mode`; `p6_cpu_boundary` ya no rechaza esos registros en una 68010), cota +10 bytes, DET02 con la 68010 en la A500 y
el A600 a mitad de un bucle en modo bucle, de trampas con VBR y MOVES y de un error de direccion (`tests/state/test_det02_cpu010.py`, 119 pruebas) y cada campo con su poke (`tests/state/test_state_cpu010.py`).

`config_hash`: la palabra `flags` esta llena (0-7 flags, 8-22 memoria, 23-24 FPU, 25-27 disquete, 28-30 raster, 31 marca de memoria), asi que la CPU se pliega como el audio (T73): fuera de la CPU de la
maquina el hash es `H("p6/config/cpu/v1" || hash_hasta_aqui || modelo, multiplicador)` con el modelo resuelto (0 = 68000, 1 = 68010, 2 = 68020) y el multiplicador del reloj (0 mientras es el de la maquina),
calculado despues del de audio; en la CPU de la maquina (tambien cuando la config la nombra) el hash es **el de siempre**. Un estado o una pelicula de una CPU es INCOMPATIBLE en otra, y con el hash forjado el
cuerpo de `P6CP` la rechaza por su longitud. Opciones: libretro `puae6_cpu_model` (auto, 68000, 68010, 68020), el importador `.uae` (`cpu_model`: la CPU de la maquina se describe, la 68010 es la opcion), headless
`--cpu auto|68000|68010|68020` y el sync setting `CpuModel` de BizHawk (en la identidad del estado y de la pelicula solo fuera de `Auto`: `;cpu=M68010`).


`p6_config.cpu_multiplier` (un byte, offset 64: el primero de lo que era `reserved1`; T56 toma el siguiente para `hdf_controller` y el resto queda como `reserved7[6]`): el `cpu_clock_multiplier` de upstream (N << 8) con los valores de PUAE5 (`puae_cpu_multiplier`):
**cero es el de la maquina** (2 en las de 68000, 4 en el A1200), y 1, 2, 4, 8, 10, 12 o 16; otro valor es `P6_ERROR_UNSUPPORTED`. Un ciclo de reloj de la CPU dura `CYCLE_UNIT / N` (`cpucycleunit`), de modo que
N = 2 son los 7,09 MHz de una 68000 y N = 4 los 14,19 MHz de la 68EC020 del A1200; 1 son 3,55 MHz y 16 56,75 MHz. Es **determinista**: solo cambia cuantos ciclos de color emulados cuesta cada acceso y cada
instruccion (el bus del chipset, el DMA y el refresco siguen donde estan), nunca se mide tiempo real ni hay "velocidad maxima" ligada al host. Tambien es una opcion de reinicio y entra en el mismo hash derivado.
Medido (`tests/host/test_cpu_clock.py`, 2000 pasadas de `TST.W (An)+` / `DBRA` desde ROM, en pulsos E del CIA-A, uno cada 5 relojes de color): la 68000 tarda 36004 relojes de CPU segun el manual,
es decir `36004 * (512 // N) / 2560` pulsos (el `cpucycleunit` de upstream es entero: 512 // N), que es 3600 con el reloj de la maquina y 7200 / N cuando N divide 512; medido 7205, 3603, 1802, 902, 719, 592 y 452
para N = 1, 2, 4, 8, 10, 12 y 16. El mismo bucle en modo bucle de la 68010: 4807, 2404, 1203, 602, 480, 396 y 302 (escala igual). La 68EC020 del A1200: 6102, 3252, 2002, 1002, 813, 743 y 552: nunca va mas lenta
con un reloj mas rapido, pero no escala por completo (la cache de instrucciones corre al reloj de la CPU y las lecturas de ROM esperan al bus). Los ticks de cada unidad y los marcos de audio entregados son los de la maquina por defecto salvo lo que mide la instruccion que cierra la unidad (<= 16 relojes de color, un marco de audio): no cambian con
el multiplicador (el chipset no se mueve). Opciones: libretro `puae6_cpu_model` (auto, 68000, 68010, 68020) y
`puae6_cpu_multiplier` (auto, 1, 2, 4, 8, 10, 12, 16), el importador `.uae` (`cpu_model`, `cpu_multiplier`), headless `--cpu auto|68000|68010|68020` y `--cpu-multiplier auto|1|2|4|8|10|12|16` y los sync
settings `CpuModel` y `CpuMultiplier` de BizHawk (en la identidad del estado y de la pelicula solo fuera del defecto).

### Opciones de audio de Paula (T73)

`p6_config.sound_separation`, `sound_interpolation`, `sound_filter` y `sound_filter_type` (un byte cada uno, offsets 56 a 59: son los 4 primeros bytes de lo que era `reserved[0]`; los otros 4 pasan
a `reserved8[4]` (offset 60) y `reserved[1]` a `reserved1` (offset 64), a cero; el struct sigue midiendo 72 bytes y nada se mueve). **Cero en los cuatro es el perfil** (separacion 70 %, interpolacion `anti`, filtro
emulado, tipo `auto` = modelo A500 en A500/A600 y modelo A1200 en A1200), asi que una fuente anterior a las opciones no cambia. Resto de valores: separacion `1 + porcentaje/10` (1 = 0 %, mono; 11 = 100 %),
interpolacion `1 + sound_interpol` (1 none, 2 anti, 4 rh, 5 crux; **3, sinc, es `P6_ERROR_UNSUPPORTED`**: su cola no esta en el estado nativo y el parche 0099 la rechaza), filtro `1 + sound_filter`
(1 off, 2 emulado, 3 siempre activo) y tipo 1 = A500, 2 = A1200. Fuera de rango, `P6_ERROR_UNSUPPORTED` con la razon.

**Clasificacion: son opciones de reinicio, no de presentacion.** El estado de los filtros (`sound_filter_state`, `led_filter_on`) y el integrador de la interpolacion (`sample_accum`, `current_sample`,
`last_sample`...) estan en el cuerpo AUDP del estado nativo, asi que filtro, tipo e interpolacion cambian el estado guardado; la separacion no esta en el estado pero cambia los `mixed_mul` del mezclador
y, con ellos, todas las muestras futuras: un estado cargado con otra separacion continuaria con otro PCM. Ninguna toca el hardware que ve el resto del motor (CPU, DMA, RAM, el filtro del LED como estado de
la maquina): solo el camino de mezcla de `audio.cpp` y su estado. Cambian el hash A/V; el hash de hardware de las series por defecto no cambia.

`config_hash`: con los valores del perfil de la maquina el hash es **el de siempre** (la palabra `flags` no tiene bits de audio). Si alguno difiere, `bridge/state.cpp` deriva
`H("p6/config/audio/v1" ‖ hash_de_siempre ‖ separacion, interpolacion, filtro, tipo)` con los cuatro valores **resueltos** en los numeros de upstream (separacion 0..10, `sound_interpol`, `sound_filter`, tipo 0/1), de modo que dos
escrituras de la misma maquina (0 y el valor del perfil; `auto` y el modelo de la maquina; cualquier tipo con el filtro apagado, donde el tipo no hace nada) dan el mismo hash. Un estado o una pelicula
con una combinacion es INCOMPATIBLE en otra; se recalcula con `tests/state/envelope.py` (`config_hash_audio`). Aritmetica: `rh`/`crux`/`none` y la mezcla son enteras; el filtro y el
`anti` usan los floats que ya usaba el perfil (`-ffp-contract=off`, ADR 0007); el cruce de tipo de filtro (A1200 en A500 o A500 en A1200) y el resto de combinaciones se comprobaron en la matriz de T25: 48 casos (A500 y A1200, dos ROMs, tipos cruzados, filtro off/on, tres interpolaciones, tres separaciones y una combinada) dan los mismos hashes de hardware y A/V
unidad a unidad en macOS arm64 (Apple Clang) y en Linux arm64 y x86_64 (Clang y GCC), O0; el cruce se soporta (T73).
Opciones: libretro `puae6_sound_stereo_separation` (0..100 en pasos de 10, 70 por defecto), `puae6_sound_interpolation` (none, anti, rh, crux), `puae6_sound_filter` (emulated, off, on) y
`puae6_sound_filter_type` (auto, A500, A1200), el importador `.uae` (`sound_stereo_separation` 0..10, `sound_interpol`, `sound_filter`, `sound_filter_type` standard/enhanced). Headless (T73a): `--sound-sep 0..100`, `--sound-interp none|anti|rh|crux`, `--sound-filter emulated|off|on`
y `--sound-type auto|a500|a1200` (y `--dump-audio DIR`, que escribe `audio-NNN.bin` por unidad como el anfitrion libretro); BizHawk: los sync settings `SoundSeparation` (`P0`..`P100`), `SoundInterpolation`
(`None`, `Anti`, `RH`, `Crux`), `SoundFilter` (`Emulated`, `Off`, `On`) y `SoundFilterType` (`Auto`, `A500`, `A1200`), con los defectos del perfil. En la identidad del estado y de la pelicula del
wrapper entran solo fuera del perfil (`;sound=separation=P0,filter=On...`, solo lo que difiere): un estado o una pelicula de una combinacion es `OtherConfig` en cualquier otra. El texto es mas
estricto que el hash nativo (que iguala `A500` con `auto` en un A500), nunca mas laxo; el guest de Waterbox no serializa la maquina (se instantanea entera), asi que el `config_hash` nativo solo
lo ven los estados de libretro y la sonda. El esquema los marca `named` en headless y `setting` en el wrapper (`wrapper_name` da el miembro del enum cuando el valor es un numero); `tests/waterbox-state/test_wrapper_audio.py`
compara el PCM de las tres superficies para cada valor y combinacion.

### Velocidad de disquete (T62)

`p6_config.floppy_speed` (un byte, offset 60: el quinto de lo que era `reserved[0]`; `reserved8` queda con 3 bytes desde el offset 61, a cero, y el struct sigue midiendo 72 bytes). Es el `floppy_speed` de
upstream, con los valores de PUAE5 (`puae_floppy_speed`: 100, 200, 400, 800 y turbo = 0): **cero es el perfil** (la unidad real, 100 %), 1 = 100 % nombrado, 2 = 200 %, 3 = 400 %, 4 = 800 %, 5 = turbo;
mayor de 5, `P6_ERROR_UNSUPPORTED` con la razon. Es una **opcion de reinicio**, no de presentacion: cambia cuando termina cada transferencia del DMA de disco (a 200/400/800 % el controlador lee la pista
mas deprisa y vacia el FIFO de golpe: `get_floppy_speed`, `doreaddma`; en turbo `DSKLEN_2` mueve la transferencia entera de una vez y espera dos lineas, `linecounter`, antes de la interrupcion DSKBLK),
y con ello el orden de todo lo demas. **Nunca depende del reloj del host**: el DMA cuenta ciclos de color emulados, no se duerme ni se mide tiempo real. Medido con `tests/state/test_det02_floppy.py`
(un programa que lee pistas de `$1900` palabras una tras otra desde la unidad 2; unidades de video hasta que termina la primera / lecturas terminadas en 40 unidades): 100 % 10/3, 200 % 5/7,
400 % 2/15, 800 % 1/30, turbo 0/6046.

`config_hash`: la velocidad entra en la palabra `flags` del hash en los bits 25 a 27 (0 = unidad real, tanto si la config dice 0 como 1; 1 = 200 %, 2 = 400 %, 3 = 800 %, 4 = turbo), sin chocar con la memoria
(bits 8-22 y 31), la FPU (23-24) ni `p6_config.flags` (0-7): con la unidad real el hash es **el de siempre**. Un estado o una pelicula de una velocidad es INCOMPATIBLE en otra. El estado nativo carga
una palabra mas por la velocidad turbo (parche 0280): el DSKX pasa de 164 a 168 bytes (41 a 42 palabras) y lleva `linecounter` (las dos lineas que espera una transferencia turbo; un estado tomado en ellas
no terminaba la transferencia al cargarse), y la validacion acepta el `-1` que una lectura turbo deja en la longitud del DMA (`0xffff` en DISK) solo si ese contador esta en marcha y el DMA apagado.
Opciones: libretro `puae6_floppy_speed` (100, 200, 400, 800, turbo; media), el importador `.uae` (`floppy_speed` 0/100/200/400/800), headless `--floppy-speed 100|200|400|800|turbo` y el sync setting
`FloppySpeed` de BizHawk (`P100`, `P200`, `P400`, `P800`, `Turbo`; en la identidad del estado y de la pelicula solo fuera de `P100`: `;floppyspeed=Turbo`). Los programas que miden el tiempo de sus
propias lecturas y las protecciones de copia pueden no funcionar por encima del 100 %; las pistas con temporizacion por palabra (ADF extendidos, IPF) no se ofrecen en estas maquinas.

### Resolucion y modo de linea (T72)

`p6_config.video_resolution` y `p6_config.video_line_mode` (un byte cada uno, offsets 61 y 62: el sexto y el septimo de lo que era `reserved[0]`; `reserved8` queda con 1 byte en el offset 63, a cero, y el struct
sigue midiendo 72 bytes). Son el `gfx_resolution` y el `gfx_vresolution` de upstream, con los valores de PUAE5 (`puae_video_resolution` lores / hires / superhires, `puae_video_vresolution` single / double):
**cero es el perfil** (hires y doble linea), `video_resolution` 1 = lores, 2 = hires nombrado, 3 = super-hires, `video_line_mode` 1 = simple, 2 = doble nombrado; mayor de 3 / 2, `P6_ERROR_UNSUPPORTED`. Son
**opciones de reinicio del motor** (ADR 0006, "Resolucion y modo de linea"): cambian lo que el dibujante pinta, el buffer anunciado y los pixeles historia del estado. `config_hash`: entran en los bits 28 a 30 de
la palabra de flags (28-29 la resolucion fuera de hires: 1 lores, 2 super-hires; 30 la linea simple), **solo fuera del perfil**, sin chocar con la memoria (8-22, 31), la FPU (23-24) ni la velocidad de disquete
(25-27): con el raster de siempre, escrito como sea, el hash es **el de siempre**. Un estado o una pelicula de un raster es INCOMPATIBLE en otro.

Rasters (activo nominal PAL / NTSC; buffer anunciado, constante desde el load): hires + doble (defecto) 756 x 574 / 756 x 484 en 760 x 576 (760 x 486 en NTSC con Agnus OCS); hires + simple 756 x 287 / 756 x 242 en 760 x 288
(760 x 243); lores 378 de ancho en 380 (doble 574 / 484 filas, simple 287 / 242); super-hires 1512 en 1520. Las maquinas con Agnus ECS o AGA (A500+, A600, A2000, A1200) reservan en NTSC la altura PAL (la mitad con linea simple). `video_width x
video_height` de cada `p6_frame` es el anuncio de `p6_query_video`; un raster que no cabe (un haz ECS programado mas grande que el buffer de la opcion) se entrega como `P6_FRAME_NO_VIDEO`, nunca se escribe fuera, y
todo lo que hay fuera de `active` vale cero como siempre. El aspecto de pixel es el del estandar por el factor del raster (lores x2 de ancho, super-hires la mitad, linea simple x2 de alto: 52:25 en PAL lores doble,
26:25 lores simple, 13:50 super-hires simple). Con **linea simple** un invitado entrelazado se ve campo a campo (cada unidad es un campo completo de 287 / 242 filas) y `field_parity` es siempre
`P6_FIELD_PROGRESSIVE`; con linea doble los campos se tejen como siempre. La autoresolucion de PUAE5 (`auto`) no se ofrece (necesita reconfigurar el motor en marcha).

Opciones: libretro `puae6_video_resolution` (lores, hires, superhires; video) y `puae6_video_line_mode` (single, double; video), el importador `.uae` (`gfx_resolution`, `gfx_linemode` none / double), headless
`--resolution lores|hires|superhires` y `--line-mode single|double`, y los sync settings `VideoResolution` (`Lores`, `Hires`, `SuperHires`) y `VideoLineMode` (`Single`, `Double`) de BizHawk (en la identidad
del estado y de la pelicula solo fuera del perfil: `;resolution=Lores;linemode=Single`). En libretro el recorte y la posicion (T31c) se miden en pixeles lores y lineas, de modo que un nivel es la misma zona
de la imagen en cualquier raster, y el aspecto forzado PAL / NTSC lleva tambien el factor del raster. AV01: `tests/step/test_resolution.py` (cada raster en PAL y NTSC con programas de Copper y plano de bits,
entrelazado, haces ECS y AGA; baselines propios en `tests/step/resolution-baselines.json`, y el raster del perfil contra los dorados de siempre) y `tests/libretro-host/test_resolution.py`.

Opciones (todas de reinicio, del esquema `config/schema/options.json`): libretro `puae6_chip_size` (Auto, KB512, MB1, MB2), `puae6_slow_size` (Auto, None, KB512, MB1, MB1_5, MB1_75) y
`puae6_fast_size` (None, MB1, MB2, MB4, MB8), headless `--chip KIB`, `--slow KIB|none` y `--fast KIB`, y los sync settings de BizHawk `ChipMemory`, `SlowMemory` y `FastMemory` (el heap mmap
de Waterbox crece con la memoria pedida por encima de la propia de la maquina, ver `manifests/waterbox-memory.json`). Un tamano que la maquina no puede tener hace fallar la carga con la razon
del puente; uno que el esquema no tiene, nombrando la clave y el valor. Cambiar una de ellas con la maquina en marcha solo se avisa y se aplica al volver a cargar (la cota de estado no se mueve).

Presets publicados junto a los de serie (`config/schema/options.json`, `presets`): `a1200_fast8` (el A1200 de PUAE5: A1200 con 8 MiB de Fast) y `a600_puae5` (A600 con 2 MiB de Chip y
8 MiB de Fast; el A600 de PUAE5 lleva ademas Gayle y el Kickstart 3.1 de su modelo, que este no: corre el Kickstart que se le da, y el 2.05 es el probado). Son combinaciones de
opciones, no un campo nuevo de la ABI: libretro los ofrece como `puae6_preset` (none, a1200_fast8, a600_puae5), que se aplica sobre el modelo y la memoria elegidos.

### Modelo automatico y etiquetas del nombre (T76)

Solo el core libretro. `puae6_machine=auto` (el defecto) y `puae6_video_standard=PAL auto` / `NTSC auto` son valores que **solo ofrece libretro** (`libretro.extra_values` del esquema): no existen en `p6_config`,
en headless ni en BizHawk, que eligen la maquina de forma explicita. `retro_load_game` los resuelve una sola vez, despues de leer los discos y antes de `p6_init`, con la funcion pura `resolve_model`
(`adapters/libretro/model.h`) de las opciones, de si el contenido tiene disco duro y de la lista de nombres (ruta del contenido, discos de la lista en orden, miembros de un archivo tras el archivo); no lee
ficheros, reloj ni entorno, de modo que el mismo contenido elige siempre la misma maquina. Lo resuelto es el `p6_config` normal (`machine`, `video_standard` y, en dos etiquetas, `chip_kib`/`fast_kib`), asi que
**entra en `config_hash` y en la identidad del estado**: un estado de un contenido cuya etiqueta eligio otra maquina u otro estandar lo rechaza `p6_state_load` como el de cualquier otra maquina. Un modelo,
preset o estandar elegido a mano nunca lo cambia una etiqueta. La tabla de etiquetas, la precedencia y las opciones `puae6_model_fd`, `puae6_model_hd` y `puae6_filename_tags` estan en `docs/options.md`
("Modelo automatico y etiquetas del nombre"). Una etiqueta de algo que no existe (68030/68040, CDTV, MultiDrive, Arcadia) se ignora con un aviso y no elige otra maquina. BizHawk no cambia en T76: su
`Machine` es un sync setting y la deteccion por nombre no es trivial alli (queda fuera de esta tarea).

### Kickstart alternativo y rom.key (T51)

**Elegir el Kickstart.** La opcion `puae6_kickstart` (solo libretro, un selector del esquema sin campo en `p6_config`) elige una version en lugar de la propia de la maquina: sus valores son los de
`puae_kickstart` de PUAE5 (el nombre "normal" de cada ROM: 1.2, 1.3, 2.04, 2.05, 3.1 r40.063, 3.0 y 3.1 r40.068; no los A4000 ni `aros`) y los nombres que busca para cada version son los de
`uae_kickstarts[]` de PUAE5 (normal, Amiga Forever, TOSEC `[!]`, TOSEC; `tests/libretro-host/test_firmware.py` los lee del codigo de PUAE5 y exige que el adaptador los tenga todos). `auto` busca los
nombres del modelo como antes. Una version elegida busca **solo** sus nombres (un directorio con el 1.3 no arranca una maquina a la que se pidio el 2.05, y `auto` nunca busca el 2.05 en el A500) y la
decision de si la maquina puede ejecutarla es del puente, por los bytes del ROM y nunca por el nombre. Los bytes que arrancan son el firmware y su SHA-256 su identidad (como siempre): un estado o
una pelicula tomados con otro Kickstart los rechaza la carga (`p6_state_load`; la cabecera `PUAE6Firmware` de las peliculas de BizHawk), con los mismos bytes bajo otro nombre u otra opcion los acepta.

**Un ROM de otra CPU.** El parche 0290 guarda, cuando el ROM es uno que el motor conoce (su tabla de `rommgr.cpp`, por contenido) y pide mas CPU que el preset, el nombre que el motor le da y lo que pide;
`p6_load` responde `WRONG_MEDIA` con el motivo: `the ROM "KS ROM v3.1 (A1200)" needs a 68EC020 or later but the A500 has a 68000: it is the firmware of another machine`. La comprobacion es la del parche
0107 (68030 o mas, 68030, chipset del A4000, 68EC020, 68020 con bus de 32 bits). Un ROM que el motor no conoce (un micro-ROM, un dump desconocido) pasa por su cabecera y su suma de comprobacion.

**ROMs cifradas de Amiga Forever (`rom.key`).** Un fichero que empieza por los 11 bytes `AMIROMTYPE1` es el ROM con cada byte XOR de los bytes del fichero de clave `rom.key` repetidos desde su primer
byte (el fichero entero, de cualquier longitud: el de Amiga Forever pesa unos 1,5 KiB; el adaptador lee hasta 64 KiB); es el formato de `decode_cloanto_rom_do` de WinUAE (`rommgr.cpp`), y se acepta la
clave si lo descifrado empieza como un Kickstart (`$4EF9` en el desplazamiento 2, o `$1111` / `$1114` al principio), la misma prueba. El adaptador libretro (`adapters/libretro/romkey.h`, funciones puras)
lee `rom.key` (o `ROM.KEY`) del directorio de sistema, descifra y da a `p6_load` el ROM **descifrado**, cuyo SHA-256 es la identidad: el mismo ROM descifrado, o el cifrado con su clave, son un firmware y un
estado se mueve entre ambos. La clave se usa y se descarta: ni sus bytes ni su longitud ni su hash llegan al puente, a un estado o a un log. Sin clave, con una clave vacia o mayor de 64 KiB, o con una que no
descifra, el nombre se salta con un aviso que lo dice y se prueba el siguiente (no esconde un ROM bueno); si no queda ninguno el fallo nombra el motivo. Un ROM cifrado que llega tal cual a `p6_load` (headless,
otro adaptador) lo rechaza con `firmware is an encrypted Amiga Forever ROM (AMIROMTYPE1): decrypt it with its rom.key before p6_load`. BizHawk: el nucleo recibe la clave por `Puae6Parameters.RomKey`
(`integration/bizhawk/core/Puae6/RomKey.cs`, el mismo algoritmo y la misma prueba, comprobado en `tests/ffi/test_core.py` contra un cifrado escrito aparte); el gestor de firmware de EmuHawk no tiene
entrada para una clave (su base de datos nombra los Kickstart sin cifrar por hash), de modo que desde EmuHawk el firmware se elige entre los Kickstart sin cifrar de la familia (`Amiga/A500`,
`Amiga/A600`, `Amiga/A1200`: el 1.2, el 2.04 y el 3.1 r40.063 son opciones de esas entradas) y un ROM cifrado sin clave hace fallar la creacion del nucleo con el motivo. No hay ROM cifrado real de Amiga
Forever del usuario: las pruebas usan un ROM sintetico cifrado con una clave sintetica; **el descifrado con material real de Amiga Forever NO se ha ejecutado**.

**AROS.** No se integra. El ROM `aros.rom.cpp` de WinUAE (un gzip de 561 072 bytes que PUAE5 usa de reserva) se descarga aparte (el README de WinUAE manda bajar `aros.rom.cpp.zip`), no trae ni licencia ni
receta de construccion ni version de AROS en el arbol fijado, y su licencia (AROS Public License, derivada de la MPL 1.1, con partes de terceros) depende de cual sea ese binario: sin eso no esta claro que se
pueda redistribuir con el nucleo (decision de T06b2ba: retirado del perfil). Tampoco habria identidad estable sin fijar su version, ni compatibilidad medida; la opcion `aros` de PUAE5 no se ofrece.

**Matriz medida** (propietario, opt-in con `P6_KICKSTART_ROM` 1.3, `P6_KICKSTART_A600_ZIP` 2.05 y `P6_KICKSTART_A1200_ROM` 3.1; `tests/libretro-host/test_firmware.py`, `OwnersKickstartsTests`): con `puae6_kickstart`
llegan a la pantalla de insertar disco, tras 1 200 unidades y sin cambiar nada mas, el 2.05 en el A500, el A500 OG, el A500+, el A1000, el A2000 y el A1200, y el 1.3 en el A600, el A500+, el A2000 y el A1200; el
3.1 r40.68 del A1200 se rechaza con el motivo anterior en el A500, A600, A500+, A500 OG, A1000, A2000 OG y A2000 (68000). Eso comprueba que la maquina corre, no compatibilidad con juegos. NO probado: el 1.2, el 2.04, el 3.0
y el 3.1 r40.063 (no hay copia autorizada), cualquier ROM cifrado real de Amiga Forever y un Kickstart elegido desde EmuHawk.


### Gayle con IDE (T56)

Decision (2026-10-13, la del orquestador; el usuario puede cambiarla): el A600 y el A1200 de serie NO cambian: Gayle sigue apagado (`cs_ide = cs_pcmcia = cs_compatible = 0`), con los mismos hashes, `config_hash` y estados.
El IDE de Gayle es una opcion de reinicio, `p6_config.hdf_controller` (un byte, offset 65, en lo que era `reserved7`): `P6_HDF_CONTROLLER_UAE` (0, por defecto: `uaehf.device` y la placa UAEFS, como PUAE5) o
`P6_HDF_CONTROLLER_IDE` (1). Solo el A600 y el A1200 tienen Gayle: IDE en otra maquina, o un valor mayor que 1, es `P6_ERROR_UNSUPPORTED` con el motivo en `p6_init`. **Gayle se enciende solo si hay un HDF detras**: una
config con IDE y sin HDF en `p6_load` es la maquina por defecto (mismo preset, mismos bytes, mismo `config_hash`, mismos estados y peliculas), nunca un Gayle vacio (`bridge/config.cpp ide_attached`). Con IDE y al menos un HDF,
`config_hash` es el de siempre derivado con un byte (dominio `p6/config/ide/v1`), igual que la CPU y el audio: un estado del IDE no carga en la maquina del controlador UAE (ni al reves) y los dos son INCOMPATIBLE por `config_hash`.

Motor (parches 0320-0322): `gayle.cpp` e `ide.cpp` se compilan SOLO para el puerto IDE de Gayle (los registros de la interrupcion IDE, el ID de Gayle y los discos duros): quedan fuera la ranura PCMCIA y todas sus tarjetas
(PCMCIA NO es necesaria para el IDE de Gayle: `cs_pcmcia` sigue en 0, `$DA8000` y la ventana de $A00000 leen lo que leian), Fat Gary/Ramsey, el SCSI del A4000T, el Dataflyer, los dispositivos ATAPI (CD-ROM, cinta), los `.ini`
de identidad y el hilo del IDE: cada orden de lectura o escritura se sirve al escribirla (`process_rw_command`), y la interrupcion llega por el `irq_delay` del disco, en lineas de tiempo emulado, nunca por una decision de
planificacion. Los HDF son las unidades 0 a 3 del IDE (maestro y esclavo del canal de Gayle en `$DA2000`, el par detras del IDE splitter en `$DA3000`), en el orden de la lista de `p6_load` y hasta `P6_MAX_HDF` = 4, sobre el
mismo backend de bloques de T29 (lectura de la base inmutable, overlay de trozos de 4 KiB, `p6_media_export`): el mismo `content_id`, el mismo presupuesto, la misma exportacion, los mismos limites. Una imagen sin RDB lleva el
RDB virtual del motor (como en el controlador UAE) y la geometria que el disco anuncia cubre TODO el disco, RDB incluido (el `hdf_hd_open` de upstream dimensiona los cilindros por la particion sola y el Kickstart 2.05, que
direcciona por CHS, daba "read error on disk block 1163"). Con IDE no hay lista de montaje, ni placa UAEFS, ni zona de traps, ni cola EXTER (no existen): el Kickstart minimo es el que lleve el scsi.device de la maquina (el
1.3 no lo trae; 2.05 y 3.1 si). Cada reset reabre los discos (`gayle_reset`), como `filesys_reset` con sus unidades.

Estado (`bridge/state_hdf.cpp visit_ide`, la seccion de P6DK que solo existe con un HDF; un estado sin HDF no cambia ni de bytes): despues de la identidad (numero de HDF y content id de cada base) lleva 4 palabras de Gayle (estado y
habilitacion de la interrupcion IDE, el contador del ID y el splitter), las cuatro ranuras IDE con 35 palabras cada una (registros, estado de la transferencia en curso, geometria que fijo el invitado) y los bytes del buffer de sector que
la transferencia tiene en la mano (hasta 131584 por ranura; `p6_ide_live_bytes`; el buffer nace en ceros y cada transferencia lo pone a cero antes de empezar, de modo que un invitado que lee el puerto de datos mientras se escribe, o mas alla de lo que una lectura cargo, ve ceros y no lo que dejo el monton o una transferencia anterior: lo que el invitado puede leer es funcion de sus ordenes), la cadena autoconfig (con una tarjeta mas, el propio puerto IDE, que no es autoconfig) si no hay Fast RAM, y el overlay de cada HDF como hasta ahora. Las
funciones `p6_gayle_words_valid` y `p6_ide_words_valid` son puras: el puente las pregunta al validar, antes de que el reset head haya corrido, para que aplicar un estado validado no pueda fallar; un cuerpo fuera de lo que
Gayle o un disco pueden tener (un byte mayor que 255, una transferencia mas larga que el buffer, una transferencia en una ranura sin disco, un buffer de otra longitud, los dos discos de un canal en lineas de seleccion
distintas) es `CORRUPT_STATE` y no toca nada. La cota `p6_state_bound` sigue la misma regla que con el controlador UAE (todos los trozos de cada HDF como sucios, mas 4 buffers de sector).

Superficies: headless `--hdf-controller uae|ide`, el probe de estado, la opcion `hdf_controller` del esquema (`config/schema/options.json`) y con ella `puae6_hdf_controller` de libretro (reboot; la clave nativa tambien vale en un `.uae`),
la sync setting `HdfController` de BizHawk (`Puae6HdfController`, campo `HdfController` de `P6Config`; la identidad del estado y de la pelicula la nombra solo fuera de `Uae`: `;hdfcontroller=Ide`) y el guest Waterbox, que cambia con los
parches 0320-0322 (`manifests/waterbox-memory.json` regrabado, ver docs/03-waterbox.md). Un estado libretro o BizHawk del IDE no carga en el controlador UAE ni al reves (`config_hash`). El `.uae` sigue sin montar discos (`hardfile2=...,ide0` se rechaza como
cualquier disco del fichero: los discos son medios que el core recibe).
Pruebas: `tests/hdf/test_ide.py` (MED02/MED03: un micro-ROM habla con los registros de Gayle: ID, IDENTIFY, lecturas, escrituras a traves del limite de un trozo del overlay, lectura fuera del disco, el maestro, el esclavo y el
segundo canal; la exportacion es la base con el overlay y la base no cambia; con IDE y sin HDF la maquina es la de siempre), `tests/state/test_det02_ide.py` (DET02 con la controladora ocupada, en A600 y A1200; en un limite BSY con la
interrupcion a una linea, en otro con la interrupcion pendiente; y, opt-in, los Kickstart 2.05 y 3.1 reales con el Workbench 2.05 como HDF, con un volumen sin cerrar que el sistema de ficheros valida escribiendo por IDE),
`tests/state/test_state_ide.py` (STATE01 por tamaño, identidad del controlador y de la base, cuerpos hostiles, DET03 y re-guardado byte a byte) y `tests/step/test_kickstart_ide.py` (opt-in con `P6_KICKSTART_A600_ZIP`,
`P6_KICKSTART_A1200_ROM` y `P6_WB205_ZIP`: el Workbench 2.05, ADF leido en memoria como HDF por IDE, llega pixel por pixel a la pantalla a la que llega por el controlador UAE en ambos Kickstart). Sin los ficheros del usuario
esas pruebas quedan BLOCKED; no se copia ni se calcula el hash de ninguno.

## ROMs, discos y persistencia

Firmware y cada medio tienen content ID (SHA-256 de contenido normalizado segun
formato), nombre logico y licencia/procedencia para fixtures. Los paths del SO no
son identidad ni parte del estado. Medios ausentes o distintos provocan error.
El motor no enumera home, no descarga BIOS y no carga plugins de forma implicita.

Implementado (T12a, docs/adr/0008-media-identity.md): `p6_load` acepta hasta 16 ADF de 901120 bytes (y, desde T57a, ADF extendidos: ver mas abajo) cuyo `content_id` es el
SHA-256 de sus bytes; cualquier otra lista se rechaza antes de tocar el motor (tamaño o formato: WRONG_MEDIA; tipo: UNSUPPORTED;
duplicado o campos reservados: BAD_ARGUMENT; mas de 16: CAPACITY). Cada imagen se registra como `media-<hex>` y el perfil vacia
los nombres `dfN.adf` de upstream y protege contra escritura todos los discos.

Eventos (T12b): `p6_media_event_enqueue` encola hasta 16 MOUNT/EJECT sobre DF0 y DF1 con secuencia estrictamente creciente en toda la
sesion; se validan al encolar y se aplican en el siguiente borde, tras la cabecera del reset y antes de la entrada. Los encolados antes del
primer `p6_run_frame` fijan los discos del arranque; los posteriores usan el cambio retardado del motor (unas 100 unidades) y un EJECT
cancela uno pendiente. Detalle y errores: docs/adr/0008-media-identity.md.

Manifiesto append-only (T17e, `P6_CAP_MEDIA_APPEND`): `p6_media_append` registra un ADF despues de `p6_load`, siempre al final, con la misma validacion
que `p6_load` (tipo, flags, 901120 bytes, content id = SHA-256) antes de tocar nada; un content id ya registrado devuelve su indice; con `P6_MAX_MEDIA`
medios un disco nuevo es CAPACITY, y durante `p6_run_frame` es BUSY. Registrar no cambia la maquina, las unidades ni la cola de eventos, y los medios no se
quitan durante la sesion. Un estado guarda el hash del prefijo del manifiesto que tenia (ADR 0009): carga si el manifiesto vivo empieza por ese prefijo, de modo
que los estados anteriores a una adicion siguen siendo validos. El wrapper BizHawk no usa esta funcion: sus medios son fijos por instancia y sus peliculas
exigen el MediaHash exacto.

ADF extendido (T57a, `P6_CAP_MEDIA_EXT_ADF`, bit 7): **no es un tipo nuevo**; `P6_MEDIA_ADF` es "una imagen de disquete" y su formato lo dicen los bytes (`ports/portable/media/trackdisk.h`): el ADF plano
de 901120 bytes, o el ADF extendido de WinUAE, `UAE-1ADF` (version 2: cabecera de 12 bytes, una fila de 12 bytes por pista con tipo 0 = sectores AmigaDOS o 1 = MFM crudo, longitud en bytes y longitud en bits)
y `UAE--ADF` (version 1: 160 pistas de sincronizacion y longitud), que mezclan pistas crudas, de la longitud que sea, con pistas AmigaDOS. Se escogio un formato dentro del tipo y no un tipo nuevo porque el contrato de
`p6_media` ya es "bytes + SHA-256 de esos bytes" y ninguna otra cosa del puente (eventos, manifiesto, estados, `p6_media_append`, los adaptadores) distingue un disquete de otro; el tipo nuevo habria obligado a
repetir cada comprobacion de `media_type == ADF` sin ganar nada, y un llamante antiguo recibe `WRONG_MEDIA` con el motivo. La identidad es el SHA-256 de los bytes tal como se dieron (un ADF extendido y uno plano
del mismo disco son dos medios, y los mismos bytes en un ZIP o sueltos son uno), el tope es 3 MiB por imagen y 16 discos planos (14 417 920 bytes) en TOTAL entre todos los disquetes del manifiesto (`CAPACITY` al
pasarse, en `p6_load` y en `p6_media_append`): el heap sellado de Waterbox se dimensiono para 16 ADF planos y su presupuesto no cambia. El validador (`classify_floppy`, antes de tocar el motor) comprueba contra los
bytes todo numero que el lector del motor toma de la cabecera: 1 a 166 pistas (la tabla `MAX_TRACKS` de una unidad), campos reservados a cero, tipo 0 o 1, cada pista dentro del fichero y de a lo mas 20000 bytes (mas
seria alta densidad, que este core no tiene), las pistas AmigaDOS de al menos once sectores y las crudas con al menos 16 bits y no mas bits que bytes tiene su espacio; el motor, en cambio, no se toca (`disk.cpp` ya lee
ambos formatos en `drive_insert` y `drive_fill_bigbuf`): `has_special_header` ya no los rechaza, solo el ejecutable Amiga. **Solo lectura**: el overlay de T27 son sectores AmigaDOS y pistas crudas de la geometria del ADF
plano de 880 KiB, que estos formatos no tienen (pistas de longitud propia, sin sectores fijos); con `P6_CONFIG_FLOPPY_WRITES` estos discos siguen protegidos (/WPRO, `media_forced_write_protect`), no consumen el pool,
`p6_media_write_status_get` los da sin sectores ni pistas crudas y `p6_media_export` responde `UNSUPPORTED` (el llamante tiene el fichero: no hay nada que exportar); escribir un ADF extendido seria otra tarea (el overlay
de pistas crudas de T27a2 ya guarda una vuelta por pista y podria cubrirlo). **Estado de pista**: el buffer MFM de la pista que esta bajo la cabeza (`bigmfmbuf`, su longitud en bits, el desplazamiento de salto y de
indice) viaja en DSDn mientras el motor gira, la posicion en el MFM en DISK, y el parche 0330 anade a DSKX, por unidad, `prevtracklen` (la longitud a la que se escalo por ultima vez la posicion de la cabeza, que
`updatemfmpos` usa al pasar a una pista de otra longitud: es 0 justo tras insertar el disco y `restore_disk` la copiaba de la pista de la maquina VIVA, no de la del estado; una cabeza que recorre pistas de seis longitudes
diverge sin ella, `tests/state/test_det02_trackdisk.py`); el parche 0331 pone a cero los bits de la ultima palabra de una pista cruda que pasan de su longitud (el buffer va en el estado, asi que sus bytes han de ser
funcion de la imagen). El DSKX pasa de 168 a 184 bytes (42 a 46 palabras). La lectura de una pista cruda es la del motor: empieza donde esta la cabeza (un punto aleatorio del giro, por el generador sembrado), una pista mas
corta que la lectura pasa por el indice sin el salto de bits de las pistas AmigaDOS (`skipoffset` es -1), y la longitud de la pista (no el reloj) fija lo que tarda una vuelta. Con `floppy_speed` distinto del 100 % se lee
(el DMA cuenta en ciclos de color), pero las protecciones de copia que miden el tiempo no se prometen. Fixtures de las pruebas: `tests/media/trackdisk.py` los escribe desde la descripcion del formato (MFM valido con una
sincronizacion unica); las pruebas son MED01 (`tests/media/test_trackdisk.py`, `test_trackdisk_formats.py`, `media_probe.cpp`), DET02 (`tests/state/test_det02_trackdisk.py`) y FUZZ01 (`trackdisk` en `tests/fuzz`).

FDI (T57b, `P6_CAP_MEDIA_FDI`, bit 8): otro formato de `P6_MEDIA_ADF`, por los bytes ("Formatted Disk Image file"), con la misma identidad (SHA-256 del fichero), los mismos topes (3 MiB, 16 discos planos en total) y
el mismo caracter de solo lectura que el ADF extendido. Lo lee el `fdi2raw.cpp` de WinUAE, compilado en el motor (`FDI2RAW` en `sysconfig.h`; se usa solo en memoria: el `zfile` del blob) con tres parches: 0332
(acotado), 0333 (determinista) y 0334 (la pista en el estado). **Pistas que se aceptan**: 0x00 vacia (una pista sin formatear: ceros), 0x01 Amiga de doble densidad (once sectores de datos; el lector construye el MFM
con sus cabeceras, sumas y hueco, empezando en el sector que diga la pista) y 0x80..0xBF de bajo nivel (las transiciones de flujo de una vuelta: `pulses`, cuatro descriptores {medio, minimo, maximo, indice} y los
cuatro flujos, en forma plana o Huffman; los pulsos de estabilidad menor que la maxima son bits debiles). Las demas (las formas PC y Atari, las descritas por sectores y las crudas 0xF0) las rechaza
`classify_floppy` con el motivo, porque `fdi2raw` las lee sin mirar donde acaba el dato. El validador comprueba la cabecera (version 1 o 2; desde la 2.1, el CRC-32 de los primeros 508 bytes), 166 pistas como mucho,
que cada pista este dentro del fichero, que una pista Amiga sea de once sectores y que la estructura de una de bajo nivel (pulsos <= 262144, flujos dentro de la pista, minimo y maximo juntos, los planos con sus
`pulses` palabras alineadas) quepa; el contenido de un flujo Huffman lo comprueba el decodificador al leerlo. **Determinismo**: `fdi2raw` sortea los bits debiles y el temblor de los pulsos estables con el generador
del motor (`uaerand`), de modo que cada decodificacion cambiaba el futuro de la maquina y el buffer que un estado decodifica de nuevo no era el que tenia el original: el parche 0333 da al decodificador un generador
propio (xorshift32) sembrado por (pista, revolucion), y `fdi2raw_set_revolution` recibe la revolucion que cuenta `disk.cpp`. **Pista y estado** (parches 0334 y 0335): la cabeza que llega a otra pista empieza en la
revolucion 0 y cada indice que cruza una pista con bits debiles pide la siguiente (`fetchnextrevolution`, `multi_revolution`, `revolution_check`); el estado lleva por unidad `multi_revolution`, `revolution_check`,
`fdirev` (la revolucion de la pista) y `fditrack` (la pista a la que pertenece), de modo que una maquina que carga el estado, con el motor encendido (DSDn trae el buffer) o apagado (la pista se decodifica otra vez,
como la misma revolucion de la misma pista, bit a bit), sigue como el original; el DSKX pasa de 184 a 248 bytes (46 a 62 palabras). La temporizacion de una pista FDI es un valor por BYTE de la pista (`DISK_update` la
lee como `tracktiming[mfmpos / 8]`) y `save_disk2` de upstream guardaba uno por palabra de 16 bits: la mitad de la temporizacion no viajaba y una maquina que cargaba el estado leia la segunda mitad de la pista a la
velocidad de otra vuelta (hallazgo de una pista FDI con pulsos debiles en toda su vuelta, `tests/state/test_det02_trackdisk.py`); el parche 0335 guarda las palabras y despues `(longitud + 7) / 8` valores, y la cota de
DSDn pasa de 4 a 6 bytes por palabra de la pista. **Memoria**: el buffer de entrada del lector es el de la pista mas larga del fichero mas 4 KiB de ceros (no los 4 MiB del formato), la cache guarda una pista de
bajo nivel (un megabyte como mucho) y el buffer de salida son 40 000 bytes: no hay nada mas que reservar en el heap sellado. Fixtures: `tests/media/fdi.py` escribe FDI desde el formato (pistas Amiga, de bajo nivel
con pulsos exactos y con bits debiles); las pruebas son MED01 (`tests/media/test_fdi.py`), DET02 (`tests/state/test_det02_trackdisk.py`, con el motor encendido y apagado) y FUZZ01 (`trackdisk`). Un FDI real y libre
se prueba por variable de entorno (opt-in, ver `tests/media/test_fdi_real.py`).

IPF (T58, `P6_CAP_MEDIA_IPF`, bit 9): otro formato de `P6_MEDIA_ADF`, por los bytes (el registro `CAPS` de 12 bytes con el que empieza todo IPF), con la misma identidad, los mismos topes y el mismo caracter de solo lectura. El
decodificador es el lector libre de MAME (`third_party/mame-ipf`, BSD-3-Clause; docs/adr/0013-ipf-decoder-licence.md), no capsimg: `ports/portable/media/ipf_track.h` lo ofrece como un indice de pistas (el fichero se lee por
offsets, nunca se copia ni se escribe) y una funcion que decodifica UNA revolucion de una pista a palabras MFM de 16 celdas, una temporizacion por cada 8 celdas (milesimas de celda nominal de 2000 ns: la unidad de `tracktiming`) y
la marca de celdas debiles. **Validacion**: `classify_floppy` solo mira el registro `CAPS` y el tamano; `validate_media` abre el IPF con el decodificador (CRC-32 de cada etiqueta y de los datos de cada pista, INFO antes de IMGE y
DATA, una descripcion y unos datos por clave, cilindros 0 a 82 y cabezas 0 y 1, cada pista una vez, a lo mas 160000 celdas y 256 KiB de datos por pista) y decodifica todas las pistas una vez: lo que el motor no podria leer es
`WRONG_MEDIA` con el motivo. T58c permite claves de indice 0..168 (tambien numeradas desde 1) y las dos descripciones del cilindro 83 solo como tipo 1 vacio, sin celdas, bloques ni datos; esas descripciones no se ofrecen al motor, que conserva sus 166 pistas. **Motor** (parche 0340): `drive_insert` abre el indice del medio del almacen, `drive_fill_bigbuf` decodifica la pista bajo la cabeza como la revolucion que cuenta `fdirev` para la pista `fditrack` (los
mismos campos de DSKX que el FDI: el estado no crece) y `fetchnextrevolution` pide la siguiente en cada indice de una pista con celdas debiles (`multi_revolution`). Las celdas debiles y las de una pista de ruido toman sus bits de un
generador propio sembrado por (pista, revolucion), nunca de `uaerand`; el lector no guarda nada entre dos lecturas, asi que no hay estado suyo que guardar: el buffer y su temporizacion van en DSDn, el indice se rehace al insertar el
medio del estado. La temporizacion es la del formato en valor absoluto (una celda de 2000 ns es 1000), de modo que una vuelta dura lo que sumen sus celdas; el empalme de escritura de la pista es `skipoffset` y la unidad salta alli de 1
a 3 celdas (`floppy_random_bits` de WinUAE, del generador sembrado del motor), de modo que una lectura que pasa por el empalme no lee un par de celdas de la pista y WORDSYNC realinea las palabras en la siguiente sincronizacion.
Pruebas: MED01 (`tests/media/test_ipf.py` el decodificador contra un oraculo que codifica el MFM por su cuenta, `test_ipf_provenance.py`, `test_ipf_engine.py` la guest leyendo), DET02 (`tests/state/test_det02_ipf.py`) y FUZZ01
(`tests/fuzz/test_ipf.py` el decodificador sin motor, `TrackDiskFuzzTests` el motor con imagenes IPF mutadas). Un IPF real se prueba por variable de entorno (`tests/media/test_ipf_real.py`, `P6_IPF_FILE`); las puertas de arranque/restauracion de libretro y del core BizHawk son `tests/libretro-host/test_ipf_boot.py` y `tests/waterbox-media/test_ipf_boot.py`, con `P6_KICKSTART_ROM` (1.3) y `P6_IPF_FILE` (IPF o ZIP de un IPF). Sin assets esas puertas quedan BLOCKED. Ambos frontends ejecutan tambien un boot block libre con marcador en Chip RAM; la restauracion compara imagen/audio/ticks y continua en un proceso nuevo. Los IPF comerciales no se descargan por los tests ni se incorporan al repositorio.

ADF MVP: buffer inmutable de solo lectura, cargado antes de iniciar/sellar. Mount,
eject y swap operan sobre IDs precargados. Inventariar los metadatos de floppy
que tambien afectan al futuro: posicion, track buffers, cambio, motor, bits/MFM,
revolucion, proteccion y escritura diferida; no basta con bytes del archivo.

Escritura M3: base inmutable + copy-on-write dentro del estado. La granularidad
(sector, pista o bloque) debe reflejar el formato real; preservar escrituras de
pista/MFM cuando sea necesario, no forzar todo a sectores ADF validos. Limite de
overlay reservado al cargar, incluido en bound de estado. Overflow es error claro.

Implementado (T27a, `P6_CAP_MEDIA_WRITE`, parche 0120): con `p6_config.flags = P6_CONFIG_FLOPPY_WRITES` todos los disquetes de la sesion son escribibles sobre un
overlay de granularidad SECTOR (512 bytes, el mismo en que el motor cambia un ADF: decodifica la pista MFM que escribio el invitado y escribe sus sectores por zfile). La
base (el ADF registrado, su `content_id`, el CRC que reporta el drive y el `media_manifest_hash` de un estado) NO cambia nunca: la identidad de un medio es su base, y el
overlay es estado de maquina como la RAM. Un `p6_load` reserva un pool unico y fijo de `P6_OVERLAY_SECTORS` = 3520 sectores (dos discos) compartido por todos los medios
(si el flag esta puesto; sin el, todo disco esta protegido como en ABI v1 y el estado no cambia ni de tamaño ni de bytes). El pool va en el chunk P6DK despues de los cuerpos
del motor (u32 cuenta, u64 escrituras rechazadas, por sector en orden de primera escritura: u8 medio, u16 sector, 512 bytes; un sector una sola vez, medio dentro del
manifiesto del estado, validado antes de aplicar), seguido de las pistas crudas (abajo), y `p6_state_bound` incluye los dos pools enteros (`12 + 3520 * 515` mas `4 + 16 * (6 +
12784)` bytes, 2 017 456 en total, mas que sin el flag), de modo que no crece con las escrituras. Cargar un estado SUSTITUYE el overlay: uno tomado antes de escribir devuelve el disco como estaba (rewind), uno posterior trae las escrituras (como la RAM). El
flag forma parte de `config_hash`: un estado de una sesion con escrituras no carga en una sin ellas ni al reves. Un sector cuyo contenido nuevo es igual al de la base no
consume hueco. La escritura de una pista es todo-o-nada; con menos de 11 sectores libres el drive informa el disco como protegido (/WPRO, visto por el invitado y por el
motor en `drive_writeprotected`), asi que una pista que no cabe no se escribe a medias ni se pierde sin aviso; `refused_writes` cuenta una escritura que llegase al overlay sin
hueco (el motor no la intenta mientras el drive esta protegido). El overlay no crece el tamaño del disco: el 901120 anunciado es fijo.
Pistas que no son AmigaDOS (T27a2, parche 0121): un ADF solo tiene once sectores por pista y el motor descartaba (`drive_write_adf_amigados` falla, `convert_adf_to_ext2` no
esta compilado) una pista escrita que no decodifica. Ahora se guarda tal cual la escribio el invitado, como las palabras MFM de la pista, en un segundo pool de 16 pistas
(`P6_OVL_RAW_SLOTS`, compartido por todos los medios, 12784 bytes cada una: una vuelta NTSC) que viaja en el estado a continuacion de los sectores, y la unidad lee la pista
desde ahi hasta que el invitado la escribe de nuevo como AmigaDOS (entonces la copia cruda se borra). Un disco con una pista cruda NO se puede exportar como ADF: `p6_media_export`
responde UNSUPPORTED con el motivo y no escribe nada, y `p6_media_write_status_get` cuenta `raw_tracks`; la maquina, en cambio, sigue leyendo lo que escribio y los estados la
restauran. Si el pool de pistas crudas esta lleno, la escritura no se guarda y se cuenta en `refused_writes` (es el unico caso en que ese contador se mueve con el motor real).
La lectura de una pista cruda incluye el salto de 1-3 bits que el motor da al pasar por el indice (`floppy_random_bits`), como una unidad real.
Exportar es explicito: `p6_media_write_status_get` describe el overlay (capacidad, uso, sectores distintos de la base de un medio, rechazos, hash de la base) y
`p6_media_export` copia el disco actual (base con el overlay encima, 901120 bytes) a un buffer del llamante sin cambiar nada en la maquina: ni flush, ni evento, ni estado.
Hacerlo duradero (fichero temporal, flush, rename) es del llamante; run, save y load nunca escriben fuera del core, y una rama especulativa (rewind, runahead, segunda
instancia) no debe exportar.

Libretro (T27b): `puae6_floppy_writes` (Restart, `disabled` por defecto, el flag `P6_CONFIG_FLOPPY_WRITES` del esquema de opciones) hace escribibles los disquetes; el overlay va en los
estados, el rewind y el runahead (la cota de `retro_serialize_size` crece 2 017 456 bytes) y los ficheros de disco NO se tocan nunca. `puae6_floppy_save` (`off` / `on_close`, se aplica
al momento) decide si las escrituras se guardan: solo en `retro_unload_game`/`retro_deinit` (jamas durante un run, una carga de estado, un rewind o un runahead) y solo de los discos que
cambiaron respecto de los bytes con que se cargaron. Cada uno se exporta con `p6_media_export` y se escribe en el save directory del frontend como `puae6-<24 hex del SHA-256 del
disco tal como lo dio su fichero>.adf` mediante un fichero temporal vecino, flush/fsync y rename (siempre queda el disco viejo o el nuevo); al cargar con escrituras habilitadas, esa
copia ES el disco (su SHA-256 es su identidad: un estado guardado con otra copia no carga, como con cualquier otro medio). Un disco con una pista que no es AmigaDOS no se guarda y se
avisa; con `off` se avisa de cuantos discos cambiaron y se descartan. La cabecera de un ADF guardado que el puente tomaria por otro formato (extendido o ejecutable) se rechaza al
guardar y al usar. headless siempre `flags = 0`.

Discos de guardado (T60): cada `#SAVEDISK:<volumen>` de una M3U añade en su posicion un ADF OFS DD vacio construido en memoria, dentro del mismo limite de 16 discos.
Disk Control lo muestra como `Save Disk N` (N empieza en cero y cuenta solo estas directivas). Como PUAE5, el volumen se limita a 30 bytes, `/` y `:` se sustituyen por `_`, y un nombre
vacio o que empieza por `EMPTY` sin distinguir mayusculas usa `Save Disk N`. La fecha Amiga es fija (epoca mas N ticks), sin reloj host, y distingue dos discos con el mismo nombre.
No se crea ningun fichero al cargar. Las opciones de escritura y guardado siguen siendo explicitas: sin `puae6_floppy_writes` el disco esta protegido y se avisa; con escrituras y
`puae6_floppy_save=on_close`, solo un disco modificado se exporta al cerrar mediante el mismo reemplazo atomico, como `<nombre de playlist sin extension>.saveN.adf` en el save directory
(convencion de PUAE5; playlists con el mismo nombre comparten esos ficheros). La siguiente carga con escrituras habilitadas usa esa copia, cuya identidad sigue siendo el SHA-256 de sus
bytes. El original y la playlist permanecen intactos. El overlay y el disco seleccionado viajan en el estado: rewind y runahead no escriben ficheros y el cierre exporta solo la rama final.

BizHawk/Waterbox (T28, T28b): el sync setting `FloppyWrites` (por defecto `false`: `flags = 0` y el nucleo no tiene `ISaveRam`) fija `P6_CONFIG_FLOPPY_WRITES`. El overlay viaja en el snapshot
del sandbox (rollback) y lo duradero es `ISaveRam`: la lista acotada de sectores distintos de la base (`P6SR`, 12 + 40 + 514 n + 32 bytes, nunca una imagen), tomada solo por una llamada explicita
(`CloneSaveRam`, que EmuHawk hace en `FlushSaveRAM` y en el autoflush) y devuelta solo antes de la primera unidad (`StoreSaveRam`), porque la base vive en el heap sellado. EmuHawk dimensiona el
buffer de la carga con `CloneSaveRam()` de un nucleo recien creado, que aqui es `null`; por eso `integration/bizhawk/patches/0001-register-puae6.patch` añade `Puae6.Puae6Core` a la lista de
nucleos que reciben el fichero `.SaveRAM` entero (con un EmuHawk sin parchear el fichero se escribe pero no se autocarga). Un `.SaveRAM` que el nucleo rechaza
(`SaveRamRejectedException`, que es una `IOException` precisamente porque `LoadSaveRam` solo captura esa) se avisa en pantalla con su motivo, sin excepcion sin capturar y sin cambiar nada.
`ReplayInProgress` no lo pone ningun frontend real: BizHawk ya separa la SaveRAM de las peliculas (ruta propia por pelicula, peliculas que empiezan desde SaveRAM, `LoadSaveRam` no se
ejecuta con una pelicula en cola); es un gancho para herramientas y para el arnes de tests (documentado en el comentario de la propiedad, `Puae6Core.Persistence.cs`).

Guardar estado revierte overlay, drive state, indice de disco y eventos pendientes.
Flushear a almacenamiento del host es operacion explicita aparte, atomica y nunca
se ejecuta durante replay especulativo/runahead/rewind. No escribir sobre el medio
base. Reiniciar el invitado no descarta sus escrituras salvo orden explicita.

HDF: dispositivo de bloques y backend puro; no exponer filesystem del host a TAS.
Medios grandes pueden usar lecturas inmutables por content ID+offset en un backend
aprobado; el cursor o cache mutable no puede ocultar estado fuera del sandbox.
Cualquier caching externo debe ser semantica de funcion pura y probado tras reset
arbitrario de la cache. MVP prefiere precargar floppies para reducir esa superficie.

Implementado (T29a, parches 0160-0167): `P6_MEDIA_HDF` en la lista de `p6_load` monta un disco duro por el controlador UAE (`uaehf.device` y la placa UAEFS, como
PUAE5; sin Gayle) como DH0, DH1... en el orden de la lista, hasta `P6_MAX_HDF` = 4. La imagen es un multiplo de 512 bytes (al menos un bloque), `flags` = 0, su `content_id` es el SHA-256
de sus bytes y no hay tope fijo de tamaño: se copia entera al almacen de blobs (`P6_BLOB_HDF`, `p6_blob_pread`: lectura por offset de la base inmutable, sin cursor ni fichero del
host) y el presupuesto de memoria es su tamaño (CAPACITY si no cabe). Una imagen que empieza por `RDSK` trae su RDB; cualquier otra es una particion (32 sectores, 1 cabeza, 2 bloques
reservados, prioridad de arranque 0). Un HDF no es un disquete: ni `MOUNT` en una unidad, ni `p6_media_append`. La E/S es sincrona y sin retardo emulado: la peticion se sirve
dentro de `BeginIO` (sin hilo por unidad) y el invitado ve la respuesta en la siguiente INT2 (cola EXTER de `native2amiga`, sin hilos); `HD_SCSICMD` responde `IOERR_NOCMD` (no se compila
`scsi.cpp`), y se rechazan los volumenes del host en vivo (directorios, archivos, CD, cinta) y cualquier controlador distinto del UAE; un directorio, un LHA o un ZIP entran como una imagen construida por el adaptador ("Volumenes (T64a)").
Kickstart 1.3 o posterior (version 34+): con una anterior `p6_load` responde UNSUPPORTED. La placa UAEFS y la ROM de arranque UAE ($F00000) existen SOLO si hay un HDF: sin el, la maquina,
sus digests y sus hashes son los de antes. RDB y particiones se validan sin reservar tamaños leidos del disco (tamaño de bloque del RDB: potencia de dos entre 256 y 8192; cadena de
FSHD acotada a 1024; sin VHD ni CHD). Con un HDF montado el estado nativo existe desde T29c (parrafo siguiente; T29a y T29b respondian UNSUPPORTED). Cierre de T29 (T29d): `P6_CAP_MEDIA_HDF` (bit 6) en `p6_query_caps` anuncia `P6_MEDIA_HDF`, su overlay de escritura, la exportacion y el estado; `p6_headless` acepta `--hdf IMAGEN` (repetible) y `--export-hdf FICHERO`; el guest Waterbox recibe los HDF por `p6w_init` sin copiarlos al heap sellado; el fuzzer de discos duros (`FUZZ01`, parche 0190) muta RDSK, PART, FSHD y LSEG.

Escritura de HDF (T29b, sin parches del motor: todo en `ports/portable`): un HDF es siempre escribible. La base registrada es inmutable (ni el buffer del llamante ni la copia del almacen de blobs cambian
jamas); una escritura del invitado copia el trozo de 4 KiB (`P6_HDF_CHUNK`, el `HARDDISK_CHUNK` de MacHawk) a un overlay la primera vez que lo toca, lo marca en un bitmap de sucios y escribe ahi; toda lectura
ve el overlay sobre la base. La memoria del overlay (el tamaño de la imagen mas su bitmap) se reserva al registrar el medio en `p6_load`, asi que una escritura no asigna ni puede fallar por falta de sitio
(presupuesto como MacHawk: tamaño del fichero, ahora dos veces: base y sitio para el overlay). `p6_media_export` de un indice de HDF copia la imagen tal como esta ahora (la base con el overlay encima) a un
buffer de al menos su tamaño (CAPACITY sin escribir nada si es menor) y `p6_media_write_status_get` cuenta en trozos (`overlay_capacity` = trozos de la imagen, `overlay_used` = `medium_sectors` = trozos
escritos, `refused_writes` = `raw_tracks` = 0). Nada sale del proceso solo: ni flush ni fichero; la exportacion duradera es del llamante. `CMD_WRITE`, `CMD_FORMAT` y sus versiones TD64/NSD escriben; una escritura
fuera de la imagen es `IOERR_BADLENGTH` y no almacena nada. El overlay es estado desde T29c (parrafo siguiente).

Estado de un HDF (T29c, parches 0180-0182, `bridge/state_hdf.cpp`): con un HDF montado el chunk P6DK lleva, despues de los cuerpos del motor y antes del overlay de los disquetes, una seccion que NO existe sin HDF (un estado sin
HDF no cambia ni de bytes). Lleva el controlador UAE como valores y bytes del invitado, jamas punteros ni rutas del host (el `save_filesys` de upstream guarda rutas y reconstruye las unidades montandolas): el numero de HDF y el
content id de cada base, los globales de `filesys.cpp` (incluido el avance de la interrupcion EXTER en curso y si retiene su semaforo), por unidad los campos que el arranque y el invitado escribieron en `UnitInfo` (placa de
dispositivo, `startup`, nodo, paquete de parametros, particiones del RDB, el gestor de ficheros que trajo el RDB como bytes en `rdb_filesysstore`, hasta 262144), el `hardfpd[]` de `uaehf.device` (peticiones sin terminar con sus
48 bytes de IORequest), los 64 KiB de la zona de traps ($F00000) con sus cursores y la linea de peticion de EXTER (`uae_int_requested`), la placa UAEFS (base, si tiene memoria, si responde, 64 KiB), la cadena autoconfig cuando
no hay Fast RAM (con ella la lleva P6RM; con HDF la cadena tiene dos tarjetas mas: la placa UAEFS y la ROM de arranque UAE), la cola EXTER (los mensajes en orden; uno con puntero del host no se guarda) y, por HDF, el overlay:
los trozos de 4 KiB sucios (indice ascendente y bytes), NUNCA la base. Cargar un estado SUSTITUYE el overlay entero. La identidad de la base (content id) se comprueba antes de tocar la maquina: un estado tomado con otro HDF
(aunque del mismo tamaño), sin HDF, o con un HDF y un disquete donde esta imagen tiene el disquete y el HDF, es INCOMPATIBLE (`media_manifest_hash`, y el content id del cuerpo contra el registrado si alguien lo forja con
checksums validos); un cuerpo fuera de lo que la maquina puede tener es CORRUPT_STATE y no toca nada. Un HDF no es un anexo del manifiesto (esta desde `p6_load`), asi que un prefijo del manifiesto que no lo contenga no es
un estado de esta imagen. `p6_state_bound` cuenta cada trozo de cada HDF como escrito (el tamaño de la imagen mas 4 bytes por trozo, mas hasta 256 KiB de gestor de ficheros por HDF y los 64 KiB + 64 KiB de zona de traps y placa):
es una cota que no cambia hasta `p6_unload` (contrato de STATE01), asi que crece con el tamaño del HDF aunque el estado real solo lleve los trozos sucios; un estado de un HDF de 4 MiB con tres trozos sucios ocupa lo que la
maquina (unos 1,3 MB), no 4 MiB. Quien reserve el buffer por la cota con HDF grandes paga el tamaño de la imagen; la memoria del overlay ya se reserva entera en `p6_load` (T29b) y el coste en Waterbox se mide en T29d.

Libretro (T63a, `adapters/libretro/hdf.h`, `libretro_core.cpp`): un `.hdf` (o un `.hdz`, gzip de un HDF) como contenido o como entrada de una playlist `.m3u` se registra en `p6_load` como `P6_MEDIA_HDF`
(requiere `P6_CAP_MEDIA_HDF`; si no, el load falla con mensaje). Los discos duros van DESPUES de todos los disquetes del manifiesto, en el orden en que la playlist los nombra (DH0, DH1... hasta `P6_MAX_HDF` = 4), y se montan
todos a la vez desde el arranque; Disk Control no los lista (la bandeja sigue siendo solo de disquetes, con los mismos indices con o sin discos duros) y una playlist puede mezclar disquetes y discos duros en cualquier
orden. Un HDF no es un disquete: no se inserta, no se expulsa y `add_image_index`/`replace_image_index` solo admiten disquetes. Cotas, siempre aplicadas antes de reservar nada y nunca con el tamaño que el fichero dice
tener: un HDF (o un HDZ ya decodificado) como mucho `kMaxHdfBytes` = 128 MiB y todos los de una carga juntos 128 MiB (el puente guarda la imagen tres veces: el buffer del core, la base registrada y el sitio del overlay,
y la cota de estado `p6_state_bound`, y con ella `retro_serialize_size`, crece con el tamaño de la imagen: ver Estado de un HDF); un `.hdz` ha de ser gzip de un miembro que acaba el fichero (CRC y tamaño comprobados) o un ZIP con exactamente un `.hdf` (los demas miembros se ignoran; varios piden una playlist) y
una bomba se detiene en la cota (`container.h` `gunzip` con limite); la imagen es un multiplo de 512 bytes de al menos un bloque (si no, el load falla nombrando el fichero y el tamaño); dos HDF con los mismos bytes no
se montan dos veces (mensaje) y mas de cuatro tampoco. Un fichero cuyo nombre es `.hdf` es la imagen tal cual aunque empiece como un gzip (la identidad de un medio son sus bytes). Geometria: el motor decide
(T29a): una imagen que empieza por `RDSK` trae su RDB y sus particiones; cualquier otra es UNA particion de 32 sectores, 1 cabeza, 2 bloques reservados y prioridad de arranque 0; el core solo lo registra en el log
(`hard disk DH0 (ruta): N blocks with a Rigid Disk Block` / `one partition`). PUAE5 estimaba 16 superficies para imagenes de 1 GiB o mas; con la cota de 128 MiB no hace falta. Los cambios del invitado: el HDF es
siempre escribible, el fichero NUNCA se toca y las escrituras (trozos de 4 KiB) son estado de la maquina, asi que los estados, el rewind y el runahead las llevan y las devuelven. Salen de la maquina solo si el
contenido se cierra (`retro_unload_game`/`retro_deinit`, jamas durante un run, una carga de estado, un rewind o un runahead) y `puae6_hdf_save` = `on_close` (por defecto `off`: se avisa de cuantos discos duros
cambiaron y se descartan): `p6_media_write_status_get` dice si el invitado escribio algun trozo (un disco intacto no se exporta), `p6_media_export` copia la imagen (base con overlay) y, si difiere de los bytes con los
que se cargo, se escribe en el save directory como `puae6-<24 hex del SHA-256 del HDF tal como lo dio su fichero>.hdf` por un fichero temporal vecino, flush/fsync y rename (siempre queda el disco viejo o el nuevo; un fallo
se avisa y deja el viejo). Con `on_close` la carga siguiente del mismo fichero usa esa copia (su SHA-256 es su identidad: un estado guardado con otra copia no carga); una copia de otro tamaño se avisa y se usa el original.
Un estado libretro (marco `P6LRSTAT`) lleva el HDF a traves del estado nativo (controlador UAE y trozos sucios, nunca la base); la identidad de la base se comprueba antes de tocar la maquina, asi que un estado de otro
disco, o sin disco, se rechaza. Con Kickstart real (opt-in, nunca copiado) KS 1.3 monta la particion del RDB tambien desde una playlist con un disquete, y KS 3.1 (A1200) valida el volumen y el core lo guarda al cerrar.
No hecho (deliberadamente): disco de arranque vacio (`puae6_boot_hd` de PUAE5). Una particion sin sistema de ficheros pide formatearla desde el invitado, la cota de 128 MiB no cubre sus opciones de hasta 512 MB, y
necesitaria crear ficheros en el save directory fuera de la exportacion explicita: queda como subtarea de T63 (junto a WHDLoad, T30). BizHawk/Waterbox monta los discos como MacHawk en T63b.

ADF extendido y FDI en el nucleo BizHawk/Waterbox (T57): sin cambios en `p6.h` ni en `p6w.h`; el cargador de EmuHawk entrega tambien los ficheros `.fdi` como disco (el ADF extendido es un `.adf`), el
constructor del nucleo solo acota su tamaño (3 MiB como mucho si no son un ADF de 901120 bytes) y el guest, con el mismo `classify_floppy`, dice por que rechaza uno (`ArgumentException` con el motivo al crear el
nucleo). Son medios de solo lectura aun con `FloppyWrites`: `ISaveRam` no los incluye (un `.SaveRAM` que nombra sectores suyos es `BadFields`), y la identidad de estados y peliculas es el SHA-256 de los bytes que
se dieron al nucleo, como siempre. Un snapshot del sandbox a mitad de una vuelta de un FDI con pulsos debiles vuelve en el mismo sandbox y en otro, y las unidades siguientes son las mismas
(`tests/waterbox-media/test_media_trackdisk.py`, WB03). El guest cambia con los parches 0330 a 0335: `manifests/waterbox-memory.json` se registra de nuevo con la receta de `docs/03-waterbox.md` (las 16 claves,
`runtime_sha256` como estaba). EmuHawk no se ha ejecutado aqui (el constructor se prueba por compilacion en net8 y net48 y por el arnes de `tests/ffi`).

HDF en el nucleo BizHawk/Waterbox (T63b, `integration/bizhawk/core/Puae6`, `tests/waterbox-media/test_media_hdf.py`): sin cambios en `p6.h`, `p6w.h` ni en el motor (no hay bit de capacidad ni parche nuevos). El cargador da
al nucleo los ficheros `.hdf` junto a los `.adf` (`Puae6Parameters.HardFiles`; el constructor de EmuHawk reparte `lp.Roms` por extension), hasta `P6_MAX_HDF` = 4, multiplos de 512 bytes y sin dos copias de una imagen
(`ArgumentException` con el motivo al crear el nucleo). Se montan en `p6w_init` tras los disquetes (el medio p6 de un HDF es `MediaCount + n`, el que usan `p6w_media_export` y `p6w_media_write_status`), siempre
escribibles y sin `FloppyWrites`. El mmap heap del sandbox crece `2 x HardFileHeapKiB` de cada imagen (`Puae6Core.HardFileHeapKiB`, la misma regla que el smoke y que `manifests/waterbox-memory.json`; el heap sellado no
crece). La identidad de estados y peliculas añade, solo si hay HDF, el numero de HDF y el SHA-256 de cada imagen tal como se dio al nucleo (el hash de medios de un nucleo sin HDF es el de siempre), asi que otro HDF
(aunque del mismo tamaño), ninguno o dos se rechazan antes de tocar el sandbox (`OtherMedia`, o `OtherHeaps` si el heap difiere); lo que el invitado escribio es el overlay del sandbox y viaja en el snapshot.
`ISaveRam` existe cuando hay algo escribible (disquetes con `FloppyWrites` o algun HDF). Una SaveRam con HDF es la version 2 de `P6SR` (la de solo disquetes sigue siendo la version 1, byte a byte la de T28):
`"P6SR" | u32 2 | u32 disquetes | (disquetes como en la version 1) | u32 discos duros (1..4)` y por cada uno `u32 indice | 32 bytes content id de la base | u64 bytes de la imagen | u32 4096 | u32 trozos | (u32 trozo |
bytes del trozo)*` en orden creciente (el ultimo trozo de una imagen que no es multiplo de 4096 es corto) y el SHA-256 de todo lo anterior. Es el formato disperso de MacHawk (`UAE.ISaveRam.cs`: trozos de 4 KiB sucios)
con la identidad de la base añadida y SOLO los trozos cuyos bytes difieren de la imagen (un trozo escrito con los mismos bytes no esta; el nucleo exporta la imagen con `p6_media_export` y la compara con su base,
asi que `CloneSaveRam`/`SaveRamModified` cuestan el tamaño de la imagen cuando hay escrituras, y nada si el overlay esta vacio). `StoreSaveRam`, solo antes de la primera unidad, reconstruye el guest con la base y los
trozos encima (como para los disquetes: el overlay del guest empieza vacio y el hash de lo guardado entra en la identidad); una SaveRam de otra imagen (`OtherMedia`), cortada (`BadLength`), danada (`Corrupt`), de un
nucleo con otros medios (`BadFields`), a un nucleo sin nada escribible (`WritesDisabled`) o despues de la primera unidad (`TooLate`) se rechaza sin cambiar nada. EmuHawk la carga entera por el parche
`0001-register-puae6.patch` (T28b); el menu "Export Hard Disk" de MacHawk no se porta (el API es `GetHardDiskContents`, `GetHardDiskName`, `HasHardDisk`).

Cierre de T63 (HDF con Workbench, opt-in con `P6_KICKSTART_A600_ZIP` + `P6_WB205_ZIP`, sin cambios de produccion): los bytes del ADF de Workbench 2.05 (901120 = 1760 bloques = 55 cilindros de 32, `DOS\0`,
bloque raiz 880) SON una particion plana valida (T29a: 32 sectores, 1 cabeza, 2 reservados, DosType = primer long del bloque 0), asi que "el HDF con Workbench" es el ADF leido en memoria del zip del usuario y dado
como `P6_MEDIA_HDF` sin disquete: el A600 con Kickstart 2.05 arranca desde DH0 (unidad ~510) a la misma ultima imagen, pixel por pixel, que el mismo disco desde DF0 (unidad ~2370), en el motor nativo
(`tests/hdf/test_workbench_hdf.py`), en libretro (`WorkbenchHardFileTests` de `tests/libretro-host/test_hdf.py`) y en el nucleo BizHawk dentro de Waterbox (`WorkbenchHardFileTests` de
`tests/waterbox-media/test_media_hdf.py`, WB03); DET02 lo cubre con estados dentro del arranque, de la carga que sigue a responder el script del disco y con overlay real (`tests/state/test_det02_hdf.py`). WB 2.05 no escribe
al arrancar; las escrituras de las pruebas salen de una copia derivada en memoria con el flag de bitmap valido del bloque raiz a 0: el sistema de ficheros valida el volumen entre las unidades 170 y 190
y reescribe los bloques 880 y 881 (un trozo de 4 KiB, el 110), que salen solo por la exportacion explicita (`puae6_hdf_save=on_close`, atomica, y la SaveRAM P6SR v2 en BizHawk) y vuelven con estados, rewind,
run-ahead y SaveRAM. Limite conocido, ajeno a los HDF (T63): el A600 con Kickstart 2.05 no repone exactamente desde un estado tomado cerca de las unidades 95-125 (DET02 en la 120; run-ahead
desde el run 0 de libretro), con o sin disco; el A500 con Kickstart 1.3 si.

Archivos de discos (T59, `adapters/libretro/{archive,sevenzip}.h`, `tests/libretro-host/test_archives.py`, FUZZ01 `archive`; sin parches del motor ni ABI nueva): un `.zip`, un `.7z` o un `.rp9` con VARIOS disquetes es un
conjunto, leido en memoria como cualquier contenedor (sin directorio temporal ni escritura; el archivo no se conserva) y expandido en su sitio en la playlist: es lo que hace PUAE5 (descomprime, ordena los discos y escribe un M3U) sin el directorio ni el
fichero. La identidad de cada medio sigue siendo el SHA-256 de su ADF (ADR 0008), de modo que el mismo conjunto desde un ZIP, un 7z, un RP9 o una playlist de ADF sueltos son los mismos medios en el mismo orden, el mismo futuro y estados que cargan entre
si (`test_archives.py`). Reglas: (1) un disco es un miembro `.adf`, `.adz`, `.dms`, `.fdi` o `.ipf`; un miembro en un formato que el core rechaza (`.ctr`...) o un `.7z` anidado rechaza todo el archivo nombrandolo (un conjunto al que
falta un disco en silencio es peor que ninguno); lo demas (lecturas, capturas, carpetas, basura de macOS) se ignora y el registro dice cuantos. (2) El orden es el de PUAE5 (`qsort` con `sensible_strcmp`, libretro-glue.c) hecho total: los nombres con
"(Program" van primero (No-Intro marca asi el disco de programa; PUAE5 lo hace con una comparacion incoherente que depende de `qsort`), despues byte a byte con el espacio contando como `/` (asi `Turrican.adf` < `Turrican II.adf` < `Turrican III.adf`, que
el orden de bytes pone al reves), despues bytes, despues la posicion; el orden del propio archivo nunca cuenta, y los miembros en carpetas participan con su ruta completa. (3) `fichero.zip#miembro.adf` (tambien `.7z` y `.rp9`, y como entrada de un M3U) es
ese miembro solo: la forma en que RetroArch nombra un fichero de un archivo; el nombre es la ruta del miembro o su nombre de fichero si es unico; el ultimo `#` cuenta y solo tras una extension de archivo. (4) RP9 (el paquete de RetroPlatform de Cloanto: un ZIP con
`rp9-manifest.xml`; un ZIP con ese manifiesto se lee como RP9 sea cual sea su extension): la seccion `<media>` de `<application>` lista los `<floppy>` del paquete y su atributo `priority` da el orden (el del documento si falta o empata); es la playlist. Un
`<floppy root="shared">` esta en la carpeta compartida del reproductor, no en el paquete, y se ignora con aviso; un floppy que el manifiesto nombra y el paquete no trae, o con una ruta insegura, rechaza el paquete. El resto del manifiesto (maquina, memoria, Kickstart,
perifericos, discos duros, CD, cintas, snapshots, arranque) NO se lee y el registro lo dice: la maquina se elige con `puae6_machine`. El formato del manifiesto se reconstruyo de la documentacion publica de Cloanto (kb 15-122/19-109, sin esquema ni ejemplos de `<media>`) y
del lector de Amiberry (BlitterStudio/amiberry, `rp9_manifest.cpp`, solo para saber que elementos y atributos existen); no se probo con un paquete real de Cloanto, solo con los sinteticos de `tests/libretro-host/archives.py`. (5) Un 7z se lee con un lector propio (`sevenzip.h`, ver abajo); es solo de discos: un 7z de ficheros se rechaza
(un volumen viene de un ZIP o un LHA); un archivo con discos y un `.hdf` es un conjunto de discos y el `.hdf` se ignora; un `.zip` sin discos es un volumen (T64a) y un disco duro dentro de un ZIP es un `.hdz` (T63a): la regla no deja ningun archivo ambiguo. (6) Cotas, antes de
reservar nada: 4096 entradas, 16 discos (`P6_MAX_MEDIA`: tambien para el total de imagenes de una playlist, porque un estado lleva la lista de la bandeja), un miembro de 2 MiB (un fichero de disco), 64 MiB de archivo, 32 MiB de datos desempaquetados de un 7z (la suma de lo que
declaran sus carpetas, comprobada antes de decodificar nada), un manifiesto de 256 KiB y 64 niveles de elementos; un archivo hostil es un mensaje, nunca una reserva grande. Disk Control (`replace_image_index`) acepta un archivo de UN disco o un `fichero#miembro`; uno de varios se rechaza
diciendolo (se anade de uno en uno). El lector de 7z (`sevenzip.h`, escrito aqui y no el SDK de LZMA de Igor Pavlov, que seria un tercero de ~6000 lineas de C con otro sistema de compilacion; el SDK es dominio publico y `references/machawk-uae/deps/7zip` lo tiene, pero la
parte que hace falta es pequena) lee la cabecera de firma con sus dos CRC, cabeceras planas y comprimidas con LZMA (incluso anidadas), carpetas solidas y no solidas, ficheros vacios y carpetas, con los metodos Copy, LZMA y LZMA2; los filtros (BCJ, Delta, ARM...), BCJ2, PPMd, BZip2 y AES se
rechazan nombrando el metodo. El diccionario es el propio buffer de salida (el tamano de diccionario que pide el archivo no cuesta nada) y una distancia es valida si no llega antes del inicio de los datos (o del ultimo reinicio de diccionario de LZMA2). Se comprobo contra el LZMA de liblzma (el
codificador de Python, por un escritor de 7z en Python puro) y contra 7-Zip real (p7zip 17.05) (LZMA, LZMA2 multihilo, `lc`/`lp`/`pb` extremos, sin cabecera comprimida, no solido, Copy). BizHawk/Waterbox: sin cambios (`integration/bizhawk`): el cargador de BizHawk (HawkFile) ya abre `.zip` y
`.7z` y da al nucleo el miembro elegido (`archivo.zip|miembro` en su sintaxis), asi que cada disco llega como un fichero propio; el conjunto ordenado, la playlist en memoria y el `.rp9` son de libretro (en EmuHawk no se ejecutaron aqui).
Volumenes (T64a, `adapters/libretro/{ffs_volume,lha,volume}.h`, `docs/adr/0011-volumes-from-the-host.md`; sin parches del motor ni ABI nueva): un directorio, un `.lha`/`.lzh` o un `.zip` de ficheros como contenido
(o como entrada de un M3U) se lee UNA vez, al cargar, y se convierte en una particion plana FFS en memoria (la forma de un HDF sin RDB) que el nucleo registra como un `.hdf` (`P6_MEDIA_HDF`, DH0, DH1...
tras los disquetes, fuera de Disk Control). El host nunca se monta ni se vuelve a leer: lo que entra en la maquina es esa imagen, con content id = SHA-256 de sus bytes, y estados, rewind, run-ahead, overlay de
escrituras, exportacion (`puae6_hdf_save=on_close`) y Waterbox son los de cualquier HDF. La eleccion frente a un volumen de archivo de UAE como el de MacHawk (filesys/zfile en memoria) y por que esta es la de
menor superficie estan en el ADR 0011. Determinismo (identidad = contenido): la imagen depende solo de los nombres (ISO 8859-1) y los bytes de los ficheros y de los directorios vacios; no de la ruta, del nombre
del directorio o del archivo, de fechas, permisos, usuario, orden de `readdir` ni orden de los miembros. Hijos y bloques en orden de bytes del nombre; todas las fechas 1990-01-01 00:00:00; el volumen se llama
`PUAE6`; proteccion 0; DOS\1 (FFS), DOS\3 si un nombre tiene un byte mayor que 127; 1 MiB libre para el invitado. El mismo arbol desde un directorio, un LHA y un ZIP es el mismo medio y un estado tomado con uno
carga con otro. Fuentes: un directorio por el VFS de libretro version 3 (`stat` y las llamadas de directorio; version 1 o sin VFS, la biblioteca C), sin `.DS_Store`, `._*` ni `__MACOSX`, con un enlace simbolico
tratado como lo que apunta; un LHA con cabeceras de nivel 0, 1 y 2 y metodos `-lh0-`, `-lh5-`, `-lh6-`, `-lh7-` y `-lhd-` (`lha.h`, port de `tools/whdload/lha.py`, CRC-16 de cada miembro); un ZIP almacenado o
deflate (todos sus miembros por `container.h`; un `.zip` con un `.adf`/`.adz`/`.dms` sigue siendo un disco). El escritor es un port de `tools/whdload/ffs.py` y es su oraculo: `tests/hdf/test_volume.py` compara los
bytes. Cotas aplicadas antes de reservar y nunca con un tamaño que la fuente declara: 4096 entradas, 48 MiB de ficheros (25 paginas de bitmap en la raiz, ~49 MiB; la cadena de extension no se escribe), 64 MiB el
archivo, 64 niveles, nombres de 30 bytes. Es un error con mensaje, y la carga falla, un nombre fuera de ISO 8859-1, `:` o `/` en un nombre, dos nombres que solo difieren en mayusculas, un fichero que tambien es
directorio, un miembro repetido, un LHA con CRC o metodo que no se lee, un nombre de ZIP o LHA que sale del volumen (`..`, absoluto, vacio) o un arbol que no cabe. Limites: Kickstart 1.3 o anterior no arranca estos
volumenes (la ROM no trae FFS y no se escribe OFS); el contenido decide que arranca (un volumen sin `S:Startup-Sequence` no arranca solo: la UX de WHDLoad es T65); el volumen es de solo lectura para el HOST (la fuente
no se toca jamas) y escribible para el invitado como cualquier HDF. Pruebas: `tests/hdf/test_volume.py` (el escritor y el lector contra el oraculo, ASan/UBSan), `tests/libretro-host/test_volume.py` (LR01/MED02/MED03/DET02:
directorio, LHA y ZIP en el nucleo con el VFS 3, con el 1 y sin VFS; la micro-ROM lee y escribe bloques; estados entre procesos y entre fuentes; Fatal Mission con WHDLoad arranca desde un directorio y desde un LHA con el
Kickstart 3.1 del usuario, opt-in), `FUZZ01` (`volume` de `tests/fuzz`). Un LHA no puede nombrar un fichero con el byte 0xFF (es el separador de ruta de sus cabeceras).

Volumenes en el nucleo BizHawk/Waterbox (T64b, `integration/bizhawk/core/Puae6/Volumes.cs`, `tests/waterbox-media/test_media_volume.py`): sin cambios en `p6.h`, `p6w.h` ni en el motor (el guest no cambia). `Puae6Parameters.Volumes` lleva LHA y ZIP de ficheros (EmuHawk: un `.lha`, un `.lzh` o un `.zip` sin disco que el cargador da como ROM; un directorio no es un fichero ROM del cargador de BizHawk y no se ofrece; el cargador de BizHawk abre un `.zip` y da al nucleo sus miembros, salvo el de MacHawk, que da entero el que trae un `.slave`, `LoadWhdLoadArchive`; EmuHawk no se ha ejecutado aqui: el constructor se prueba por compilacion en net8 y net48 y por el arnes de `tests/ffi`); el constructor del nucleo los convierte con `Puae6Volumes` (el mismo algoritmo que `volume.h`, `ffs_volume.h` y `lha.h`, con las mismas cotas, y el ZIP con su propio lector de directorio central: ninguna ruta del host, ningun orden ni fecha del archivo entra) en discos duros que se montan como `Puae6Parameters.HardFiles`, tras ellos y con el mismo tope de `P6_MAX_HDF` = 4 y sin dos copias de una imagen. Un archivo que no es un volumen hace fallar la creacion del nucleo con el motivo (`ArgumentException`, nombra el volumen). La identidad de estados y peliculas es la del HDF (el SHA-256 de la imagen que se dio al nucleo): el mismo contenido desde un LHA y desde un ZIP es un medio, y un estado tomado con uno carga con el otro. Los bytes son los de libretro y los del oraculo (`tools/whdload/ffs.py` y `lha.py`): el arnes `Ffi.dll volume` los escribe y `test_media_volume.py` los compara en archivos sinteticos de todos los niveles de cabecera y metodos y en arboles aleatorios. Escrituras, SaveRAM P6SR v2, exportacion, estados, peliculas y rewind son los de cualquier HDF (T63b). Con el Kickstart 3.1 del usuario (opt-in) Fatal Mission con WHDLoad arranca desde un LHA dentro de Waterbox, escribe `boot.log` y `exit.log` en el volumen y vuelve desde un estado, en el mismo sandbox y en otro proceso.

## Regiones de memoria

Implementado (T20a): `p6_memory_regions` (capacidad `P6_CAP_MEMORY_REGIONS`) devuelve vistas de la memoria que el motor asigno, no copias: Chip RAM,
Slow RAM y la ROM con su tamano real (el perfil A500 no tiene Fast RAM y una RAM que la maquina no tiene no se informa), con los bytes en el orden
del 68000 (big endian) y el offset n de una region es el byte n de esa memoria. Solo la RAM lleva `P6_REGION_WRITABLE`. Una escritura del host es una
accion externa que la maquina no hizo: quien graba una pelicula la trata como entrada. Los punteros valen hasta la siguiente llamada que cambie la
maquina (una unidad, un reset, un unload); el motor libera y reasigna su RAM en `memory_reset`, asi que un puntero guardado puede quedar colgando.
Solo se pide en LOADED (BAD_STATE si no), `count` es obligatorio y con `regions` nulo y capacidad cero devuelve el numero necesario; una capacidad
insuficiente devuelve CAPACITY con el numero necesario y sin salida parcial.

El tamano de la Slow RAM es el que la CPU alcanza, no el asignado (T75a): el motor asigna 2 MiB (potencia de dos) para las opciones de 1,5 y 1,75 MiB, pero solo mapea
`currprefs.bogomem.size` en `$C00000`, asi que `p6_memory_regions` informa 1536 o 1792 KiB y la cola asignada no se ofrece. Los adaptadores (el mapa de libretro y el dominio `Slow RAM`
de BizHawk, y cualquier otro cliente) ven el mismo tamano que la CPU para cada opcion; el adaptador libretro ya no recorta nada. La memoria asignada y el estado no cambian.

El guest de Waterbox las ofrece al wrapper como `p6w_memory_areas` (adapters/waterbox/p6w.h): la estructura es la de `LibWaterboxCore.MemoryArea` de
BizHawk y cada area es una funcion de acceso que se ejecuta dentro del sandbox y resuelve el puntero del motor en cada llamada. Es necesario por
dos razones medidas: una escritura managed en una pagina que el seguimiento de paginas sucias aun protege es un `AccessViolationException` fatal
(T19c2), y justo despues del seal ninguna pagina de la maquina ha sido escrita. Lo fuera de rango, una escritura en la ROM o un acceso sin maquina
no hacen nada (una lectura da ceros) y los cuenta `p6w_memory_refused`.

## Estados nativos (libretro/headless)

Reutilizar el serializador semantico upstream donde sea valido, por I/O en memoria,
añadiendo un envelope y extensiones para bridge/inputs/medios/filtros que falten.
No guardar el heap de un proceso nativo mediante memcpy; punteros/ASLR/padding lo
hacen distinto de Waterbox. No prometer compatibilidad .uss completa sin una tarea.

Cabecera propuesta: magic, format_version, core_build_id, upstream_sha,
patchset_hash, codegen_hash, profile_id, config_hash, media_manifest_hash,
payload_length y checksum. Codificar endian/tamanos y validar overflow antes de
asignar. El build ID incluye todo cambio que pueda modificar la semantica.

Inventario minimo: CPU/prefetch/cache permitida, scheduler y eventos, chipsets,
CIA/timers/RTC, RAM/mapas, discos/overlay, input y latched keys, audio/fases,
video/historia necesaria, bridge y control del bucle. Los recursos host se vuelven
a enlazar, no se guardan sus direcciones. Las caches derivadas solo se omiten con
reconstruccion determinista probada.

Estado valido unicamente en el boundary definido y sin callback activo.
state_load primero valida cabecera, identidad y limites; si falla, no deja la
maquina medio restaurada. El payload se valida estructuralmente antes del commit
o se usa rollback a un estado anterior. Una corrupcion tardia tambien se prueba.

retro_serialize_size anuncia al terminar load un bound que no crece hasta unload.
Reservar maximo de perfil/medios/overlay, no medir solo el estado actual. Si la
feature todavia no es correcta, devolver 0 y no declarar rewind. No se escriben
bytes fuera de capacidad.

Estados libretro (T18). El core enlaza `p6_state` y solo ofrece estados si `p6_query_caps`
anuncia `P6_CAP_STATE`; si no, el tamaño es 0. `p6_state_load` exige la longitud exacta que
escribio `p6_state_save` y libretro entrega blobs de `serialize_size` (rewind, runahead) o
del tamaño de un fichero, asi que el adaptador enmarca el estado nativo, sin adivinar:
`[cabecera de 64 bytes][seccion del adaptador][estado nativo][ceros hasta serialize_size]`.
La cabecera (little-endian) lleva el magic `P6LRSTAT`, la version del marco (1), las longitudes
exactas de la seccion y del estado nativo y el SHA-256 de la seccion. `serialize_size` =
cabecera + cota de la seccion + `p6_state_bound`, calculado una vez tras la carga; la cota de la
seccion es fija (cola de audio acotada, hasta 16 imagenes, y la ultima imagen solo si el
frontend no puede repetir cuadros). `retro_serialize` exige un buffer de al menos ese tamaño,
escribe exactamente `serialize_size` bytes (rellena con ceros el espacio que el estado nativo no
usa y no toca lo que haya mas alla) y devuelve false, sin tocar la maquina, antes de la primera
unidad (no hay maquina que describir). `retro_unserialize` valida todo antes de tocar nada:
magic, version, longitudes sin desbordamiento contra el tamaño recibido, checksum de la seccion,
seccion decodificada y encajada en esta carga (la lista de imagenes de la bandeja debe ser la
misma) y todos los bytes tras el estado nativo EN CERO; despues pasa exactamente su longitud a
`p6_state_load`, que valida identidad, firmware, configuracion y manifiesto de medios, y solo
cuando la maquina lo ha aceptado se aplica la parte del adaptador. Un blob mas corto que
`serialize_size` es valido si lo que falta eran ceros de relleno (un fichero sin relleno carga);
uno mas largo es valido si lo añadido es cero; basura en el relleno, un corte dentro del estado,
una cabecera o seccion alteradas o un estado de otra configuracion, firmware o medios se
rechazan con false y dejan la maquina bit a bit como estaba.

La seccion del adaptador lleva lo que el adaptador guarda entre dos runs y el frontend aun no ha
visto: la bandeja de discos (seleccion, bandeja abierta, peticiones registradas y no aplicadas, el
medio que tiene la unidad, contador de eventos de medios), el audio que el frontend no ha tomado y
la geometria (y los pixeles, solo sin GET_CAN_DUPE) del ultimo cuadro. Un load los sustituye por
los del instante guardado: una rama de rewind o runahead no deja ni peticiones de disco, ni audio
pendiente, ni entrada de su futuro abandonado. La entrada no se guarda: el adaptador la lee de nuevo
en cada `retro_run` y su memoria de flancos esta en el estado nativo. La geometria ya anunciada es
conocimiento del frontend y no se toca. Un estado de un frontend de otro tipo (con o sin
GET_CAN_DUPE) carga igual: la imagen se descarta o no se pide.

Quirks: el core anuncia `ENDIAN_DEPENDENT` y `PLATFORM_DEPENDENT` (el estado es de este build,
firmware, configuracion y medios, como dice la compatibilidad inicial) y no `INCOMPLETE` ni
`CORE_VARIABLE_SIZE`. No anuncia `MUST_INITIALIZE`: el tamaño se conoce tras la carga, y RetroArch
desactiva el runahead de un core que lo declara. RetroArch 1.18 no conoce la llamada de quirks de
la cabecera fijada (responde UNSUPPORTED). Netplay NO se declara ni se prueba: sigue
deshabilitado hasta su propia prueba. Runahead de segunda instancia tampoco: hay una instancia nativa
por imagen cargada y RetroArch 1.18 corre con una sola instancia aunque se la pida. El runahead
de instancia unica y el rewind se prueban en RetroArch real (ticks de cada unidad iguales a la
ejecucion normal, incluidas las repeticiones tras un load); RetroArch solo ofrece runahead si el
`.info` del core dice `savestate_features = "deterministic"`, y ese `.info` no se publica
mientras las limitaciones siguientes no esten cerradas.

Limitaciones conocidas del estado nativo (T15, no del adaptador; los tests
`test_GAP_*` de `tests/libretro-host/test_state.py` son su reproductor y pasan el dia que se cierren):
el cuadro dibujado en la primera unidad tras un load puede salir con la paleta de la linea
temporal abandonada (o la de una maquina recien arrancada, al cargar un fichero en otro proceso)
si el invitado puso la paleta una vez y no la reescribe; una unidad que no tenia cuadro nuevo
puede dibujarse tras un load; y tras un load y algunas unidades un byte del cuerpo CHIP (DIWHIGH)
difiere del de una maquina que nunca cargo. CPU, RAM, audio, ticks y la parte del adaptador
convergen; con un juego real (Speedball 2, pistas en carga y en juego) los cuadros tambien.

Compatibilidad inicial: mismo formato/build compatible declarado, mismos medios
y configuracion. Cross-build/cross-ISA/cross-version se habilita solo con evidencia;
ABI portable no equivale a estados portable ni a determinismo bit-exacto.

## Estados Waterbox

Contrato distinto: snapshot del sandbox mas estado C# no contenido en el sandbox.
No ejecutar el serializador nativo para fingir una prueba Waterbox. Ver documento 03.
La restauracion debe reponer callbacks y limpiar referencias host temporales.
Compatibilidad snapshot depende de WBX, layout, toolchain y runtime exactos. No se
intercambia con un .state nativo y no se hereda el identificador del UAE anterior.
