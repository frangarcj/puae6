# ADR 0013: IPF con el lector libre de MAME (no capsimg)

Continua la ADR 0008 (identidad de los medios, puntos 12 a 16). Paridad con la lectura de IPF de PUAE5 (`reports/research/parity-puae5.md`, P10).

## Contexto

PUAE5 y MacHawk leen IPF con **capsimg**, la biblioteca de SPS. Su licencia (`references/capsimg/LICENCE.txt`) permite el uso y la redistribucion **no comerciales**, exige las fuentes de
cualquier derivado y prohibe habilitar otros formatos: es incompatible con la GPLv2 del motor si se enlaza (una restriccion mas) y MacHawk la enlaza estatica en el guest de Waterbox.
El usuario pidio (2026-10-08) una version libre. Ademas el emulador de controlador de CAPSImg guarda su estado dentro de la biblioteca, fuera de cualquier snapshot (DET02, STATE01),
y WinUAE la carga con `dlopen`, que ni el guest ni el perfil determinista admiten.

## Decision

1. **El decodificador es `ipf_dsk.cpp` de MAME** (`src/lib/formats`, Olivier Galibert), en el commit fijado `7870bf4b4a562b63d1f048d5b1cf8b3250ba45e8` (2026-08-28), importado a `third_party/mame-ipf`.
   Es la lectura de IPF mas completa que existe con licencia libre: las pistas de CAPS (encoder 1) y de SPS (encoder 2), bloques MFM y crudos, huecos descritos, bits debiles, ruido,
   los tipos de pista con velocidad variable y el corte de escritura.
2. **Licencia, verificada en el fichero y no en una nota.** Los dos ficheros (`ipf_dsk.cpp`, `ipf_dsk.h`) empiezan por `// license:BSD-3-Clause` y `// copyright-holders:Olivier Galibert`; el
   texto de la licencia es el de `docs/legal/BSD-3-Clause` del mismo commit; el `COPYING` de MAME dice que el proyecto es GPL-2.0 y que "los ficheros individuales pueden estar bajo licencias
   menos restrictivas, como indican las cabeceras". Se usan solo esos dos ficheros. BSD-3-Clause es compatible con la GPL-2.0-or-later del resto del proyecto (sin clausula de publicidad);
   obliga a conservar el aviso y la renuncia en las fuentes y a reproducirlos en la documentacion de una distribucion binaria (`third_party/mame-ipf/NOTICE.md` y `LICENSE-BSD-3-Clause.txt`
   viajan con cualquier paquete, T40) y a no usar el nombre de los autores para promocionar. **No es asesoria legal**; el registro de la auditoria esta en `manifests/licenses.json`
   (componente `mame-ipf`) y en `third_party/mame-ipf/PROVENANCE.json` (URL, commit, SHA-256 de cada fichero, autores de la historia del fichero, lo que se reviso).
3. **Procedencia verificable.** `third_party/mame-ipf/original/` guarda los dos ficheros tal cual salen de MAME (su SHA-256 esta en `PROVENANCE.json`); los que se compilan son esos mismos
   con los cambios de `local-changes.diff`. `tests/media/test_ipf_provenance.py` aplica el diff al reves y comprueba el hash del original, comprueba que ningun otro fichero de MAME
   esta en el arbol, que los includes del decodificador son los tres que sustituyen las cabeceras propias, que ni el codigo ni el nombre de capsimg aparecen y que la entrada de
   `licenses.json` apunta a lo que hay. La procedencia es un registro de datos, no un `git subtree`: el unico origen es el commit fijado y el unico input de red fue su descarga.
4. **Tres cabeceras propias sustituyen a las de MAME** (`flopimg.h`, `multibyte.h`; `ioprocs.h` ya no hace falta): llevan solo los nombres y valores que el decodificador usa. La
   capa de imagenes de MAME (conversion a transiciones de flujo, `floppy_image`, E/S de flujos) no se trae; el decodificador entrega la pista como celdas con su tiempo y su tipo
   (0, 1, debil) y la conversion a bits MFM es codigo del proyecto (`ports/portable/media/ipf_track.cpp`). La frontera de ficheros es la frontera de licencias.
5. **Cambios locales** (`local-changes.diff`, 14 KiB de diff, casi todo lineas borradas): se quita la interfaz de flujo de MAME (`identify`, `load`, `parse`, el explorador de etiquetas que escribia en el buffer
   de entrada: un medio del guest es de solo lectura); las sumas de tamanos son de 64 bits y se acotan a lo que una unidad de doble densidad lee (160000 celdas por pista, 169 claves de indice (0..168; T58c),
   cilindros accesibles 0 a 82, 256 KiB de datos por pista); las compensaciones a descripciones se comprueban contra el tamano de los datos; un patron de hueco de cero bits se rechaza (era una
   division por cero); tres comparaciones que daban la vuelta con huecos pequenos o la primera pista se escriben sin que puedan darla; la pista terminada se mueve al llamante.
   Los encontro la lectura del codigo (cada suma, desplazamiento y division sobre un numero del fichero) y los comprueba el fuzzer con ASan/UBSan y un oraculo del formato (FUZZ01 T58a): quitar
   uno a uno tres de esos cambios (el patron vacio, la comparacion de la posicion del indice y la suma de tamanos reales) hace fallar `tests/fuzz/test_ipf.py`; los otros cuatro son
   cinturon y tirantes (los cubre otra comprobacion) y solo fallarian juntos.
