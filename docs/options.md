# Opciones del core libretro (UX de T31)

## Cartuchos y Freeze

`puae6_cartridge` requiere recargar el contenido. `disabled` es el defecto;
`hrtmon` usa el monitor incluido en A1200. Los valores Action Replay necesitan
una ROM raw propia en el directorio de sistema del frontend:

| Valor | Maquina | Tamano | Nombre principal | Alias |
|---|---|---|---|---|
| `ar1` | A500 stock, 68000 Exact/Compatible | 64 KiB | `ar1.rom` | `Action_Replay_I_v1.00.rom` |
| `ar2` | A500 stock, 68000 Exact/Compatible | 128 KiB | `ar2.rom` | `Action_Replay_II_v2.14.rom` |
| `ar3` | A500 stock, 68000 Exact/Compatible | 256 KiB | `ar3.rom` | `Action_Replay_III_v3.17.rom` |
| `ar1200` | A1200 stock, 68EC020 | 256 KiB | `ar1200.rom` | `Action_Replay_1200.rom` |

Se usa el primer nombre presente. Una ROM ilegible o incorrecta detiene la carga;
no se sustituye por el alias ni por otro monitor. Los aliases requieren poder
listar el directorio (VFS3 o stdio); con VFS1/2 se pide el nombre principal si no
abre, porque no se puede distinguir ausencia de falta de permiso. Los AR no admiten expansiones,
escritura de disquete ni contenido CD en estos perfiles. El core conserva las
opciones elegidas y explica una incompatibilidad al cargar.

La accion `Freeze` del mapper o `puae6_hotkey_freeze` mantiene el nivel mientras
se pulsa. Tras salir del monitor hay que soltar y volver a pulsar para entrar
otra vez. Los estados conservan ese nivel y la identidad de la ROM; restaurarlos
no vuelve a leer el fichero. BizHawk tambien ofrece Ar1/Ar2/Ar3/Ar1200 mediante
Cartridge y su proveedor de firmware Amiga/AR1, AR2, AR3 y AR1200; los perfiles
stock y la identidad de ROM son los mismos. BK2/TAS, seek y restauracion en
otro proceso estan comprobados con los cuatro modelos.

La fuente unica de las opciones es `config/schema/options.json`; `python3 tools/generate_options.py` genera de ella
`adapters/libretro/options_generated.h` (lo que ve el frontend) y `config/schema/options_table.generated.json` (lo que
deben respetar headless y el wrapper BizHawk). Este documento explica las opciones de la **interfaz de usuario libretro**
(T31a-T31d); las opciones de maquina (modelo, memoria, estandar de video, disquetes) estan en el esquema y en
`docs/02-contracts.md`.

## Volumen mecanico de disquetes (T74b)

`puae6_drive_sound_volume` controla la mezcla del motor y los pasos de las
disqueteras: `0` (silencio, defecto), `10`, `25`, `50`, `75` o `100` por ciento.
Headless usa `--drive-sound-volume` con esos mismos valores; BizHawk ofrece
`Settings.DriveSoundVolume` (`P0`, `P10`, `P25`, `P50`, `P75`, `P100`).
Se aplica en la siguiente unidad, sin reiniciar. No cambia SyncSettings ni la
identidad de peliculas o estados. Cargar un estado conserva el volumen que
el usuario tenga seleccionado; las fases mecanicas si se restauran.

## CD32 stock (T66r3)

Libretro acepta CD directo ISO/CUE/CCD/NRG DAO/ETNF/MDS v1 con `puae6_machine=auto|CD32`
y Kickstart Automatic. Requiere las mitades de 512 KiB `kick40060.CD32` y
`kick40060.CD32.ext` en el directorio de sistema (tambien sus alias Amiga Forever).
NRG DAO admite varias sesiones explicitas: conserva LBAs y huecos y presenta
un TOC virtual con pistas globales y leadout final (ADR 0018), sin seleccion
mutable de sesion ni sectores inventados entre sesiones. La identidad incluye
tabla y archivo completos en nativo. En BizHawk, seleccionando PUAE6 de forma
explicita, DAO multisesion usa metadata tipada y stream canonico con SBI: conserva
CUE0/A2, pistas globales y huecos sin fabricar sectores. No acredita Session Info,
FullTOC fisicos ni seleccion mutable de sesion.
NRG DAO admite tambien Mode2 codigo03 con cuerpo de 2336 bytes: reconstruye
solo la cabecera MSF por LBA y conserva subheader, datos y EDC/ECC. Mode02
admite payload de2048 bytes: sintetiza subheader cero y EDC/ECC de Mode2 Form1,
con la cabecera excluida de ECC. Ambos entregan raw2352; la vista2048 de Mode2
sigue sin soporte.
NRG ETNF admite una sesion con pistas00/02 de2048 bytes,03 de2336 o audio07
de2352, tambien mezcladas. Exige datos contiguos, longitudes alineadas al stride
de cada pista y posiciones logicas compactas. Sectores y bytes se acumulan por
separado: el offset de una pista no es su sector inicial por su propio stride.
Este subconjunto sigue los fragmentos por pista de libMirage
`a9d6a2044b3c7bdf63ca44517ddd5738fd68c69e`, image-nrg/parser.c:835,853..883;
libcdio `68ea374f` rechaza esas mezclas al exigir aquella igualdad homogenea.
Reconstruye pregaps sinteticos de150 sectores, como libMirage y GNU
libcdio/VCDImager: ETNF omite sus bytes y no almacena la longitud original,
por lo que los pregaps no estandar no se pueden recuperar. La identidad incluye
el archivo completo y esta politica canonica.
NRG ETN2 admite el mismo subconjunto de una sesion mediante footer NER5 y
registros de32 bytes: offsets y longitudes BE64, modo y sector compacto BE32,
ocho bytes reservados cero. LibMirage fijado, parser.c:373..383/835..883,
respalda directamente el mismo pregap150. La identidad tiene dominio P6ETN2;
DAO y ETNF conservan sus identidades. El parser valida extents superiores a4GiB
sin leer el payload; las vistas conservan sus limites actuales de LBA/MSF.
Libretro tambien admite el perfil ETNF multisesion global de CDI2Nero: conserva
INDEX01 global y huecos, con pregap sintetico Canonical150 y TOC virtual.
No recupera pregaps ni Q originales. Padding entre pistas, TINF y ETN2
multisesion siguen sin soporte. BizHawk admite tambien ETNF global por XML,
CD directo y catalogos mixtos, con restauracion en proceso nuevo; admite
el mismo subconjunto ETNF/ETN2 con pregap canonico de 150 sectores al elegir
PUAE6 explicitamente; esto no equivale a soporte TAO general.
MDS admite una sesion CD y un MDF compartido: audio de 2352 bytes, Mode1 de
2048/2352 y Mode2 raw de 2352; tambien sectores de 2448 con SUB96 intercalado. El pregap es
sintetico y los offsets apuntan a datos; TOC, PLBA y longitudes deben concordar.
Los gaps sinteticos Mode2 entregan Form1 vacia: cabecera MSF real y cuerpo cero,
incluidos EDC/ECC; raw2448 agrega los subcanales P/Q canonicos. La vista2048 de
Mode2 sigue sin soporte y los sectores almacenados se conservan byte a byte.
El nombre MDF puede ser relativo, UTF16LE o `*.mdf`; MDF solo no se carga.
La identidad incluye el MDS y MDF completos: cambiar metadata, datos o padding
impide restaurar sus estados. Versiones/modos/multipart no soportados se rechazan.
CD32 sin un CD directo se rechaza. Los tags CD32 de disquetes conservan su
significado anterior.
BizHawk selecciona `Machine=CD32` al cargar CD, salvo `Machine=CDTV` elegido explicitamente, y pide los firmwares
`Amiga/CD32` y `Amiga/CD32ext`, o el combinado de 1 MiB del firmware manager.
El perfil usa 68EC020 exacto, AGA, 2 MiB Chip, sin Fast/FPU/Z3 ni disqueteras.
SaveRAM contiene los 1024 bytes crudos de la EEPROM. BizHawk solo permite
importarlos antes de ejecutar o cargar un estado; libretro los ofrece como
RETRO_MEMORY_SAVE_RAM. El preset `cd32_fast8` añade 8 MiB de Fast RAM (CD32FR),
equivalente a `puae6_machine=CD32` + `puae6_fast_size=MB8`. En BizHawk se eligen
`Machine=CD32` y `FastMemory=MB8`; en headless `--machine cd32 --fast 8192`.
Requiere reinicio y los estados/peliculas del stock no se intercambian con FR.
Las peliculas BK2/TASproj CD32 admiten inicio desde SaveRAM o estado binario
con NVRAM inicial importada. Ambos frontends ofrecen catalogos fijos de hasta
16 slots, aliases y cambio de CD con restauracion fria. BizHawk permite mezclar
DAO multisesion, ISO V1 y CHD V2; conserva sus IDs al promover el transporte a V3.
Brian the Lion acredita arranque real, interaccion y restauracion de juego. Las
etiquetas CD32FR/FastRAM seleccionan FR en libretro, como se describe abajo.

