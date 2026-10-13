# ADR 0008: medios y firmware por identidad

## Contexto

`p6_load` recibe el firmware y una lista de imagenes de disco con un `content_id` de 32 bytes. El contrato
(docs/02-contracts.md) dice que un path del host no es identidad ni parte del estado y que un medio ausente o distinto
es un error. El motor de WinUAE, en cambio, abre disquetes por nombre a traves de `zfile`, no valida tamaño ni formato
(una imagen de 901120 bytes con la marca `UAE-1ADF` y 0xFFFF pistas desborda la tabla de pistas y termina en SIGSEGV; una
de 4 MiB corrompe la estructura de la unidad en silencio), y `default_prefs` deja `df0.adf`..`df3.adf` como nombres de
disco. Mediciones y referencias de codigo: informe de investigacion de T12 (resumen en reports/tasks/T12a.md).

## Decision

1. **El bridge es el unico guardian.** `validate_media` (bridge/media.cpp) corre antes de tocar el motor y rechaza, por este
   orden y sin efecto alguno sobre la instancia: mas de 16 medios (CAPACITY), array nulo (BAD_ARGUMENT), tipo distinto de
   ADF (UNSUPPORTED), flags no nulos, imagen ausente y ids duplicados (BAD_ARGUMENT), tamaño distinto de 901120 bytes o
   cabecera de ADF extendido / ejecutable Amiga (WRONG_MEDIA) y, al final, un `content_id` que no es el SHA-256 de los
   bytes (WRONG_MEDIA). El hash se calcula solo para listas que ya pasaron las comprobaciones baratas.
2. **Identidad = SHA-256 de los 901120 bytes crudos.** Para el ADF la forma normalizada es la imagen; un ADZ o DMS futuro
   se identificaria por los bytes descomprimidos. El firmware se identifica por el SHA-256 de su buffer, calculado en la
   carga; no cambia la ABI. `Session` guarda `firmware_sha256`, `media_count` y los ids en el orden del array para la
   cabecera de estado de T14 (`media_manifest_hash`).
3. **Nombre logico = `media-` + 64 hex minusculas** (70 caracteres, sin punto). Sin punto no hay extension reconocible, asi
   que el motor no puede tratar la imagen como PC/ST/IMG. `p6_blob_register` copia la imagen al almacen de blobs del
   motor (id valido: alfanumerico, `_`, `-`, un punto simple; nunca rutas ni URI). Nunca existe un path del host.
4. **Sin nombres implicitos.** `build_preferences` vacia `floppyslots[0..3].df` (upstream pone `df0.adf`..`df3.adf`) y
   fija `floppy_read_only` (el interruptor general de `drive_writeprotected`); `check_preset` exige ambas cosas y el perfil las
   declara como `override`. No se fija `forcedwriteprotect` por unidad: `reset_drive` reinserta los discos con ese parametro a
   falso y borra el valor (medido), asi que no seria una garantia. La proteccion descansa en tres vias del motor
   (`floppy_read_only`, la rama P6 de `DISK_validate_filename` y un blob sin `data/allocsize` que devuelve EROFS) y
   `drive_write_data` sale sin escribir si la unidad esta protegida.
5. **Vida util.** Los buffers del llamante deben vivir hasta `p6_unload` (contrato ya escrito), pero el bridge no guarda
   punteros: el motor trabaja sobre su copia (14,4 MB para 16 discos, que cuentan para heap y snapshot de Waterbox, T19).
   Una carga que falla libera lo registrado (`release_engine` limpia los blobs); un fallo de validacion no registra nada.
6. **Capacidad.** `p6_caps.max_media = P6_MAX_MEDIA (16)`. El bit `P6_CAP_MEDIA_EVENTS` sigue apagado: los eventos de
   mount/eject son T12b. El almacen tiene 32 assets, de sobra para firmware + 16 discos.

## Eventos de medios (T12b)

7. **Cola acotada y validada al encolar.** `p6_media_event_enqueue` (16 eventos como mucho) rechaza sin dejar rastro, y sin
   consumir la secuencia: estado distinto de Loaded (BAD_STATE), versiones o campos reservados (BAD_ARGUMENT), tipo desconocido,
   unidad > 3 (BAD_ARGUMENT), unidad 2 o 3 (UNSUPPORTED: el A500 solo tiene DF0 y DF1), MOUNT con indice fuera de la lista o
   con la marca de expulsion, EJECT con un indice, secuencia no mayor que la ultima aceptada (BAD_ARGUMENT; la primera puede
   ser cualquiera) y cola llena (CAPACITY). Los errores del motor no llegan al llamante: la cola solo contiene medios ya
   registrados, porque el motor ignora el resultado de una insercion fallida.
8. **Aplicacion en el borde**, tras la cabecera del reset y antes de la entrada: MOUNT llama a `disk_insert` y EJECT a
   `disk_eject`, y ambos reflejan el disco en `changed_prefs` para que un reset lo conserve. Un MOUNT del disco que ya esta en
   la unidad no hace nada (el motor tampoco), un EJECT de una unidad vacia tampoco, y un MOUNT sobre una unidad ocupada usa el
   cambio retardado nativo (el motor expulsa en el siguiente vsync e inserta 31200 hsync despues: unas 100 unidades PAL, ~119
   NTSC, para que los programas detecten el cambio). Un EJECT cancela un cambio que aun no ha llegado.
