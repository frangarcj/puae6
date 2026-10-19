# WHDLoad en PUAE6

Los adaptadores preparan en memoria un bundle de tres volumenes desde una
instalacion WHDLoad ya hecha, con el proveedor y Kickstart aportados por el usuario.
Libretro acepta directorio/LHA/ZIP; BizHawk acepta LHA/ZIP. El preparador CLI
permite ademas construir los HDF antes de cargarlos, con entradas explicitas.

## Bundle de tres volumenes (T65a)

`python3 tools/prepare_whdload.py bundle config/whdload/fatal-mission.json --input whdload=WHDLoad_usr.lha --input game=FatalMission.lha --out DIR`
prepara `WHDLoad.hdf`, `RASystem.hdf`, `WHDSaves.hdf` y una lista `WHDLoad (A1200).m3u`, sin manifiestos historicos.
La configuracion de T30 sigue comprobando los hashes de los ficheros del usuario. El plugin `C/WHDLoad.VFS` debe ser el incluido en WHDLoad 20.0; falta o version distinta es un error.
`--buttonwait` añade `ButtonWait` a las preferencias reales que lee WHDLoad (solo tiene efecto si el slave lo admite).
`--kickstart RUTA` se puede repetir: reconoce en memoria los cuatro Kickstart de MacHawk y los coloca con su nombre WHDLoad en `RASystem:Devs/Kickstarts`; no registra sus hashes.

El programa propio `C:P6Assign` conecta `S:` y `DEVS:` a `RASystem:` mediante la API AmigaDOS v36; no contiene ni necesita `Assign`, `Copy` ni otro comando de Commodore.
WHDLoad lee sus preferencias de `RASystem:S/WHDLoad.prefs`. Los datos del juego son un ZIP almacenado de orden y fecha fijos en `RASystem:Game.zip`, leido por la interfaz VFS de WHDLoad, que es de solo lectura.
Su cabecera identifica creador Amiga y permisos RWED Amiga: el plugin del usuario rechaza archivos marcados como PC/Unix.
`SavePath=WHDSaves:` dirige las escrituras `resload_SaveFile` al volumen separado. Los tres HDF usan los overlays, estados y exportacion existentes; el host no monta carpetas ni cambia las entradas.

Cargar la lista M3U en libretro con maquina A1200 y el Kickstart 3.1 del usuario, o pasar los tres HDF en ese orden a `Puae6Parameters.HardFiles` con `SyncSettings.Machine=A1200` en BizHawk.
Se guardan mediante `puae6_hdf_save=on_close` en libretro y SaveRAM/exportacion en BizHawk. No hay exportacion oculta al ejecutar o restaurar estados.
El comando es la alternativa explicita a la carga automatica descrita abajo.
Estos HDF contienen entradas privadas y no se distribuyen.

T65c1 añade `Puae6WhdLoad.Build`/`Prepare`, la misma receta en C# sin Python en
ejecucion. Usa los lectores LHA/ZIP y escritor FFS existentes; las constantes propias
P6Assign/arranque/prefs/roles se generan desde T65a, sin incluir programas del usuario.
`Prepare` verifica los dos miembros de WHDLoad 20.0; `Build` consume bytes explicitos.
El ZIP de datos usa cabeceras almacenadas fijas, creador Amiga, RWED y UTF8 cuando
el nombre Latin1 lo necesita. ButtonWait es false por defecto como T65a (PUAE5 lo
activa por defecto). Los constructores puros se prueban por separado del loader real.
WHDSaves vacio es el mismo medio compartible entre packs: SaveRAM P6SRv2 valida el ID
de los medios que contienen cambios; estados y peliculas validan las tres bases y
configuracion. No hay namespace por juego ni un formato nuevo de SaveRAM.

