# ADR 0009: formato del estado nativo y separacion del snapshot Waterbox

(La ficha de T14 lo llamaba 0003; ese numero ya es la seleccion de CPU.)

## Contexto

docs/02-contracts.md pide, para libretro y headless, un estado nativo con envelope versionado que valide cabecera, identidad y
limites antes de tocar la maquina, y dice que se reutilice el serializador semantico de upstream "donde sea valido". La auditoria de
T14a (manifests/state-inventory.json, clave `savestate`, 142 componentes, 126 dentro del perfil A500) muestra que ese punto de partida
no existe en el binario y que, donde existe, no describe una maquina que continua bit a bit:

- `SAVESTATE` no esta definido por el perfil y todo el contenedor de savestate.cpp (cabecera ASF, `save_chunk`, `save_state_internal`,
  `restore_state`) esta bajo `#ifndef P6_PORTABLE`. No hay formato upstream que reutilizar y los `save_*`/`restore_*` de CPU, chipset,
  CIA, disquetes y blitter no estan compilados. Solo lo estan `save_audio`, `save_input` y los serializadores BPLX y de sprites de
  drawing.cpp, y nadie los llama.
- Los serializadores upstream son para restaurar y volver a correr parte del reset, no para continuar: no guardan `halted` ni
  `spcflags`, el anillo de entrada de Denise `rga_denise`, el TOD pendiente de CIA-B, la fase de sondeo del teclado, las fases del
  filtro y del resampler de audio, `dsklen2`, `linecounter`, el cambio de disco retardado, los registros de control del RTC, ni el
  estado vivo del PRNG; el de CIA tiene un error (asigna el latch del timer B al del TOD) y `save_cia` modifica la CIA viva.
- La frontera de unidad cae en mitad de una linea (vpos 4, hpos ~1), con casi una linea de entrada de Denise sin consumir.
- Estado que no se alcanza desde fuera: `suspended` y `hardboot` de `m68k_go_unit` (tras una carga fresca fuerzan la cabecera de
  reset completa), el puntero `next` del scheduler secundario, el anillo de teclas de keybuf.cpp.

De los 126 componentes del perfil, 75 necesitan codigo propio (41 `extension` y 34 `serialize_extended`), 23 son reconstruibles y solo 11
se sirven con un serializador upstream tal cual. Los hallazgos transversales estan en `savestate.findings` del inventario.

## Decision

1. **Contenedor propio, cuerpos propios.** T15 escribe el envelope y un serializador por subsistema, campo a campo (u8/u16/u32/u64 little
   endian, sin padding, sin punteros, con cuentas y limites explicitos). Los `save_*` upstream se leen como catalogo de lo que cada
   modulo considera estado; no se habilita `SAVESTATE` para reutilizarlos tal cual. Donde un serializador upstream sirva, se copia su
   codificacion de campos, no su llamada. **Revisado el 2026-10-06 (punto 8):** se mantiene para los chunks ya hechos (CPU,
   scheduler, RTC, CIA, entrada) y la sesion del bridge; disco, audio, chipset y Denise pasan a cuerpos upstream.
2. **Cabecera de 336 bytes, little endian:**

   | offset | tamaño | campo |
   | --- | --- | --- |
   | 0 | 8 | magic `P6NATIVE` |
   | 8 | 4 | format_version (1) |
   | 12 | 4 | header_size (336) |
   | 16 | 4 | profile_id (1 = A500 determinista) |
   | 20 | 4 | flags (0; cualquier otro valor se rechaza) |
   | 24 | 8 | unit_index de la frontera en que se guardo |
   | 32 | 8 | master_ticks de esa frontera |
   | 40 | 8 | payload_length |
   | 48 | 4 | chunk_count |
   | 52 | 4 | reservado (0) |
   | 56 | 32 | core_build_id |
   | 88 | 20 | upstream_commit (binario) |
   | 108 | 4 | reservado (0) |
   | 112 | 32 | patch_series_hash |
   | 144 | 32 | codegen_hash |
   | 176 | 32 | config_hash |
   | 208 | 32 | media_manifest_hash |
   | 240 | 32 | firmware_sha256 |
   | 272 | 32 | payload_sha256 |
   | 304 | 32 | header_sha256 |

   `header_sha256` = H("p6/native-header/v1" ‖ los primeros 304 bytes) y `payload_sha256` = H("p6/native-payload/v1" ‖ el payload). El payload son `chunk_count` chunks seguidos, cada uno con
   16 bytes de cabecera (`tag` de 4 caracteres, `chunk_version` u32, `body_length` u64) y su cuerpo. Un chunk por subsistema del
   inventario (cpu, scheduler, ram, rtc, chipset, video, audio, cia, disk, input, bridge), con etiquetas `P6xx`; un chunk repetido,
   desconocido, fuera de orden o ausente se rechaza.
3. **Identidades, todas con SHA-256 y un prefijo de dominio distinto:**
   - `config_hash` = H("p6/config/v1" ‖ machine, video_standard, audio_rate_hz, flags, random_seed, rtc_epoch_seconds), cada campo en su
     ancho fijo LE. La semilla y la epoca del reloj forman parte de la identidad porque cambian el futuro.
   - `media_manifest_hash` = H("p6/media-manifest/v1" ‖ cuenta u32 ‖ por cada medio: tipo u32, content_id de 32 bytes), en el orden del array.
     Es el hash del prefijo del manifiesto con el que se guardo el estado (T17e): el manifiesto solo crece por el final (`p6_media_append`), la
     cuenta forma parte del hash y la carga la recupera buscando el unico prefijo del manifiesto vivo que da ese hash; cargar exige que el
     manifiesto vivo empiece por ese prefijo y que todo indice de medio del estado (P6BR: unidades y cola de eventos) sea menor que su cuenta.
     La comparacion de identidad del paso (5) usa ese prefijo; si ninguno coincide el estado es INCOMPATIBLE como antes.
   - `firmware_sha256` = SHA-256 de los bytes del firmware (ya lo guarda `Session`).
   - `core_build_id` = H("p6/core-build/v1" ‖ format_version ‖ upstream_commit ‖ patch_series_hash ‖ codegen_hash ‖ hash de los fuentes del
     bridge y de ports/portable ‖ profile_id). El compilador y sus flags de coma flotante no entran hasta que una prueba de determinismo
     entre compiladores demuestre que hace falta (los ticks, la imagen y el audio de macOS, Linux y Waterbox coinciden hoy); se
     registra como riesgo.
   Hoy no existe ningun `codegen_hash` y `P6_UPSTREAM_COMMIT`/`P6_PATCH_SERIES_SHA256`/`P6_PROFILE` son definiciones solo de `p6_headless`:
   T15 añade al bridge una unidad de identidad de build que los reciba.