9. **Disco de arranque.** Los eventos encolados antes del primer `p6_run_frame` solo fijan las preferencias: gana el ultimo de
   cada unidad y la cabecera del reset inserta el disco de inmediato, con /CHNG alto. Aplicarlos despues del reset costaria
   120 unidades de unidad vacia. `disk_insert` antes de ese reset se perderia, por eso se escriben las preferencias.
10. **Sin proteccion por unidad.** `forcedwriteprotect` de cada ranura no se puede fijar (ver 4); se pone en `changed_prefs`
    solo porque el motor lo lee en su comprobacion de cambios.

## Limites

- Solo ADF de doble densidad de 901120 bytes. HD, ST/IMG, IPF, SCP, ADZ y DMS quedan fuera del MVP. (El ADF extendido
  `UAE-1ADF`/`UAE--ADF` entra con T57a: ver mas abajo.)
- Dos discos con los mismos bytes no son dos medios (id duplicado): el llamante cambia de disco por indice, no por copia.
- El motor sigue aceptando imagenes de otros tamaños si alguien las registrara fuera del bridge; la barrera esta en `p6_load`.
- El disco de arranque (montado antes del primer frame) y los cambios en caliente llegan con T12b.

## Alternativas descartadas

- Confiar en el `content_id` del llamante: no detecta "disco equivocado" en la carga y deja pasar un ADF extendido hostil.
- Reutilizar SHA-1/CRC32 del motor: la identidad del contrato es SHA-256 y los tests de Python ya la usan.
- Pasar por el motor un path virtual con extension `.adf`: reabre la rama PC-format y el reconocimiento por extension.

## ADF extendido (T57a)

11. **Un formato dentro del tipo ADF, no un tipo nuevo.** `P6_MEDIA_ADF` es una imagen de disquete y su formato lo dicen los bytes: `classify_floppy`
    (ports/portable/media/trackdisk.h) distingue el ADF plano de 901120 bytes, `UAE-1ADF` (version 2) y `UAE--ADF` (version 1) y rechaza
    el ejecutable Amiga como siempre. La capacidad `P6_CAP_MEDIA_EXT_ADF` (bit 7) anuncia que el puente los acepta. La identidad sigue siendo la del punto 2
    ampliada: el SHA-256 de los bytes del fichero tal como se dieron (no hay forma normalizada de una pista cruda, que un ADF no puede representar).
12. **El validador sustituye al tamaño.** Para estos formatos el tamaño no basta: el lector de upstream toma del fichero el numero de pistas (la tabla del
    motor tiene 166), el desplazamiento y la longitud de cada una y los bits de las crudas (el buffer MFM tiene 0x8000 palabras) sin comprobar nada. El
    validador comprueba cada numero contra los bytes que va a leer (docs/02-contracts.md, "ADF extendido") y su hermano independiente, escrito
    otra vez en `tests/media/test_trackdisk_formats.py` y en el objetivo `trackdisk` del fuzzer, discute cada byte de la cabecera. El motor no se toca para leerlos.
13. **Cotas.** 3 MiB por imagen y, entre todos los disquetes del manifiesto, lo que ocupan 16 ADF planos (14 417 920 bytes): el heap sellado de Waterbox y su
    presupuesto (docs/03-waterbox.md) se dimensionaron para eso y no cambian. Pasarse es `CAPACITY` (en `p6_load` y en `p6_media_append`), nada queda registrado.
14. **Solo lectura.** El overlay de T27 es de sectores AmigaDOS y de pistas crudas de la geometria del ADF plano; un ADF extendido tiene pistas de longitud propia y
    no se escribe: sigue protegido con `P6_CONFIG_FLOPPY_WRITES`, `p6_media_write_status_get` lo da vacio y `p6_media_export` responde `UNSUPPORTED`.
15. **FDI (T57b).** Otro formato dentro del tipo ADF (`P6_CAP_MEDIA_FDI`), con el mismo validador (`classify_floppy`, la cabecera de 512 bytes, el CRC-32 de las versiones 2.1 y posteriores, 166 pistas,
    cada pista dentro del fichero), los mismos topes y el mismo caracter de solo lectura. De los tipos de pista del formato entran tres: vacia, Amiga de doble densidad y de bajo nivel (las transiciones de flujo
    de una vuelta, con sus bits debiles); las demas se rechazan porque `fdi2raw` las lee sin comprobar el final del dato. El decodificador de upstream se compila (`fdi2raw.cpp`, parches 0332 a 0335): acotado
    (cada lectura contra el final de los bytes de la pista y limites fijos: pulsos, profundidad del arbol de Huffman, bits de salida, pasos), determinista (los sorteos de los bits debiles salen de un generador
    del decodificador sembrado por pista y revolucion, no del del motor) y con su estado (docs/adr/0009-state-format.md, punto 17). No se usa capsimg ni ninguna biblioteca.
16. **Enganche para IPF (T58).** El lector de IPF producira pistas MFM con su temporizacion por celda; su sitio natural es `drive_fill_bigbuf` junto a las pistas de
    este formato (la rama `ADF_IPF`, hoy sin compilar, y `fetchnextrevolution` para varias vueltas), con el estado de pista de este punto 12 mas el contador de
    revolucion de T57b, y el validador de `trackdisk.h` como compuerta antes del motor (un formato nuevo es un `Format` mas y una funcion de comprobacion).
