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

## Controles y guardado

Jugador 1: joystick del segundo puerto Amiga y raton del primero; jugador 2:
joystick del primer puerto. B/A/X son los tres botones del joystick. El teclado
se lee por simbolo y Caps Lock funciona como pestillo. Cambia los dispositivos
por puerto desde el menu del frontend. Puedes conectar dos ratones independientes.
Usa Disk Control para cambiar discos.

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

## Limites de esta entrega

Preview local del core nativo macOS ARM64. Las rutas iniciales A500, A600 y A1200
usan interprete de CPU. El preset A4030 Normal es experimental: las pruebas
sinteticas pasan, pero falta validar su arranque con firmware A4000 real.
040/060, cuatro jugadores, maquinas CD32/CDTV y WHDLoad completo siguen en
desarrollo. El guardado real de puntuaciones del nuevo bundle WHDLoad esta
pendiente; para esta preview utiliza los flujos de disquete/HDF ya disponibles.
El mando CD32 no implica soporte de la maquina CD32 ni de sus discos CD.
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