4. **Orden de validacion de `state_load`, sin tocar la maquina hasta el ultimo paso:** (1) longitud ≥ header_size; (2) magic, version,
   header_size, flags y reservados; (3) `header_sha256`; (4) `payload_length` igual a longitud − header_size y ≤ `state_bound`, con
   aritmetica u64 comprobada antes de reservar nada; (5) identidad campo a campo (un campo distinto es INCOMPATIBLE y el mensaje dice
   cual); (6) `payload_sha256` (CORRUPT_STATE); (7) analisis estructural a un area de preparacion: tabla de chunks, versiones, longitudes
   y, por chunk, rangos, enumeraciones y cuentas; (8) aplicacion. Como (1)-(7) validaron todo, un fallo en (8) es un error interno: la
   imagen pasa a Faulted (solo `p6_unload` la recupera) y nunca queda medio restaurada en silencio. Una corrupcion tardia del payload
   se detecta en (6) o (7), antes de (8).
5. **`state_bound` se calcula al cargar** con el maximo del perfil y de los medios (RAM segun la configuracion, tamaños fijos, y el
   maximo de cada lista: anillo de Denise, eventos, cola de eventos de medios, anillo de teclas, cola de audio) y no crece hasta el
   unload; `retro_serialize_size` sigue en 0 hasta que T18 lo conecte.
6. **Se guarda en la frontera tal cual esta**: con el anillo `rga_denise`, los latches del haz, la fase de sondeo del teclado y el
   estado vivo del PRNG. Drenar el anillo o rehacer parte del reset para tener una linea limpia cambiaria el futuro. Una restauracion no
   ejecuta la cabecera de reset: T15 necesita parches que pongan `suspended` y `hardboot` de `m68k_go_unit` en su valor de una maquina
   que ya arranco y que serialicen el estado que hoy es local a una funcion o a un fichero.
7. **Waterbox es otro formato.** Un snapshot Waterbox es el de las paginas sucias del sandbox mas el estado C# del wrapper; no pasa por
   este serializador y no lo necesita. Se separan por construccion: magic distinto (`P6NATIVE` frente a `P6WATERB` en el envelope del
   wrapper), el cargador nativo rechaza `P6WATERB` (y el wrapper `P6NATIVE`) sin analizar nada, y cada hash usa su propio dominio
   (`p6/…` para el nativo, `p6/waterbox-…` para el wrapper, que ademas incluye el hash del WBX, del runtime y del layout). Los digests
   canonicos de los probes (T14b) usan el dominio `p6/probe/v1/<subsistema>` y se calculan de campos, no de paginas; el digest de un
   snapshot Waterbox es el SHA-256 de los bytes del runtime. Los dos no son comparables entre si ni entre builds, y ninguno promete
   compatibilidad cruzada ni con los `.uss` de WinUAE.

8. **Revision 2026-10-06: cuerpos upstream para disco, audio, chipset y Denise.** El codec campo a campo de esos subsistemas
   (T15d1-T15f1b4h2b2b2a, parches 0086-0137) llevo cinco dias y unas 85 subtareas sin una carga publica ni un DET02 de punta a punta,
   y su coste crecia con cada campo (y creceria otra vez con ECS/AGA). Queda en la rama `archivo/t15-codec-por-campos` (c5c137a)
   como oraculo y fuente de escenarios. Nueva regla:
   - WinUAE 6.0.3 serializa los registros y parte del estado en vuelo, pero no la posicion del haz (lee VPOSR/VHPOSR y los descarta),
     el anillo rga_denise ni el Copper en espera: su restore lo rehace con `custom_reset`. Esas piezas son extensiones nuestras.
     Cuerpos: CHIP, CHPX, CHPD (retardos de eventos), CHSL (slots), SPRn, BLIT/BLTX,
     BPLX/SPRX, AUDn, DISK/DSKn. Esos `save_*`/`restore_*`/`*_finish` se compilan en el perfil con parches de guarda, sin definir
     `SAVESTATE` globalmente: en los ficheros A500 casi todos los `#ifdef SAVESTATE` solo envuelven esas funciones; los tres sitios
     que cambian comportamiento (`m68k_go`, `quitstatefile`, `savestate_init`) quedan fuera.
   - Sus bytes van, como subcuerpos con tag y longitud (sin contenedor ASF ni zfile), en el chunk de su subsistema: P6DK (DISK, DSKn,
     DSDn), P6AU (AUDn), P6CH (CHIP, CHSL, BLIT, BLTX, SPRn) y P6VD (BPLX); las extensiones van en el mismo chunk. No se promete
     compatibilidad `.uss`. Lo que ya poseen P6CP/P6SC/P6RT/P6CI/P6IN/P6BR no se duplica.
   - Apply restaura sobre la maquina viva sin `custom_reset`, `m68k_reset2` ni `memory_clear` (el camino upstream de `m68k_go` si los
     ejecuta, y eso no continua bit a bit). DET02 decide que falta: cada perdida se cubre con una extension pequeña o un parche al
     cuerpo upstream, no con un serializador paralelo.
   - **Validacion**: hash, identidad y estructura (longitudes, cuentas, indices y enumeraciones que acaben indexando) antes de Apply;
     un subcuerpo upstream debe tener la longitud que produce `save_*` de este build para la config y los medios cargados. No se exige
     una continuacion segura para valores que este build no produce: no se amplia ni se blinda la aritmetica del motor frente a
     magnitudes construidas. Los limites naturales (por ejemplo, contadores i32 tras decenas de horas emuladas) se documentan.
   - Orden: T15h (sesion), T15i (cuerpos upstream en sus chunks + Apply sin reset + DET02 de punta a punta en los micro-ROM; T15i1-T15i3), T15d/T15e/T15f/T15g
     (extensiones guiadas por DET02) y el cierre T15.

