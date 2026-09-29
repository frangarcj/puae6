# T06ce — frontera de presentacion de video

`ports/portable/video_present.{h,cpp}` define los 14 simbolos de presentacion del
renderer con una implementacion real: un unico buffer XRGB8888 estatico de 1824x626
(stride fijo 7296, 4,36 MiB en BSS, puesto a cero al iniciar), determinista y sin
consultar pantalla, refresco ni reloj. Diseñado por un agente a partir de la
aritmetica de salida del propio motor (`custom.cpp compute_framesync`) y de la
referencia de od-win32/libretro, revisado aqui.

## Lo que el motor compilado hace (cambia el plan)

- Nadie llama a `render_screen` ni a `show_screen` salvo `full_redraw_all` (codigo
  muerto): el traspaso de cada unidad lo hace el bridge con `p6_video_present()`.
- `graphics_setup` y `graphics_init` no se llaman: `p6_video_init()` los sustituye y debe
  ejecutarse tras `custom_init`/`init_m68k` y antes del primer reset del m68k.
- Un modo mayor que el buffer se rechaza antes de escribir nada (`lockscr` falla,
  `p6_video_status()` lo informa y se encola una linea de log).
- `gfxboard_refresh` solo se llama con `picasso_on`, que jamas se activa sin tarjetas
  graficas: parche 0058 con `PICASSO96`.

## Trampa de configuracion

Upstream deja `gfx_autoresolution_vga = true` en `default_prefs`; `p6_video_init` lo
rechaza (`P6_VIDEO_CFG_AUTORESOLUTION`), asi que el preset de T08/T13 debe ponerlo a
false. Añadido, con el resto de opciones de video, a `manifests/config-invariants.json`.

## Enlace completo

Con todo esto la puerta de imports llego a 0 simbolos sin resolver, y el enlace de la
dylib de prueba seguia fallando por tres definiciones duplicadas (`gui_data`, `quit_to_gui`,
`no_gui`) en main.cpp y en `gui.cpp` del port. El parche 0059 deja el estado al port. Desde
ahora la puerta exige enlace limpio y prohibe duplicados.

## Pruebas

`tests/host/test_video_present.py`: sonda (O0/O2, ASan/UBSan) con el `gfxutil.cpp` real y
`diagnostics.cpp` del port: secuencias lock/unlock/render/show, orden de bytes de un pixel
XRGB, contadores de fotograma y pasada, rectangulo activo, rechazo de modos grandes e
idempotencia (el agente comprobo que 32 de 32 rupturas deliberadas del proveedor la
hacen fallar), y el objeto sin importes de reloj, locale, hilos ni asignacion.

## Para T08 (no hecho aqui)

Recorte y aspecto (el rectangulo activo es todo el raster), campos entrelazados
(`gfx_iscanlines` vale 1 por defecto), pruebas HAM/EHB con micro-ROM, genlock sin efecto
visible, medir el maximo real de capacidad, politica de descarte y byte X, y que devuelve
`p6_run_frame` cuando una unidad acaba sin nueva pasada de dibujo. `init_row_map` conserva
cachés estaticas (drawing.cpp:1309-1327) que sobreviven a unload/load: forman parte del
inventario de re-inicializacion (T13/T15).

## Limite

`drawing.cpp` no se ha ejecutado contra el proveedor: la sonda reproduce su protocolo
observable de bloqueo y desbloqueo. Eso lo prueba el arranque real (T08).
