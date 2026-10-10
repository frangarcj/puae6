# Opciones del core libretro (UX de T31)

La fuente unica de las opciones es `config/schema/options.json`; `python3 tools/generate_options.py` genera de ella
`adapters/libretro/options_generated.h` (lo que ve el frontend) y `config/schema/options_table.generated.json` (lo que
deben respetar headless y el wrapper BizHawk). Este documento explica las opciones de la **interfaz de usuario libretro**
(T31a-T31d); las opciones de maquina (modelo, memoria, estandar de video, disquetes) estan en el esquema y en
`docs/02-contracts.md`.

## Clases y la frontera con la emulacion

| Clase | Que es | Entra en `p6_config`, `config_hash` y estado | Cuando se aplica |
|---|---|---|---|
| `reboot` | parte de la maquina (modelo, RAM, estandar, audio, escritura de disquetes) | si | al cargar el contenido; un cambio en marcha solo se avisa |
| `presentation` | solo cambia lo que el adaptador muestra o como lee la entrada del frontend | **no** | de inmediato |
| `presentation` + `restart` | igual, pero el adaptador solo puede leerla al cargar (formato de pixel, modo inicial del D-Pad) | **no** | al recargar; el menu la marca `(Restart)` y un cambio en marcha se avisa |

Regla de diseno (ficha T31): ninguna opcion de esta pagina cambia la maquina. Lo que hacen es:

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

Diferencias deliberadas con PUAE5: sin `physicalmouse=double`, sin `joyport_order` ni dispositivos CD32/analogico/lightgun (necesitan puertos nuevos en la ABI, tareas P21/P22 aparte),
sin `save_disk` (depende de P12), y el teclado virtual / la barra de estado son T31a / T31b.