6. **Interfaz** (`ports/portable/media/ipf_track.h`): el fichero se lee por offsets, nunca se copia (el motor lee los bytes que el puente registro; las pruebas un array). `open` lo
   recorre entero una vez (CRC-32 de cada etiqueta y de los datos de cada pista, la estructura, un indice de pistas de unos 17 KiB) y `decode` lee los datos de UNA pista, la decodifica y la
   deja como palabras MFM de 16 celdas (la primera en el bit alto, el orden de `bigmfmbuf`), una temporizacion por cada 8 celdas (milesimas de celda nominal de 2000 ns, la unidad de
   `tracktiming`) y la marca de bits debiles. Las celdas debiles y de ruido toman sus bits de un generador **propio, sembrado por (pista, revolucion)**, nunca de `uaerand`: la misma pista
   y revolucion son los mismos bits en cualquier proceso y su estado no esta en ninguna parte (no hay nada que guardar).
7. **Formato del medio.** IPF es otro formato de `P6_MEDIA_ADF` (como el ADF extendido y el FDI de T57), de solo lectura, con la identidad por contenido de siempre (SHA-256 de los bytes tal
   como se dieron), el tope de 3 MiB por imagen y los 16 discos planos en total. `classify_floppy` lo reconoce por el registro CAPS de 12 bytes; el puente (`validate_media`) lo abre con el
   decodificador y decodifica **todas** las pistas una vez antes de decir al motor que existe: un IPF que el motor no podria leer se rechaza con el motivo, no se juega como un disco de
   pistas sin formato.

8. **Motor y estado (T58b, parche 0340).** `disk.cpp` abre el indice del IPF al insertar (`p6_ipf::open_blob`: lee el medio del almacen por offsets), lo cierra al expulsar y decodifica la pista bajo la cabeza en
   `drive_fill_bigbuf` y la siguiente revolucion en `fetchnextrevolution` (solo si la pista tiene celdas debiles: `multi_revolution`). El contador de revolucion y la pista a la que pertenece son `fdirev` y `fditrack`, ya
   en DSKX por el FDI (ADR 0009, punto 17): el estado nativo no crece. Es posible porque el decodificador no tiene estado entre dos lecturas (la revolucion es un argumento) y porque el indice se deriva del medio, que el
   estado ya nombra por contenido y vuelve a insertar al cargarse. La temporizacion de una pista IPF se guarda por byte (parche 0335, ya hecho para el FDI). La posicion del empalme de escritura es `skipoffset`.

## Lo que no hay

- Escritura en IPF (los medios son de solo lectura, como el FDI), IPF de alta densidad, los tipos de pista que MAME rechaza, las etiquetas de CT RAW / KryoFlux.
- Un IPF real y libre: los de SPS son de juegos comerciales (no se descargan). Las pruebas escriben sus IPF desde el contenido que quieren (`tests/media/ipf.py`) y hay una prueba por
  variable de entorno (`P6_IPF_FILE`) para un IPF autorizado por el usuario, no ejecutada mientras no haya ninguno.
- Fidelidad de proteccion: el decodificador es el de MAME y aqui no hay un IPF protegido contra el que medirlo. Los bits debiles son aleatorios con probabilidad 1/2 por celda, la
  temporizacion es la de los tipos de pista de MAME (2 a 9) y el empalme de escritura se marca como en MAME.

## Que cambiaria la decision

Que MAME cambiara la licencia de estos ficheros (el commit fijado seguiria siendo BSD-3, pero no se actualizaria a una version con otra), que apareciera un IPF real con una proteccion que el
decodificador no lea (se documenta el caso minimo y se decide entre un parche local y esperar a MAME), o que SPS publicara una biblioteca de licencia libre (no cambiaria nada mientras esta funcione).

## Aclaracion de T58c: geometria del fichero y de la unidad

El IPF de Amiga Soccer suministrado por el usuario el 2026-10-18 usa claves 1..168 y descripciones vacias hasta el cilindro 83. MAME ya reservaba `84*2+1` entradas; el endurecimiento inicial lo redujo indebidamente a 168. El indice admite ahora 169 claves y 84 cilindros de metadatos, pero la unidad conserva sus 166 pistas. El cilindro 83 solo puede ser tipo 1 sin celdas, bloques ni datos: no se ofrece al motor, no amplía `MAX_TRACKS`, y una pista exterior con contenido se rechaza. La regresion es sintetica y el corpus mutado incluye esa geometria. Informe y limites nativos/Waterbox: `reports/tasks/T58c.md`.
