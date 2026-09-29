# T06b2cc — disk.cpp sin creación, overlay, catálogo ni reloj host

El parche 0047 excluye de disk.cpp, con regiones `#ifndef P6_PORTABLE`, todo lo
que necesita el host para medios de solo lectura: creación de ADF (fecha del
reloj del host y escritura), overlay/save-image y cambio de protección por
atributos del fichero (chmod/unlink), catálogo XML de bootblocks (abrcheck) y
cambio de disco probando nombres en el sistema de ficheros. El catálogo de
perfiles FloppyBridge queda bajo su propio `#ifdef FLOPPYBRIDGE` (upstream lo
definía fuera de la feature). Nada se sustituye por un retorno de éxito; el
texto excluido sigue visible en el arbol y el delta es de 14 regiones.
Cola: 47 parches/27 archivos/1289 líneas (+47).

Objetivo cumplido: disk.cpp compila como objeto p6_engine (O0 con ASan/UBSan, y
O0/O2 en el test). Ahora solo falla inputdevice.cpp en el build global
(`build/t06b2cc-engine-build.log`). Los 5 avisos de disk.cpp son los mismos de
antes (3 `-Wswitch`, 2 `-Wformat` en logs de depuración); no se tocan aquí.

## Decisión no obvia: ejecutables

`zfile_gettype` de P6 clasifica por *tipo de blob registrado*, no por contenido.
Un asset registrado como ADF con cabecera de ejecutable Amiga (`00 00 03 f3 00 00
00 00`) SÍ llegaba a la rama exe→ADF de drive_insert, que estampa la fecha del
host. Excluir la rama sin más habría tratado el ejecutable como ADF crudo. Por
eso DISK_validate_filename lo rechaza con ENOTSUP, con relleno cero para ficheros
cortos igual que el sondeo legacy (`buffer` puesto a cero antes de leer). Difiere
de upstream en dos puntos aceptados: upstream solo convierte si `!canauto` y el
tamaño cabe en un HD, y aquí se rechaza siempre (un ejecutable no es un
disquete). Convertirlos con reloj emulado es una decisión futura, no un cambio
implícito de esta tarea.

## Estado de escritura

Con el overlay excluido, `writediskfile` es siempre NULL y `wrprot` es siempre
true (lo fija DISK_validate_filename), por lo que `drive_write_data` sale en su
guarda de protección existente. La ruta de escritura de pistas
(drive_write_adf_amigados/ext2/pcdos) sigue compilada pero inalcanzable; sus
`zfile_fwrite` los cubre el proveedor portable. La conversion a ext2 y su aviso
NUMSG_NEEDEXT2 quedan excluidos. Escritura reversible en RAM es M3, no esto.

## Límites

- `SAVESTATE` sigue sin definirse en el perfil (T06b2ao, ADR 0001): save_disk y
  restore_disk no forman parte del objeto hasta el gate T15. Esto afecta a todos
  los módulos, no solo a disk; no es una regresion de esta tarea.
- Imports que aun tiene disk.o y pertenecen a la continuación T06b2cb:
  `target_addtorecent` (lista reciente de escritorio, desde disk_insert),
  `fullpath` (DISK_history_add), `console_out(_f)` (volcados de depuración),
  `gui_ask_disk` y `au`. La lista completa esta en
  `build/t06b2cc-disk-imports.txt`.
- inputdevice.cpp aun llama a `disk_prevnext` (AKS_DISK_PREV/NEXT), ahora
  excluida; el caller debe guardarse al cerrar esa unidad. Compila porque
  disk.h la declara, pero no enlazaria.
- La rama de error del sondeo de cabecera (`header_error`) no tiene prueba: un
  stream de blob no puede fallar la lectura desde fuera del validador.
- No certifica geometria ADF, inserción/DMA con medio real, enlace ni Waterbox.

## Validación

- `tests.host.test_disk_asset`: añade ejecutables (completo, prefijo corto con
  cero implícito, casi-cabecera aceptada, 3 bytes aceptado), liberación de
  streams tras rechazar (P6_BLOB_STREAMS+2 rechazos) y conserva el resto. Mutación:
  el mismo test contra el cuerpo del parche 0046 aborta en `rejected(...)`.
- `tests.host.test_disk_source` (nuevo): objeto completo en O0/O2 con nm: símbolos
  de emulación definidos, funciones excluidas ausentes, ninguno de los imports
  host (nombres exactos; `zfile_fopen` es el proveedor portable) y depfile sin
  tinyxml2, floppybridge ni od-win32 salvo m68k.h.
- Series: materialize_upstream verifica hash previo/posterior y ausencia de
  offsets del 0047. build/upstream y build/generated de la serie de 46 parches se
  apartaron en `build/stale/` (la herramienta no sobreescribe destinos distintos).
