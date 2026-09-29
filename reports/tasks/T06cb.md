# T06cb — maquinaria .uss y servicios de escritorio fuera del enlace

Reparto en paralelo entre dos agentes (savestate.cpp por un lado, ocho unidades por
otro) sobre copias del scratchpad, mas una tercera tanda propia para los restos;
diffs y decisiones revisados antes de integrar. Parches 0055 (savestate.cpp),
0056 (inputdevice, disk, rommgr, main, custom, newcpu, cfgfile, memory) y 0057
(inputdevice, keybuf). La puerta de imports baja de 155 a 29 simbolos.

## Cambios con logica (no solo guardas)

- `savestate_check` devuelve false en el perfil: las ramas DORESTORE/DOREWIND/SAVE
  quedan fuera porque sin restaurador dejarian `savestate_state` en RESTORE/REWIND.
- rommgr: los ids logicos de firmware se comparan con igualdad exacta
  (`my_issamepath` -> `_tcscmp`); la identificacion por nombre de fichero se excluye
  (aceptaba contenido sin hash); el aviso de ROM necesaria es una cadena fija en ingles
  que va a la cola de diagnosticos (no hay tabla de recursos de escritorio).
- inputdevice: se guardan todas las acciones AKS de GUI/pantalla/volumen/RTG/captura/
  estado/teclado en pantalla/pegar; se conservan reset, eject, cambio de puertos,
  config de entrada, IRQ7, disk swapper, refresco, quit/restart y rewind.
  `setsystime` y `tablet_lightpen`/`mouseedge`/`switchdevice` quedan fuera;
  `needcputrace` devuelve false.
- `uae_restart` equivale a `uae_quit` para el bridge (`quit_program == -UAE_QUIT`).

## Efectos a vigilar

- La config publica debe exigir `tod_hack = 0` y no exponer AKS_STATE* ni los cambios
  de `savestate_state`; se añadieron al manifiesto de invariantes (T13).
- Un firmware con hash desconocido se rechaza.
- `savestate_dorewind` sigue definida pero inerte (`staterecords` nunca se reserva).

## Tests que cambiaron

`test_disk_source` (24 regiones), `test_savestate_source` (los simbolos del .uss ya no
existen), `test_main_source` (las funciones de ciclo de vida se comparan con el original
tras quitar guardas y comentarios) y los probes `cpu_irq`/`custom_cia`, que ahora usan
la definicion real de `uae_int_requested` del port.

## Estado

Host 57 modulos/92 tests y codegen 27 tests en verde. Cola: 57 parches, 30 archivos,
1868 lineas (ADR 0004). Quedan 29 simbolos: video (T06ce) y dispositivos de entrada
(T06cd).