## A4000/030 (T55b)

El modelo `A4030` usa la placa A4000 real: AGA, Gary/Ramsey, Ricoh RTC, mapa de
32 bits e IDE fisico incluso sin disco. Solo admite precision CPU `normal` y no
implementa MMU; `exact` y `compatible` se rechazan con el motivo. El preset
`a4030_normal` selecciona modelo y precision juntos. El Kickstart se busca como
`kick40068.A4000` o `amiga-os-310-a4000.rom`; BizHawk pide el firmware `Amiga/A4000`.

`puae6_fast_size=Auto` (defecto) usa la RAM del preset: 8 MiB Zorro II en A4030 y
ninguna en los modelos anteriores. `None` desactiva la placa; `MB1`..`MB8` fijan
su tamaño. Headless acepta `--machine a4030 --cpu-compatibility normal` y
`--fast auto|none|KIB`; BizHawk usa `Machine=A4030`, `CpuCompatibility=Normal`
y `FastMemory=Auto|None|MB1|MB2|MB4|MB8`. Cambiarlos requiere recargar.
Z3 usa una opcion distinta: `puae6_z3_memory=None|MB1|MB2|MB4|MB8|MB16|MB32|MB64|MB128|MB256|MB512`,
`--z3 none|1|2|4|8|16|32|64|128|256|512` o los mismos nombres `Z3Memory`
en BizHawk. None es el defecto; requiere A4030/Normal y autoconfig real.
El presupuesto mmap aumenta el doble de la RAM elegida. Otros modelos se
rechazan. Cambiarla requiere recargar y cambia la identidad del estado.
La aceptacion completa de T55b, incluido Waterbox real, sigue pendiente.

## Clases y la frontera con la emulacion

| Clase | Que es | Entra en `p6_config`, `config_hash` y estado | Cuando se aplica |
|---|---|---|---|
| `reboot` | parte de la maquina (modelo, RAM, estandar, audio, escritura de disquetes) | si | al cargar el contenido; un cambio en marcha solo se avisa |
| `presentation` | solo cambia lo que el adaptador muestra o como lee la entrada del frontend | **no** | de inmediato |
| `presentation` + `restart` | igual, pero el adaptador solo puede leerla al cargar (formato de pixel, modo inicial del D-Pad) | **no** | al recargar; el menu la marca `(Restart)` y un cambio en marcha se avisa |

Regla de diseno de la interfaz de presentacion/entrada (ficha T31): sus opciones no cambian la maquina. Lo que hacen es:

* **Presentacion** (T31c, T31b, T31a): se dibuja sobre una **copia** del frame que el motor ya produjo (o un buffer del adaptador). El hash de la imagen nativa
  (`p6_frame`), el hash de la configuracion y el estado nativo no se mueven con ninguna de ellas, active o no. `tests/libretro-ui` lo comprueba comparando unidades,
  cadencia, audio y el estado nativo de una ejecucion con todas las opciones visuales activadas frente a otra con todas apagadas.
* **Entrada** (T31d, T31a): el vkbd, el turbo, el raton por stick o D-Pad y el mapper se convierten en el paquete `p6_input` normal **antes** de `p6_run_frame`
  (teclas Amiga, botones, cuentas de raton). Lo que el adaptador debe recordar entre dos `retro_run` (contador de turbo, cursor y tecla retenida del vkbd, modo del
  D-Pad, mensaje del OSD) viaja en la seccion del adaptador del estado libretro (`state_frame.h`), de modo que cargar, rebobinar y adelantar (runahead) lo reproducen.
  Nada cuenta tiempo de pared: el turbo cuenta unidades emuladas.
* El core es utilizable sin esta interfaz: **todos los defaults dejan la presentacion y la entrada como eran** (imagen nativa sin copiar, RetroPad como joystick).

## Video (T31c) — categoria `Video`

Implementado en `adapters/libretro/present.h`; comprobado con `tests/libretro-ui/present_probe.cpp` y `test_present.py`.

| Clave | Valores (defecto en negrita) | Efecto |
|---|---|---|
| `puae6_video_aspect` | **pixel**, 4:3, PAL, NTSC | Ratio anunciado (`SET_GEOMETRY`/`SET_SYSTEM_AV_INFO`): el del pixel de la maquina, 4:3 fijo, o el pixel PAL (26:25) / NTSC (43:50) sea cual sea la maquina. No toca ningun pixel. |
| `puae6_video_crop` | **disabled**, minimum, smaller, small, medium, large, larger, maximum, auto | Ventana del raster nativo (756x574 PAL, 756x484 NTSC, hires y doble linea). Los niveles son los tamanos de PUAE5 en pixeles lores y lineas normales, doblados: minimum 360x270 (NTSC 240) ... maximum 320x200; nunca mayor que el raster. `auto` toma el area dibujada: la caja de los pixeles distintos del borde de cada fila, entre los margenes negros del renderizador, con minimo 640x400. |
| `puae6_video_crop_mode` | **both**, horizontal, vertical, 16:9, 16:10, 4:3, 5:4 | Que dimensiones recorta el crop, o la ventana mas pequena de una relacion de aspecto de imagen que contenga el tamano. |
| `puae6_video_crop_delay` | disabled, **enabled** | Con crop `auto`: `enabled` espera a que el area dibujada lleve 50 unidades emuladas iguales antes de mover la ventana (el tamano de la imagen no salta entre dos pantallas); `disabled` la sigue al momento. |
| `puae6_video_vertical_pos` | **auto**, -20 ... 70 (pasos de 2) | Donde empieza la ventana, en lineas normales (2 filas del raster). `auto` la centra (en el area dibujada para `auto`). Un numero es la distancia del borde superior de la ventana al del raster; positivo sube la imagen, negativo la baja; lo que cae fuera del raster es negro. Sin crop mueve la imagen dentro del mismo marco. |
| `puae6_video_horizontal_pos` | **auto**, -40 ... 40 (pasos de 2) | Igual en horizontal, en pixeles lores (2 columnas): positivo mueve la imagen a la derecha. |
| `puae6_video_gamma` | -0.5 ... **0** ... +0.5 (pasos de 0.1) | Gamma de la copia mostrada: exponente `1/(1+valor)` sobre R, G y B; positivo aclara los tonos medios. |
| `puae6_video_color_depth` (Restart) | **24bit**, 16bit | Formato de pixel anunciado al cargar: XRGB8888 o RGB565 (conversion a la salida; la maquina siempre dibuja en 24 bits). Si el frontend no acepta RGB565 el core avisa y sigue en 24 bits. |

