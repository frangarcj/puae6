# PUAE6: previews nativas

Esta entrega contiene el core libretro y un ejecutable de consola. No es una
aplicacion grafica independiente. Cada ZIP identifica su plataforma en
`manifest.json`: macOS ARM64, Linux ARM64 o Linux x86_64. Usa el paquete de tu
arquitectura y un frontend libretro compatible ya instalado. No incluye ROMs
Kickstart ni juegos. La receta Linux usa Clang y las bibliotecas de sistema de
Ubuntu 24.04; no incorpora bibliotecas externas ni instala dependencias.

## Empezar

1. Extrae el ZIP en una carpeta propia.
2. En tu frontend, carga manualmente `bin/puae6_libretro.dylib` mediante
   «Load Core / Cargar nucleo» y selecciona tu disco o playlist. En Linux el
   archivo es `bin/puae6_libretro.so`.
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

Para CD32, abre directamente un ISO, CUE, CCD, NRG DAO/ETNF/ETN2, MDS v1 o CHD, con modelo
y Kickstart en Automatic. Tambien puedes abrir una M3U con hasta16 entradas CD. Pon `kick40060.CD32` y `kick40060.CD32.ext` en el directorio de
sistema; ambos son necesarios. El preset `cd32_fast8` añade 8 MiB de Fast RAM.
En Automatic, `(CD32FR)` o la palabra `FastRAM` en el nombre del CD tambien
activan esos 8 MiB; los ajustes explicitos y File Name Tags tienen prioridad.
CD32 guarda su NVRAM mediante SaveRAM del frontend. Se ha probado arranque,
partida y restauracion de Brian the Lion; no se promete compatibilidad con todos
los juegos. MDS admite una sesion y un MDF compartido; abre el MDS conservando
su MDF al lado. NRG DAO admite varias sesiones con sus direcciones y huecos
explicitos; presenta las pistas globales y el leadout final en un TOC virtual,
sin seleccion mutable de sesion. ETNF y ETN2 admiten una sesion con tamaños de
sector mezclados, datos contiguos y posiciones compactas; reconstruyen pregaps
canonicos de 150 sectores. ETNF multisesion admite el perfil global de CDI2Nero,
con INDEX01 global, huecos y pregaps sinteticos de 150 sectores; no recupera Q ni
pregaps originales. ETN2 multisesion y TINF siguen pendientes.
Para cambiar CD, abre la bandeja con Disk Control, elige otro indice y cierra
la bandeja. El cambio se aplica en el siguiente frame y la seleccion se restaura
con el estado. La lista de CD es fija: no admite añadir, quitar o reemplazar
entradas durante la sesion, ni mezclar CD/disquetes o usar #SAVEDISK. CHD admite
CDv5 sin parent con codecs none/cdzl/cdlz/cdfl y pregap inicial almacenado (HTOA);
CDI, GD y DVD quedan fuera.
Se ha probado la misma partida/restauracion con Brian convertido localmente a CHD.

A4000 usa `kick40068.A4000` en el mismo directorio de sistema. Los presets
`a4030_normal`, `a4040_normal` y `a4060_normal` seleccionan 030, 040 y 060.
Su arranque con firmware real y restauracion estan probados; siguen siendo
experimentales, sin MMU ni caches. La FPU interna de 040/060 se activa explicitamente
con la CPU correspondiente y usa SoftFloat. La restauracion FE del 68040 conserva
los operandos pequenos normalizados por FPSP. FRESTORE040 admite tambien ciertos
frames CU06 reconstruidos por FPSP, incluidos NaN SINGLE; los denormales CU06
y la restauracion completa de todos los formatos siguen pendientes.

En A1200 puedes elegir `Cartridge: HRTMon builtin` y asignar `HRTMon Freeze`
en las opciones de hotkeys o del mapper. Activa el monitor una vez por pulsacion;
suelta antes de volver a pulsar. El monitor GPL procede del snapshot WinUAE fijado,
se incluye en el core y no requiere descargar una ROM adicional.

Action Replay I/II/III usa un A500 stock y las ROMs propias `ar1.rom`, `ar2.rom`
o `ar3.rom` en el directorio de sistema. Action Replay 1200 usa A1200 stock y
`ar1200.rom`. Elige el modelo en Cartridge y asigna Cartridge Freeze; suelta
antes de volver a entrar. Estos perfiles requieren discos sin escritura y
memoria stock. El guardado comprueba la identidad de la ROM y conserva el estado
del monitor; las ROMs
comerciales se aportan por separado.

CDTV esta disponible en libretro con un catalogo fijo: elige CDTV o usa esa etiqueta
en el nombre del CD en Automatic. Requiere `kick34005.A500` y la EXT2.7
`kick34005.CDTV` (tambien admite `CDTV-Extended-ROMv2.7.rom`) en el directorio
de sistema. Una M3U admite hasta16 CD; usa Disk Control para abrir la bandeja,
elegir y cerrar. La insercion espera a que el controlador observe la ausencia
del CD anterior. No permite añadir/quitar/reemplazar entradas ni carga ADF/HDF
en este perfil.

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

Preview local del core nativo para la plataforma indicada en el ZIP. Las rutas
iniciales A500, A600 y A1200
usan interprete de CPU; A4000 y CD32 conservan los limites experimentales indicados
arriba. Algunos slaves WHDLoad necesitan Kickstarts o RTB adicionales; esos
archivos debe aportarlos el usuario
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
En Linux se usan Clang/Clang++, CMake, Ninja y Python3 ya disponibles; añade
`-DCMAKE_TOOLCHAIN_FILE=cmake/toolchains/linux-clang.cmake` al configurar. Para
generar un ZIP y comprobarlo extraido, desde los fuentes limpios y comprometidos
ejecuta `python3 tools/package.py build`. La receta elige la plataforma nativa,
compila tambien el host de consola y ejecuta la misma prueba instalada de avance,
restauracion y rechazo de firmware ausente. El smoke instalado guarda un estado
PAL valido de linea simple y lo carga en otro proceso con las mismas opciones.
Una sesion NTSC de linea doble lo rechaza por
identidad de configuracion: su estado antes/despues permanece byte exacto y
sus ocho unidades, estado final y dumps de video/PCM coinciden con un control
NTSC independiente. Los datos temporales se eliminan al terminar. Esta prueba
no promete compatibilidad de estados entre builds ni con otros cores.
`python3 tools/package.py check --archive /ruta/preview.zip` repite esa comprobacion, sin necesitar el checkout
usado para compilar los binarios. No descarga dependencias.

PUAE6 incluye un derivado de SoftFloat2a con cambios de
QEMU, WinUAE y PUAE6; conserva sus avisos mixtos SoftFloat2a/BSD/GPL y atribucion
FPSP. `licenses/` acompaña los binarios con los textos GPL, MAME/IPF, libchdr,
zlib, LZMA, dr_flac y SoftFloat; los fuentes y `manifests/licenses.json` conservan
la procedencia por componente. No se distribuye CAPSImg ni software WHDLoad de terceros.