T65c2 conecta esa receta al loader BizHawk: `WhdLoad=Auto` detecta un unico `.slave`
en LHA/ZIP y fuerza A1200 antes de solicitar firmware. Cero slaves sigue como volumen
generico; varios o una mezcla con otros medios fallan claramente. `Disabled` conserva
el flujo generico anterior, incluidos sus scripts. `WhdLoadButtonWait` es opcion de
reinicio. El proveedor unico es `WHDLoad_usr.lha` en `GetUserPath(Amiga,false)`; los
callers de `Puae6Parameters` pueden dar `WhdLoadProvider` y Kickstarts/RTB explicitos.
Se valida el proveedor antes de pedir firmware y se copia el firmware reconocido a
RASystem, en memoria. Las escrituras usan los overlays/snapshots y SaveRAM existentes;
solo `CloneSaveRam`/`StoreSaveRam` hacen persistencia explicita, sin exportacion al rewind.

`tests.whdload.test_bundle` comprueba el contenido y determinismo sin assets. Sus dos pruebas opt-in usan los inputs de T30: exigen el fichero real `FM1.hisc` de 112 bytes en WHDSaves,
los datos inalterados y replay con PCM/imagen; Waterbox añade un sandbox nuevo. No sustituye guardar partida por escribir un log del script.

Fatal Mission no guarda al salir del titulo con RMB: ese camino del ejecutable
vuelve al sistema sin pasar por el hook `resload_SaveFile` que su slave instala
en `$aca`. El hook esta en el fin de partida/tabla de records, antes de volver
al titulo. La prueba corta conserva ese camino como salida sin guardado.
El recorrido que guarda entra en partida con Fire, espera a terminar la
descompresion inicial (juego visible en la unidad 900), pulsa RMB durante la
partida y espera la tabla (visible en 1200, tras su espera minima de 200 VBL).
Con puntuacion cero basta Fire para confirmar la tabla; si se registra un record,
hay que confirmar las tres letras con flancos de Fire. El recorrido de prueba
regresa al titulo en 1700 y exige `FatalMission/FM1.hisc` de 112 bytes, DATA
inmutable y replay. La prueba separada de AmigaDOS comprueba la lectura de
`S:WHDLoad.prefs` y la escritura de WHDSaves sin sustituir el oraculo del juego.

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

## Limites de la integracion

La carga automatica prepara la receta propia de tres volumenes y nunca monta
carpetas vivas del host. No ejecuta instaladores ni proporciona temas, splash
personalizado o instalaciones Arcadia. Los scripts del pack no sustituyen el
arranque propio. Un slave que necesita otro Kickstart o RTB requiere aportarlo
explicitamente; el paquete WHDLoad oficial no incluye RTB. El preparador CLI y
los parametros C# permiten esas entradas. Disabled conserva el volumen generico
con sus propios scripts y preferencias.

## Resultado con un juego real (T30b)

Fatal Mission (WHDLoad 20.0 build 7051 + la instalacion de Kyzer, datos incluidos; "Classic PD" en Aminet) en el
A1200 con el Kickstart 3.1 del usuario, sin disquete: el volumen preparado es DH0:, el script escribe `boot.log`,
WHDLoad carga el juego y muestra el titulo (~unidad 290), el fuego del joystick del puerto 1 lo arranca, F10 (QuitKey
de WHDLoad) lo termina y el script escribe `exit.log`. La imagen que sale de la maquina (base + overlay de 4 KiB) es
un FFS valido (`inspect`) con exactamente esos dos ficheros mas, y es IDENTICA byte a byte en el motor nativo
(`p6_state_probe`), en libretro (`puae6_hdf_save=on_close`) y en el nucleo BizHawk dentro de Waterbox (`export`).

* DET02 (nativo): pasan los estados dentro del arranque (unidades 120, 240), con el titulo (330), con el juego en marcha
  (420, 700) y tras F10 (1502). T30b encontro que con el juego en marcha el PCM tras cargar era otra forma de onda
  aunque todos los digests coincidieran; T15m lo cerro (parche 0250): Paula mezcla el volumen que fijo al cargar el
  ultimo periodo (`mixvol`), no el del registro, y `restore_audio` ponia el segundo en el primero; un reproductor que
  escribe el volumen en cada vertical con periodos de varias lineas deja los dos distintos en la frontera. Ahora el
  volumen del mezclador viaja en AUDP y el escenario `tone-volume` de `tests/state/test_det02.py` lo cubre sin juego.
