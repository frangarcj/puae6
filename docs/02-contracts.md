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
Kickstart arranca solo); `need_fullpath` y `block_extract` son true (el core lee el ZIP en memoria; el frontend no extrae nada) y las extensiones son `adf|adz|dms|zip|m3u`. El Kickstart 1.3 se busca en el directorio de sistema del frontend por una lista fija de nombres conocidos (`kick34005.A500`,
`amiga-os-130.rom` y tres nombres de coleccion); `p6_load` acepta o rechaza el fichero por su contenido (tamaño y SHA-256), no por el nombre, y un fichero presente pero
rechazado no oculta uno bueno bajo el nombre siguiente. El core copia el buffer porque `p6_load` lo toma prestado hasta el unload. Los ficheros se leen enteros a traves del VFS
del frontend (version 1: open, size, read, close) y con stdio POSIX si no lo ofrece, siempre con cota y nunca con el tamaño que el fichero dice tener: playlist 64 KiB, una ruta
4096 bytes, un disco 2 MiB antes de decodificar, un Kickstart 512 KiB. Contenedores (T17d, adapters/libretro/container.h, inflate.h, dms.h, cabeceras sin motor): el formato se
reconoce por los bytes, no por el nombre; ADZ (gzip), ZIP (almacenado o deflate, exactamente un disco: varios piden una playlist) y DMS (los modos de xDMS 1.3, dominio publico,
reescrito con limites y comparado con el de WinUAE) se decodifican en memoria, con salida acotada y todos los CRC comprobados, al ADF de 901120 bytes, y la identidad es el SHA-256
de ese ADF: el contenedor no entra en estados ni peliculas. Se rechazan con mensaje IPF/CT RAW (la licencia de capsimg prohibe redistribuirla con el core; un lector con licencia
compatible seria tarea propia), FDI, SCP, 7z, RAR, LHA, ADF extendido, ZIP cifrado/ZIP64/multivolumen y DMS cifrado, de alta densidad o FMS. Las rutas son bytes UTF-8 que se pasan tal cual y nunca son identidad: los medios se nombran por el SHA-256 de sus
bytes (ADR 0008), y dos discos con los mismos bytes comparten un medio del manifiesto. Una playlist M3U tiene una entrada `fichero|etiqueta` por linea (la etiqueta es opcional y por
defecto es el nombre sin extension), las lineas `#` son comentarios (`#SAVEDISK:` avisa y se ignora: los discos solo se escriben con `puae6_floppy_writes`, y se guardan como dice el parrafo de escritura de libretro de mas abajo), las rutas relativas lo son al directorio de la
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
desde el load: 760 x 576, salvo un A500 NTSC (760 x 486; no puede salir de su estandar). Una
configuracion NTSC de la maquina ECS reserva las 576 filas para no perder la imagen de un haz PAL.

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

