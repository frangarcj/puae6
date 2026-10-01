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
   codificacion de campos, no su llamada.
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

## Resultado de T15a (2026-10-01): contenedor, identidades, cota y chunk de RAM

Escrito: `ports/portable/state_io/archive.h` (escritor/lector por campos), `envelope.h/.cpp` (cabecera, hashes de identidad y validacion), `bridge/state.h/.cpp`
(registro de chunks, `current_identity`, `bound/save/load` y los `p6_state_*` publicos), `bridge/build_identity.cpp`, los objetivos CMake `p6_state_io`,
`p6_state`, `p6_envelope_probe` y `p6_archive_probe`, y las operaciones `--save-at/--load-at/--public-at/--adf/--seed` de `p6_state_probe`. Solo existe el chunk de
RAM (`P6RM`: tamaños de Chip y Slow, y sus bytes); el estado completo de un estado nativo es de T15b-T15h y el cierre T15.

Decisiones que el ADR no fijaba o que esta tarea precisa:

1. **Un visitante por chunk, cuatro modos.** `Archive` ejecuta la misma funcion para `Save` (escribe lo vivo), `Measure` (cuenta el tamaño maximo, las listas a su
   maximo), `Validate` (lee y comprueba rangos sin tocar nada) y `Apply` (lee y guarda). Validate y Apply son el mismo codigo sobre los mismos bytes, de modo que el
   analisis estructural del paso (7) es tan estricto como la aplicacion del paso (8) por construccion y el orden de campos no puede divergir entre guardar y cargar.
   El primer problema es pegajoso, `finish()` exige haber consumido el cuerpo exacto, y nada se copia como memoria: enteros de ancho fijo LE, bytes, cuentas con
   maximo y flotantes por patron de bits.
2. **Etiquetas de chunk:** `P6CP P6SC P6RM P6RT P6CH P6VD P6AU P6CI P6DK P6IN P6BR`, en ese orden (cpu, scheduler, ram, rtc, chipset, video, audio, cia, disk,
   input, bridge). El validador de referencia de T14b usa las mismas.
3. **`codegen_hash` y `core_build_id`.** Los fuentes generados nacen en tiempo de build, no de configure; `codegen_hash` es el SHA-256 de `manifests/codegen.json`
   (la receta: generadores, banderas, entorno), de la que GEN01 hace funcion los bytes generados junto con los ficheros upstream y los parches. `sources_hash` es el
   SHA-256 de las lineas `ruta:sha256` ordenadas de `bridge/*.{cpp,h}` y `ports/portable/**/*.{c,cpp,h,inc}`, salvo `bridge/state_probe.cpp` (una herramienta de
   prueba). CMake los pasa como definiciones solo de `build_identity.cpp` y cada fichero es dependencia de configuracion: editar cualquiera cambia `core_build_id` y
   un estado de la build anterior se rechaza como INCOMPATIBLE (comprobado a mano al editar `state.cpp`). Un build sin esas definiciones no compila (`#error`).
4. **`p6_state` es una biblioteca aparte de `p6_bridge`.** El guest Waterbox toma sus fuentes de `p6_bridge`, y un snapshot Waterbox no es este formato
   (punto 7): el guest no lleva el serializador nativo ni el SHA-256 de estado. Cada adaptador que quiera estado nativo enlaza `p6_state` (headless y libretro,
   en el cierre de T15 y en T18).
5. **Chunks no escritos y funciones publicas.** Un chunk sin visitante es un marcador de version 0 y cuerpo vacio que solo las herramientas de prueba pueden
   escribir y leer (`Options::allow_placeholders`). Las funciones publicas `p6_state_bound/save/load` devuelven `P6_ERROR_UNSUPPORTED` y nombran los chunks que
   faltan mientras `complete()` sea falso; `P6_CAP_STATE` sigue limpio. No hay forma de producir un estado parcial por la ABI.
6. **Cotas.** `bound` = 336 + Σ(16 + cuerpo maximo de cada chunk), calculado por `Measure` sobre el perfil (no sobre el estado vivo) y por tanto constante durante la
   sesion. El envelope comprueba `payload_length ≤ bound − 336` y cada cuerpo contra su cota; un cuerpo de mas se rechaza ya como `payload_length` mientras la cota
   sea exacta (RAM lo es: un estado ocupa 1 049 096 bytes y su cota tambien). Los placeholders tienen cota 0.
7. **Longitud exacta.** `p6_state_load` exige `bytes == 336 + payload_length`; un buffer mayor se rechaza. T18 (`retro_unserialize` recibe un tamaño ≥ `serialize_size`)
   debera pasar la longitud exacta leyendo `payload_length` de la cabecera, o rellenar de forma definida al guardar y recortar al cargar.