9. **Revision 2026-10-08 (T24c): el A1200 (68EC020 cycle exact, AGA, 2 MiB de Chip, sin reloj).** Misma regla del punto 8 (cuerpos upstream donde existen, extensiones minimas guiadas
   por DET02, sin codec por campos paralelo), y el mismo contenedor: once chunks, mismas etiquetas, orden y version 1. El cuerpo de dos chunks sigue a la maquina, como el de P6RT en el
   A600 (la maquina forma parte de la identidad, `config_hash`); el layout del A500 y del A600 no cambia en un solo byte con respecto a T23b.
   - **P6CP**: tras el cuerpo del 68000 (sin cambios; la mascara de SR pasa de 0xA71F a 0xF71F, T0 y M) un bloque de 662 bytes: VBR, MSP, CAAR, SFC, DFC, CACR; la cola de prefetch
     con su pipeline y la cache de instrucciones de 64 lineas (lo que el `save_cpu` de upstream guarda para un 68020; ese cuerpo esta bajo SAVESTATE, asi que se lee de `regs` y de
     `caches020`, estatica del fichero, mediante el parche 0108); y lo que el nucleo cycle exact guarda entre dos instrucciones y que DET02 demostro decisivo, campo a campo:
     `ce020extracycles` (un contador de 16 instrucciones que paga dos ciclos mas; una carga en proceso nuevo desplazaba la interrupcion cuatro ciclos), `ce020prefetchendcycle`
     (un prefetch que empieza en el ciclo en que acabo el anterior paga dos; `master`, proceso nuevo), `ce020startcycle`/`ce020endcycle` (el tramo que `do_cycles_ce020_internal`
     solapa con el ciclo de memoria en vuelo; `tone`, misma imagen), el resto sub-CCK de `do_cycles_ce020` (una estatica local de funcion, parche 0108; misma imagen) e
     `instruction_cnt` (lo leen los hacks de audio que un 68020 activa, comparado con el `dmaofftime_cpu_cnt` absoluto que AUDP ya guarda; escenario `audio-dma-wait`, con los
     limites dentro del hueco). No viajan, con su razon en el inventario: `cacheholdingdata_valid` y `fc030` (solo el prefetch del 68030, no compilado), `memory_waitstate_cycles`
     (solo las placas de CPU), `ce020memcycle_data` y `ce020_tail*` (se escriben antes de leerse o estan bajo `#if 0`). `p6_cpu_boundary` deja pasar T0, M, VBR, SFC, DFC y MSP en un
     68020 y los sigue rechazando en un 68000. Cada campo del bloque tiene un poke del probe que lo cambia en la maquina viva y cambia el futuro (`A1200NecessityTests`).
   - **P6CH**: el sub-cuerpo CAGA, la paleta AGA de 256 entradas de 24 bits mas el bit de genlock (1024 bytes, `save_custom_agacolors`/`restore_custom_agacolors`, compilados bajo
     `P6_STATE_BODIES` desde el parche 0108), justo despues de CHIP y solo en una maquina AGA (CHIP guarda 12 bits de las 32 primeras). BPLCON3, BPLCON4 y FMODE ya viajan en CHIP;
     FMODE se valida con la mascara del registro (0xC00F). El id 12 del vocabulario de eventos es `setup_fmodes_delayed`, alcanzable solo con AGA. `p6_custom_rebuild_derived`
     recalcula tambien `fetchmode_size`/`fetchmode_mask`, que `setup_fmodes` omite una vez que `fmode_inuse` iguala a FMODE.
   - **P6VD**: PIXL viaja a **3 bytes por pixel** (R, G, B, en los mismos grupos de literales y repeticiones) en una maquina AGA, porque el codigo de 12 bits no puede nombrar un color de
     24 bits y la imagen iria casi toda en crudo; un pixel con bits sobre los 24 hace fallar el guardado con un mensaje claro (el perfil no los produce). El A500 y el A600 conservan
     el codigo de 12 bits. Ademas, en una maquina AGA, DRNG: el anillo por el que Agnus entrega sus datos a Denise (4096 ranuras de 22 bytes y sus cinco cursores, 90132 bytes, los
     cursores por el parche 0109). El punto 6 supuso el anillo ocioso en la frontera; un programa que reescribe FMODE sin parar lo mantiene lleno y las primeras posiciones de una
     linea dependen de lo que las ranuras guardan de la vuelta anterior del anillo y de donde esta el cursor (DET02 `aga-fmode-toggle`, imagen reiniciada: veinte pixeles a la izquierda
     de la primera linea del display distintos tres cuadros despues de la carga; poniendo solo `rga_denise_cycle` al valor del original, la imagen era la del original).
   - **Transitorio de FMODE tras una carga (hallazgo de T24c, cerrado en T24): inocuo, medido, no corregido.** La maquina cargada tiene `fetchmode_fmode_bpl/spr` (Agnus) y `fmode_denise`/
     `denise_sprfmode`/`denise_bplfmode` (Denise) con el FMODE de los registros desde la carga, y la original los cambia despues: Agnus al disparar el evento `setup_fmodes_delayed` (id 12, 2 CCK tras
     la escritura) y Denise cuando su pase de linea consume la ranura de FMODE del anillo (que DRNG lleva). Con una escritura de FMODE por el Copper en la linea 3, CCK $DE, el evento queda pendiente en la frontera
     de unidad (el probe lo confirma en la tabla de eventos). Medido con lldb sobre `p6_state_probe` (A1200, estado de la unidad 3 cargado en un proceso nuevo): la original dispara el evento en (vpos 4, hpos 4)
     y su Denise toma el FMODE nuevo en el pase de la linea 4, que corre en (4, 22); la cargada tiene los valores de Agnus 1 CCK antes y los de Denise desde la carga. No hay consumidor en medio: una unidad
     acaba en el borrado vertical (vpos 4, hpos 3 o 4), el DMA de planos empieza en el CCK $18 como pronto, el DMA de sprites no corre por encima de la linea 25 (un sprite con vstart 5 no aparece: su imagen es la
     del programa sin sprite), y Denise solo se mueve en sus pases, cuyo primer paso tras la carga consume la ranura de FMODE antes que cualquier otra (`do_denise_cck` -> `expand_fmode`), y despues las de
     SPRxPOS/SPRxCTL/DATA que el Copper escribe tras la frontera, en el mismo orden que la original. `tests/state/test_fmode_transient.py` pone el peor caso (fetch de 16, 32 y 64 bits en los dos sentidos, por
     separado para planos y sprites, con y sin un sprite manual armado por el Copper en los CCK siguientes a la frontera, DDFSTRT $18) y comprueba que el evento esta pendiente en cada frontera guardada: 150 pruebas DET02
     iguales en los cinco modos (misma imagen, imagen reiniciada, proceso nuevo, con y sin el haz puesto). Limite: la prueba vale para la frontera de un haz estandar (PAL y NTSC, borrado vertical hasta la linea 4); con un haz programado (BEAMCON0) que acabe la unidad en otro sitio no se ha buscado, y
     si algun dia un programa lo hiciera visible, lo que faltaria en el estado seria el FMODE de Denise con su evento, no una tolerancia en DET02.
   - **P6AU**: el estado de un canal acepta tambien 0x13 (un canal en estado 3 al que se apaga el DMA sigue con un periodo manual y marca el estado con 0x10); la validacion lo rechazaba
     y el guardado fallaba. Es una correccion de la validacion, comun a las tres maquinas; el layout no cambia.
   - **Cota por maquina** (`retro_serialize_size`/`state_bound`, medidos en el probe): A500 3 608 513 bytes, A600 3 608 482 (31 menos: sin cuerpo de reloj), A1200 5 890 716 (cada una 52 mas desde T15l, punto 13). La del A1200 es
     la del A600 mas 1 MiB de Chip (2 MiB frente a 1), 8 + 1024 (CAGA), 8 + 90132 (DRNG), 662 (bloque del 68020) y el raster a tres bytes por pixel en lugar de dos
     (1824 x 626 = 1 141 824 bytes mas); `tests/state/test_state_a1200.py` lo comprueba al byte. Un estado de un A1200 con un programa de dos colores ocupa unos 2,2 MB; con una imagen AGA
     llena de colores, algo mas; no hay compatibilidad entre maquinas: un estado de una es INCOMPATIBLE en otra por `config_hash`, antes de tocar nada.
