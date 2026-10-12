# WHDLoad en PUAE6 (T30)

Estado: flujo OFFLINE reproducible. Una instalacion WHDLoad (el directorio que deja su instalador, o el `.lha` del
autor) se convierte, antes de cargar nada, en UNA imagen de disco duro (`.hdf`) y un manifiesto; el emulador solo
recibe esa imagen. No es la UX de PUAE5 (ver "Lo que no replica").

## Que hay

| Pieza | Donde |
| --- | --- |
| Preparacion (CLI) | `tools/prepare_whdload.py` (`prepare`, `identify`, `inspect`) |
| Decodificador LHA (lh0/lh5/lh6/lh7, cabeceras 0/1/2, con cotas) | `tools/whdload/lha.py` |
| Escritor/lector FFS determinista | `tools/whdload/ffs.py` |
| Manifiesto de instalacion | `config/whdload/*.json` (hoy `fatal-mission.json`) |
| Licencias de lo capturado y de las entradas | `manifests/licenses.json` (`machawk-whdload-*`, `user_supplied_inputs`) |
| Pruebas | `tests/whdload/` |

## Como funciona

`python3 tools/prepare_whdload.py prepare config/whdload/fatal-mission.json --input whdload=WHDLoad_usr.lha --input game=FatalMission.lha --out DIR`
(o las variables `P6_WHDLOAD_LHA` y `P6_FATALMISSION_LHA`) escribe `DIR/fatal-mission.hdf` y `DIR/fatal-mission.manifest.json`.

* El config fija TODO lo que entra: cada fichero con su SHA-256 (si el de la entrada no coincide, la preparacion se
  para y nombra los dos hashes: es otra version del instalador o del paquete), el texto de la `S:Startup-Sequence` y
  de `S:WHDLoad.prefs` (generados, nunca los del juego), el nombre del volumen, el DosType (`DOS1`/`DOS3`/`DOS5`),
  el sello de tiempo, el tamano minimo y, si se quiere, `image_sha256` (la imagen ya identificada: otra que no la
  produzca es un error).
* Salida determinista: miembros ordenados por los bytes de su nombre, sellos fijos del config, sin rutas, usuario,
  reloj ni orden del sistema de ficheros. Mismas entradas y mismo config dan los mismos bytes y el mismo manifiesto
  (se prueba desde otros directorios, con otras fechas de fichero y con la entrada como directorio o como LHA).
* Nunca se ejecuta nada de una entrada: el LHA se decodifica en Python puro (limites: 4096 miembros, 64 MiB por
  miembro, 128 MiB en total, nombres solo relativos y sin `..`, `:` ni control, CRC-16 de cada miembro); un directorio
  de entrada no sigue enlaces. Un script del juego es un dato: lo que arranca lo decide el config.
* El volumen es una particion FFS plana (32 sectores, 1 cabeza, 2 bloques reservados): lo que `P6_MEDIA_HDF` monta
  como DH0 sin RDB (T29a). El replay recibe esos bytes (`p6_media.image`) y nada mas: no hay tipo de medio
  "carpeta", y `p6_headless --hdf CARPETA` falla (tests/whdload/test_replay.py).
* `identify IMAGEN --sha256 HEX` comprueba una imagen hecha por otro y su estructura; `inspect IMAGEN` la lista.

## Lo que falta es visible

* Una entrada que falta: error que nombra la variable de entorno, `--input` y la URL de donde sale; no se escribe nada.
  Con `--allow-missing-assets` se escribe la imagen incompleta con `"complete": false` y la lista `missing`, y el
  proceso sale con 3.
* Un asset del usuario (`user_assets`: el Kickstart y su `.RTB` que un slave pide en `Devs:Kickstarts`): se da con
  `--asset RUTA_EN_EL_VOLUMEN=FICHERO` o la variable del config; solo se comprueba su tamano, su hash NO se
  guarda en ningun manifiesto, y una imagen que lo lleva dice `"redistributable": false`. Ni el Kickstart ni nada
  comercial esta en el repositorio ni en CI publico.
* Una licencia sin entrada en `manifests/licenses.json` (`user_supplied_inputs`) para un fichero del config detiene
  la preparacion. WHDLoad (copyright de Bert Jahn, sin concesion de redistribucion en el paquete) y el juego de
  prueba (Fatal Mission, "Classic PD" segun Aminet) estan marcados `unverified`: la imagen es para quien aporta las
  entradas.

## Como preparar un juego propio

1. Instalar el juego con su instalador WHDLoad en un Amiga/emulador y copiar el directorio resultante (o usar el `.lha`
   de la instalacion si ya incluye los datos; muchas instalaciones piden la imagen del disco original y no los
   incluyen: eso solo lo hace el instalador, que PUAE6 no ejecuta).
2. Copiar `config/whdload/fatal-mission.json`: declarar las entradas (`kind` `dir` o `lha`), cada fichero con su
   SHA-256 (`shasum -a 256`), el `.slave` en `FatalMission/...` y el texto de arranque. El esquema se valida
   estrictamente (una clave desconocida es un error).
3. Si el slave pide un Kickstart, anadir en `user_assets` `Devs/Kickstarts/kick40068.A1200` (y su `.RTB`) con
   `"size"`; WHDLoad comprueba su contenido al ejecutar.