8. **Medios.** `media_manifest_hash` usa tipo y `content_id` en el orden del array de `p6_load`; ABI v1 solo admite ADF. Probado con imagenes ADF sinteticas
   (`--adf`): sin disco, otro disco, dos discos y el orden cambiado se rechazan nombrando `media_manifest_hash`; el mismo disco en otro proceso vale.

Lo que esto demuestra (`tests/state/test_envelope_native.py`, `test_state_container.py`, `test_components.py`): el C++ da los bytes del vector dorado, los mismos
hashes y el mismo veredicto, codigo y campo en cada ataque de T14b (diferencial contra `envelope.py`, 200 estados aleatorios incluidos); `Archive` hace ida y vuelta
de cada tipo de campo y rechaza rangos, listas largas, truncados en todas las longitudes y capacidad insuficiente sin salir de su bloque; sobre el motor real, la RAM
(Chip y Slow) vuelve a la unidad guardada en el mismo proceso y en uno nuevo, y 81 estados malos (23 truncados, 28 con un byte cambiado, 13 de otra maquina y 17 con la estructura de chunks mal y los
checksums rehechos) se rechazan con su codigo y su campo sin mover un solo digest de la maquina ni el futuro de las seis unidades siguientes. Un fallo inyectado en la
fase de aplicar tras validar (`Options::fail_apply`, solo de pruebas) deja la imagen Faulted, `p6_run_frame` se niega, `p6_unload` la recupera y la imagen recargada es la
de un proceso limpio desde la unidad 1 (antes de la cabecera de reset chipset, audio y entrada conservan lo de la sesion anterior, como ya registro T13b).

Mutaciones: 46 defectos sembrados (24 en el envelope, 13 en el registro y los puntos de entrada, 9 en `Archive`) y los 46 hacen fallar algun test. Los dos
primeros intentos dejaron vivos dos huecos que ahora tienen test: un chunk que declara un byte mas de lo que queda del payload pero dentro de su cota (sin el control,
la vista queda fuera del payload y la resta sin signo se desborda en la vuelta siguiente) y el camino Faulted (`fail_apply`).

Lo que NO demuestra: que un estado restaure una maquina. Solo se restaura RAM; CPU, scheduler, chipset, CIA y el resto son placeholders hasta T15b-T15h, y el futuro
de la maquina restaurada (DET02/DET03) es del cierre T15. Tampoco hay aun un fallo de aplicacion real que pueda ocurrir: `fail_apply` simula el de un visitante con un
defecto, no lo prueba imposible.

## Resultado de T15b1 (2026-10-01): chunk de CPU y carga sobre una imagen que aun no ha arrancado

Escrito: `bridge/state_cpu.cpp` (`visit_cpu`, chunk `P6CP` v1, 155 bytes), los parches 0076 (`main.cpp`: `p6_rng_get/set`) y 0077 (`newcpu.cpp`: `p6_cpu_boundary`, al final del
fichero, sin tocar `m68k_go_unit`), `p6_bridge::run_reset_head` en `frame_step.cpp`, `Options::only_chunks`, `on_field`, `fail_apply` y `fail_head`, y las
operaciones `--only`, `--save-fields-at`, `--mutate-at` y `--poke-at` de `p6_state_probe`. `tests/state/harness.py` reune lo que comparten los tests de cada chunk.

Esto corrige y precisa el punto 6:

