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
