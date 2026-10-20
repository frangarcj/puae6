# PUAE6: primera preview para macOS ARM64

Esta entrega contiene el core libretro y un ejecutable de consola. No es una
aplicacion grafica independiente. Requiere un Mac Apple Silicon y un frontend
libretro compatible ya instalado. No incluye ROMs Kickstart ni juegos.

## Empezar

1. Extrae el ZIP en una carpeta propia.
2. En tu frontend, carga manualmente `bin/puae6_libretro.dylib` mediante
   «Load Core / Cargar nucleo» y selecciona tu disco o playlist.
3. Pon tu Kickstart autorizado en el directorio de sistema del frontend:
   `kick34005.A500` para A500/1.3, `kick37350.A600` para A600/2.05 o
   `kick40068.A1200` para A1200/3.1. El core comprueba su contenido.
4. Selecciona el modelo en las opciones del core y reinicia el contenido si lo
   cambias. La seleccion automatica usa A500 para disquetes y A1200 para disco duro;
   las etiquetas del nombre y las opciones pueden elegir otro modelo.

Medios: ADF/ADZ/DMS, ADF extendido, FDI e IPF; ZIP/7z/RP9 de discos,
playlists M3U, HDF/HDZ y volumenes LHA/directorio/UAE dentro de los limites del
core. Los errores de firmware o contenido se comunican al frontend.

## CD32 y A4000 experimentales

Para CD32, abre directamente un ISO, CUE, CCD, NRG DAO o MDS v1, con modelo y Kickstart
en Automatic. Pon `kick40060.CD32` y `kick40060.CD32.ext` en el directorio de
sistema; ambos son necesarios. El preset `cd32_fast8` añade 8 MiB de Fast RAM.
En Automatic, `(CD32FR)` o la palabra `FastRAM` en el nombre del CD tambien
activan esos 8 MiB; los ajustes explicitos y File Name Tags tienen prioridad.
CD32 guarda su NVRAM mediante SaveRAM del frontend. Se ha probado arranque,
partida y restauracion de Brian the Lion; no se promete compatibilidad con todos
los juegos. MDS admite una sesion y un MDF compartido; abre el MDS conservando
su MDF al lado. CHD nativo, otras variantes NRG y cambio de CD siguen pendientes.

A4000 usa `kick40068.A4000` en el mismo directorio de sistema. Los presets
`a4030_normal`, `a4040_normal` y `a4060_normal` seleccionan 030, 040 y 060.
Su arranque con firmware real y restauracion estan probados; siguen siendo
experimentales, sin MMU ni caches, y FRESTORE040 conserva casos pendientes.

## Controles y guardado

Jugador 1: joystick del segundo puerto Amiga y raton del primero; jugador 2:
joystick del primer puerto. B/A/X son los tres botones del joystick. El teclado
se lee por simbolo y Caps Lock funciona como pestillo. Cambia los dispositivos
por puerto desde el menu del frontend. Puedes conectar dos ratones independientes.
Usa Disk Control para cambiar discos.

Los jugadores 3 y 4 usan un adaptador paralelo de joystick. Conecta Joypad en
los puertos 3 y 4 del frontend; cada uno tiene direcciones y un boton de fuego.
El juego debe admitir ese adaptador.

Tambien estan disponibles Lightgun y Lightpen, con coordenadas absolutas y
disparo/contacto. Solo puede conectarse un sensor optico a la vez; el juego
debe admitirlo. El mapeo de posicion respeta el recorte de pantalla.

Tambien puedes elegir `CD32 pad`: B/A/X/Y son rojo/azul/verde/amarillo, L/R son
retroceso/avance y Start es Play. Para usarlo en jugador 2, desactiva el raton
de jugador 1, que comparte el puerto Amiga 1.

`puae6_floppy_drives` conecta de una a cuatro disqueteras. MultiDrive o `(MD)` en
el nombre de una playlist monta sus discos en unidades sucesivas; Auto conecta
cuatro en ese caso. Un numero elegido a mano limita las unidades disponibles.

`puae6_cpu_compatibility` elige exact, compatible o normal para 68000/68010/68020.
Exact es el valor inicial; los otros niveles cambian la temporizacion emulada.
Reinicia el contenido al cambiarlo. La etiqueta `(CE)` fuerza exact salvo que
desactives File Name Tags.