10. **Revision 2026-10-09 (T27): el overlay de escritura de los disquetes es estado, la identidad del medio sigue siendo su base.** Con `P6_CONFIG_FLOPPY_WRITES`
    (que forma parte de `config_hash`) el chunk P6DK lleva, despues de los cuerpos del motor, el pool de sectores que el invitado escribio: u32 cuenta (<= 3520), u64
    escrituras rechazadas, y por sector u8 medio, u16 sector, 512 bytes, en el orden de primera escritura. Decisiones: (a) la identidad de un medio (content id, el CRC que
    guarda DSKn, `media_manifest_hash`) es la de su BASE inmutable y no incluye el overlay, de modo que un estado guardado antes de escribir sigue cargando y devuelve el
    disco como estaba, y `disk_matches_manifest` no cambia; el overlay no es identidad sino contenido, como la RAM, y cargar un estado lo SUSTITUYE entero; (b) el pool es de
    tamaño fijo y su tamaño completo entra en `state_bound` (4 + 8 + 3520 x 515 bytes, mas 4 + 16 x (6 + 12784) de las pistas crudas, 2 017 456 en total) aunque el estado lleve menos, asi que serializar no crece con las escrituras; sin el flag
    no hay cuerpo y los estados no cambian ni de bytes; (c) se valida antes de aplicar: cuenta <= pool, medio dentro del manifiesto del estado, sector < 1760, ningun
    sector repetido; (d) la granularidad es el sector porque es en lo que el motor cambia un ADF (decodifica la pista MFM y escribe sectores por zfile); el estado de pista
    y MFM (buffer, posicion, DMA en curso) ya viaja en DSDn/DISK/DSKX y la escritura es sincrona al fin del DMA (`drive_write_data`), asi que no hay escritura diferida
    fuera de ellos. (e) Una pista escrita que no decodifica en once sectores (formato propio) no es una escritura de sectores y un ADF no tiene sitio para ella: se guarda tal
    cual en un segundo pool (16 pistas de 12784 bytes) que sigue al de sectores en el mismo cuerpo (u32 cuenta, y por pista u8 medio, u8 pista, u32 bits y las palabras
    MFM), con las mismas validaciones, y el disco con una pista cruda no se exporta como ADF (UNSUPPORTED) en lugar de perderla sin decirlo.