4. Registrar la licencia de cada entrada en `manifests/licenses.json` (`user_supplied_inputs`, con la evidencia).
5. `prepare`, comprobar el hash y pinearlo en `image_sha256`.

Tamano: el volumen mide lo que su contenido pide (multiplo de 32 bloques) y como minimo `min_kib`; el HDF de prueba
mide 880 KiB (el buffer de exportacion de `p6_state_probe`). Hasta 25 paginas de bitmap (~49 MiB), sin cadena de
extension; nombres de 30 bytes como mucho, ASCII con `DOS1`.

## Restricciones de formatos e instaladores

* Entradas: directorio o LHA (-lh0-, -lh5-, -lh6-, -lh7-). No hay LZX, ZIP, LHA con otros metodos (-lh1- a -lh4-,
  -lzs-), DMS ni imagenes de disco como entrada del preparador.
* No se ejecutan instaladores (Installer, DIC, RawDIC, Patcher, scripts): la instalacion debe estar hecha.
* `S:Startup-Sequence` generada: sin `C:` de AmigaOS (los comandos de Commodore no son redistribuibles); solo lo que
  el Shell de la ROM trae (`Echo`, redireccion) y `C:WHDLoad`.
* Referencia de pruebas: A1200 con Kickstart 3.1 (el del usuario, por variable de entorno). Un volumen FFS con
  Kickstart 1.3 en el A500 NO arranca todavia: la maquina muestra "Not a DOS disk in unit 0" (causa sin investigar: sospecha de que el FFS no esta en la ROM 1.3 para discos duros). No se declara soportado.

## Lo que no replica de PUAE5 (T65/P17)

La UX de WHDLoad de PUAE5 (opciones `puae_use_whdload`, temas, prefs/splash, `buttonwait`, `QuitKey` con volcado al
descargar, `WHDSaves.hdf` aparte, copia de Kickstarts de `RASystem:`, instalaciones Retroplay/Arcadia, "Boot HD",
directorios/LHA montados como volumen vivo) no esta aqui. El flujo de T30 es offline y explicito: la imagen ya hecha
es el contenido (libretro: `.hdf`; BizHawk: `HardFiles`), y lo que el invitado escribe sale por la exportacion
explicita de T63a/T63b. P16/P17 (importador dentro del core, UX) quedan en sus tareas.

## Resultado con un juego real (T30b)

Fatal Mission (WHDLoad 20.0 build 7051 + la instalacion de Kyzer, datos incluidos; "Classic PD" en Aminet) en el
A1200 con el Kickstart 3.1 del usuario, sin disquete: el volumen preparado es DH0:, el script escribe `boot.log`,
WHDLoad carga el juego y muestra el titulo (~unidad 290), el fuego del joystick del puerto 1 lo arranca, F10 (QuitKey
de WHDLoad) lo termina y el script escribe `exit.log`. La imagen que sale de la maquina (base + overlay de 4 KiB) es
un FFS valido (`inspect`) con exactamente esos dos ficheros mas, y es IDENTICA byte a byte en el motor nativo
(`p6_state_probe`), en libretro (`puae6_hdf_save=on_close`) y en el nucleo BizHawk dentro de Waterbox (`export`).

* DET02 (nativo): pasan los estados dentro del arranque (unidades 120, 240), con el titulo (330) y tras F10 (1502).
  Con el juego en marcha (420, 700) el PCM tras cargar es otra forma de onda aunque todos los digests coincidan: es
  una brecha de estado de Paula (T15m), fijada en `DIVERGENCES` de `tests/whdload/test_det02_whdload.py` y repetida
  como fallo esperado en libretro; el nucleo BizHawk no la tiene (su estado es el snapshot del sandbox).
* WB03: estado antes de que el script escriba (unidad 120): mismo sandbox, sandbox nuevo y otro proceso dan las mismas
  unidades y el mismo volumen; un estado de otro volumen se rechaza. Lo mismo en mitad del juego (700).
* Hasta que T15m cierre la brecha, "restaurar" en libretro y nativo es exacto en imagen y maquina, no en sonido, con un
  juego que tiene audio DMA sonando.

Para repetirlo: `P6_WHDLOAD_LHA`, `P6_FATALMISSION_LHA` y `P6_KICKSTART_A1200_ROM` y, con el arbol de `build/link-audit`,
`P6_STATE_PROBE_BUILD=debug python3 -m unittest tests.whdload.test_boot_native tests.whdload.test_det02_whdload tests.whdload.test_libretro_whdload tests.whdload.test_waterbox_whdload`
(el primero y el segundo tardan unos 8 y 17 minutos en -O0; la de Waterbox necesita `tools/build_waterbox.py --puae6`).

## Pruebas

Sin entradas externas corren (`python3 -m unittest discover -s tests/whdload -t .`): LHA y FFS con archivos
sinteticos, determinismo, fallos visibles, licencias y "el replay no monta carpetas". Con `P6_WHDLOAD_LHA`,
`P6_FATALMISSION_LHA` y `P6_KICKSTART_A1200_ROM` (descargas libres y el Kickstart del usuario, nunca en el
repositorio): preparacion real con la imagen pineada, arranque, entrada, escritura y estados en el motor nativo
(`test_boot_native.py`, `test_det02_whdload.py`), en libretro (`test_libretro_whdload.py`) y en BizHawk/Waterbox
(`test_waterbox_whdload.py`).
