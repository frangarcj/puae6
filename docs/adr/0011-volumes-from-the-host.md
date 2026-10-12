# ADR 0011: directorios, LHA y ZIP como volumenes: una imagen FFS construida en memoria

## Contexto

PUAE5 monta un directorio, un `.lha` o un `.zip` como volumen de AmigaDOS (`filesystem2=rw|ro`, `filesys.cpp` con volumenes de directorio y de archivo,
`zfile_archive`). El contenido de un directorio sale del host en cada acceso; el de un archivo, de un `zfile`. MacHawk (`../MacHawk`, solo lectura) hace lo
mismo para Waterbox: `UAE.WHDLoad.cs` entrega el `.lha`/`.zip` como fichero de solo lectura en memoria (`AddReadonlyFile`) y UAE lo monta como volumen de archivo
(`filesys.c` y `zfile_archive.c` parcheados para leer a traves de un `zfile` en memoria, `bizhawk-unzip.c`). Su estado es la instantanea del heap entero del guest:
no hay semantica de estado propia.

Las reglas de este proyecto (AGENTS.md, docs/02-contracts.md "HDF"): el host no se monta en vivo; lo que entra en la maquina es un objeto en memoria con content id; el
estado nativo (DET02) es obligatorio en libretro y headless; sin reloj, entropia ni orden del host; nada de stubs que devuelvan exito. `ports/portable/hostfs_unsupported.cpp`
deja `filesys.cpp` entero compilado pero con los servicios del host fallando, y `bridge/media.cpp` rechaza cualquier unidad que no sea un `P6_MEDIA_HDF` antes de que
exista la maquina.

## Opciones

**(a) Volumen de archivo de UAE, como MacHawk.** `filesys.cpp` ya esta compilado; faltaria (1) un `zfile` sobre memoria con `unzip` y un lector de LHA (`archivers/lha`,
`zfile_archive.cpp` no se compilan hoy), (2) un backend de `fsdb`/`my_*` en memoria para el volumen de directorio (hoy todo falla), (3) el estado. El tercero es el que
pesa: el arbol de `a_inode` (ids asignados por el orden de visita), los `lock`, `notify`, `ExAll`, los `fileinfo`/handles abiertos y las cadenas de la cola de peticiones
del filesystem son estado de la maquina. El `save_filesys` de upstream guarda RUTAS del host y reconstruye montando; T29c lo descarto por eso para los HDF. Habria que
escribir un codec campo a campo de toda esa estructura (el camino que T15 archivo) y revalidar DET02 contra el, y los tiempos de `a_inode` y de los `fsdb` (fechas del
fichero, orden de `readdir`) serian entrada del host salvo que un parche los anule uno a uno. Superficie: parches grandes al motor, estado nuevo, determinismo por
auditar archivo a archivo.

**(b) Imagen FFS de solo lectura construida en memoria por el adaptador y montada como `P6_MEDIA_HDF`.** El adaptador lee la fuente una vez, al cargar (VFS en libretro),
y construye una particion plana FFS (la forma de un HDF sin RDB: 32 sectores, 1 cabeza, 2 bloques reservados, DosType en el primer long del bloque 0) con orden, fechas y
nombre de volumen fijos. Para el motor es un HDF mas, por el camino ya probado de T29/T63: estado nativo (el cuerpo P6DK con el overlay de 4 KiB sucios), identidad
(SHA-256 de la imagen), escrituras a un overlay exportable, rewind, run-ahead. Cero parches al motor, cero ABI nueva.

## Decision

**(b).** Da estado nativo y determinismo con la menor superficie: lo unico nuevo es codigo del adaptador, acotado y verificable contra un oraculo; todo lo que el motor hace con
un HDF (estado, DET02, overlay, exportacion, Waterbox) ya esta probado. (a) obligaria a escribir y validar un estado de `filesys` que el proyecto ya descarto una vez, y
la unica ventaja de (a) (los volumenes de PUAE5 son una vista viva, con escritura directa al host) es justo lo que el stop de la ficha deja fuera. Del trabajo de MacHawk se
reutiliza la idea (el host nunca se monta: un `.lha`/`.zip` entra como bytes en memoria y las escrituras del invitado van a paginas sucias exportables), no su codigo.

## Implementacion (T64a)