11. **Revision 2026-10-10 (T29c): el controlador de un disco duro y su overlay son estado; la base es identidad.** Con al menos un `P6_MEDIA_HDF` el chunk P6DK lleva, despues de los cuerpos del motor y
    antes del overlay de los disquetes, la seccion de `bridge/state_hdf.cpp`; sin HDF no hay seccion y el chunk no cambia ni de bytes. Decisiones: (a) cuerpo propio, no `save_filesys`: el de upstream guarda la ruta
    del host de cada unidad y el fichero del sistema de ficheros, y reconstruye las unidades montandolas; aqui las unidades las reconstruye cada carga desde la configuracion de montaje y la seccion lleva lo que el
    arranque y el invitado les hicieron, mediante accesores de los parches 0180-0182 (valores y bytes del invitado, ningun puntero ni cadena del host: `rdb_filesysstore` viaja como contenido, a lo sumo 262144 bytes que
    `pt_rdsk` ya acota); (b) orden: numero de HDF y content id de cada base, globales de `filesys.cpp`, por HDF la unidad de `filesys` y la de `uaehf.device`, zona de traps, placa UAEFS, cadena autoconfig si no hay Fast RAM,
    cola EXTER y los overlays; (c) el overlay lleva solo los trozos de 4 KiB sucios, como lista de indices ascendentes con sus bytes (el bitmap disperso: el indice ES el bit, y un trozo sin nombrar es el de la base);
    cargar lo SUSTITUYE entero; (d) la identidad de la base es su content id, comprobado en dos sitios antes de tocar la maquina: el `media_manifest_hash` de la cabecera (que ya incluye tipo y content id) y, ya
    validado el cuerpo, `hdf_matches_manifest` (el content id de la seccion contra el registrado), ambos INCOMPATIBLE. La busqueda del prefijo del manifiesto (T17e) solo acepta un prefijo que contenga todos los HDF,
    porque un HDF no se anexa: sin ello el estado de `[disquete]` seria el de `[disquete, HDF]`; (e) `state_bound` es el maximo con todos los trozos sucios (tamaño de la imagen + 4 bytes por trozo) mas el maximo del
    controlador, y no cambia durante la vida de la imagen (el contrato de la cota); el estado real es mucho menor; (f) la cadena autoconfig de una maquina con HDF tiene la placa UAEFS y la ROM de arranque UAE
    ademas de la tarjeta de Fast RAM si la hay: `visit_expansion` (P6RM) cuenta tarjetas segun haya HDF y la seccion la lleva cuando no hay Fast RAM; (g) la posicion en el anillo de la cola EXTER no es comportamiento
    (se guardan los mensajes en orden y se vuelve a empezar en 0), y la linea de peticion `uae_int_requested` se guarda aparte porque el invitado la baja leyendo un byte de la zona de traps. Pruebas:
    `tests/state/test_det02_hdf.py` (DET02 con el controlador ocupado y con los Kickstart 1.3 y 3.1 del propietario, opt-in; cada componente se comprobo anulando su aplicacion y la prueba falla),
    `tests/state/test_state_hdf.py` (STATE01 por tamaño de HDF, identidad de la base, cuerpos hostiles, DET03 entre procesos y re-guardado byte a byte).

## Evidencia

- Inventario: 142 componentes; dentro del perfil: 41 `extension`, 34 `serialize_extended`, 23 `rebuild`, 11 `serialize`, 17 `noncausal`.
  Diez componentes tienen un serializador upstream compilado hoy y ninguno de ellos es un subsistema completo.
- Verificacion por muestreo contra el codigo (lo leido por los agentes de la auditoria): `static bool suspended` y `static int hardboot`
  en `m68k_go_unit`; `c->tol = w` en `restore_cia`; `SPCFLAG_UNIT_END` una vez por campo en custom.cpp; `static int next` en
  `event2_newevent_xx`; `keybuf_init` solo desde bridge/lifecycle.cpp; el reset head reseedea el PRNG a `currprefs.seed`.
- Un fallo de sesion que la auditoria destapo y que T13d corrige: tras mover el raton en una sesion, una recarga de `input_all` lee
  JOY0DAT $0A10 donde un proceso limpio lee $0101.

## Alternativas descartadas

- **Copiar el segmento de datos o el heap del proceso:** punteros, ASLR y padding lo hacen distinto en cada proceso y no es portable
  entre macOS y Linux; es lo que hace Waterbox por otra via y por eso Waterbox no necesita esto.
- **Habilitar `SAVESTATE` y usar `.uss` tal cual:** arrastra ramas de ejecucion protegidas por `SAVESTATE` en doce ficheros, el contenedor esta
  fuera de la compilacion, el formato no lleva identidad ni limites, y sus serializadores pierden el estado de la lista anterior. Prometeria
  una compatibilidad `.uss` que no tendriamos.
- **Un serializador solo para los campos que un diff empirico detecte** (como el volcado del segmento de datos de T13b): no da catalogo, y
  lo que no cambia en los micro-ROM cambiaria con un Kickstart.
- **Un unico hash de estado comun a nativo y Waterbox:** confundiria dos contratos distintos y haria creer que un estado vale en ambos.

## Costo

- Setenta y cinco componentes con codigo propio y parches (presupuesto del ADR 0004: hoy 74 parches; se espera un grupo de parches de
  acceso a estado por fichero, no cambios de comportamiento).
- Tamaño estimado de un estado: RAM chip + slow (del orden de 1 MiB con la configuracion A500 habitual) mas unas decenas de KiB de
  estado de chipset y scheduler; a comparar con los 9,1 MB del snapshot Waterbox tras una unidad, que captura paginas enteras.
- Cada actualizacion de upstream cambia `patch_series_hash` y por tanto invalida los estados guardados: es intencionado.

10. **Revision 2026-10-09 (T49): memoria configurable y una placa de Fast RAM Zorro II.** Mismo contenedor (once chunks, mismas etiquetas, orden y version 1) y la misma regla: el
    cuerpo de un chunk sigue a la configuracion, que forma parte de la identidad. Una maquina cuya memoria es la de su preset no cambia un solo byte (el layout, la cota y el
    `config_hash` del A500, el A600 y el A1200 son los de antes).
    - **`config_hash`**: el sobre no cambia (`machine`, `video_standard`, `audio_rate_hz`, `flags`, `random_seed`, `rtc_epoch_seconds`). La memoria entra en la palabra `flags` del
      hash cuando NO es la del preset: bit 31, `fast_kib / 1024` en los bits 8 a 11, `slow_kib / 64` en 12 a 16 y `chip_kib / 64` en 17 a 22; los bits 0 a 7 siguen siendo `p6_config.flags` (T27 usa el 0) (con los tamanos efectivos: pedir la
      memoria propia de la maquina, con ceros o con sus tamanos, es la misma configuracion). `p6_config.flags` solo lleva banderas definidas (`validate_config` rechaza cualquier otro bit), de modo que la memoria nunca choca con ellas.
    - **P6RM**: con Fast RAM el cuerpo anade, tras Chip y Slow, `ram.fast_size` (u32, el configurado, no el asignado: la placa se asigna al cargar la imagen y la cota y la
      validacion de una imagen que no ha empezado lo conocen), los bytes de la placa y el bloque de la cadena autoconfig (22 campos y 256 bytes de nibbles; parche 0112): la tarjeta
      en la que esta el invitado, el byte bajo de la base a medio escribir, los nibbles que presenta, la base de la placa (la que sugiere la cadena, la que le dio el invitado, o
      `$FFFFFFFF` tras un shut-up), si responde en esa base y si la ventana de `$E80000` sigue contestando. El `apply` desmapea la placa viva, pone la cadena y mapea la placa donde
      la tenia el estado (`map_banks_z2`); el mapa de bancos y las tablas CE siguen siendo derivados. Cada campo se valida en rango y contra los demas antes de tocar nada.
    - **Cota**: la del preset mas `4 + fast + 278` bytes (278 = el bloque de la cadena); es la misma antes de la primera unidad y despues.
    - Prueba: `tests/state/test_state_fast.py` (layout, cota, identidad, rechazo campo a campo, DET02 en cada paso de la cadena en el A1200, el A600 y el A500) y, con firmware autorizado,
      `tests/state/test_det02_fast.py` (Kickstart 1.3, 2.05 con el Workbench 2.05, y 3.1 con una demo AGA).