Notas:

* La memoria del crop automatico (caja que sigue y contador de unidades) no viaja en el estado: tras cargar, el primer frame adopta la caja de inmediato. Es una vista
  del frame, no de la maquina, y cuenta unidades emuladas, nunca reloj de pared.
* El estado guardado por un frontend que no repite imagenes (`can_dupe` falso) lleva la ultima imagen **tal como se presento** (4 o 2 bytes por pixel); si se carga en
  una sesion con otro formato se descarta esa imagen y la siguiente unidad dibuja una nueva.
* Fuera de T31c (quedan como paridad PUAE5 abierta): `puae_gfx_flickerfixer` (quitar artefactos de interlace), `zoom_mode*` (marcadores obsoletos de PUAE5, sustituidos por
  los niveles de crop), y la resolucion/linea (`P24`, cambia el raster canonico y es de reinicio).

## Entrada (T31d) — categorias `Input`, `RetroPad Mapper` y `Hotkeys`

Implementado en `adapters/libretro/mapper.h` (funcion pura `mapper_step`) e `input_map.h` (lectura del frontend); comprobado con
`tests/libretro-ui/mapper_probe.cpp` (C++ sobre controles sinteticos) y `test_mapper.py` (el core completo: opciones dentro, paquetes `p6_input` fuera, y la memoria
a traves de estados, rewind y runahead). Todo es entrada normal: el paquete que recibe `p6_run_frame` sigue siendo teclas Amiga, botones de joystick y cuentas de raton.
Aplica al **jugador 1** (el RetroPad del puerto libretro 0); el joystick del jugador 2 no tiene mapper.

Memoria que el adaptador guarda entre dos `retro_run` (seccion del adaptador del estado, version 4): modo del D-Pad, toggles de turbo/crop/aspecto, fase del turbo (unidades),
cuenta del reset, botones y hotkeys pulsados en el run anterior (para los flancos), restos fraccionarios del raton y de los sticks. Nada usa reloj de pared.

| Clave | Valores (defecto en negrita) | Efecto |
|---|---|---|
| `puae6_mapper_up/down/left/right/b/a/y/x/select/start/l/r/l2/r2/l3/r3` | **---** (funcion por defecto), none, joy_up ... joy_fire3, mouse_left/right/middle, mouse_move_up/down/left/right, toggle_mouse, toggle_turbo, toggle_aspect, toggle_crop, reset, key_* (las 95 teclas Amiga sin Caps Lock, con los nombres del wrapper BizHawk) | Que hace cada boton. Sin mapear: D-Pad = direcciones, B/A/X = botones 1/2/3 (re-repartidos por `face_buttons`), el resto nada. |
| `puae6_mapper_lu/ld/ll/lr` , `ru/rd/rl/rr` | igual | Las cuatro direcciones de cada stick (mas alla de 20000/32767). Sin mapear, el stick izquierdo son las direcciones del joystick y el derecho nada. |
| `puae6_face_buttons` | **disabled** (B fuego, A 2o), jump (A = arriba), rotate (Y fuego, B 2o), rotate_jump (Y fuego, B arriba) | Reparto de los botones de cara cuando no estan mapeados. |
| `puae6_joyport` (Restart) | **joystick**, mouse | Que maneja el D-Pad al cargar: el joystick o el raton (D-Pad mueve, B/A/X = botones izq/dcha/central). `toggle_mouse` / hotkey lo cambia en marcha; el cambio viaja en el estado. Necesita el puerto de raton (dispositivo "Joystick + Mouse"). |
| `puae6_dpad_mouse_speed` | 1 ... **6** (100 %) ... 18 | Cuentas por unidad de un movimiento digital (D-Pad en modo raton, botones `mouse_move_*`). |
| `puae6_analog_mouse` | **disabled**, left, right, both | Mueve el raton con un stick. El stick que mueve el raton no da direcciones de joystick (salvo que una de sus direcciones este mapeada: gana el mapeo). Sin puerto de raton el stick sigue siendo un stick. PUAE5 trae `both` por defecto; aqui es `disabled` porque el stick izquierdo ya es un joystick. |
| `puae6_analog_mouse_deadzone` | 0 ... **20** ... 50 (%) | Zona muerta; mas alla la velocidad crece de 0 a la maxima en el borde del stick (se escala el stick entero, asi una diagonal no es mas rapida). |
| `puae6_analog_mouse_speed_left/right` | 0.1 ... **1.0** ... 3.0 | A 100 % un stick a fondo mueve 10 cuentas por unidad. La aritmetica es entera (milesimas de cuenta con resto que se arrastra; se pierde al soltar el stick): igual en todos los hosts. |
| `puae6_mouse_speed` | 10 ... **100** ... 300 (%) | Escala el movimiento del raton del frontend; los restos fraccionarios se arrastran. 100 % es identico al comportamiento anterior. |
| `puae6_turbo_fire` | **disabled**, enabled | Mientras se mantiene el boton turbo se pulsa y suelta su funcion. El hotkey de turbo invierte este ajuste. |
| `puae6_turbo_fire_button` | **B**, A, Y, X, L, R, L2, R2 | Boton que dispara solo (sea lo que sea que tenga mapeado). |
| `puae6_turbo_pulse` | 2, 4, **6** ... 12 | Unidades por ciclo: mitad pulsado, mitad suelto, empezando pulsado; la fase es un contador de unidades emuladas (0 al soltar). |
| `puae6_hotkey_mouse_toggle`, `_turbo_toggle`, `_aspect_toggle`, `_crop_toggle`, `_reset` | **---**, f11, f12, pause, print, scrolllock, numlock, menu, rctrl, rshift | Tecla del teclado fisico que ejecuta la accion y **no llega al Amiga**. `reset` mantiene Ctrl-Amiga-Amiga 40 unidades (el reset suave del teclado). PUAE5 trae RCtrl como cambio joystick/raton; aqui queda sin asignar porque RCtrl es el Ctrl del Amiga. |

El dispositivo `CD32 pad` se selecciona por jugador en el frontend: B/A/X/Y son rojo/azul/verde/amarillo, L/R son retroceso/avance y Start es Play. Jugador 1 usa el puerto Amiga 2 y jugador 2 el 1; para este ultimo, jugador 1 debe dejar libre el raton del puerto Amiga 1.

Dos ratones: selecciona Mouse en ambos jugadores. El primero usa el puerto Amiga 1; el segundo, el 2, y tiene prioridad sobre el pad de jugador 1 con un aviso explicito. `puae6_mouse_speed` escala ambos con fracciones independientes; el stick de jugador 1 puede seguir moviendo su raton con `puae6_analog_mouse`. Los movimientos pendientes y las fracciones viajan en el estado. Esta ampliacion corresponde al desarrollo posterior a la preview del 19 de octubre.

Siguen pendientes `joyport_order`, joystick analogico y lightgun/lightpen (T70). El disco de guardado se crea con `#SAVEDISK` en M3U; el teclado virtual y la barra de estado se describen a continuacion.

## OSD: barra de estado, mensajes y LED (T31b) — categoria `OSD`

Implementado en `adapters/libretro/overlay.h` (fuente 5x7 propia y primitivas de dibujo), `statusbar.h` y `libretro_core.cpp`; comprobado con
`tests/libretro-ui/overlay_probe.cpp` (dibujo sobre imagenes sinteticas) y `test_statusbar.py` (el core completo con el frontend minimo y el floppy guest de `tests/scenarios/disk`).
La barra se dibuja sobre la **copia** de la imagen que recibe el frontend, a partir de: la luz de unidad de la unidad recien ejecutada (`P6_FRAME_DRIVE_LIGHT`), el paquete `p6_input`
que el propio adaptador hizo para esa unidad y la memoria de entrada (bandera de la barra y mensaje de la ultima accion con las unidades que le quedan: viajan en el estado, asi un
rewind o un runahead muestran la misma barra). El estado nativo, el audio y la cadencia no cambian con ninguna de estas opciones.

