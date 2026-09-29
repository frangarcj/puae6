# T06cd — dispositivos logicos de entrada

Resuelve los 14 simbolos de `input_device_backends` con implementaciones reales en
`ports/portable/input_devices.cpp` (sin API del sistema, sin estado en ejecucion, sin
asignacion): tablas `inputdevicefunc_*`, mapeos por defecto (`input_get_default_*`),
`target_inputdevice_(un)acquire`, `target_checkcapslock`, `release_keys`, `is_tablet` y
`tablet_log`. Diseñado por un agente sobre la referencia de DirectInput de od-win32 y
revisado aqui.

## Dispositivos

Un teclado (128 widgets = scancodes Amiga en orden), un raton (ejes X/Y relativos, 3
botones) y dos joysticks (ejes digitales X/Y, 3 botones); el joystick n sirve el puerto
n. La tabla de teclas sale en tiempo de compilacion de las filas DEFEVENTKB de
`inputevents.def`: filtrar `events[]` por el prefijo "KEY_" tambien casaria `KEY_START`.
Un solo raton, porque `inputdevice_validate_jports` rechaza el mismo dispositivo en dos
puertos.

## Hallazgos que importan

- El `init()` del teclado debe llamar a `inputdevice_setkeytranslation`: nada mas lo hace
  en este perfil y sin ello `inputdevice_default_prefs` desreferencia NULL (reproducido).
- Caps Lock: `ID_FLAG_TOGGLE` se elimina y `target_checkcapslock` devuelve 0, de modo que
  el bit 0x62 del mapa de teclas ES el latch; asi no hay estado oculto fuera de snapshots.
- `input_get_default_*` rechaza puertos 2-3 y modos CD32/CDTV/lightpen devolviendo 0 sin
  tocar nada, pero el motor ignora el retorno: la config publica debe rechazarlos
  (manifests/config-invariants.json).
- Con 0056/0057, `target_can_autoswitchdevice`, `is_touch_lightpen`, `key_swap_hack` y
  los tres de teclado en pantalla/pegar ya no se referencian; el agente los dejo en una
  seccion marcada que se elimino aqui.

## Pruebas (tests/host/test_input_devices.py)

Sonda de tablas y mapeos (O0/O2, ASan/UBSan; el agente comprobo que 9 mutantes de la
fuente la hacen fallar) y un arnes que enlaza el `inputdevice.cpp` y `keybuf.cpp`
REALES y parcheados con los proveedores: prefs por defecto, cuatro disposiciones de
puertos (raton/joy, joy/raton, joy/joy, ninguno), 128 teclas directas y por tabla,
Caps Lock, cadena de reset Ctrl+Amiga+Amiga, adquisicion y cambio de dispositivo. El
arnes usa `-undefined dynamic_lookup` (solo Mach-O): una llamada fuera de lo cubierto
falla en ejecucion.

## Para T10 (no hecho aqui)

Lag en lecturas del invitado (JOYxDAT, POTGOR, lineas de fuego del CIA, get_next_key);
orden de teclas por unidad (keybuf guarda 256 y descarta al desbordar); dueño del latch
de Caps Lock; teclas mantenidas durante un reset; estado fuera de snapshots (`static float
fract` de `setmousestate`, `mouse_axis[]`, `keybuf[256]`).

La puerta de imports baja de 29 a 15 simbolos (solo video).