11. **Revision 2026-10-10 (T54): la FPU 68881/68882.** Misma regla: el cuerpo de P6CP sigue a la configuracion (el modelo esta en `config_hash`, bits 23-24 de la palabra `flags`, sin
    tocar el hash de las maquinas sin FPU). Con FPU, P6CP anade tras el bloque del 68020 un bloque de **110 bytes**: FP0-FP7 (u16 signo/exponente + u64 significando, patrones crudos: cualquier
    valor es valido), FPCR (solo los bits de `0xFFF0`), FPSR (`0x0FFFFFF8`), FPIAR, `fpu_state` (0..1), `fp_exp_pend` (0 o el vector 48..55 de una excepcion publicada que tomara la siguiente
    instruccion FPU) y `fsave_data` (palabra de comando y operando excepcional de la ultima excepcion, parche 0142). No se guardan, con su razon en el inventario: `fpu_exp_state` y
    `fp_unimp_pend` (solo las ramas 040/060; un save las rechaza), `fp_opword`/`fp_ea`/`fp_ea_set` (se escriben y se leen en la misma instruccion), el estado de SoftFloat (modo y precision
    siguen a FPCR por `fpp_set_fpcr`; las banderas se limpian al empezar cada instruccion) y `floatx80_internal_*`. El digest del probe suma los mismos campos.
    - **Cota**: la del A1200 mas 110 (5 890 716 -> 5 890 826); la misma antes y despues de la primera unidad.
    - Prueba: `tests/state/test_state_fpu.py` (layout, cota, identidad, rechazo campo a campo, un poke por campo que cambia el futuro) y `tests/state/test_det02_fpu.py` (aritmetica, modos de
      redondeo, excepcion pendiente en la frontera, FSAVE/FRESTORE/FMOVEM y, con el Kickstart 3.1 autorizado, el arranque con un 68882).

12. **Revision 2026-10-10 (T73): opciones de audio de Paula en la identidad.** El contenedor, los chunks y las cotas no cambian (AUDP ya lleva el estado de los filtros y del integrador de la
    interpolacion; la separacion solo cambia constantes del mezclador). Lo que cambia es la identidad: con la separacion, la interpolacion, el filtro o su tipo distintos del perfil de la maquina
    (70 %, anti, emulado, modelo propio), `config_hash` pasa a `H("p6/config/audio/v1" ‖ config_hash_de_siempre ‖ 4 bytes resueltos)`; en el perfil es el de siempre, asi que las series, los estados y las
    peliculas existentes siguen valiendo. El resto de la envoltura (`machine`, `flags`...) no tiene campo nuevo: la palabra `flags` no tiene bits libres para 220+ combinaciones, y una segunda palabra
    plegada solo fuera del perfil no mueve ningun hash conocido. `sinc` (cola no guardada) se rechaza en `p6_init`. Prueba: `tests/audio/test_audio_options.py` (hash esperado recalculado con
    `tests/state/envelope.py`, estado aceptado por las mismas opciones y rechazado por cualquier otra, rewind con las opciones activas).

13. **Revision 2026-10-11 (T15l): estado oculto del arranque del Kickstart 2.05.** DET02 no veia cuatro cosas que el 2.05 hace entre las unidades 60 y 200 (A600, con un disquete, con un disco duro y
    sin ningun medio); el run-ahead de libretro, que carga un estado en la misma imagen decenas de unidades antes de que se note, las encontro. Ninguna estaba en un cuerpo ni en un registro:
    - **BEAM** pasa de 67 a 80 palabras (parche 0220, +52 bytes en las tres maquinas): `nosignal_cnt`, `nosignal_status`, `nosignal_trigger` y `lines_after_beamcon_change` (un cambio de la viga
      programada, con el que el 2.05 enciende la pantalla, deja el monitor "sin senal" dos vertical blanks: `getlinetype` devuelve `LINETYPE_BLANK` y la imagen sale negra; una maquina cargada la
      dibujaba encendida una o dos unidades antes), `syncs_stopped` (BPLCON0 ERSY, unidades 115-118: Agnus para los contadores del haz al final de una linea y corre sus lineas desde un evento de
      hsync falso; `restore_custom` nunca lo pone) y `bltadd`/`conflictaddr` de las cuatro ranuras del pipeline de DMA (`rga_pipe`, que `save_custom_slots` no escribe: un blit con la escritura de D
      en vuelo avanza el puntero por `bltadd` al terminar; con cero, el blit de la pantalla de la unidad 194 escribia 35 x 145 palabras sobre una sola). CHSL va antes que BEAM, que pone esos
      campos encima.
    - **Seccion de disco duro** (parche 0221): `expamem[256..0x3000)`, la imagen de autoconfiguracion de la tarjeta a la que ha llegado la cadena. P6RM lleva los 256 primeros bytes; los de
      UAEFS (DiagArea, codigo de arranque y bootblock) los construye `expamem_init_filesys` cuando la cadena llega a la tarjeta y `expamem_map_filesys` los copia a la memoria de la placa al darle
      el invitado su base (unidades 67-73): una maquina que cargaba un estado anterior mapeaba una placa con $FF donde la original tenia el codigo, y la diferencia solo se veia 40 unidades despues (la
      unidad 113 duraba 3 CCK mas). +11 520 bytes con un disco duro (el estado del A600 con el Workbench como disco duro mide 4 920 658 de cota).
    - **Cotas** (medidas en el probe, sin disco duro): A500 3 608 565 bytes, A600 3 608 534, A1200 5 890 768 (52 mas cada una; el A1200 con FPU, 110 mas).
    - Lo que dejo pasar a DET02: su horizonte es de 6 unidades tras la carga, y la divergencia del disco duro aparecia 40 unidades despues del estado. Por eso el barrido es por unidad (A600 con el
      2.05: 60-200 sin medio, 61-200 con el disco duro, en los modos de la misma imagen con el haz puesto y de proceso nuevo; el de la misma imagen sin poner el haz, 108-118) y la prueba de
      libretro hace run-ahead desde el run 1 (`tests/libretro-host/test_hdf.py`). Prueba: `tests/state/test_det02_a600.py` (`kickstart-window`, `kickstart-late`, `workbench-window`),
      `tests/state/test_det02_hdf.py` (`workbench-hdf-autoconfig`, sin el fallo esperado de las 120) y los rechazos de BEAM de `tests/state/test_state_upstream.py`.