| Clave | Valores (defecto en negrita) | Efecto |
|---|---|---|
| `puae6_statusbar` | **bottom**, bottom_minimal, bottom_basic, bottom_basic_minimal, top, top_minimal, top_basic, top_basic_minimal | Donde va la barra y cuanto muestra. Full: LED de DF0, los dos puertos Amiga (puerto 1 = raton con sus 3 botones, puerto 2 = joystick con 4 direcciones y 3 botones; encendido lo que se esta enviando), modo raton y turbo del pad, y el mensaje. Basic: LED y mensaje. Minimal: solo el LED. Escala 2 si cabe, si no 1. |
| `puae6_statusbar_startup` | **disabled**, enabled | La barra esta visible al cargar. |
| `puae6_statusbar_messages` | **disabled**, enabled | Con la barra oculta, el mensaje de una accion (joystick/raton, turbo, crop, aspecto, reset) se muestra solo durante 150 unidades emuladas. |
| `puae6_hotkey_statusbar` | **f12**, f11, pause, ... | Tecla que muestra/oculta la barra (F12 es el defecto de PUAE5; el Amiga no tiene F12). Tambien el objetivo `toggle_statusbar` del mapper. |

Interfaz LED (`RETRO_ENVIRONMENT_GET_LED_INTERFACE`): el core llama a `set_led_state` solo cuando un LED cambia, en el orden de PUAE5 (0 power, 1 unidades de disquete, 2 HD/CD/MD,
3 DF0, 4-6 DF1-DF3, 7 HD, 8 CD/MD). La ABI nativa da una unica luz de unidad (el LED de alguna unidad esta encendido; libretro maneja solo DF0), que enciende los LED 1 y 3;
**no se emiten** el LED de power (no esta en la ABI), la pista, DF1-3, HD ni CD. Al cerrar el contenido los LED se apagan. Con runahead, las unidades especulativas tambien llaman (como en
PUAE5); un frontend que lo evita ignora el LED de esas llamadas.

## Teclado virtual (T31a) — categoria `OSD`

Implementado en `adapters/libretro/vkbd.h` (disposicion, navegacion y dibujo) y su paso en `mapper.h` (`vkbd_step`); comprobado con `tests/libretro-ui/vkbd_probe.cpp`
(C++ sobre controles e imagenes sinteticos) y `test_vkbd.py` (el core completo: paquetes, imagen, estado). El teclado virtual es **entrada normal**: las teclas que pulsa el
RetroPad son bits del mismo mapa de teclas de `p6_input` que pone el teclado fisico, asi que la maquina no distingue una tecla virtual de una real y una grabacion de paquetes
lo reproduce. Se dibuja sobre la copia de la imagen (los pixeles de fuera del teclado se oscurecen segun *Dimming*; el estado nativo, el audio y la cadencia no cambian por abrirlo).
Su memoria (abierto, cursor, tecla sujeta, modificadores enganchados, cuenta de repeticion, transparencia) va en la seccion del adaptador del estado (version 6).

Uso: **Select** (por defecto, como en PUAE5; o el boton mapeado a `toggle_vkbd`, o el hotkey) abre y cierra. Abierto, el RetroPad es del teclado y no da nada al joystick ni al mapper
(el raton del frontend y el teclado fisico siguen funcionando): D-Pad mueve el cursor (repite a las 16 unidades y despues cada 4), **B** pulsa la tecla bajo el cursor mientras este abajo, **A** quita/pone la
transparencia, **Y** cambia Caps Lock y **X** pulsa Espacio. Un modificador (Shift, Ctrl, Alt, Amiga) pulsado con B se **engancha** hasta pulsarlo otra vez; Caps Lock es el mismo cerrojo que
el de la tecla Caps Lock del anfitrion. Cerrar suelta todo lo que sujetaba. La run en que se abre o se cierra no cuenta como pulsacion de nada. La disposicion es la del teclado Amiga:
bloque principal, cursores y teclado numerico (96 teclas, todas las de `input_map.h`).

| Clave | Valores (defecto en negrita) | Efecto |
|---|---|---|
| `puae6_vkbd_theme` | **auto**, auto_outline, beige, beige_outline, cd32, cd32_outline, light, light_outline, dark, dark_outline | Colores y estilo de las etiquetas (sombra o contorno). Auto: beige en A500/A600, gris claro en A1200. |
| `puae6_vkbd_transparency` | 0%, **25%**, 50%, 75%, 100% | Cuanto dejan ver las teclas la imagen (100 % solo etiquetas y marcos). **A** la apaga y enciende. |
| `puae6_vkbd_dimming` | 0%, **25%**, 50%, 75%, 100% | Cuanto se oscurece la imagen alrededor del teclado. |
| `puae6_hotkey_vkbd` | **---**, f11, f12, ... | Tecla del teclado fisico que abre/cierra el teclado virtual (no llega al Amiga). |
| `puae6_mapper_select` | **toggle_vkbd** (el resto de botones: ---) | Select abre el teclado por defecto. |

Fuera de T31a: el color del puntero de lightpen/lightgun y la tecla SVDS (save disk) de PUAE5 (necesitan P21 y P12), y las teclas "J/M" del vkbd de PUAE5 (el cambio joystick/raton esta en el
mapper: `toggle_mouse`).

## Modelo automatico y etiquetas del nombre (T76) — categoria `Machine`

`puae6_machine=auto` (el valor por defecto, como `puae_model=auto` de PUAE5) y `puae6_video_standard=PAL auto` / `NTSC auto` (como `PAL auto` / `NTSC auto` de PUAE5) se resuelven **al cargar el contenido y
antes de que exista la maquina**. Implementado en `adapters/libretro/model.h` (`resolve_model`, una funcion pura: sin ficheros, reloj, entorno ni locale), llamado una sola vez desde `retro_load_game` con
las opciones, si el contenido tiene disco duro y la lista de nombres. El resultado es un `p6_config` normal: entra en `config_hash` y en la identidad del estado como cualquier modelo (un estado de un
contenido cuya etiqueta eligio otra maquina u otro estandar se rechaza, `tests/libretro-host/test_model_tags.py`). Headless y BizHawk no la usan: eligen la maquina de forma explicita (flag `--machine`,
sync setting `Machine`); `auto` no existe en el esquema de `p6_config` (`config/schema/options.json`: `libretro.extra_values`).

Opciones (todas `reboot`, no son campos de `p6_config`, salvo las dos primeras que ganan un valor):

