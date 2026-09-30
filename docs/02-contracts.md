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

Una instancia nativa activa por imagen. create/init concurrente devuelve BUSY.
No API reentrante. Todos los callbacks documentan si son solo-init, por-frame o
lecturas puras autorizadas. No invocar libretro fuera de su hilo de entrada.

## Frame, campos y reloj

Unidad de avance: una frontera vertical de video emulado, no una cantidad fija de
instrucciones ni una espera de 20 ms. En entrelazado, distinguir campo y frame
compuesto. run_frame entrega metadatos de field parity y no oculta dos avances
para dibujar una sola imagen. La cadencia frontend se documenta para cada modo.

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

## ROMs, discos y persistencia

Firmware y cada medio tienen content ID (SHA-256 de contenido normalizado segun
formato), nombre logico y licencia/procedencia para fixtures. Los paths del SO no
son identidad ni parte del estado. Medios ausentes o distintos provocan error.
El motor no enumera home, no descarga BIOS y no carga plugins de forma implicita.

ADF MVP: buffer inmutable de solo lectura, cargado antes de iniciar/sellar. Mount,
eject y swap operan sobre IDs precargados. Inventariar los metadatos de floppy
que tambien afectan al futuro: posicion, track buffers, cambio, motor, bits/MFM,
revolucion, proteccion y escritura diferida; no basta con bytes del archivo.

Escritura M3: base inmutable + copy-on-write dentro del estado. La granularidad
(sector, pista o bloque) debe reflejar el formato real; preservar escrituras de
pista/MFM cuando sea necesario, no forzar todo a sectores ADF validos. Limite de
overlay reservado al cargar, incluido en bound de estado. Overflow es error claro.

Guardar estado revierte overlay, drive state, indice de disco y eventos pendientes.
Flushear a almacenamiento del host es operacion explicita aparte, atomica y nunca
se ejecuta durante replay especulativo/runahead/rewind. No escribir sobre el medio
base. Reiniciar el invitado no descarta sus escrituras salvo orden explicita.

HDF: dispositivo de bloques y backend puro; no exponer filesystem del host a TAS.
Medios grandes pueden usar lecturas inmutables por content ID+offset en un backend
aprobado; el cursor o cache mutable no puede ocultar estado fuera del sandbox.
Cualquier caching externo debe ser semantica de funcion pura y probado tras reset
arbitrario de la cache. MVP prefiere precargar floppies para reducir esa superficie.

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
feature todavia no es correcta, devolver 0 y no declarar rewind. Un buffer mayor
se ignora o se rellena de forma definida; no se escriben bytes fuera de capacidad.

Compatibilidad inicial: mismo formato/build compatible declarado, mismos medios
y configuracion. Cross-build/cross-ISA/cross-version se habilita solo con evidencia;
ABI portable no equivale a estados portable ni a determinismo bit-exacto.

## Estados Waterbox

Contrato distinto: snapshot del sandbox mas estado C# no contenido en el sandbox.
No ejecutar el serializador nativo para fingir una prueba Waterbox. Ver documento 03.
La restauracion debe reponer callbacks y limpiar referencias host temporales.
Compatibilidad snapshot depende de WBX, layout, toolchain y runtime exactos. No se
intercambia con un .state nativo y no se hereda el identificador del UAE anterior.