`p6_config.machine` elige entre tres presets del MISMO perfil de build `deterministic-a500` (no hay un segundo perfil: mismo motor, mismo scheduler `p6_run_frame`, sin ifdefs
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
| Gayle / IDE / PCMCIA | no existe | NO emulado: `cs_ide = cs_pcmcia = cs_compatible = 0`, `gayle.cpp`/`ide.cpp` no se compilan; un `.hdf`/PCMCIA no se acepta | NO emulado (igual; el IDE es T56); `$DE1000` sin Gayle cae en el espejo de custom |
| Kickstart probado | 1.3 (r34.5) | 2.05 (r37.350), con Workbench 2.05 r37.71 de disco | 3.1 (r40.68), hasta la pantalla de insertar disco y con una demo AGA gratuita (Catabasis) cargada de disco |
| Selector | libretro `puae6_machine` = `A500` (defecto) / `A600` / `A1200` (reinicio; el `A1200` es el A1200OG de PUAE5); headless `--machine`; BizHawk sync setting `Machine` | igual | igual |
| FirmwareID BizHawk | Amiga/A500 | Amiga/A600 | Amiga/A1200 |
| Estados y peliculas | propios del A500 | propios del A600 | propios del A1200: cada maquina rechaza las de las otras dos antes de tocar nada (`config_hash` lleva la maquina; matriz 3 x 3 en las pruebas) |
| Chipset de VPOSR / DENISEID | familia 0 (OCS) | familia 2 en la tabla upstream = 8372A (`$2xxx` PAL, `$3xxx` NTSC), DENISEID `$FFFC` | Alice `$23xx` PAL / `$33xx` NTSC, Lisa `$00F8` |
| Cota de estado (`retro_serialize_size`) | 3 608 513 B | 3 608 482 B | 5 890 716 B (cuerpo del 68020, paleta AGA, anillo de Denise y raster a 3 bytes por pixel; ADR 0009 punto 9) |
| Waterbox, mmap medido (presupuesto 16 MiB) | 5 586 944 B | 5 046 272 B | 6 094 848 B (36 % del presupuesto, sin cambiarlo) |
| Waterbox, snapshot tras 60 unidades | 9 154 004 B | 9 137 620 B | 10 186 196 B |

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

NO probado (no afirmar compatibilidad): otros Kickstart que el motor acepta por contenido (2.04 o 3.1 en el A600, 2.05 en el A500, el Kickstart 3.0 en el A1200); configuraciones del
el Kickstart 3.1 del A600 (r40.063, el del A600 de PUAE5; no hay copia autorizada) y Chip de 1,5 MiB; FPU (T54); Gayle e IDE (T56) y PCMCIA; 68030/040/060 y otros 020 sin el bus de 24 bits (T55); la interfaz de EmuHawk
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
atajo no lo encuentra; sin Gayle, `$DE1000` y su banco leen el espejo de custom en lugar de un registro de Gayle. El A1200 de serie es el A1200OG de PUAE5; el que lleva 8 MiB de Fast RAM es el preset `a1200_fast8` (T49).

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

Cotas de estado medidas (micro-ROM): A500 3 608 513, A600 3 608 482, A1200 5 890 716 (sin cambios); A500 con 2 MiB de Fast 5 705 947; A600 con 2 MiB de Chip y 8 MiB de Fast 13 045 948;
A1200 con 8 MiB de Fast 14 279 606; A1200 con 8 MiB de Fast y 1,5 MiB de Slow 16 376 758.

Presets publicados junto a los de serie (`config/schema/options.json`, `presets`): `a1200_fast8` (el A1200 de PUAE5: A1200 con 8 MiB de Fast) y `a600_puae5` (A600 con 2 MiB de Chip y
8 MiB de Fast; el A600 de PUAE5 lleva ademas Gayle y el Kickstart 3.1 de su modelo, que este no: corre el Kickstart que se le da, y el 2.05 es el probado). Son combinaciones de
opciones, no un campo nuevo de la ABI.

## ROMs, discos y persistencia

Firmware y cada medio tienen content ID (SHA-256 de contenido normalizado segun
formato), nombre logico y licencia/procedencia para fixtures. Los paths del SO no
son identidad ni parte del estado. Medios ausentes o distintos provocan error.
El motor no enumera home, no descarga BIOS y no carga plugins de forma implicita.

Implementado (T12a, docs/adr/0008-media-identity.md): `p6_load` acepta hasta 16 ADF de 901120 bytes cuyo `content_id` es el
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
guardar y al usar. headless siempre `flags = 0`; el wrapper BizHawk lo fija a 0 hasta T27c.

Guardar estado revierte overlay, drive state, indice de disco y eventos pendientes.
Flushear a almacenamiento del host es operacion explicita aparte, atomica y nunca
se ejecuta durante replay especulativo/runahead/rewind. No escribir sobre el medio
base. Reiniciar el invitado no descarta sus escrituras salvo orden explicita.

HDF: dispositivo de bloques y backend puro; no exponer filesystem del host a TAS.
Medios grandes pueden usar lecturas inmutables por content ID+offset en un backend
aprobado; el cursor o cache mutable no puede ocultar estado fuera del sandbox.
Cualquier caching externo debe ser semantica de funcion pura y probado tras reset
arbitrario de la cache. MVP prefiere precargar floppies para reducir esa superficie.

## Regiones de memoria

Implementado (T20a): `p6_memory_regions` (capacidad `P6_CAP_MEMORY_REGIONS`) devuelve vistas de la memoria que el motor asigno, no copias: Chip RAM,
Slow RAM y la ROM con su tamano real (el perfil A500 no tiene Fast RAM y una RAM que la maquina no tiene no se informa), con los bytes en el orden
del 68000 (big endian) y el offset n de una region es el byte n de esa memoria. Solo la RAM lleva `P6_REGION_WRITABLE`. Una escritura del host es una
accion externa que la maquina no hizo: quien graba una pelicula la trata como entrada. Los punteros valen hasta la siguiente llamada que cambie la
maquina (una unidad, un reset, un unload); el motor libera y reasigna su RAM en `memory_reset`, asi que un puntero guardado puede quedar colgando.
Solo se pide en LOADED (BAD_STATE si no), `count` es obligatorio y con `regions` nulo y capacidad cero devuelve el numero necesario; una capacidad
insuficiente devuelve CAPACITY con el numero necesario y sin salida parcial.

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