| Clave | Valores (primero = defecto) | Que hace |
|---|---|---|
| `puae6_machine` | `auto`, `A500`, `A600`, `A1200`, `A500OG`, `A500PLUS`, `A1000`, `A2000OG`, `A2000`, `CDTV` | `auto` = etiquetas y tipo de contenido; cualquier otro valor es el modelo y **ninguna etiqueta lo cambia**. Un `puae6_preset` distinto de `none` cuenta como modelo elegido. |
| `puae6_video_standard` | `PAL auto`, `NTSC auto`, `PAL`, `NTSC` | `PAL auto` = la etiqueta de region si la hay, si no PAL; `NTSC auto` = igual con NTSC por defecto. `PAL`/`NTSC` fijan el estandar. |
| `puae6_model_fd` | `A500` y los demas modelos | modelo de `auto` para disquetes o sin contenido. El CD directo selecciona CD32 o CDTV segun sus etiquetas, descritas abajo. `puae_model_fd` de PUAE5. |
| `puae6_model_hd` | `A1200` y los demas | modelo de `auto` para contenido **con** disco duro (`.hdf`, `.hdz`, directorio o `.lha` como volumen, solo o con disquetes). `puae_model_hd` de PUAE5; el disco duro pide un Kickstart 2.0 o posterior. |
| `puae6_filename_tags` | `enabled`, `disabled` | `disabled`: no se lee ninguna etiqueta (el modelo sale de las opciones y del tipo de contenido, el estandar de la opcion). |
| `puae6_kickstart` | `auto`, `kick33180.A500` (1.2), `kick34005.A500` (1.3), `kick37175.A500` (2.04), `kick37350.A600` (2.05), `kick40063.A600` (3.1 r40.063), `kick39106.A1200` (3.0), `kick40068.A1200` (3.1 r40.068) | T51, `puae_kickstart` de PUAE5 (mismos valores, sin los A4000 ni `aros`). `auto` busca los nombres del modelo; una version busca solo los nombres de esa version (los de `uae_kickstarts[]` de PUAE5: normal, Amiga Forever, TOSEC `[!]`, TOSEC) y la maquina la ejecuta si el contenido del ROM lo permite: un ROM que pide mas CPU que la maquina (el 3.1 del A1200 en una maquina con 68000) se rechaza con el motivo. No es un campo de `p6_config`: la identidad del firmware es el SHA-256 de sus bytes (un estado tomado con otro Kickstart se rechaza). Un ROM cifrado por Amiga Forever se descifra con el `rom.key` del mismo directorio de sistema, ver `docs/02-contracts.md` ("Kickstart alternativo y rom.key"). |

Un `.uae` como contenido: el modelo y el estandar automaticos se resuelven **con el nombre del propio `.uae`** (no con los discos que nombra) antes de leer sus claves, que se comprueban contra ese modelo y lo
refinan; lo que el fichero nombra (`chipset_compatible`, `chipset`, `puae6_machine`, un preset, `ntsc`) es un modelo o estandar elegido y la etiqueta no lo cambia. Un `.uae` no puede pedir `auto`.

### Que nombres se miran y en que orden

1. La **ruta del contenido** tal como la da el frontend (carpetas incluidas, como PUAE5: `Juegos (A600)/Game.adf` lleva la etiqueta `(A600)`).
2. Cada **disco de la lista**, en el orden de la lista (`.m3u`, o los de un `.uae`), con su ruta.
3. Tras un archivo (`.zip`, `.7z`, `.rp9`), los **nombres de los miembros** que contiene, en el orden de T59.

El orden del archivo, la hora, el directorio de trabajo y el sistema de ficheros no cuentan. La comparacion es byte a byte y distingue mayusculas (`(a1200)` no es una etiqueta).

### Precedencia (fija)

1. **Lo elegido a mano gana**: un modelo o preset distinto de `auto`, un estandar distinto de los `auto`: ninguna etiqueta lo cambia. Las etiquetas se miran igualmente, para que el registro diga que una no se uso
   (`the tag (A1200) in ... is not used: the model is fixed by puae6_machine or puae6_preset`).
2. **Etiquetas fuertes de modelo** (tabla siguiente): se prueban los nombres en orden y **decide el primer nombre que contiene una**; dentro de un nombre gana la primera fila de la tabla (orden de PUAE5: un
   nombre con `(A1200)` y `(A500)` es un A1200). Una etiqueta aun no seleccionable automaticamente (`(A4030)`, `(030)`, `(A4040)`, `(040)`) es un **aviso** y deja el modelo a las opciones: no deja que otra
   etiqueta mas debil elija otra maquina en su lugar.
3. **Palabras de chipset** (`AGA`, `AmigaCD`, `ECS`, `OCS`): solo si ningun nombre tiene una etiqueta fuerte; de nuevo decide el primer nombre que tenga una. Cambian el modelo solo si el modelo del tipo de
   contenido no tiene ya ese chipset (PUAE5): `AGA` -> A1200 si no es ya A1200; `ECS` -> A600 si no es ya A500+, A600 o A2000; `OCS` -> A500 si no es ya A500, A500OG, A500+, A1000 o A2000OG.
4. Si no, el modelo del tipo de contenido: `puae6_model_hd` si hay disco duro, `puae6_model_fd` si no.
5. **Estandar**: el primer nombre con una etiqueta de region decide; dentro de un nombre `NTSC`/`(USA)` gana a `PAL` y a los paises (PUAE5). Sin etiqueta, el de la opcion.

Una palabra suelta (`PAL`, `NTSC`, `AGA`, `ECS`, `OCS`, `AmigaCD`, `CD32`, `CDTV`) no puede tocar una letra o un digito por ningun lado: `PALADIN` no es PAL ni `SAGA` es AGA (el `strstr` de PUAE5 los
encuentra). Es la unica diferencia de lectura con PUAE5; las etiquetas entre parentesis y `_Arcadia` se buscan en cualquier posicion.

### Tabla de etiquetas

| Etiqueta (en el orden de la tabla) | Efecto en PUAE6 | PUAE5 |
|---|---|---|
| `(A4030)`, `(030)`, `(A4040)`, `(040)` | **aviso**, sin efecto automatico: A4030 y A4040 con CPU040/060 se eligen explicitamente mediante opciones o preset | A4000/030 y /040 |
| `(A1200OG)`, `(A1200NF)` | A1200 (68EC020, AGA, 2 MiB Chip, sin Fast RAM) | A1200OG |
| `(A1200)`, `(CD32)`, `(CD32NF)`, `(CD32FR)`, `CD32` | A1200 **con 8 MiB de Fast RAM**: el preset `a1200_fast8` (solo toma la memoria que las opciones dejan en su valor por defecto; `puae6_fast_size=MB4` se queda) | A1200 (y `CD32` en contenido de disquete) |
| `(A600)` | A600 con 2 MiB de Chip y 8 MiB de Fast: el preset `a600_puae5` (misma regla; el Kickstart es el del A600 de este core, 2.05, no el 3.1 de PUAE5) | A600 |
| `(A500+)`, `(A500PLUS)` | A500PLUS | A500PLUS |
| `(A500OG)`, `(512K)`, `(512KB)` | A500OG | A500OG |
| `(A500)` | A500 | A500 |
| `(A2000OG)`, `(A2000)`, `(A1000)` | A2000OG, A2000, A1000 (**extension de PUAE6**: PUAE5 no tiene etiqueta para ellos) | (ninguna) |
| `AGA`, `AmigaCD` / `ECS` / `OCS` | palabras de chipset, regla 3 | igual |
| `NTSC`, `(USA)` | NTSC si `puae6_video_standard` es `PAL auto` o `NTSC auto` | igual (`NTSC`, `(USA)`) |
| `PAL`, `(Europe)`, `(France)`, `(Germany)`, `(Italy)`, `(Spain)`, `(Finland)`, `(Denmark)`, `(Sweden)` | PAL, con la misma condicion | igual |
| `CDTV` | CDTV en CD directo; **aviso** en disquetes/discos duros | CDTV (solo con CD) |
| `(MD)` | monta discos sucesivos en unidades separadas; Auto habilita DF0..DF3, un numero explicito limita el montaje | MultiDrive |
| `_Arcadia` | **aviso**: no hay pad Arcadia | pad Arcadia |
| `(CE)` | fuerza precision de CPU exacta al ciclo, incluso si el menu eligio compatible o normal | fuerza CE |

Con File Name Tags activado, `(CE)` se aplica tambien despues de las claves de un `.uae`, como en PUAE5.

En disquetes, la memoria de las etiquetas va con el modelo: no hay etiquetas independientes de memoria, FPU ni velocidad; son `puae6_chip_size`, `puae6_slow_size`, `puae6_fast_size`, `puae6_fpu_model` y `puae6_floppy_speed`. Lo que
una etiqueta no puede honrar **nunca** falla en silencio ni elige otra cosa: se ignora con un aviso en pantalla y en el registro (una sola vez por etiqueta, aunque la tengan varios nombres).