`puae6_collision_level` usa todas las colisiones por defecto (Auto/Full).
Los niveles reducidos cambian lo que ve el juego y pueden afectar su funcionamiento;
forman parte de la configuracion de estados y peliculas.

`puae6_drive_sound_volume` activa el sonido del motor y de los pasos de
la disquetera: 0 (defecto, silencio), 10, 25, 50, 75 o 100 %. Puede cambiarse
durante la partida; cargar un estado conserva el volumen actual. En consola
se elige con `--drive-sound-volume`.

Las escrituras de disquete estan desactivadas por defecto. Para conservarlas,
activa `puae6_floppy_writes` y el guardado `puae6_floppy_save=on_close`, configura
un directorio de guardado y cierra el contenido normalmente. Se conserva la base
y se exporta una copia mediante reemplazo atomico. Un cierre forzado puede perder
los cambios no exportados. ADF extendido, FDI e IPF son de solo lectura.

Los estados son de esta compilacion y configuracion; no se promete compatibilidad
con otras builds ni con PUAE/WinUAE/BizHawk. Conserva este paquete junto con tus
peliculas/estados. No sobrescribas una instalacion antigua de UAE.

Para un disco de guardado nuevo, incluye `#SAVEDISK:Mi partida` como linea en una
playlist M3U. Se crea en memoria y se conserva al cerrar como
`<nombre-playlist>.save0.adf` (save1 para el segundo), con las mismas opciones de
escritura/guardado. Playlists con el mismo nombre comparten esos archivos: usa
nombres distintos para partidas independientes.

## Packs WHDLoad

Pon tu `WHDLoad_usr.lha` oficial de WHDLoad 20.0 en el directorio de sistema del
frontend, junto al Kickstart A1200 indicado arriba. Abre el LHA, ZIP o directorio
de un juego ya instalado con exactamente un `.slave`. `puae6_whdload=auto`
selecciona A1200 y prepara sus tres discos en memoria. No ejecuta instaladores.

Activa `puae6_hdf_save=on_close` y configura un directorio de guardado para
conservar las partidas al cerrar normalmente. WHDSaves es compartible entre
packs; los datos originales del juego permanecen intactos. Rewind y runahead
conservan unicamente las escrituras de la rama que finalmente cierras.

`puae6_whdload_buttonwait=enabled` añade ButtonWait, si el juego lo admite;
por defecto esta desactivado. Ambas opciones requieren volver a cargar el contenido.
Para una instalacion con su propio script de arranque, usa `puae6_whdload=disabled`.
La carga automatica admite un pack solo; para mezclar otros medios usa ese flujo
explicito. Los errores nombran el proveedor ausente, la version distinta o el
slave ambiguo; el core no descarga esos archivos.

## Limites de esta entrega

Preview local del core nativo macOS ARM64. Las rutas iniciales A500, A600 y A1200
usan interprete de CPU; A4000 y CD32 conservan los limites experimentales indicados
arriba. CDTV sigue pendiente. Algunos slaves WHDLoad
necesitan Kickstarts o RTB adicionales; esos archivos debe aportarlos el usuario
mediante el preparador explicito descrito en `docs/whdload.md` de los fuentes.
La integracion Waterbox/MacHawk es un producto separado y no se instala
con este ZIP. No se declara CI remota ni reproducibilidad binaria auditada.

## Consola y fuentes

`bin/p6_headless --run /ruta/a/tu/kickstart.rom --units 120 --disk /ruta/a/disco.adf`
ejecuta sin interfaz; `--machine a600` o `--machine a1200` seleccionan esos modelos.
`bin/p6_libretro_host` es una herramienta de consola para comprobar la API; no
sustituye al frontend grafico.

`source.tar.gz` contiene los fuentes correspondientes, parches, herramientas y
tests publicos. Extraelo y compila con Python3, CMake, Ninja y Xcode Command Line
Tools: `cmake -S source -B source/build/native -G Ninja -DCMAKE_BUILD_TYPE=Release`,
seguido de `cmake --build source/build/native --target puae6_libretro p6_headless`.
No descarga dependencias. Los avisos por componente estan en los fuentes y en
`manifests/licenses.json`; se conservan los textos GPL, BSD de MAME e indicaciones
MIT de libretro. No se distribuye CAPSImg ni software WHDLoad de terceros.