1. **Un estado solo se toma de una imagen arrancada.** `p6_load` no ejecuta la cabecera de reset, y es ella quien construye las tablas de despacho de la CPU
   (`build_cpufunctbl`, `set_x_funcs`) y los manejadores del scheduler (`init_eventtab`); ademas chipset, audio y entrada no son los de un encendido hasta que corre
   (T13b). Antes de la primera unidad un "estado" seria solo configuracion, firmware y medios: `p6_state_save` devuelve `P6_ERROR_BAD_STATE` ("run p6_run_frame
   once"). `bound` no depende de ello.
2. **Cargar sobre una imagen que no ha arrancado ejecuta primero la cabecera.** Es la misma llamada que hace `p6_run_frame` (`apply_boot_media` y
   `m68k_go_unit(M68K_UNIT_APPLY)`), y los chunks sobrescriben lo que dejo. Solo `frame_step.cpp` puede avanzar el motor (lo exige `tests/step`), asi que la ejecuta
   `p6_bridge::run_reset_head()`, que esta alli. Asi no hace falta ningun setter de `suspended` y `hardboot` (valen siempre `(true, 0)` en una imagen pasada su cabecera, y
   no viajan en el cuerpo) ni exportar las funciones que construyen las tablas, y un estado cargado en un proceso nuevo
   llama a punteros que existen: un test en el mismo proceso lo ocultaria, porque una recarga conserva las tablas de la sesion anterior. Coste: el retardo aleatorio
   de arranque de la cabecera (hasta 0x7fff CCK emulados) y un `memory_clear` ocurren una vez sobre una imagen nueva; lo que la cabecera deja del lado del host (cola
   PCM, imagen presentada, `cycles_seen`) lo reponen los chunks de audio, video y sesion (T15e, T15g, T15h).
3. **A7, USP e ISP en bruto**, no plegados como hace upstream (USP si S=0, ISP si S=1): la ranura inactiva esta obsoleta en el motor y puede ser la viva tras un reset
   (`m68k_reset2` no limpia ninguna), y el digest del probe hashea las dos. **TRACE y DOTRACE tal cual**, porque `activate_trace()` borra TRACE mientras T1 sigue
   puesto y no se puede derivar de T1|T0. **`quit_program` se guarda** (-4..0, sin -1): un reset de teclado puede dejarlo negativo en una frontera.
4. **Un guardado que un visitante rechaza** (una excepcion en vuelo, una bandera que el perfil nunca deja pendiente, un registro de 68010 puesto) es
   `P6_ERROR_BAD_STATE` con el motivo, no `CAPACITY`: solo lo es si el buffer no habria bastado.
5. **Cada campo se aplica y los cuerpos no pueden desacoplar Validate de Apply.** Un visitante no puede comprobar la variable viva (en Validate vale lo que tenga la
   maquina): toda comprobacion usa el valor que devuelve `val()`. `--mutate-at` carga cada cambio de un byte del cuerpo (cuatro valores por byte) con los checksums
   rehechos y comprueba que ninguno deja la imagen en fallo, que un rechazo no cambia la maquina y que el veredicto no depende de la maquina destino.

Lo que demuestra (`tests/state/test_state_cpu.py`, 12 tests, y `test_state_container.py` actualizado): para ocho programas (interrupciones, STOP, HALT, entrada,
operaciones largas, copper, trace con T1 y modo usuario con ISP obsoleto) y para la Kickstart 1.3 del propietario en tres fronteras, el digest del subsistema cpu y
los bytes del chunk vuelven a los de la unidad en que se tomo el estado, en el mismo proceso y en uno nuevo; los 44 campos del cuerpo se aplican (cambiar uno
cambia el digest, y cada bit del SR por separado); unas 500 mutaciones de un byte, ninguna deja la imagen en fallo; 16 cuerpos mal formados se rechazan con su motivo sin mover la maquina; y una CPU con
una excepcion en vuelo, una bandera pendiente o un registro de 68010 no se guarda.

Mutaciones: 59 defectos sembrados en `state_cpu.cpp` y `state.cpp` (omitir la aplicacion de cada campo, quitar cada validacion o rango, juzgar la maquina viva en
vez del cuerpo, no comprobar la frontera al guardar, no ejecutar la cabecera de reset, ignorar `only_chunks`, aceptar un guardado antes de la primera unidad, un fallo de
la cabecera que no deja la imagen en fallo...); 58 hacen fallar un test y uno es equivalente (`regs.sr = sr`: el digest reconstruye `regs.sr` con `MakeSR` desde los flags,
asi que no es estado observable). La primera tanda dejo vivos siete defectos reales (SR cambiado de un solo bit, mascara y flags X N Z V sin aplicar; banderas especiales
del estado unidas a las de la maquina en vez de reemplazarlas) y una tanda mal escrita (un nombre de test inexistente daba `rc=1` sin probar nada): ahora el arnes falla si un
test de la lista no se ejecuta, el test de campos prueba cada bit del SR por separado y `--poke-at` pone banderas validas en la maquina antes de cargar. Las comprobaciones del
parche 0077 (`p6_cpu_boundary`) no se pueden mutar sobre `build/upstream` (la generacion verifica el arbol y se niega a construir): tienen un test directo cada una por `--poke-at`.

Lo que NO demuestra: que el futuro de la maquina restaurada sea igual (DET02), porque hasta que existan los demas chunks lo restaurado es solo RAM y CPU; que
CPUINRESET (acorde de reset por teclado) o un `quit_program` negativo se restauren en una frontera real, porque el probe no pulsa teclas (T15c); ni las comprobaciones
cruzadas opcionales (`halted != 0` implica mascara 7, CPUINRESET implica S=1), que no se activaron porque falta una frontera real que las confirme.