* WB03: estado antes de que el script escriba (unidad 120): mismo sandbox, sandbox nuevo y otro proceso dan las mismas
  unidades y el mismo volumen; un estado de otro volumen se rechaza. Lo mismo en mitad del juego (700).

Para repetirlo: `P6_WHDLOAD_LHA`, `P6_FATALMISSION_LHA` y `P6_KICKSTART_A1200_ROM` y, con el arbol de `build/link-audit`,
`P6_STATE_PROBE_BUILD=debug python3 -m unittest tests.whdload.test_boot_native tests.whdload.test_det02_whdload tests.whdload.test_libretro_whdload tests.whdload.test_waterbox_whdload`
(el primero y el segundo tardan unos 8 y 17 minutos en -O0; la de Waterbox necesita `tools/build_waterbox.py --puae6`).

## Carga automatica libretro

`puae6_whdload=auto` (defecto) reconoce un unico `.slave` en un directorio, LHA
o ZIP de archivos. Coloca el `WHDLoad_usr.lha` autorizado en el directorio system
del frontend: se verifican WHDLoad 20.0 y su VFS por los pins de T30/T65a.
El core fuerza A1200 y Kickstart 3.1 A1200, conserva CPU/RAM explicitos y copia
el firmware reconocido a RASystem antes de calcular las identidades de las bases.
No se usan los scripts ni preferencias del pack/proveedor. Varios slaves o una
mezcla del pack con otros medios producen error; cero slaves usa el volumen normal.
`puae6_whdload=disabled` conserva ese flujo normal incluso si hay un slave.
Las dos opciones WHDLoad necesitan recargar contenido; `puae6_whdload_buttonwait`
vale `disabled` por defecto y admite `enabled`. Para conservar partidas activa
`puae6_hdf_save=on_close`: solo el cierre exporta atomicamente la rama actual de
los HDF. WHDSaves vacio identifica el mismo medio compartible entre juegos.
Los estados vinculan las tres bases y la configuracion; nunca escriben al host.

## Pruebas

El constructor C++ puro de `adapters/libretro/whdload_bundle.h` recibe el arbol
del juego, WHDLoad y su VFS como bytes, mas Kickstarts/RTB explicitos. Genera
`WHDLoad.hdf`, `RASystem.hdf` y `WHDSaves.hdf` con la receta T65a; rechaza slaves
ambiguos, rutas inseguras y limites excedidos sin devolver imagenes parciales.
`ButtonWait` vale false por defecto (PUAE5 usa true); las preferencias conservan
`NoWriteCache`, `SplashDelay=0` y `ConfigDelay=0`. Los datos propios compartidos
con C# se generan con `python3 tools/generate_whdload.py`; no incluyen binarios
de terceros. El probe del constructor solo acredita bytes, no ejecucion del juego.
`python3 tools/check.py --check MED02 --task T65b1` compara los tres HDF byte a
byte, tambien con nombres Latin-1/ZIP UTF-8; con las dos variables LHA del usuario
incluye los archivos reales sin requerir ROM ni compilar el motor.

Sin entradas externas corren (`python3 -m unittest discover -s tests/whdload -t .`): LHA y FFS con archivos
sinteticos, determinismo, fallos visibles, licencias y "el replay no monta carpetas". Con `P6_WHDLOAD_LHA`,
`P6_FATALMISSION_LHA` y `P6_KICKSTART_A1200_ROM` (descargas libres y el Kickstart del usuario, nunca en el
repositorio): preparacion real con la imagen pineada, arranque, entrada, escritura y estados en el motor nativo
(`test_boot_native.py`, `test_det02_whdload.py`), en libretro (`test_libretro_whdload.py`) y en BizHawk/Waterbox
(`test_waterbox_whdload.py`).