14. **Revision 2026-10-12 (T15n): dos huecos que hallo el corpus diferencial de T32b.** Programas que el DET02 anterior no tenia (la CPU reescribiendo BPLCON0/1/2, BPLCON3, DIWHIGH y los colores
    sin parar en el A600; una viga programada cargada en un proceso nuevo) mostraron dos cosas que un estado no llevaba:
    - **BEAM** pasa de 80 a 89 palabras (parche 0261, +36 bytes en las tres maquinas): el secuenciador de bitplanes de Agnus, `bprun`, `bprun_cycle`, `ddf_stopping`, `ddf_enable_on`, `ddf_limit_in`,
      `ddf_limit`, `ddfstrt_match`, `hwi_old` y `ddfstrt_val` (palabras 80 a 88; este ultimo es el ciclo en que puede empezar una captura, `DDFSTRT | 1`: lo pone una escritura de DDFSTRT y `custom_reset` nunca, de modo que una maquina que no la escribio tiene 0 donde `restore_custom` deriva 1, y sin llevarlo `ddf_enable_on` se ponia en otro ciclo tras una carga que en la maquina que nunca cargo). `custom_reset` los deja en reposo y ningun cuerpo los nombra; en un vertical blank estan en reposo para casi todos los programas, pero
      uno que reescribe DIWHIGH sin parar abre y cierra la ventana vertical en mitad de una captura y los deja en cualquier sitio (ddf_stopping 1 o 0, bprun_cycle 36 o 16 en la frontera). La maquina
      que cargaba el estado mantenia los suyos, y la captura de la primera linea visible paraba un ciclo despues de empezar (`bprun` pasaba a 0 donde el de la original seguia en 1): sin DMA de
      bitplanes en esa linea, la CPU, que el DMA tenia fuera del bus, corria 148 ciclos de color antes y la frontera de la unidad caia 6 ciclos de color mas tarde. `ddf_limit_out` no va: `decide_bpl`
      lo deriva de `ddf_limit_in` en cada ciclo de color. El inventario dejaba esta omision dicha (`chipset-bitplane-fetch-sequencer`: "bprun/ddf_* son estado vivo para cualquier programa que abra la
      ventana pronto"); la prueba que faltaba era un programa que lo hiciera en la frontera.
    - **`p6_custom_rebuild_derived` ejecuta `init_beamcon0`** en un Agnus ECS o AGA (parche 0260, sin cambio de layout): restore_custom lee `new_beamcon0` y los registros que programan la viga
      pero las longitudes de linea y de cuadro, la frecuencia vertical, los margenes de la imagen y las tablas de hsync que `init_beamcon0` deriva de ellos los obtiene WinUAE de `custom_reset` e
      `init_hz`, que el bridge nunca ejecuta en una maquina viva. Un proceso nuevo que cargaba una viga programada (BEAMCON0 y HTOTAL, una linea mas ancha) conservaba los del reset PAL, y el grupo
      `timing` del digest del chipset difiria nada mas cargar. BEAM va despues y repone los contadores que `init_beamcon0` reinicia (`lines_after_beamcon_change`).
    - **Cotas** (medidas en el probe, sin disco duro): A500 3 608 617 bytes, A600 3 608 586, A1200 5 890 820.
    - Prueba: `tests/reference/test_corpus_det02.py` y `test_corpus_det03.py` (`ecs-ring-cpu-pal` en la misma imagen y en la reiniciada, `ecs-beam-var-wide-pal` en un proceso nuevo; tenian estas
      divergencias fijadas como pruebas de que seguian ahi y salieron de la tabla al cerrarse), y los rechazos de BEAM de `tests/state/test_state_upstream.py`.

15. **Revision 2026-10-12 (T62): la velocidad de disquete es identidad, y el turbo lleva su contador de lineas.** `p6_config.floppy_speed` (100, 200, 400, 800 % y turbo) entra en la palabra `flags`
    de `config_hash` en los bits 25 a 27, solo fuera de la unidad real (el hash de siempre en el perfil); un estado de una velocidad es INCOMPATIBLE en otra. El turbo de upstream (`floppy_speed` 0)
    hace que `DSKLEN_2` mueva la transferencia entera de una vez y espere dos lineas (`linecounter`) antes de la interrupcion DSKBLK; mientras cuenta, `DISK_hsync` vuelve antes de `DISK_update`, asi
    que un estado tomado en esas dos lineas que no lo llevara no terminaria la transferencia. **DSKX** pasa de 41 a 42 palabras (parche 0280, +4 bytes en las tres maquinas: la palabra 5 es el
    contador, 0 a 2). La longitud del DMA de DISK deja de estar acotada por 0x3fff solo en un caso: una lectura turbo la deja en -1 (`0xffff` en el campo de 16 bits), y la validacion lo acepta
    unicamente con el contador en marcha y el DMA apagado (y rechaza el contador con el DMA encendido o la longitud -1 sin contador). Los 200, 400 y 800 % no anaden estado: son la duracion de un bit
    (`trackspeed`, que DSDn y DSKX ya llevan) y el vaciado del FIFO, que DISK lleva. Prueba: `tests/state/test_det02_floppy.py` (DET02 en cada velocidad, un limite dentro de las dos lineas, el
    estado con el contador a cero que no reproduce al original, las refutaciones del contador) y `tests/libretro-host/test_floppy_speed.py`.

16. **Revision 2026-10-13 (T57a): la escala de la posicion de la cabeza es estado.** Una pista cruda de un ADF extendido (o, con T57b, de un FDI) tiene la longitud que dice la imagen, no los 12 668 bytes
    de una pista AmigaDOS, y `updatemfmpos` escala la posicion de la cabeza por la razon entre la longitud de la pista nueva y `prevtracklen` cuando la cabeza llega a otra pista. `prevtracklen` es 0 justo
    tras insertar un disco y la longitud de la ultima pista llenada despues; `restore_disk` la copiaba de la pista de la maquina VIVA (la que corre cuando se carga el estado), no de la del estado, y una
    cabeza que sale de la pista despues de una carga se escalaba por la razon de dos longitudes que no tenian nada que ver con el estado. **DSKX** pasa de 42 a 46 palabras (parche 0330, +16 bytes en
    todas las maquinas: la palabra 9 de cada unidad, 0 a `kMfmBitsMax`). El buffer de la pista (DSDn) ya viajaba; el parche 0331 pone a cero los bits que pasan del final de una pista cruda para que
    sus bytes sean funcion de la imagen. Prueba: `tests/state/test_det02_trackdisk.py` (el anillo de busqueda sobre pistas crudas de seis longitudes divergia sin el 0330).

17. **Revision 2026-10-13 (T57b): la pista FDI lleva su revolucion, y la temporizacion va por byte.** El lector de FDI decodifica cada revolucion de una pista con bits debiles de forma distinta, como una
    funcion de (pista, revolucion) (patch 0333), y `fetchnextrevolution` pide la siguiente en cada indice mientras la pista tenga `multi_revolution`. Nada de eso estaba en un cuerpo: DSDn trae el buffer de la
    pista pero no el indicador, ni la cuenta, ni a que pista pertenece. **DSKX** pasa de 46 a 62 palabras (parche 0334, +64 bytes en todas las maquinas: las palabras 10 a 13 de cada unidad,
    `multi_revolution` 0 o 1, `revolution_check` 0 a 2, `fdirev` 0 a 2^24, `fditrack` -1 a 165). Con el motor apagado DSDn no existe (upstream guarda el buffer solo mientras gira), el estado no trae la pista y la maquina
    que carga la decodifica otra vez: como la revolucion `fdirev` de la pista `fditrack`, que es lo que la decodificacion del original hizo, bit a bit. La temporizacion de una pista (FDI, IPF) tiene un valor por byte
    de la pista y `save_disk2` guardaba uno por palabra de 16 bits (parche 0335): DSDn con el bit de temporizacion lleva las palabras y despues `(longitud + 7) / 8` valores de 16 bits, y la cota de DSDn pasa de 4 a 6
    bytes por palabra del buffer (`kDsdMax`), asi que la cota de estado de todas las maquinas crece en 4 x 32 768 bytes. Pruebas: `tests/state/test_det02_trackdisk.py` (un FDI con pulsos debiles en toda la pista, antes
    del 0335 divergia desde la primera unidad; con el motor encendido y apagado; el estado con una de las palabras cambiada no continua).


### RTC Ricoh (T55a)

El cuerpo `P6RT` sigue el chip de la maquina. El RF5C01A conserva el prefijo de
reloj emulado y anade 35 bytes: controles D/E/F, 16 alarmas y 16 bytes de RAM de
bateria. El visitante valida los nibbles de control, las mascaras de alarmas de
`put_clock_ricoh` y las colas inaccesibles de los bancos antes de aplicar nada.
`delayed_write` solo exportaria un archivo de bateria excluido del perfil; no
decide el futuro. El cuerpo del MSM6242B no cambia. Hasta el preset A4000 real,
solo una seleccion diagnostica del probe alcanza Ricoh; su test compara todos
los subsistemas, PCM e imagen tras restaurar en el mismo proceso y uno nuevo.
Esto no valida un A4000 ni su ejecucion Waterbox.

## Riesgos

- Estado oculto que aun no se ha catalogado: la auditoria es de lectura y no ejecuto los serializadores; T15 la confirma con pruebas que
  cambian cada campo omitido antes de guardar. Un componente `rebuild` que no se pueda mostrar igual se serializa.
- Flotantes: las fases del filtro y del resampler de audio se guardan por patron de bits; la determinacion entre compiladores queda por
  probar (DET03).
- El analisis estructural por chunk debe ser tan estricto como la aplicacion, o (8) fallaria tarde; se prueba con el fuzzer de T15.
- Una omision de la auditoria se descubriria como una divergencia de futuro en DET02, no como un fallo de carga.

## Validacion

1. T14b: el envelope de esta ADR como codificador y validador de referencia con vectores (magic, version, longitudes y desbordes, cada
   campo de identidad, checksum, dominios distintos) y probes de digest canonico por subsistema alcanzable, iguales en un proceso limpio
   y en una recarga.
2. T15 (STATE01, STATE02, DET02, DET03, FUZZ01): ida y vuelta en el mismo proceso y en uno nuevo, estados cerca de DMA, audio, disco y
   teclas; por cada campo omitido, una prueba en la que cambiarlo antes de guardar cambia el futuro; estados truncados, manipulados y de
   otro build o ROM rechazados dejando la maquina usable.
3. T18: `retro_serialize_size` y buffers exactos, menores y mayores; replay y rewind en un frontend real.
4. T21/T22: el wrapper de Waterbox rechaza un estado nativo y el cargador nativo rechaza un envelope de Waterbox.
5. `tests/state/test_components.py` (T14a): ningun componente causal sin propietario, politica y prueba pendiente identificada, y cada
   chunk de savestate.cpp presente en el inventario.