### Playlists y cambio de CD en libretro

Un CD directo o `.m3u` de 1..16 CDs ofrece Disk Control. La playlist conserva
orden, rutas y etiquetas; aliases con los mismos bytes comparten un disco del
catalogo fijo. No mezcla CD con disquetes ni `#SAVEDISK`. Seleccionar exige
expulsar primero; el cambio se aplica al comenzar el siguiente `retro_run`.
Añadir, reemplazar o eliminar entradas devuelve false: recargar otro conjunto
requiere una sesion nueva. Los estados restauran seleccion, ausencia y peticiones
pendientes; el mismo conjunto renombrado se admite, pero otro orden, contenido o
mapa de aliases se rechaza. BizHawk ofrece tambien catalogo fijo, seleccion y
ausencia mediante controles, conservados en estados y registro/replay TAS.

### CD directo: CD32, CD32FR y CDTV

Un CD directo con `puae6_machine=auto` parte de CD32, independientemente de
`puae6_model_fd` y `puae6_model_hd`. Con File Name Tags activado, su tabla propia
elige la primera coincidencia en este orden dentro de cada nombre:

| Etiqueta | Efecto |
|---|---|
| palabra `CDTV` | CDTV stock, antes de las etiquetas siguientes |
| `(CD32FR)`, palabra `FastRAM` | CD32 con 8 MiB de Fast RAM cuando `puae6_fast_size` es Auto |
| `(CD32)`, `(CD32NF)`, palabra `CD32` | CD32 stock cuando la memoria es Auto |

Las opciones explicitas ganan: `puae6_machine=CD32` ignora las etiquetas de modelo;
el preset `cd32_fast8` conserva sus 8 MiB incluso con `(CD32NF)`. Un modelo
explicito incompatible con CD se rechaza. Una memoria distinta de Auto conserva
su valor: None desactiva Fast, MB8 activa la placa y MB4 sigue siendo unsupported
en CD32. Con File Name Tags desactivado, Auto elige stock salvo memoria o preset
explicitos. Las etiquetas de region conservan la precedencia anterior y `FastRAM`
respeta los mismos limites de palabra y mayusculas. En disquetes, las etiquetas
CD32 siguen eligiendo A1200 con Fast; esta tabla solo se usa para CD directo.

### Que se dice y donde

El registro (`RETRO_LOG_INFO`) dice siempre el modelo y el estandar elegidos y por que (`model A600 (puae6_machine=auto): the tag (A600) in ... names the A600 with ...`, `NTSC (puae6_video_standard auto): ...`)
y despues `machine A600 (puae6_machine)`. Los avisos tambien salen como mensaje en pantalla. Cambiar una de estas opciones en marcha se avisa y se aplica al recargar, como el resto de las `reboot`.

## Importador `.uae` (cierre de T31) — contenido `.uae`

Implementado en `adapters/libretro/config_import.h/.cpp` (analizador y traduccion, sin motor ni ABI) y en `libretro_core.cpp` (lectura por la VFS y aplicacion). Comprobado con
`tests/libretro-ui/uae_probe.cpp` (C++ con ASan/UBSan sobre ficheros sinteticos, incluida una mutacion con semilla) y `test_uae.py` (el core completo con el frontend minimo).

PUAE5 acepta cualquier `.uae` como contenido (`core.c:7125-7260`) y por eso puede arrancar una maquina para la que este proyecto no tiene perfil. Aqui un `.uae` es una **lista de claves
del esquema comun** mas **rutas de disquete**: el importador lee una *allowlist* y nunca devuelve un exito que ignore algo que cambiaria la maquina. La opcion `uae_config_file` sigue
en `unsupported` del esquema (no es una clave de `p6_config`: ni headless ni el wrapper de BizHawk importan `.uae`); la extension `uae` solo la anuncia el core libretro.

### Politica

* **Se acepta y se traduce** lo que el esquema tiene (modelo, memoria, FPU, estandar de video, frecuencia de audio, proteccion de escritura) y los discos.
* **Se comprueba** lo que solo describe la maquina que el perfil ya es (chipset, CPU, ciclo exacto, numero de unidades): un valor que no coincide **se rechaza**, uno que coincide no hace nada.
* **Aviso** (el fichero carga): claves de ventana/entrada del anfitrion (`gfx_*`, `win32.*`, `input.*`, `joyport*`, `mouse_*`, ...). No cambian la maquina; el core registra una linea por clave y un
  mensaje en pantalla con su numero. Los metadatos (`config_*`) se aceptan sin aviso.
* **Se rechaza** lo que cambia la maquina y no cabe en el esquema (directorios y discos duros del anfitrion, CD, Kickstart por ruta, SCSI, RTG, JIT, placas, redes, rutas absolutas) **y toda clave
  desconocida**: no se puede saber que una clave desconocida deje la maquina como esta, y un `.uae` con una clave ignorada en silencio es como dos sesiones divergen. La carga falla con la clave y la linea
  (`line 12 (filesystem2): not supported: ...`); el log lleva **todos** los problemas (hasta 32; el resto se cuenta) y la pantalla los tres primeros.
* Una clave de ruta (`filesystem*`, `hardfile*`, `uaehf*`, `cdimage*`, `kickstart*`, `cart*`, `flash_file`) **sin valor** no monta nada y no se rechaza.
* Las claves, los valores booleanos (`true/false/yes/no`) y los valores de las claves de WinUAE no distinguen mayusculas; los nombres de valor de las claves nativas `puae6_*` (`MB4`, `A1200`) van tal cual los del menu.

### Precedencia (determinista)

De menor a mayor: **1.** defaults del esquema; **2.** opciones del frontend (su `puae6_preset` ya expandido sobre modelo y memoria, como hace `read_options`); **3.** el preset que nombre el fichero
(`puae6_preset=`); **4.** las claves del fichero. El orden de las lineas no importa; dos claves que fijan la misma opcion (`chipset_compatible` y `puae6_machine`, o una clave repetida) se rechazan,
porque ganaria la que apareciese despues. Una opcion que el fichero no nombra conserva el valor del frontend. **Las opciones de presentacion, entrada y OSD nunca se leen del `.uae`** (las del
menu, `puae6_video_*`, `puae6_mapper_*`, ... se rechazan con ese motivo): el fichero solo escribe las opciones `reboot` del hash de configuracion. Si el fichero no nombra maquina, preset ni
`chipset_compatible`, el chipset (`ocs`/`ecs`/`aga`) fija el modelo (A500/A600/A1200; `ecs_agnus` solo comprueba el A2000 que nombra `chipset_compatible`); sin ninguno de ellos manda el modelo del frontend y `cpu_model`/`chipset` se comprueban contra el.
Para el aviso "cambia en el menu" (`refresh_options`) el core compara con lo que el *frontend* tiene, no con lo que impuso el fichero: cambiar solo una opcion de presentacion no avisa de nada.

El resultado es el mismo `p6_config` (y el mismo `config_hash` y estado guardado) que las opciones equivalentes: `test_uae.py` carga el fichero y, en otro proceso, las opciones, y compara
imagenes, audio y el estado guardado unidad por unidad.

### Texto y limites

`clave=valor` por linea; se recortan espacios y tabuladores alrededor de clave y valor; un par de comillas dobles alrededor del valor se quita; `;` y `#` al principio de linea son comentarios
(no hay comentarios al final de linea); acepta BOM UTF-8 y fines de linea LF, CRLF y CR. Sin `include`, sin variables de entorno ni `$(...)` (una ruta con ellas se rechaza), sin plugins ni shell.