- `adapters/libretro/ffs_volume.h`: el escritor FFS, un port de `tools/whdload/ffs.py` (el escritor de T30a, que es su oraculo: `tests/hdf/test_volume.py` compara los bytes
  en arboles aleatorios, con nombres de Latin-1, ficheros de varias decenas de bloques y cadenas de extension). `adapters/libretro/lha.h`: el lector de LHA (niveles de
  cabecera 0, 1 y 2; `-lh0-`, `-lh5-`, `-lh6-`, `-lh7-`, `-lhd-`), port de `tools/whdload/lha.py`, tambien oraculo. `adapters/libretro/volume.h`: las fuentes (directorio por el
  VFS de libretro version 3 o la biblioteca C, LHA, ZIP con todos sus miembros por `container.h`) y la imagen. `libretro_core.cpp`: contenido `.lha`/`.lzh`, un `.zip` que no
  trae disco, un directorio (extension `/` en `valid_extensions`, como PUAE5) y las mismas tres cosas como entradas de un M3U; el resultado se registra como un `.hdf`.
- **Determinismo (identidad = contenido).** La imagen depende solo de los nombres (ISO 8859-1) y los bytes de los ficheros y de los directorios vacios: no de la ruta ni del
  nombre del directorio o del archivo, ni de fechas, permisos, usuario, orden de `readdir` o de los miembros del archivo. Los hijos de un directorio y los bloques se asignan
  en orden de bytes del nombre; todas las fechas son 1990-01-01 00:00:00; el volumen se llama siempre `PUAE6`; la proteccion es siempre 0 (rwed); DOS\1 (FFS) salvo que
  un nombre tenga un byte mayor que 127 (DOS\3). El mismo arbol desde un directorio, un LHA y un ZIP, desde cualquier sitio y con cualquier fecha, es la misma imagen y el mismo
  medio: un estado tomado con el directorio carga con el LHA del mismo arbol (probado con Fatal Mission y WHDLoad, opt-in).
- **Espacio y escrituras.** La imagen deja 1 MiB libre (`kVolumeFreeBlocks`) para el invitado. El invitado puede escribir (un HDF siempre es escribible, T29b); la fuente NO se
  toca jamas (probado con instantaneas de rutas, tamaños y fechas), las escrituras son el overlay que viaja en estados, rewind y run-ahead y salen por la exportacion
  explicita de T63a (`puae6_hdf_save=on_close`, `puae6-<id>.hdf` en el directorio de guardado, atomica). El volumen es de solo lectura respecto del host, no del invitado.
- **Cotas, siempre antes de reservar.** 4096 entradas por volumen y por directorio, 48 MiB de bytes de ficheros (un volumen tiene 25 paginas de bitmap en el bloque raiz, unos
  49 MiB; la cadena de extension del bitmap no se escribe), 64 MiB para el `.lha`/`.zip`, 64 niveles de directorio, nombres de 30 bytes, 8192 nodos. Un tamaño declarado
  por la fuente nunca decide una reserva (la salida del decodificador crece con lo que decodifica). Un `.zip` con un `.adf`/`.adz`/`.dms` (o un disco que el nucleo rechaza)
  sigue siendo un disco con sus mensajes de siempre; sin disco y con ficheros es un volumen.
- **Nombres.** Un nombre de directorio del host es UTF-8 convertido a ISO 8859-1; un caracter fuera de el (macOS guarda é como e + acento combinado) es un error que nombra el
  fichero. ZIP: UTF-8 si su bandera lo dice, los bytes tal cual si no. `.DS_Store`, `._*` y `__MACOSX` se ignoran (basura de macOS); un enlace simbolico es lo que apunta (el VFS no
  distingue enlaces) y un bucle lo corta el tope de profundidad o el de entradas. Dos nombres que solo difieren en mayusculas, un fichero que tambien es directorio, un nombre
  de mas de 30 bytes o con `:` o `/` y un miembro duplicado son errores con mensaje.

## Limites (lo que NO hace)

- Kickstart 1.3 y anteriores no arrancan estos volumenes: la ROM 1.3 no trae FFS (es OFS) y no se escribe OFS. Hace falta Kickstart 2.0 o posterior (probado: 3.1 en el A1200).
- Un volumen no lleva `S:Startup-Sequence` ni WHDLoad por si mismo: el contenido decide que arranca (T65/P17 pone la UX de WHDLoad).
- Un volumen mide como mucho unos 49 MiB (una cadena de extension del bitmap lo ampliaria) y solo hasta 4 en total con los demas HDF.
- La imagen se construye al cargar: una carpeta de cientos de MiB se rechaza, no se carga por trozos. Los cambios de la fuente despues de la carga no llegan a la maquina.
- BizHawk/Waterbox es T64b (el cargador de EmuHawk lee ficheros de ROM; ver su informe); el escritor y el lector son los mismos algoritmos.
- No hay `filesystem2=rw` (escritura directa al host) ni vista viva de un directorio: son el montaje del host que la ficha deja fuera.