| Limite | Valor |
|---|---|
| Tamano del fichero | 128 KiB (`kMaxUaeBytes`; la lectura nunca guarda mas de 128 KiB + 1) |
| Longitud de una linea | 4096 bytes |
| Lineas | 4096 |
| Problemas y avisos conservados | 32 de cada (el resto se cuenta) |
| Discos | los de `P6_MAX_MEDIA` (16) |
| Caracteres | sin NUL ni controles (salvo tabulador); claves con letras, digitos, `_`, `.`, `-` |

Las rutas de disco son **relativas a la carpeta del `.uae`**, se pasan tal cual a la VFS (el adaptador no normaliza, como con un `.m3u`; una barra invertida no se convierte) y se rechazan si son
absolutas (`/`, `\`, `C:`, `esquema://`), empiezan por `~`, llevan `$(`/`${` o tienen un componente `..`. Los discos que nombra el fichero son la lista de Disk Control en este orden: `floppy0`, luego
`diskimage0` ... `diskimage15` por numero (la lista de intercambio de WinUAE; el que repite el archivo de `floppy0` no se anade otra vez). Sin discos, el Kickstart arranca solo.

### Claves aceptadas (traducidas o comprobadas)

| Clave | Que hace |
|---|---|
| `chipset_compatible` | `A500`, `A500+`, `A600`, `A1000`, `A1200`, `A2000`, `A4000` -> `puae6_machine` (T50: `A500+` es el A500PLUS; `A2000` es el A2000OG con `chipset=ocs` y el A2000 con `chipset=ecs_agnus` o sin linea de chipset). `-` y `Generic` no nombran maquina. Cualquier otra (`A4000`, `CD32`, ...) se rechaza. El A500OG solo se elige con `puae6_machine=A500OG`: ningun `.uae` lo distingue del A500 sin cambiar la memoria. |
| `chipset` | `ocs`/`ecs`/`aga`: si el fichero no nombra maquina la fija (A500/A600/A1200); si la nombra, debe ser la suya (`A2000`: `ocs` o `ecs_agnus`). `ecs_agnus` solo, sin `chipset_compatible=A2000`, y `ecs_denise` se rechazan. |
| `cpu_model` | `68000` o `68010` en las maquinas de 68000 (A500, A500OG, A500PLUS, A600, A1000, A2000OG, A2000), `68020` en el A1200 (su 68EC020), `68030` en A4030 (precision Normal, sin MMU) -> `puae6_cpu_model` (T52). Otro valor, o una CPU que la maquina no puede tener, se rechaza. |
| `cpu_multiplier` | `1`, `2`, `4`, `8`, `10`, `12`, `16` (veces 3,55 MHz, como PUAE5) -> `puae6_cpu_multiplier`; `0` es el de la maquina (`auto`). Otro numero se rechaza (T52). |
| `fpu_model` | `0` -> `puae6_fpu_model=None`, `68881`, `68882`. `68040`/`68060` se rechazan. |
| `chipmem_size` | Unidades de 512 KiB (como WinUAE): `1`=512 KiB, `2`=1 MiB, `4`=2 MiB -> `puae6_chip_size`. `-1` (128 KiB), `0` (256 KiB) y lo que no ofrece el core se rechazan. |
| `bogomem_size` | Unidades de 256 KiB: `0`=ninguna (`None`), `2`=512 KiB, `4`, `6`, `7` -> `puae6_slow_size`. |
| `fastmem_size`, `fastmem_size_k` | MiB (o KiB): `0`=ninguna, `1`, `2`, `4`, `8` MiB -> `puae6_fast_size`. |
| `sound_frequency` | `44100`/`48000` -> `puae6_audio_rate`. |
| `sound_stereo_separation` | `0` a `10` (decimas de 100 %, como WinUAE) -> `puae6_sound_stereo_separation` `0`..`100`. `7` es el 70 % del perfil. Fuera de rango se rechaza. |
| `sound_interpol` | `none`, `anti`, `rh`, `crux` -> `puae6_sound_interpolation`. `sinc` se rechaza (su cola no esta en el estado nativo). |
| `sound_filter` | `off`, `emulated`, `on` -> `puae6_sound_filter` (`emulated`, `off`, `on`). `fixedonly` se rechaza. |
| `sound_filter_type` | `standard` (modelo A500) y `enhanced` (modelo A1200) -> `puae6_sound_filter_type` `A500`/`A1200`. |
| `floppy_speed` | Porcentaje de la unidad real (T62): `100`, `200`, `400`, `800` -> `puae6_floppy_speed` con el mismo valor; `0` es el turbo (`turbo`). Otro numero se rechaza. |
| `gfx_resolution` | `lores`, `hires`, `superhires` -> `puae6_video_resolution` (T72: el `gfx_resolution` del motor; el defecto del perfil es `hires`). |
| `gfx_linemode` | `none` -> `puae6_video_line_mode=single`, `double` -> `double` (T72). `scanlines*` y `double2`/`double3` se rechazan: son efectos de la pantalla del anfitrion. |
| `ntsc` | `true` -> NTSC, `false` -> PAL (`puae6_video_standard`). |
| `floppy_write_protect` | `true` -> `puae6_floppy_writes=disabled`, `false` -> `enabled`. |
| `floppy0` | El disco de DF0 (ruta relativa). Vacio = ninguno. |
| `floppy1`, `floppy2`, `floppy3` | Solo vacios: libretro maneja DF0 y un disco no se puede dejar en otra unidad en silencio. Con valor: se rechaza. |
| `diskimage0` ... `diskimage15` | Discos de la lista de intercambio, tras `floppy0`. |
| `puae6_machine`, `puae6_video_standard`, `puae6_chip_size`, `puae6_slow_size`, `puae6_fast_size`, `puae6_fpu_model`, `puae6_audio_rate`, `puae6_floppy_writes`, `puae6_sound_stereo_separation`, `puae6_sound_interpolation`, `puae6_sound_filter`, `puae6_sound_filter_type`, `puae6_floppy_speed`, `puae6_floppy_drives`, `puae6_video_resolution`, `puae6_video_line_mode`, `puae6_cpu_model`, `puae6_cpu_multiplier`, `puae6_cpu_compatibility`, `puae6_hdf_controller` | Grafia nativa de las opciones `reboot` del menu (valores tal cual: `MB4`, `A1200`, `None`). `puae6_machine=auto` y `puae6_video_standard=PAL auto` / `NTSC auto` se rechazan (T76): son la eleccion automatica del frontend, que el core resuelve con el nombre del `.uae` antes de leer sus claves; un fichero nombra la maquina y el estandar. |
| `puae6_preset` | `none`, `a1200_fast8`, `a600_puae5`, `a4030_normal`, `cd32_fast8`: preset del esquema, que las demas claves refinan. |

Las flags `cpu_cycle_exact`, `cpu_memory_cycle_exact`, `blitter_cycle_exact` y `cycle_exact` deben ser `true` en exact y `false` en compatible/normal; `cpu_compatible` es `true` en exact/compatible y `false` en normal. Describen el nivel final de `puae6_cpu_compatibility`, cualquiera que sea el orden de las claves; una contradiccion se rechaza.

Valores que la maquina ya tiene (se aceptan solo con este valor, o no hacen nada):
`cpu_24bit_addressing`, `cpu_address_space_24` = `false` en A4030, `true` en los demas; `cpu_speed` = `real`; `cpu_throttle` = `0`; `cachesize` = `0`; `compfpu` = `false`; `mmu_model` = `0`; `immediate_blits` = `false`;
`waiting_blits` = `disabled`; `synchronize_clock`, `bsdsocket_emu`, `scsi` = `false`; `nr_floppies` debe coincidir con `puae6_floppy_drives` (Auto conecta dos, o cuatro con MultiDrive/`(MD)`);
`floppy0type`, `floppy1type`, `floppy2type`, `floppy3type` deben ser `0` en unidades conectadas y `-1` en desconectadas. Las claves descriptivas se comprueban despues de las opciones, sin depender de su orden.
`floppy0wp`, `floppy1wp`, `floppy2wp`, `floppy3wp` = `false` (la proteccion es un solo interruptor: `floppy_write_protect`). Cualquier contradiccion se rechaza.

### Claves rechazadas (y por que)

| Clave | Motivo |
|---|---|
| `filesystem*`, `hardfile*`, `uaehf*` | Directorios y discos duros del anfitrion no se montan desde una configuracion (el perfil determinista no tiene sistema de ficheros del anfitrion; los discos duros son medios que se le dan al core, T29). |
| `cdimage*` | No hay unidad de CD. |
| `kickstart*`, `cart*`, `flash_file` | El Kickstart se busca en el directorio de sistema por los nombres del modelo; nunca por una ruta de la configuracion. |
| `gfxcard*`, `rtg*`, `picasso96*`, `uaegfx*` | No hay placa RTG. |
| `comp_*` (y `cachesize`, `compfpu` distintos de 0/`false`) | El perfil determinista no tiene JIT. |
| `scsi_*`, `scsi=true` | No hay controlador SCSI. |
| `cpuboard*`, `ppc*`, `a2065*`, `a2091*`, `a4091*`, `catweasel*`, `toccata*`, `x86*`, `uaeboard*`, `serial_*`, `parallel_*`, `midi*`, `sampler*`, `genlock*` | Placas de expansion y dispositivos del anfitrion no forman parte de los perfiles. |
| `bsdsocket*`, `uaenet*` | Sin red en el perfil determinista. |
| `pcmcia*` | Sin PCMCIA (Gayle, en el A600 y el A1200, solo lleva su puerto IDE: opcion `puae6_hdf_controller`, T56). |
| `include*`, `plugin*`, `shell*`, `exec*`, `script*`, `cmd*`, `command*` | Un `.uae` no incluye otro, ni carga plugins ni ejecuta programas. |
| `z3mem*`, `z3fastmem*`, `mem25bit*`, `a3000mem*`, `mbresmem*`, `megachipmem*` | El importador aun no expone Z3 ni RAM de placa base; solo acepta `0`. Z3 se elige con la opcion propia descrita arriba. |
| `fastmem2*`, `fastmem3*`, `fastmem4*` | Una sola placa Fast Zorro II; las demas, solo `0`. |
| `cpu_*`, `fpu_*`, `cs_*`, `chipset_*`, `blitter_*`, `agnus_*`, `denise_*`, `rtc`, `sound_*`, `keyboard_*`, `floppy*` (el resto) | Fijados por el perfil de maquina y no son claves del esquema; el importador no puede saber si el fichero coincide, asi que los rechaza. Incluye `cpu_frequency`, `sound_mixed_stereo_delay` y los volumenes (las cuatro claves de audio de la tabla anterior si se traducen). |
| Cualquier ruta absoluta, `..`, `~`, `$(...)`/`${...}` en un disco | Fuera de la carpeta del `.uae`; el importador no expande nada. |
| Una opcion de presentacion/entrada/OSD del menu (`puae6_video_crop`, `puae6_mapper_*`, ...) | El fichero solo escribe la maquina. |
| Lo desconocido | `unknown key`. |

### Avisos (se ignoran, no cambian la maquina)

`gfx_*`, `win32.*`, `use_gui*` (ventana del anfitrion); `input.*`, `joyport*`, `mouse_*`, `magic_mouse*` (la entrada es del core: Input y RetroPad Mapper); `show_leds*`, `leds_on_screen*` (la barra de
estado es una opcion del core); `kbd_lang*` (la distribucion del teclado es del frontend); `rom_path`, `floppy_path` (no se usan rutas del anfitrion). Metadatos aceptados sin aviso: `config_*`
(`config_description`, `config_version`, `config_hardware`, `config_host`, ...), `z3mem_start`, `z3mapping`.

### Fuera de alcance (abierto)

* Los `puae_libretro_*.uae` del directorio de sistema (overrides por nucleo/contenido de PUAE5): no se leen. Un override es una segunda fuente de configuracion que cambiaria la maquina sin que el
  contenido lo diga; si se quisiera, entraria con este mismo importador y una capa mas en la precedencia.
* `floppy1`-`floppy3` con disco y mas de DF0: dependen de P13 (cuatro unidades, tag MD); `hardfile2`/`uaehf` con HDF, de T29/P15.
* Un `.uae` no se exporta: el core no escribe configuracion.

### Colisiones del invitado

`puae6_collision_level` (`--collisions` en headless, `CollisionLevel` en BizHawk)
requiere reiniciar: `auto` y `full` conservan todas las colisiones del Amiga,
`none` las desactiva, `sprites` calcula las de sprites entre si y `playfields`
anade sprites contra bitplanes. Reducirlas puede romper juegos que leen CLXDAT.
El importador `.uae` traduce `collision_level=none|sprites|playfields|full`.
Auto y Full comparten identidad de estados y peliculas; los otros niveles son
incompatibles entre si.


### CDTV y cambio de CD (T67c3b, T67c3e, T67d2, T67d4)

`puae6_machine=CDTV` permite un catalogo fijo de hasta16 CD o ningun contenido, 68000 exacto,
ECS Agnus y 1 MiB Chip. Automatic con etiqueta CDTV en un CD lo selecciona antes
de CD32FR/FastRAM; modelo/preset explicito prevalece. Sin etiquetas, CD usa CD32;
las etiquetas CDTV de disquetes mantienen su aviso y no eligen este modelo.
Requiere KS1.3 `kick34005.A500` y EXT2.7 ID22: `kick34005.CDTV`,
`amiga-os-130-cdtv-ext.rom` o `CDTV-Extended-ROMv2.7.rom`, 256 KiB cada ROM.
Los nombres solo localizan; contenido/version se validan y no aceptan EXT1.0.
Libretro permite M3U de CD y cambio mediante eject/index/insert. La nueva
insercion espera a que el controlador observe ausencia, en tiempo emulado;
el estado conserva tambien un cambio pendiente. No acepta ADF/HDF,
Kickstart alternativo, #SAVEDISK ni add/remove/replace. Headless `--machine cdtv` recibe EXT+MAIN
combinados y no carga un CD. BizHawk ofrece `Machine=CDTV` con el mismo perfil fijo y pide `Amiga/CDTV`
(KS1.3 ID6) y `Amiga/CDTVext27` (EXT2.7 ID22), 256 KiB cada una; valida los
bytes y copia EXT+MAIN. La entrada `CDTVext` legacy conserva su seleccion previa. La referencia MacHawk
`UAE.ISettable.cs` usa `quickstart=CDTV,0` y el Kickstart A500; PUAE6 conserva
esa geometria y fija su pareja 1.3/2.7 ya validada por la API nativa.
Un ISO/CUE directo identificado como Amiga o un XML `System="Amiga"` usa PUAE6
cuando se elige ese core; el disco no cambia automaticamente el modelo a CDTV.
Se admite un catalogo fijo de hasta 16 slots con aliases, o ningun contenido, sin disquetes, HDF,
volumenes, expansiones ni cartuchos. Con CD hay botones de seleccion, expulsion e insercion.
La insercion espera a la ausencia observada por el controlador; seleccionar otro slot no cambia
una solicitud pendiente, Insert la sustituye y Eject la cancela. Sin contenido esos botones
no existen; Next Drive tampoco existe para CDTV. Inyectar controles ausentes se rechaza antes
de avanzar o cambiar el estado.
Los estados conservan identidad de firmware, modelo, opciones y CD, con restauracion
fria en sandbox y proceso nuevos. No se ofrece SaveRAM para este perfil: eso no
acredita persistencia de bateria ni peliculas CDTV, aun pendientes.
