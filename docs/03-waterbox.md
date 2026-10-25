# 03. Plan especifico BizHawk / Waterbox

## Objetivo y referencia

Producir `puae6.wbx` y un adaptador C# identificado como PUAE6, coexistiendo con
UAE existente. No sobrescribir uae.wbx ni cambiar retroactivamente el core ID de
peliculas guardadas. S5-S8 en 00-sources.md contienen las rutas inspeccionadas.
Referencia principal: ../MacHawk en HEAD 9b62757833c9bdd15bf43cef058c88bf91eb0ad7,
con fingerprint local en plan/machawk.reference.json. El pin BizHawk publico
1a140d98b8cc4a705db89d4738d76196c84044ea queda como comparador secundario.

No confundir tres entregas: compilacion C++ nativa ARM64, generacion del WBX y
carga/ejecucion real por Waterbox. Cada una tiene su gate. El primer runner es
MacHawk Darwin ARM64 usando guest ELF AArch64 y el runtime local, segun
docs/07-machawk.md. Linux/Windows x86_64 son filas adicionales de portabilidad.
Que exista el runtime ARM64 no significa que PUAE6 o sus snapshots ya funcionen.

## Forma del port existente y frontera que conserva PUAE6

El UAE local de MacHawk NO es un frontend libretro generico. Su Makefile toma el
arbol libretro-uae y compila parte de su glue, pero bizhawk.c exporta Init,
FrameAdvance y GetMemoryAreas para Waterbox y llama directamente a umain/m68k_go.
Esta distincion es deliberada en PUAE6: el mecanismo de retorno por frame sirve
como referencia para bridge/frame_step, pero adapters/libretro no se enlaza dentro
del guest Waterbox y FrameAdvance no llama retro_run.

La receta nueva debe enlazar WinUAE + host port + bridge + adapters/waterbox.
Cualquier pieza tomada del libretro-uae fijado se clasifica primero como engine
patch, portable host support o frontend-only. El ultimo grupo no entra en WBX.
Esto evita que opciones RETRO_ENVIRONMENT, callbacks de frontend o globals de UX
se conviertan en dependencias accidentales de BizHawk.

## T09: prueba de riesgo antes de terminar el port

Construir con el sysroot real un mini-core que no emula Amiga: contador, RAM,
ROM sellada, buffer descartable y paquete de entrada. Esta prueba se etiqueta
SMOKE_RUNTIME, no prueba de compatibilidad. Ejecutar init -> seal -> avanzar ->
snapshot -> avanzar -> restore -> repetir; comparar resultados y referencias.

Incluir asignaciones C++, globals, constructores usados, calling convention y
buffers host. Si T07 necesita corutina, incluir su pila/continuacion en este test.
Hacer restore en un segundo sandbox equivalente y alternar dos sandboxes.
Medir memoria asignada/limite; provocar agotamiento para obtener fallo controlado.

Si no hay runtime disponible, T09 queda blocked y la compatibilidad Waterbox NO
se da por resuelta. No retrasar la primera prueba real hasta tener WHDLoad o AGA.

## Build

Usar common.mak y linkscript_aarch64.T de MacHawk con sysroot fijado, flags
WBX_TARGET_ARCH=aarch64, WBX_HOST_PAGE_SIZE=16384 y reserva de x18/x28. No inventar
un linker script propio ni enlazar bibliotecas Mach-O del host en el WBX. El build
nativo puede usar CMake; el manifiesto de fuentes compartido genera la lista SRCS
para la receta Waterbox sin convertir todo BizHawk a CMake.

Los generadores de CPU/tablas se ejecutan en el HOST de build. Sus salidas son
texto portable y se compilan para el target. No intentar ejecutar un binario WBX
durante configure ni un generador ARM64 en el runner x86_64. Cache key incluye
SHA del generador, inputs, toolchain y perfil; registrar hashes y reproducibilidad.

Build determinista inicial: interpreter, un hilo de emulacion, sin JIT, sin
bibliotecas dinamicas de plugins, sin SDL/Qt/Win32, sin sockets ni dispositivos
reales. No se afirma que Waterbox sea incapaz de JIT en general: es una exclusion
de este proyecto para acotar estado, toolchain y presupuesto de mantenimiento.

## Ciclo de vida

1. C# valida sync settings, firmware, manifiesto y limites; registra delegates vivos.
2. PreInit crea sandbox y binding de exportaciones, con heap budgets explicitos.
3. Inyectar ROM y todos los medios MVP de solo lectura con nombres logicos estables.
4. Inicializar WinUAE/bridge, tablas, buffers, banks y todos los recursos necesarios.
5. Consultar/configurar dominios; finalizar init y ejecutar PostInit/Seal en el orden
   exacto que requiere el pin. No usar Seal como equivalente a primera llamada de run.
6. En cada FrameAdvance, fijar buffers host SOLO durante la llamada, entregar entrada
   normalizada, llamar la misma p6_run_frame usada por headless/libretro y devolver
   dimensiones/muestras/ciclos/lag. No llamar retro_run desde el guest.
7. Guardar snapshot mas cualquier estado C# relevante. Al restaurar, reponer bindings.
8. Dispose libera el sandbox completo y recursos C#; no depender de free de sealed/plain.

Las bases de memory domains se asignan antes de PostInit. No cambiar RAM/banks en
caliente invalidando las vistas. Una nueva configuracion de memoria necesita nuevo
sandbox o una ruta explicita que regenere dominios y tenga pruebas.

## Clasificacion de memoria: tabla obligatoria en T19

| Categoria | Ejemplos | Tratamiento inicial |
| --- | --- | --- |
| Inmutable | ROM, lookup tables, base de discos precargada | sealed solo tras demostrar que no se parchea/escribe |
| Estado emulado | RAM, registros, scheduler, CIA, drive/overlay, RTC | memoria capturada por Waterbox |
| Estado del algoritmo | audio filtros/fases, prefetch, historia entrelazada, continuacion | capturada salvo prueba de reconstruccion exacta |
| Output de una llamada | buffer de entrega host, conversion ya completada | referencia temporal; se limpia antes de retornar |
| Cache derivada | traducciones de formato, tablas regenerables | capturada inicialmente; exclusion solo con prueba poison |
| Estado C# | edges de botones, disco seleccionado, conversion de raton, config dinamica permitida | SaveStateBinaryInternal/LoadStateBinaryInternal o derivacion exacta demostrada |

S7 define sealed/invisible/plain. El default seguro es capturar todo estado mutable.
No persistir punteros a arrays managed, FILE* del host, direcciones de callbacks no
rebindables ni cursores externos. Los punteros internos del sandbox siguen el
contrato de Waterbox y NO tienen por que reemplazarse todos por offsets; los
formatos semanticos nativos son otra cosa. No mezclar esas dos reglas.

Buffers invisible solo tras test: llenar con valores adversarios justo despues de
restore, ejecutar y comparar estado/A/V presente y futuro durante suficientes
campos. En weave/interlace hay que verificar al menos ambos campos y los cambios
de modo. Los filtros de audio NO son meros buffers de salida descartables.

Reserva de heaps por perfil, no copiar sin medir los valores de UAE.cs. Mantener
margen de capacidad y registrar peak de sbrk/mmap/plain/sealed/invisible. No asumir
que RAM invitada = memoria total. El costo de snapshot se mide aparte del RSS.

### Resultado de T19b: la clasificacion aplicada a puae6.wbx

La tabla de arriba esta aplicada region por region en manifests/waterbox-memory.json, junto con
los presupuestos de heap y lo medido (`python3 tools/build_waterbox.py --puae6-run FIRMWARE
--units N [--record-memory]` ejecuta la maquina en Waterbox real y escribe las medidas; los
tests de tests/waterbox-smoke/test_puae6.py fallan si las cifras del manifiesto dejan de ser las
de una ejecucion o si una medida pasa de la mitad de su presupuesto). Lo que quedo decidido:

- Nada se excluye del snapshot: invisible y plain estan vacios (nadie llama a alloc_invisible ni
  alloc_plain) y todo lo demas es malloc o datos estaticos, que Waterbox captura. Las unicas
  regiones que el runtime no guarda son las pilas de hilo, vacias en una frontera de unidad
  porque p6_run_frame retorna por la pila (sin corutina).
- El firmware y los ADF se copian a la heap sealed dentro de p6w_init, antes del seal; el bridge
  registra esos punteros (p6_load guarda el puntero, no una copia). El arnes sobrescribe con 0xA5
  los buffers del host tras la llamada y la maquina no lo nota: cinco programas de fixture y dos
  ADF dan las mismas unidades que headless. Los medios aun no se montan en Waterbox (no hay
  export de eventos de medios: T17/T22).
- Presupuestos: sbrk 4 MiB (medido 16 KiB), mmap 16 MiB (medido 5,3 MiB: Chip/Slow RAM y tablas),
  sealed 16 MiB (un Kickstart de 512 KiB mas P6_MAX_MEDIA ADF de 901120 bytes; el fixture usa 256
  KiB), invisible y plain 16 KiB (el minimo que acepta el runtime).
- Coste de snapshot medido con el micro-ROM: 39 KB justo tras el seal y 9,1 MB despues de la
  primera unidad. Son paginas sucias desde el seal: 4 MB son el buffer de imagen `pixels` y 2,3
  MB son tablas (cpufunctbl, mem_banks...) que la cabecera de reset construye DENTRO de la primera
  unidad, no en p6_load. La imagen del guest pesa 40 MB (34 MB de datos escribibles, casi todo bss: currprefs
  y changed_prefs, 11 MB cada uno, y el buffer de imagen); solo se guardan las paginas sucias. Ambas cosas estan en candidates_for_exclusion del manifiesto para
  T36 (prueba poison, o ejecutar esa cabecera antes del seal sin consumir una unidad); ahora no se
  toca nada.
- Las excepciones C++ se desenrollan dentro del sandbox (p6w_probe_exception: lanzar a 40
  marcos de profundidad, capturar y ejecutar los 41 destructores). El perfil A500 no alcanza el
  throw del nucleo (hardware_exception2 activa hardware_bus_error en un 68000 compatible), asi que
  eso es la evidencia de que el desenrollado funciona, no una ejecucion de la maquina.

Guest ABI: adapters/waterbox/p6w.h y puae6_guest.cpp (`p6w_abi_version`, `p6w_layout`,
`p6w_memory_info`, `p6w_init_machine`, `p6w_frame_advance`, `p6w_reset`, `p6w_shutdown`,
`p6w_probe_exception`, y desde T20a `p6w_memory_areas`, `p6w_memory_access` y `p6w_memory_refused`). `p6w_frame_advance` es exactamente una llamada a p6_run_frame. El arnes
managed (integration/bizhawk/smoke/Puae6.cs) comprueba al crear el sandbox que las estructuras que
conoce son las que el guest compilo (`p6w_layout`); no es el wrapper de BizHawk (T20).

### Re-registro completo de manifests/waterbox-memory.json (T48f)

Las 16 claves `measured*` son una sola medida del MISMO guest y el MISMO runtime: cuando cambia el guest (un parche del motor, el puente, el adaptador) o el runtime se
regraban TODAS, nunca solo la que se estaba tocando (T72 grabo solo `measured` y T51 cambio el firmware de las claves hdf: el manifiesto quedo con guests mezclados y los tests
de presupuesto seguian en verde). `tests/waterbox-smoke/test_memory_manifest.py` lo exige sin runtime: el conjunto de claves es exactamente el que producen `MEASURED_KEYS` y
`measured_key`, `MEMORY_CONFIGS` de `test_puae6.py`, los dos discos duros y el solapado de disquete lleno; todas comparten `guest_sha256` y `runtime_sha256` (y, con el guest construido en este arbol, el `guest_sha256` es el de `build/waterbox/puae6/puae6-receipt.json`;
el del runtime no se compara con el dylib construido: sus bytes llevan las rutas del arbol donde se compilo); y cada una lleva su fixture documentado por el sha256
del firmware. `tools/build_waterbox.py` se niega a construir o medir un guest con un `build/upstream` o `build/generated` de otra serie de parches (`ensure_current_trees`: rematerializa
si falta, y si existe y difiere se niega con los pasos para renovarlo en `build/stale/`, sin sobrescribir nada). Receta, con los arboles de la serie actual:

1. Construir el guest y el runtime una vez: `python3 tools/build_waterbox.py --puae6 --jobs 3`. No reconstruirlo mientras dure el resto, ni lanzar a la vez otra medicion que use `build/waterbox`.
2. Firmwares (sin Kickstart; los mismos que usan los tests):
   `python3 tests/fixtures/microrom/build_rom.py tests/fixtures/microrom/boot.s --output build/waterbox/boot.rom` (sha256 e2578b2e..., el de las 13 claves de maquina, memoria y FPU) y
   `python3 -c "import sys; sys.path.insert(0, '.'); from tests.hdf import hdf_rom; open('build/waterbox/loop.rom', 'wb').write(hdf_rom.loop_rom())"` (el micro-ROM de bucle de los discos duros, sha256 74ed176f...).
3. Las 8 maquinas, cada una con 60 unidades: `python3 tools/build_waterbox.py --puae6-run build/waterbox/boot.rom --units 60 --record-memory --machine M` para M en A500, A600, A1200,
   A500OG, A500PLUS, A1000, A2000OG y A2000 (la clave `measured` es la del A500).
4. Las 5 configuraciones de memoria y FPU, con el mismo `boot.rom` y 60 unidades: `--machine A1200 --fast 8192`; `--machine A600 --chip 2048 --fast 8192`;
   `--machine A1200 --slow 1536 --fast 8192`; `--machine A500 --fast 2048`; `--machine A1200 --fpu 68882` (son las de `MEMORY_CONFIGS` en `tests/waterbox-smoke/test_puae6.py`).
5. Los 2 discos duros, con `build/waterbox/loop.rom`, 60 unidades y `--machine A500 --hdf IMAGEN`, con las imagenes de `tests.hdf.hdf_image.rdb_image(cylinders=64)` (2 MiB,
   `measured_a500_hdf2048`) y `rdb_image(cylinders=1024)` (32 MiB, `measured_a500_hdf32768`) escritas en un directorio temporal.
6. El solapado de disquete lleno: `python3 tests/waterbox-media/test_media_limits.py --record` (5600 unidades; su propio firmware `wb12`, clave `measured_a500_floppy_writes_full`).
7. Comprobar: `python3 -m unittest discover -s tests/waterbox-smoke -p "test_memory_manifest.py"` (y `test_puae6.py`, `test_puae6_memory.py`, `test_puae6_hdf.py`) y
   `python3 -m unittest discover -s tests/waterbox-media -p "test_media_limits.py"`; en `git diff` las 16 claves cambian a la vez y `runtime_sha256` solo si cambio el runtime.

### Runtime Release por defecto y carril Debug (T34a)

Decision del usuario (2026-10-12, tras T33/T34): la copia local de `libwaterboxhost` (de `../MacHawk`, solo lectura; `tools/build_waterbox.py` la compila desde `build/waterbox/runtime-src`)
se construye en **Release** (`plan/toolchains.json`, `runtime.profile = release`): lo que BizHawk distribuye. Entrar y salir del sandbox pasa de ~3,1 ms por unidad en Debug a ~0,0004 ms
(peor caso medido en T33, A1200 + Kickstart 3.1 + demo AGA: p95 20,54 ms, el 102 % de la unidad, frente a 9,71 ms). Todo lo que sigue sale de `tools/build_waterbox.py`.

- **Carril Debug.** `P6_WATERBOX_RUNTIME=debug` en cualquier orden de `build_waterbox.py` (o en los tests, que llaman a `build_waterbox.run_puae6/run_ffi/build_core`) usa el runtime
  Debug: compilado sin `--release`, con las aserciones del runtime (`debug_assertions`: comprobaciones de mapeos y de threading) y con la expulsion del sandbox forzada en cada llamada.
  Su recibo es `build/waterbox/receipt-debug.json` (el del carril por defecto sigue siendo `receipt.json`; ambos llevan `runtime_profile`) y el host de pruebas del core se copia a `build/ffi/lane-debug/`,
  de modo que los dos carriles conviven y nunca comparten una `libwaterboxhost.dylib`. `python3 tools/build_waterbox.py --runtime-lane debug` construye solo ese runtime desde el recibo por defecto
  (medio minuto). El recibo Debug no se registra en ningun manifiesto: `runtime_sha256` de `manifests/waterbox-memory.json` es siempre el del carril Release.
- **Que corre en Debug y por que.** `cargo test` del runtime corre siempre en Debug (aserciones puestas) en cada `build()`, como antes. Ademas `tests/waterbox-smoke/test_runtime_lanes.py` ejecuta en el
  carril Debug `tests/ffi/test_ffi.py` (ABI01, la puerta de identidad WB15, canarios alrededor de lo que se le entrega al guest, guests danados) y `tests/waterbox-smoke/test_puae6_wb.py` (WB13 a WB15: llamadas
  del host que cruzan el guest, paginas de 16 KiB, restauracion): son los que pasan punteros por la frontera del sandbox y los que una entrada danada convertiria en una asercion del runtime. Ningun otro
  test depende de las aserciones ni de la expulsion forzada; el mismo test comprueba que la maquina es la misma unidad a unidad en Release, en Release con la expulsion del runtime activada (`P6_EVICT=1`) y en el carril Debug.
- **Expulsion (`wbx_set_always_evict_blocks`).** El runtime arranca con la expulsion activada (un sandbox se desmapea al desactivarse y se vuelve a mapear al activarse, para cazar punteros colgantes) y un
  Release de BizHawk la apaga en el constructor estatico de `WaterboxHost` (`#if !DEBUG`). Con un runtime Release y la expulsion activada la unidad A500 busy cuesta 8,65 ms (entrar 2,2 ms y salir 0,14 ms; baseline T34a) frente a 5,91 ms con la expulsion apagada y 9,25 ms en Debug,
  de modo que no apagarla anularia casi toda la ganancia. Los hosts de prueba (`tests/ffi/Runtime.cs`, `integration/bizhawk/smoke/Native.cs`) la apagan al cargar el runtime, igual que BizHawk, salvo con `P6_EVICT=1` (que deja la del
  runtime). El runtime Debug ignora la llamada y siempre expulsa. `P6_EVICT=0`, que ponian `tools/bench.py` y los reproductores de T28a, sigue siendo valido y es ahora el valor por defecto.
- **Reproducibilidad.** El hash de un runtime compilado dependia de la ruta del arbol (`0869b5fc` frente a `8dfef1ca` con las mismas fuentes en T48f): panics con rutas absolutas, fuentes de dependencias y
  el *install name* del dylib, que cargo fija a la ruta absoluta de `target/<perfil>/deps`. `build_waterbox.runtime_rustflags` pasa `--remap-path-prefix` para el home, el repositorio y `build/waterbox` y un
  `-Clink-arg=-Wl,-install_name,@rpath/libwaterboxhost.dylib`: el mismo codigo, la misma toolchain fijada (`plan/toolchains.json`) y las mismas fuentes dan el mismo sha256 en cualquier directorio o
  worktree (`test_runtime_lanes.py` lo reconstruye desde una copia en otro directorio y compara byte a byte; `test_memory_manifest.py` compara ahora `runtime_sha256` con el construido en este arbol). Solo el Release:
  el Debug conserva su informacion de depuracion con las rutas.
- **Re-registro.** Cambiar el perfil cambia `runtime_sha256`: hay que regrabar TODAS las claves `measured*` con la receta de arriba (T34a lo hizo con el runtime Release; las cifras de memoria y de
  snapshot no cambian porque el guest es el mismo, los hashes del runtime si).

### Resultado de T19c1: unidad tras unidad dentro de Waterbox

`python3 -m unittest discover -s tests/waterbox-smoke -p test_puae6_units.py` (varios minutos;
`P6_TEST_WB_UNITS=N` acorta las dos comparaciones largas, la aceptacion es 10000) ejecuta el
guest real y lo compara con los otros dos adaptadores sobre la misma p6_run_frame:

- WB02 / STEP01: 10000 unidades dos veces, en dos procesos a la vez, de un ROM que dibuja ambos
  campos entrelazados (la imagen cambia cada unidad), dan las mismas lineas que p6_headless
  (ticks, hash de imagen, paridad, cadencia, frames de audio), los mismos ticks, imagen y audio
  que el core libretro (incluido el hash FNV de todos los bytes PCM) y los mismos digest del
  snapshot completo entre las dos ejecuciones (diez puntos y el final: es el estado de hardware,
  que los otros adaptadores no muestran). Con un joystick fijo en los dos puertos el resultado es el
  mismo entre ejecuciones y contra libretro (el core registra el paquete que entrego: se comprueba
  que es el mismo) y el estado final difiere del de una ejecucion sin entrada, asi que la
  entrada llega a la maquina.
- WB08: con render y audio apagados por tramos (ambos, solo video, solo audio) los ticks de cada
  unidad son los de una ejecucion que lo entrega todo, las unidades con imagen tienen su mismo hash
  (el punto delicado: el entrelazado, dos campos), el PCM de cada unidad con audio es el mismo
  hash (las fases del filtro avanzaron sin entregar nada) y el estado completo del snapshot antes
  del primer tramo, cuatro unidades despues del ultimo y al final es el mismo. Sin render no se
  entrega puntero ni flag de imagen (`P6_FRAME_NO_VIDEO`), y sin audio ni puntero ni frames.
- WB07: bandas de 0xCD alrededor de p6_input, p6w_frame y p6w_init se comprueban en cada llamada;
  el guest no cambia nada de lo que recibe salvo result y error; lo que entrega (imagen y audio)
  cae dentro del sandbox y dentro de sus limites. Seis llamadas que debe rechazar (version o
  tamano de p6w_frame, tamano de p6_input, flag de render desconocido, entrada o salida nula)
  devuelven BAD_ARGUMENT con mensaje sin avanzar la maquina (la unidad siguiente es la de una
  ejecucion sin ellas). Un firmware mayor que la heap sealed devuelve CAPACITY sin abortar ni
  consumir heap, 17 medios, sin firmware, una unidad antes del init y un segundo init se rechazan
  con su codigo, y despues el mismo sandbox acepta un init bueno y ejecuta.

Lo que aun no cubre: no hay restauracion ni excepciones cruzando callbacks con el guest real
(T19c2: WB13, WB14, WB15), ni Kickstart real ni medios montados.

### Resultado de T19c2: WB13, WB14 y WB15 con el guest real

`python3 -m unittest discover -s tests/waterbox-smoke -p test_puae6_wb.py`:

- WB13: un callback del host cruza puae6.wbx (adapters/waterbox/abi_probe.cpp, el mismo codigo que el
  guest del smoke) con canarios en x19-x29, d8-d15 y sp, antes de varias unidades de una maquina en
  marcha, tambien entrando en el guest desde dentro del callback y tambien en un sandbox restaurado.
  x18, x28, el dato especifico de hilo de musl y errno vuelven intactos, el sandbox conserva su
  contexto x28 y otro sandbox del mismo guest tiene el suyo, y las unidades siguientes son las de
  una ejecucion sin callbacks. Las tres funciones pthread_key_* de la sonda estan autorizadas en
  ports/waterbox/imports.json solo para eso.
- WB14: dos grupos de 16 KiB (cuatro paginas logicas de 4 KiB) de la maquina viva: uno que nada ha
  escrito desde el seal y otro dentro de la Chip RAM, que se halla por una marca que el ROM
  tests/waterbox-smoke/chipcell.s guarda en $10000 (aun no hay export de regiones de memoria).
  Una escritura sucia el grupo entero, un restore trae las cuatro paginas y el grupo limpio vuelve a
  estar limpio, una segunda escritura se ve y se deshace, y despues la maquina continua como en una
  ejecucion sin nada de eso. Una escritura en la palabra que el ROM muestra como COLOR00 cambia la
  imagen siguiente, asi que las escrituras llegan a la maquina.
- WB15: un proceso nuevo que solo hizo init y seal del mismo guest con el mismo firmware carga el
  snapshot de otro proceso y produce las mismas unidades, el mismo PCM por unidad y el mismo estado
  completo final (con el ROM entrelazado y con botones sujetos). Un recibo cuyo hash no es el del
  guest o del runtime se rechaza (T19b) y las variantes danadas del guest real (maquina x86-64, magic
  roto, truncado, pagina RW dentro del ultimo grupo RX) las rechaza validate_elf.

### Resultado de T20a: la memoria de la maquina vista desde el host

`python3 -m unittest discover -s tests/waterbox-smoke -p test_puae6_memory.py` y `tests/host/test_bridge_memory.py`. El guest informa Chip RAM
(primaria), Slow RAM y la ROM como funciones de acceso (flags de `MemoryDomainFlags`: big endian, palabra de 2 bytes, `FunctionHook`, escribible la
RAM). Medido: a la unidad 15 Chip y Slow RAM ya estan sucias (2733 paginas, y escribir en Slow RAM no cambia la cuenta), de modo que el caso
peligroso para un poke managed es el de antes de la primera unidad (un cheat o un valor inicial): justo despues del seal las escrituras de la prueba
caen en paginas que nadie ha escrito y el runtime las registra como del guest (al menos 12 paginas sucias nuevas); un restore las deshace. La luz de
unidad (`P6_FRAME_DRIVE_LIGHT`) llega en el `flags` de `p6w_frame`.

### Resultado de T20b: el core C# abre el guest con el WaterboxHost real de BizHawk

`python3 -m unittest discover -s tests/ffi -p test_ffi.py` (construye con dotnet, sin red: los paquetes salen de la cache local de NuGet).
`integration/bizhawk/core/Puae6.Core.csproj` compila contra los fuentes de MacHawk capturados en references/machawk (los fija
references/machawk.manifest.json) como los arneses del propio MacHawk, sin tocar ningun checkout: BizHawk.Common (salvo VersionInfo.cs, cuya mitad
generada viene de un analizador que la captura no trae), BizInvoke, Emulation.Common y el host Waterbox. Dos tipos que esos fuentes mencionan y
el build independiente no necesita de verdad (GameInfo y la extension AddAxis, copiada tal cual; Extensions.cs arrastra una docena de interfaces de
servicio) estan en `Support/StandaloneStubs.cs`. `tools/build_waterbox.py` (`build_core`, `run_ffi`) pasa al build el SHA-256 del guest del
recibo, que queda en el ensamblado (`Puae6Guest.BuiltWithSha256`): el wrapper abre solo el guest con el que se construyo.

- Identidad (`GuestIdentity`), antes de `wbx_create_host`: ELF64 little endian, `e_machine` 183 y SHA-256 igual al fijado. Diez casos se rechazan sin
  llegar al host (otra maquina con el hash correcto, 32 bits, big endian, un byte cambiado, otro pin, cabecera truncada, fichero vacio, no ELF,
  ausente) y un `uae.wbx` en el mismo directorio no es una salida. Ademas se rechaza un hermano `.zst`: `WaterboxHost` carga `nombre.zst` en
  lugar de `nombre` si existe, y entonces el fichero verificado no seria el que corre. Limite: entre la verificacion y la lectura del host el
  fichero se lee dos veces; un atacante con escritura en el directorio del guest tambien podria cambiar el ensamblado, asi que no se cierra esa
  carrera. Un guest comprimido (`.zst`) no se acepta todavia: su verificacion aun no integra la descompresion.
- Layout (ABI01): los 18 valores de `p6w_layout` y cada campo de cada estructura (offsets de C# frente a `offsetof` de un compilador C sobre las
  cabeceras) coinciden, y `Puae6Guest.Open` falla si el guest abierto informa otra cosa. Un `p6w_init` con bandas de guarda al que el guest rechaza de
  cuatro maneras vuelve sin cambios salvo result y error.
- Con el host de BizHawk la maquina avanza con los mismos ticks por unidad que headless. BizHawk pide a dlopen el nombre `libwaterboxhost.dylib`
  sin ruta; en macOS solo lo encuentra en el directorio de trabajo, asi que el arnes copia la biblioteca junto al ejecutable y lo ejecuta desde alli
  (como los arneses de MacHawk). EmuHawk la tiene junto al ejecutable.

### Resultado de T20c: el core PUAE6 con la interfaz de BizHawk

`python3 -m unittest discover -s tests/ffi -p test_core.py` (once pruebas, unos dos minutos). `Puae6Core` (integration/bizhawk/core/Puae6) es
independiente del core UAE de BizHawk: ID propio (`PUAE6`), guest propio y ninguno de sus estados; no hereda de `WaterboxCore` porque su ABI de
`FrameAdvance` (`FrameInfo`) no es la p6w, y usa solo `WaterboxHost`, `WaterboxMemoryDomain` y `BasicServiceProvider` de BizHawk. Una unidad es
exactamente una llamada a `p6w_frame_advance` y el reset, si lo hay, se encola justo antes (el orden de docs/02-contracts.md).

- Entrada (IN01): los mismos guiones que tests/input pasan por `p6_run_frame` se juegan aqui a traves de `IController` (teclas, botones, ejes absolutos
  del raton) y lo que el fixture input_all deja en la Chip RAM es, palabra a palabra, lo mismo, en PAL y NTSC, con dos joysticks, con teclas y con
  Caps Lock. El raton de BizHawk es una posicion y la maquina toma movimiento: el wrapper manda la diferencia con un maximo de 120 cuentas por eje y
  unidad y el resto no se pierde, se manda despues (probado contra un guion nativo 120, 120, 60); la primera unidad solo aprende donde esta el
  puntero. Caps Lock es un pestillo que una pulsacion conmuta. `Power` es el reset duro (no hay un Reset aparte: nadie lo implemento) y los
  ticks y el contador de unidades siguen creciendo. Todo esto (referencia del raton, pestillo) es estado causal del wrapper y se guarda en el estado.
- A/V: tamano activo, aspecto, cadencia, hash de la imagen y frames de audio por unidad son los de p6_headless en las dos normas; el PCM entero es
  el del arnes de Waterbox; pedir nada de imagen y sonido durante un tramo no cambia lo que sigue; el alfa de cada pixel es opaco. La cadencia que
  informa `VsyncNumerator/Denominator` es la de la unidad (cambia con los campos entrelazados). El audio es sincrono, 48000 Hz, estereo intercalado.
  Lag y luz de unidad salen de la maquina (`P6_FRAME_LAG`, `P6_FRAME_DRIVE_LIGHT`): con el motor encendido y sin disco la luz esta encendida.
  `InputCallbacks` existe y nunca se dispara: el motor no llama al host en cada lectura de entrada.
- Dominios (WB07): Chip RAM (principal), Slow RAM y ROM (512 KiB: lo que el motor asigna, la imagen de 256 KiB espejada), big endian, palabras de
  16 bits, funciones del guest; ningun puntero temporal ni region vacia. Un poke antes de la primera unidad (paginas que nadie ha escrito desde
  el seal) lo hace el guest, la ROM no lo acepta y fuera de rango lanza.
- Dos sandboxes (WB04): dos maquinas del mismo guest con semilla y entrada distintas, por turnos en un proceso, hacen cada una lo que haria sola, y una
  sigue tras liberar la otra.
- Mutaciones sembradas en el core (botones del raton cambiados, sin pestillo, sin limite de 120, Power sin reset, luz ignorada, lag invertido,
  sin alfa): cada una hace fallar su prueba.

Lo que T20c no hace: medios (el guest no exporta eventos de medios todavia, T22/T27), estados con inventario completo del wrapper (T21), registro en
EmuHawk (T20) ni el firmware por la base de datos de BizHawk.

### Resultado de T20: PUAE6 en el BizHawk real, junto a UAE

`python3 -m unittest discover -s tests/ffi -p test_bizhawk_tree.py` (diez segundos tras la primera vez). `integration/bizhawk/patches/build_tree.py` saca los
fuentes de MacHawk con `git archive` del commit fijado (nada se escribe en ../MacHawk; los parches se comprueban con `git apply --check` contra su arbol),
los copia a `build/bizhawk-tree/<clave de las entradas>`, aplica `0001-register-puae6.patch`, instala los fuentes del core (`integration/bizhawk/core/Puae6` y
`EmuHawk`) en `BizHawk.Emulation.Cores/Computers/Amiga/PUAE6` y compila ese proyecto real (net48, los analizadores de BizHawk, paquetes de la cache local).
El parche toca tres ficheros: `CoreNames.PUAE6`, el selector de cores del Amiga en `Config.CorePickerUIData` (UAE primero, es el predeterminado) y el
`AssemblyMetadata` con el hash del guest (el del recibo de la receta). `tests/ffi/inventory` carga el ensamblado tal como se compilo y pregunta al
`CoreInventory` de BizHawk: UAE (prioridad normal) y PUAE6 (`CorePriority.Low`, constructor con `CoreLoadParameters`) estan registrados, y con el orden que
usa `RomLoader` (preferido de la configuracion, luego prioridad) un juego de Amiga sigue abriendose con UAE y PUAE6 se elige de forma explicita. Las peliculas
nombran el core que las grabo, asi que las de UAE siguen nombrando UAE.

Hallazgos: `git apply` ejecutado dentro de un subdirectorio de otro repositorio (build/ lo es) omite EN SILENCIO todo fichero fuera de ese subdirectorio y sale
con exito: la receta usa GNU patch y comprueba que cada parche queda aplicado. El net48 de BizHawk no tiene `InlineArray`, `Math.Clamp`, `SHA256.HashData`,
`Convert.ToHexString` ni `Marshal.PtrToStringUTF8`: los fuentes del core se escribieron para compilar en los dos (net8 del arnes y net48 de BizHawk).

Lo que NO queda demostrado: no se ejecuto EmuHawk (no hay Mono; el ensamblado se carga por reflexion), el cambio de `Config.cs` pertenece a un proyecto
(Client.Common) que no se compila aqui (se comprueba que el parche se aplica y que el texto esta), no hay entrada de menu ni icono, y con la API de medios
del guest todavia sin exportar el constructor de EmuHawk rechaza un juego con disco (`NotSupportedException`: PUAE6 no carga discos todavia; el cargador de
BizHawk probaria entonces el siguiente core, UAE).

### Resultado de T21a: discos en el guest y en el core

`python3 -m unittest discover -s tests/ffi -p test_media.py`. El guest exporta `p6w_media_event_enqueue` (el layout que informa pasa de 18 a 22 valores) y el
core acepta ADF al crearlo (880 KiB cada uno, hasta 16, con su SHA-256 como identidad; los `FloppyDrives` primeros, uno por defecto, estan en una unidad al
arrancar y el resto son slots). Los botones `Next Drive`, `Next Slot`, `Eject Disk` e `Insert Disk` existen cuando hay discos: la seleccion se hace primero y
despues Eject (o, si no, Insert) actua sobre ella, por flanco, al principio de la unidad, con una secuencia que solo crece y que no se reinicia con
`ResetCounters`. Lo medido: con los botones, el disco A de arranque y B insertado en la unidad 80, la Chip RAM final es byte a byte la que deja el probe nativo
de MED01 con `p6_media_event_enqueue` (PAL y NTSC, dos semillas); `Next Drive` da la vuelta con una unidad; un boton mantenido es un solo evento; el guest ve
expulsar y reinsertar (las cuatro fases de `swap_timeline`); una segunda unidad toma un disco sin cambiar lo que ve la primera; un disco que no es un ADF de
901120 bytes se rechaza. Cuatro defectos sembrados (insertar o expulsar sin flanco, `Next Drive` sin vuelta, seleccionar despues de actuar) hacen fallar su prueba.
El constructor de EmuHawk acepta ADF y rechaza HDF, LHA y discos opticos con un mensaje (desde T63b acepta tambien HDF; LHA y discos opticos siguen rechazados).

### Resultado de T21b: el estado del wrapper, su sobre y las pruebas de snapshot con el core real

`python3 -m unittest discover -s tests/waterbox-state -p "test_*.py"` (unos ocho minutos). Un estado del core es un sobre alrededor de un payload: el sobre dice para que
vale (SHA-256 del guest, tamanos de heap, hash de la configuracion de sincronizacion, SHA-256 del firmware y hash de los discos), la longitud y un checksum del
payload; el payload son los campos del wrapper (`WrapperState`, inventariados en manifests/state-inventory.json, clave `wrapper`) y despues el snapshot del sandbox.
El Kickstart y los ADF viven en el heap sealed, que un snapshot no restaura: sin la comprobacion del sobre un estado de otro firmware se cargaria sobre una maquina con
otra ROM sin protestar (el runtime solo avisa por la salida de error). Un load valida TODO antes de tocar el sandbox: magic, version, identidad, longitud (hasta 256 MiB),
checksum y los campos (una unidad que no existe, un tamano fuera de los buffers); un estado que se rechaza no cambia nada y la instancia sigue donde estaba. Si el
`LoadStateBinary` del host falla despues de empezar (no deberia ocurrir con un estado que paso el checksum), la instancia queda `_faulted` y se niega a avanzar o guardar
hasta que un estado se cargue entero.

Lo probado, con el snapshot real del sandbox y nunca con el serializador nativo (WB03/WB05: guardar en 10, 40, 55, 85, 130 y 185 unidades de un escenario de disquete con
DMA, motor, cambio de disco pendiente y una tecla retenida, avanzar 100, cargar y repetir: las mismas unidades —ticks, hash de imagen, audio y su hash, lag, luz y
eventos— y el mismo SHA-256 del estado completo; y cien alternancias de guardar/cargar; WB04: un estado de A cargado en un sandbox B nuevo y en un C que ya habia corrido,
y los tres por turnos; WB06: cada campo del wrapper con efecto causal —referencia del raton, pestillo de Caps Lock, boton retenido, unidad y slot elegidos, los cuatro
botones de disco retenidos, secuencia de medios— cambia entre guardar y cargar y recupera su efecto; WB11: otro build, otros heaps, otra region, semilla, unidades, puertos,
discos, menos discos, sin discos y otro firmware, y estados vacios, de tres bytes, con el magic o la version cambiados, de basura, cortados en la cabecera y en el payload,
con la longitud cambiada, con un bit del payload o del checksum alterado se rechazan con su motivo y la instancia sigue exactamente como un gemelo al que nunca se
le pidio nada; los bytes despues del final de un estado no se leen; WB14: pokes por los dominios a paginas limpias (justo despues del seal) y sucias se deshacen al restaurar;
WB15: un estado escrito por un proceso lo carga otro proceso nuevo y las unidades siguientes son las del que lo escribio).

Mutaciones sembradas en el core y el sobre (no restaurar cada campo del wrapper, no comprobar el firmware, no comprobar el checksum) hacen fallar su prueba.
Limites: la semilla forma parte de lo que un estado vale (como en el contrato: mismos medios y configuracion), el payload se arma en memoria (decenas de MB por estado y su
checksum: el coste de snapshot es de T33/T36) y `_driveDisk` se guarda solo para los mensajes de pantalla.

### Resultado de T22a: la pelicula del core con el codigo BK2 de BizHawk

`python3 -m unittest discover -s tests/bizhawk-movies -p test_movie_format.py`. La captura de MacHawk (references/machawk, 366 ficheros) gano cuatro ficheros pequenos con
`tools/import_sources.py` (la captura anterior se aparto a build/stale y el import reprodujo los mismos bytes): `Bk2LogEntryGenerator`, `Bk2Controller`, `IMovieController` y
`ICycleTiming`. Las tres primeras clases se compilan en el arnes y graban y leen el input log: un `.bk2` (zip con `Header.txt` e `Input Log.txt`) con una linea `|...|` por
unidad, un grupo por jugador (botones de consola —Power, el teclado, los botones de disco—, despues los del puerto 1 y los del puerto 2) y un caracter por boton (el
formato es posicional, asi que los mnemonicos repetidos del teclado Amiga no confunden nada), los ejes con su valor (el raton absoluto), el `LogKey` sale de la
`ControllerDefinition` del core y la cabecera lleva `Core PUAE6`, `Platform Amiga`, el SHA-256 del guest, el hash de los sync settings, el del firmware y el de los discos (claves
`PUAE6*`, que BizHawk conserva sin conocerlas) y al final `CycleCount` y `ClockRate`. El core implementa `ICycleTiming`: sus ticks maestros y 512 ticks por colour clock
(3546895 Hz en PAL, el reloj del chipset en NTSC).

Probado: cada boton y cada eje de cada una de las 240 unidades de la pelicula fixture (teclado con puntuacion, raton, dos joysticks, Power, Next Slot, Insert y Eject) se
decodifica a lo que se grabo; una pelicula reproducida desde su fichero deja la maquina como la sesion en vivo, unidad a unidad y en el estado final; la cabecera de otro
core (el UAE de BizHawk), otro sistema, otro guest, otros sync settings, otro firmware u otros discos —o un core con otra region, semilla, unidades, puertos, discos o firmware—
se rechaza con su motivo antes de que corra una unidad, y no hay conversion de las peliculas de UAE. Mutaciones sembradas (no comprobar el firmware, no comprobar el core, un
`ClockRate` equivocado) hacen fallar su prueba. El `CycleCount` de la cabecera es el de la ultima unidad.

### La CPU y su reloj en el wrapper (T52)

`CpuModel` (`Auto`, `M68000`, `M68010`, `M68020`) y `CpuMultiplier` (`Auto`, `X1`, `X2`, `X4`, `X8`, `X10`, `X12`, `X16`) son sync settings de reinicio del core BizHawk: dan al guest `p6_config.cpu_model`
(offset 63) y `cpu_multiplier` (offset 64) y entran en la identidad del estado y de la pelicula **solo fuera de `Auto`** (`;cpu=M68010`, `;cpuclock=X8`; el `config_hash` nativo los deriva
con el dominio `p6/config/cpu/v1`, ADR 0012). El guest cambia con los parches 0310/0311 y el bloque de 10 bytes de `P6CP`: `guest_sha256` de las 16 claves `measured*` de
`manifests/waterbox-memory.json` se registra de nuevo con la receta de arriba (las medidas no se mueven: el cambio cabe en las paginas ya contadas) y `runtime_sha256` queda como estaba.
`tests/waterbox-state/test_wrapper_cpu.py` (WB03 con la 68010 y con otros relojes en el A500 y el A1200, el estado en un sandbox nuevo, el rechazo de un estado de otra CPU o de otro reloj antes de
tocar el sandbox, la 68010 que no es la 68000, un reloj mas rapido que cambia la maquina y no las unidades, y las combinaciones que no existen que fallan al arrancar con la razon).

### El controlador de los discos duros en el wrapper (T56b)

`HdfController` (`Uae`, `Ide`) es una sync setting de reinicio del core BizHawk: da al guest `p6_config.hdf_controller` (offset 65, un byte de lo que era `reserved7`) y entra en la identidad del estado y de la
pelicula **solo fuera de `Uae`** (`;hdfcontroller=Ide`; el `config_hash` nativo lo deriva con el dominio `p6/config/ide/v1` y solo mientras hay un HDF detras del IDE: `Ide` sin HDF es la maquina por defecto, y
el texto del wrapper, que nombra el ajuste siempre, es mas estricto y nunca mas laxo). `Ide` solo existe en el A600 y el A1200; en otra maquina el core falla al arrancar con la razon del puente. El guest cambia con los parches
0320-0322 (Gayle, su IDE y el estado de ambos): `guest_sha256` de las 16 claves `measured*` de `manifests/waterbox-memory.json` se registra de nuevo con la receta de arriba y `runtime_sha256` queda como estaba.
`tests/waterbox-media/test_media_ide.py` (MOUNT: el HDF que exporta el core tras N unidades es, byte a byte, el que exporta el motor nativo en A600 y A1200, con el guest ordenado y con el que toca al azar; WB03 con la
controladora ocupada; el estado en un sandbox nuevo; el rechazo de un estado de otro controlador o sin HDF antes de tocar el sandbox; y IDE en una maquina sin Gayle que falla al arrancar con la razon).

ADF extendido y FDI (T57): el guest cambia con los parches 0330 a 0335 (la escala de la cabeza y la cola de una pista cruda, el decodificador `fdi2raw` acotado y determinista, el estado de la lectura de varias vueltas,
la temporizacion por byte); `guest_sha256` de las 16 claves `measured*` se registra de nuevo con la receta de arriba (`runtime_sha256` queda como estaba: `cf45f96e`, el runtime Release reproducible). El heap sellado no
cambia (el total de los disquetes sigue acotado por 16 ADF planos: 14 417 920 bytes), el buffer de entrada de un FDI es el de su pista mas larga (no los 4 MiB del formato) y la cota del estado nativo crece, pero no el
snapshot del sandbox mas que por las paginas que toca la pista que se lee (`tests/waterbox-media/test_media_trackdisk.py`).

IPF (T58): el guest cambia con el parche 0340 y con el lector de `third_party/mame-ipf` (BSD-3-Clause; ADR 0013) compilado en `p6_portable` (`ports/portable/media/ipf_track.cpp`, `ipf_blob.cpp`). Esa unidad usa `std::vector` y
se compila SIN excepciones en el guest (`PER_FILE_FLAGS` en `ports/waterbox/Makefile`): con ellas, los sitios `throw` de libc++ importaban `std::length_error`, `std::bad_array_new_length` y `__cxa_free_exception`,
que HOST01 (`ports/waterbox/imports.json`) no autoriza; sin ellas libc++ acaba en `__libcpp_verbose_abort`, que la misma unidad define como un trap (un fallo de reserva termina la llamada del sandbox; los tamanos estan
acotados: 160000 celdas, 256 KiB de datos por pista, 168 pistas, asi que la memoria transitoria de decodificar una pista es de unos 640 KiB de celdas mas 64 KiB, del heap normal). El indice del IPF (unos 17 KiB por
disquete) se hace al insertar el disco y se libera al expulsarlo; el disco se lee por offsets del almacen de medios, sin copiarlo. `guest_sha256` de las 16 claves `measured*` se registra de nuevo (`runtime_sha256`
queda como estaba: `cf45f96e`). Un snapshot a mitad de una vuelta de una pista con celdas debiles vuelve en el mismo sandbox y en uno nuevo (`tests/waterbox-media/test_media_ipf.py`, WB03).


### Resultado de T22b: reproduccion, seeks y ramas de peliculas con el core real

`python3 -m unittest discover -s tests/bizhawk-movies -p test_movie_playback.py` (unos veinte minutos: la pelicula larga son 10000 unidades grabadas y reproducidas dos veces).
Las peliculas se graban con el core (entrada pseudoaleatoria con semilla fija: teclas, raton, joysticks, resets y cambios de disco periodicos) y se reproducen desde su `.bk2`.

- WB02: una pelicula de 10000 unidades grabada por un proceso y reproducida por dos procesos nuevos a la vez da las mismas unidades (ticks, hash de imagen, audio y su hash, lag, luz y
  eventos de medios) y la misma maquina al final; el `CycleCount` y la cuenta de lag de la cabecera son los del final de la reproduccion (`moviefinish` falla con codigo distinto de
  cero y dice que difiere: probado con un `CycleCount` doblado) y una pelicula que acaba antes que el core es un error.
- WB09: en dos peliculas (una con disquete y otra con un ROM entrelazado, de 720 unidades) se guardan estados cada 60 unidades y se hacen 40 seeks a fotogramas distintos, hacia
  delante y hacia atras, cargando el estado anterior y reproduciendo hasta el destino: las unidades vueltas a jugar son las de la primera reproduccion y la maquina es la de la
  reproduccion continua. Seis ramas desde el mismo estado (otras entradas) van cada una por su lado, y volver al estado y a la pelicula converge a la reproduccion continua.
- WB08: apagar imagen y sonido en cuatro tramos (uno de una sola unidad) no cambia ticks, lag ni eventos de ninguna unidad, las unidades que siguen entregando imagen y sonido
  dan los mismos hashes y el estado de la maquina (el snapshot del sandbox, sin los campos del wrapper que solo existen con imagen) es el mismo al final.
- DET06: la misma reproduccion con otra zona horaria, locale, idioma, globalizacion invariante, directorio de trabajo, `HOME` y `TMPDIR` es la misma reproduccion y la grabada.
  (El directorio de trabajo es el de la biblioteca del runtime, porque BizHawk pide a dlopen su nombre sin ruta: se probaron cuatro copias en cuatro directorios.)

Alcance: Waterbox cubre tres maquinas, el A500 (OCS), el A600 (ECS, 1 MiB Chip, sin Slow ni Gayle) y el A1200 (68EC020 ciclo exacto, AGA, 2 MiB Chip, sin Gayle), todas PAL y NTSC, como sync setting `Machine` (T23e, T24f); Fast, FPU, Gayle/IDE y otras CPU siguen sin probar. Una pelicula
es de una maquina: la cabecera BK2 lleva el hash de configuracion (`machine=` forma parte de la cadena que se hashea), de modo que una pelicula o un estado de una maquina se rechaza en cualquiera de las otras dos (matriz 3 x 3), antes de tocar el
sandbox y con un mensaje que nombra la maquina para la que se hizo; las peliculas y estados del A500 y del A600 anteriores a T24f conservan su hash y siguen cargando. Probado con las micro-ROM y los discos de las pruebas en las tres
maquinas (WB03, `tests/waterbox-state/test_wrapper_machine.py`, y la reproduccion de una pelicula en el A600) y, con las variables de entorno de firmware, con Kickstart 1.3 en el A500, Kickstart 2.05 en el A600 y Kickstart 3.1 mas la demo AGA Catabasis (un estado a mitad de la demo restaurado en un sandbox nuevo y en otro proceso) en el A1200; no probado:
la interfaz de EmuHawk (no se ha ejecutado), otros Kickstart.

Presupuesto por maquina (`manifests/waterbox-memory.json`, `measured`, `measured_a600`, `measured_a1200`; el presupuesto de cada heap no cambia y no se reutiliza sin medir): mmap 5 586 944 B (A500), 5 046 272 B (A600) y 6 094 848 B (A1200, 36 % de los 16 MiB: 2 MiB de Chip y las tablas de AGA);
snapshot tras 60 unidades 9 203 892, 9 187 508 y 10 236 084 B (T62: +49 888 B por el parche 0280 y la config; antes 9 154 004, 9 137 620 y 10 186 196). Con AGA compilado el guest pesa unos 9,8 MB mas de imagen (T24b); las medidas de las tres maquinas se vuelven a grabar con `--record-memory` cuando cambia el guest, y ninguna pasa de la mitad de su presupuesto (lo exigen los tests de `tests/waterbox-smoke`). Memoria configurable (T49): un A1200 con 8 MiB de Fast RAM Zorro II, el A600 de PUAE5 (2 MiB de Chip y 8 MiB de Fast) y un A500 con Fast y Slow RAM se ejecutan en el sandbox unidad por unidad como `p6_headless`
con las mismas opciones (`tests/waterbox-smoke/test_puae6.py`), y la placa es un dominio de memoria (`Fast RAM`, entre `Slow RAM` y la ROM; `test_puae6_memory.py`, `tests/ffi`). El heap mmap
deja de ser un unico presupuesto: la regla de `memory_budget` en `manifests/waterbox-memory.json` es el presupuesto base mas DOS VECES la memoria pedida por encima de la propia de la maquina
(toda la Fast mas el Chip y el Slow que excedan), de modo que la regla de la mitad sigue valiendo y una configuracion sin memoria extra conserva el layout, el snapshot y la identidad de siempre. Medido
(micro-ROM, 60 unidades): A1200 + 8 MiB de Fast 14 499 840 B de mmap committed (de un presupuesto de 33 554 432), snapshot 10 309 076 B; A600 con 2 MiB de Chip y 8 MiB de Fast 14 499 840 B y 10 310 100 B;
A1200 con 1,5 MiB de Slow y 8 MiB de Fast 16 613 376 B (de 36 700 160) y 12 424 148 B; A500 con 2 MiB de Fast 7 700 480 B y 9 270 740 B. La placa se limpia al arrancar sin escribir los ceros que ya tiene
(parche 0114): sin eso sus 8 MiB quedaban sucios y el snapshot del A1200 pasaba de 10,2 MB a 18,7 MB. El wrapper calcula el mismo presupuesto (`Puae6Core.Heaps(settings)`, que forma parte de la identidad
de un estado) y `tests/waterbox-state/test_wrapper_memory.py` lo compara con el de la regla.
Las peliculas del core UAE de BizHawk se
rechazan por su cabecera y no se convierten. Los contadores de lag y de ciclos son los de la propia grabacion (el golden es la grabacion: si una reproduccion difiere, se corrige el
motor o el core, nunca la pelicula).

Hallazgo para el wrapper (T20): una escritura del host en una pagina que el seguimiento de paginas
sucias aun tiene protegida, hecha por codigo managed, la convierte .NET en un AccessViolationException
fatal antes de que el manejador de Waterbox la vea; hecha por codigo nativo (memcpy de libSystem)
queda registrada como la del guest. Los pokes y trucos del host deben pasar por codigo nativo.

## Estado C# y callbacks

WaterboxCore guarda ciertos contadores/RTC/base pero no conoce los campos nuevos
de PUAE6 (S6). Inventariar todos los campos del wrapper y clasificarlos. Deben
volver al valor correcto edges de eject/insert, seleccion de unidad/slot, raton
previo, entrada latched, flags de reset, cualquier indice de medio y config mutable.
Preferencia: estado causal en bridge dentro del sandbox; evitar duplicarlo en C#.

Override de SaveStateBinaryInternal y LoadStateBinaryInternal cuando proceda.
Formato propio con version y bounds. Validar identidad/build/medios ANTES de una
restauracion que pueda modificar el sandbox; si el base loader no admite ese
orden, envolverlo o definir rechazo seguro/recreacion. No intentar adivinar datos.

Despues del restore no quedan callbacks de una instancia anterior. Establecer de
nuevo input/LED/debug segun las reglas del host; conservar las referencias a los
delegates mientras el sandbox pueda llamarlos. Los buffers A/V fijados de una
llamada anterior nunca se reutilizan por su direccion vieja.

La prueba mas exigente crea B desde el mismo WBX/config/media, restaura un estado
creado en A y ejecuta las mismas entradas. Una prueba solo en A puede ocultar
punteros externos y cache/state C# omitidos.

## Dominios de memoria, bus e instrumentacion

Exponer Chip RAM como Primary, Slow/Fast RAM solo si existen y ROM como lectura.
Nombres/tamanos/flags y byte order corresponden al espacio Amiga. No anunciar una
region de tamaño cero o un puntero temporal. Verificar lecturas multi-byte sobre
bytes de guest, especialmente endian, alineamiento y alias de bancos.

Un System Bus de debugger requiere lecturas sin efectos laterales o advertencia
precisa; leer registros de dispositivos mediante un acceso normal puede cambiar
la emulacion. No implementar bus falso como una RAM lineal. Debug writes y cheats
son acciones externas explicitamente registradas o desactivadas durante pruebas TAS.

FrameAdvance informa ciclo y lag reales segun 02-contracts. Implementar servicios
minimos del pin: IEmulator, IVideoProvider, ISoundProvider sincronico, IStatable,
input y memory domains; añadir IDriveLight/region/settings y persistencia cuando
su comportamiento este probado. No inventar tipos C# que el pin no contiene.

## Medios y escritura

Primer port: medios readonly precargados. No introducir un callback host de I/O
mutable solo para evitar resolver init/seal. La lista de discos pertenece al
manifiesto de sesion; swap selecciona IDs de esa lista despues de seal.

M3 añade overlay de escritura capturado. Al cargar un estado se revierte overlay,
bitstream/track cache y metadatos de drive, no solo un buffer ADF. No usar el filesystem
del host para writes que sobrevivan a un rewind sin formar parte del estado.
ISaveRam puede usarse para una representacion acotada, pero NO mapear el HDF entero
como save RAM automaticamente. Definir export/import del overlay y distinguir
persistencia durable del rollback. Peliculas reproducidas no flushean por defecto.

Estado (T28): hecho y probado en el runtime Waterbox real (tests/waterbox-media, ejecutado con `python3 -m unittest discover -s tests/waterbox-media -p 'test_media_*.py'`).

1. Exports del guest (`adapters/waterbox/p6w.h`): `p6w_media_write_status` (estado del overlay de un medio) y `p6w_media_export` (copia el disco actual, base mas overlay, 901120 bytes, al
   buffer del host), con la disciplina p6w (version, tamaño, resultado y mensaje; los punteros del host solo valen durante la llamada) y sin efectos en la maquina. `p6w_layout`
   pasa de 22 a 31 valores y el wrapper los compara (`Abi.cs`, `Puae6.cs` del smoke y el compilador C en `tests/ffi`). El pool del overlay es memoria del sandbox y el snapshot lo captura.
2. `Puae6SyncSettings.FloppyWrites` (por defecto `false`: los discos siguen protegidos y el nucleo no tiene `ISaveRam`) fija `p6_config.flags = P6_CONFIG_FLOPPY_WRITES`. Entra en el hash de
   configuracion (`;floppywrites=1` solo si esta activo: la identidad de un nucleo sin escrituras no cambia), asi que estados y peliculas de un valor no cargan en el otro (los heaps
   tambien difieren y el sobre los comprueba antes: `OtherHeaps`). Los heaps crecen `FloppyOverlayKiB` = 4096 de "memoria extra" (x2 del mmap, regla de `memory_budget`).
3. `ISaveRam` solo como representacion acotada y explicita (`SaveRam.cs`): `"P6SR"`, version 1, por disco que cambio (indice, content id SHA-256 de la BASE tal como se dio al nucleo,
   sectores cambiados en orden creciente con sus 512 bytes) y SHA-256 del conjunto; como mucho 3520 sectores (el pool), unos 1,8 MiB, y 12 + 40 + 514 n + 32 bytes en la practica (11 392
   para los 22 sectores de MED02); nunca una imagen. `CloneSaveRam` da `null` si nada difiere de la base, si no hay escrituras y mientras `ReplayInProgress` (la peticion de una pelicula
   no lee ni sustituye la copia duradera); un disco con una pista que no es AmigaDOS no cabe en un ADF, se deja fuera y se avisa (`DisksWithRawTracks`). `SaveRamModified` compara con
   la ultima copia que se declaro limpia. `StoreSaveRam` solo antes de la primera unidad y de cargar un estado (`TooLate` despues) y nunca en replay: la base vive en el heap sellado, asi
   que el nucleo reconstruye el guest con los discos que da la SaveRam (como un nucleo nuevo; si falla, el nucleo queda como estaba) y el SHA-256 de lo guardado entra en el hash de
   configuracion: un estado hecho sin esa SaveRam no carga. Una SaveRam que no es entera, ni nuestra, ni de estos discos (`NotSaveRam`, `WrongVersion`, `BadLength`, `Corrupt`,
   `BadFields`, `OtherMedia`) se rechaza con su motivo y no cambia nada; los ceros tras el checksum son relleno y se ignoran. Rollback (snapshot) y persistencia son operaciones distintas:
   un `load` no toca la copia guardada ni la modifica.
4. WB10 (`test_media_overlay.py`): MED02 escribe, sobrescribe y relee pistas AmigaDOS por DMA; el disco coincide con el modelo, un estado a mitad devuelve bytes, pista leida, lineas del drive y
   digest completo en el mismo sandbox y en uno nuevo (WB04, tres sandboxes turnandose y otro proceso), una rama o un seek de pelicula con escrituras dejan el overlay donde dice el estado,
   y ningun fichero (discos, ROM) cambia: los archivos dados al nucleo se comparan por tamaño, mtime y SHA-256 despues de cada test. WB11: estados de otro `FloppyWrites`, otros discos
   o con otra SaveRam, y peliculas de otro ajuste, se rechazan sin tocar la instancia (mismo digest y mismo futuro que un control). WB12 (`test_media_limits.py`, escritor adversario de
   `wb12.py`): se reescriben las 160 pistas de DF0 y de DF1 (3520 sectores: justo el pool; un sector cuyo contenido iguala al de la base no gasta hueco, por eso el disco W invierte el primer
   byte de cada sector); con menos de 11 sectores libres el drive informa protegido, un tercer disco metido en DF0 queda intacto y `refused_writes` sigue en 0 (el invitado no llega a
   intentarlo); 18 pistas no AmigaDOS llenan las 16 ranuras crudas y las otras 2 se cuentan como rechazadas, el disco no se exporta (UNSUPPORTED) y la SaveRam lo deja fuera.
   Mediciones (manifests/waterbox-memory.json, `measured_a500_floppy_writes_full`, micro-ROM adversaria, 5600 unidades): el heap mmap compromete 9 502 720 bytes desde el sello (5 586 944 de
   la maquina sin escrituras: el pool de 3520 sectores se reserva al cargar) frente a un presupuesto de 25 165 824 (16 MiB mas 2 x 4096 KiB); la memoria sucia del mmap pasa de 1 081 344
   a 2 932 736 bytes. El snapshot con el pool vacio (unidad 100) es de 9 273 065 bytes y con las 160 pistas de los dos discos escritas de 11 058 644: el crecimiento (1,79 MB) es el pool, por debajo
   de los 2 017 456 bytes que la cota de estado ya incluye, y no crece mas por escribir otra vez lo mismo.

Autocarga y peliculas (T28b, `tests/waterbox-media/test_media_frontend.py`, `tests/ffi/MainFormSaveRam.cs`): `MainForm.LoadSaveRam` de EmuHawk solo pasa el fichero `.SaveRAM` entero a una lista fija
de nucleos; a los demas les dimensiona el buffer con `CloneSaveRam()` de un nucleo recien creado, que aqui es `null` (nada difiere de la base), asi que se salta la carga. Se eligio (a): `integration/bizhawk/patches/0001-register-puae6.patch`
añade `Puae6.Puae6Core` a esa lista (una linea en `MainForm.cs`; `build_tree.py --emuhawk` compila EmuHawk.exe, net48, en un arbol aparte para comprobar que lo que cambia el parche es valido contra el nucleo; no se ejecuta, es un programa de
Windows). La opcion (b), un tamaño fijo con relleno de ceros, funcionaria con un EmuHawk sin parchear pero obligaria a cada `.SaveRAM` a pesar el maximo (1 809 964 bytes frente a 11 392 de MED02) y dimensionaria el buffer con un dato que el
nucleo no tiene (cuanto va a escribir el invitado); (a) no cambia el formato ni el coste. Con un EmuHawk sin parchear el fichero se escribe (FlushSaveRAM) pero no se autocarga, que el test `stock` documenta. Un fichero que el nucleo rechaza
(`SaveRamRejectedException`, una `IOException` porque `LoadSaveRam` solo captura esa) se avisa en pantalla con el motivo y no cambia nada. `ReplayInProgress` no lo pone ningun frontend real (BizHawk separa la SaveRAM de cada pelicula, no
ejecuta `LoadSaveRam` con una pelicula en cola y empieza las peliculas "desde SaveRAM" con `StoreSaveRam` antes de la primera unidad): es un resguardo para herramientas y para el arnes; su documentacion esta en el codigo.
Abierto (T60): ningun EmuHawk real se ha ejecutado aqui (el flujo se reproduce con el mismo algoritmo de MainForm sobre el nucleo y el sandbox reales); el caso de una pista que no es AmigaDOS en una SaveRam (formato extendido) y el
rendimiento de `SaveRamModified` (exporta los discos tocados) con Kickstart real tampoco estan medidos.

Fallo intermitente al crear un host (T28a, T28a): con este equipo (macOS 26.7.1, `libsystem_malloc` xzone) del orden del 0,5 al 4 % de los procesos que crean un host Waterbox
mueren con SIGSEGV o SIGBUS dentro de malloc o se cuelgan (el manejador de `tripguard` espera un cerrojo que su propio hilo tiene). Pasa con un solo sandbox, sin `FloppyWrites`, antes de T28 y
sin .NET (`tests/waterbox-media/boot_driver.c`); no es del core ni de un segundo heap. Un test que cree sandboxes y muera con -11, -10 o un timeout de 30 minutos con esa pila no es una
regresion; el reproducer es `tests/waterbox-media/repro_heaps.py` y el parche que convertiria el cuelgue en aborto esta descrito en el informe (es de MacHawk, que aqui es solo lectura).

Discos duros en Waterbox (T29d, `tests/waterbox-smoke/test_puae6_hdf.py`, ejecutado en el runtime real): el guest recibe un `P6_MEDIA_HDF` por `p6w_init` como cualquier medio, pero NO lo copia al heap sellado (que es del
firmware y de discos que nadie parchea y tiene 16 MiB): `p6_load` copia la imagen al almacen de blobs, en el heap mmap, y reserva a su lado el sitio del overlay de escrituras (T29b), todo antes del sello, de modo que la
base es la linea de base del snapshot y solo viajan las paginas sucias del overlay. Presupuesto: el heap mmap de la sandbox crece `2 x (2 x imagen + bitmap + 64 KiB)` (memoria extra, contada dos veces como toda la regla de
`memory_budget`; `HardFileHeapKiB` en el smoke, contrapartida del `HardDiskHeapKB` de MacHawk, que guarda la imagen una vez porque escribe en su sitio). Medido (`measured_a500_hdf2048` y `measured_a500_hdf32768`, micro-ROM
que escribe un trozo por unidad, 60 unidades): 2 MiB comprometen 9 814 016 bytes de mmap y 32 MiB 72 728 576 (el doble de la imagen mas 5,6 MB de la maquina), y el snapshot es de 9,8 MB en ambos casos (el A500 sin disco duro:
9,2 MB). Pruebas, contra `p6_headless --hdf` con el mismo ROM y la misma imagen: (1) las 40 unidades (ticks, imagen, audio) son las nativas y el disco duro exportado por el guest (`p6w_media_export`) tras la ultima es, byte a
byte, el que exporta el motor nativo (`--export-hdf`); (2) WB04/WB10: un snapshot de la unidad 20 restaurado en un proceso y una sandbox nuevos continua con las unidades de la ejecucion directa, su disco duro es el de la unidad
20 nada mas cargar y el de la ejecucion directa tras las mismas unidades, y el digest final de la sandbox es el mismo; (3) WB10 en la misma sandbox: cargar de vuelta el snapshot de la unidad 12 tras la 30 devuelve el disco
duro al de la unidad 12 (no el de las escrituras posteriores) y las unidades que vuelven a correr son las de la primera pasada; (4) el presupuesto: la imagen no esta en el heap sellado, lo comprometido cabe en la mitad del
presupuesto y el snapshot crece con los trozos escritos, no con la imagen. El crash de libwaterboxhost (T28a) no aparecio en estas ejecuciones. Limites: aqui se prueba el guest y el ABI `p6w`, no el wrapper (el nucleo C# monta HDF desde T63b, parrafo siguiente).

Discos duros en el nucleo de BizHawk (T63b, `tests/waterbox-media/test_media_hdf.py`, en el sandbox real; formato de la SaveRam y de la identidad en docs/02-contracts.md, "HDF en el nucleo BizHawk/Waterbox"):
`Puae6Parameters.HardFiles` (y los `.hdf` del cargador de EmuHawk) se montan tras los disquetes con el heap `Heaps(settings, tamaños)` (twice `HardFileHeapKiB` por imagen) sin tocar el guest ni su ABI. Lo medido: el HDF que
exporta el nucleo tras 40 unidades del micro-ROM de lazo es, byte a byte, el que exporta `p6_headless`; el overlay cuenta los 42 trozos que escribio el invitado; la SaveRam de un HDF de 32 MiB con esos 42 trozos ocupa 172 328 bytes; un nucleo nuevo la
recibe antes de su primera unidad y exporta la misma imagen, devuelve la misma SaveRam byte a byte y, ejecutando mas, acaba donde un nucleo que nunca recargo; WB03/WB04/WB15 con HDF (guardar, seguir, cargar, repetir:
mismas unidades y digest; sandbox nuevo; otro proceso; el overlay vuelve atras con la carga); un estado o una pelicula de otro HDF o de ninguno se rechaza sin cambiar la instancia; los ficheros dados al nucleo no cambian
(MED02). Limites: la SaveRam se calcula exportando la imagen entera y comparandola con la base (`CloneSaveRam` y `SaveRamModified` cuestan el tamaño de la imagen cuando el invitado escribio algo; no hay API que
enumere los trozos sucios y se prefirio no tocar el guest ni el ABI), el menu "Export Hard Disk" de EmuHawk y el arranque desde RDB con un Kickstart real quedan fuera (el montaje desde RDB de un Kickstart 1.3/3.1 lo prueba
T29a/c con micro-ROM y Kickstart del propietario en el motor nativo; ningun EmuHawk real se ha ejecutado aqui).

## Suite de aceptacion Waterbox

WB01 mini-core real antes del port Amiga; no emulacion de wrapper nativo.
WB02 A500 arranca y avanza 10000 unidades de video con input fijo dos veces.
WB03 snapshot a N, ejecutar M, restaurar, repetir M: igual hash hardware/video/audio.
WB04 restore en sandbox B nuevo y dos sandboxes alternados sin contaminacion.
WB05 estado durante motor de disco activo, DMA/audio, tecla retenida y swap pendiente.
WB06 cada campo C# con efecto causal cambia entre save/load y recupera su efecto.
WB07 canarios en A/V, limites de memoria y referencias host invalidadas/repuestas.
WB08 render/sound desactivados temporalmente no cambian futura emulacion.
WB09 reproducir pelicula con reset, raton, teclado y eject/insert; rama/seek repetido.
WB10 overlay de disco vuelve atras y no modifica medio base ni archivo durable.
WB11 estado de otro core/build/ROM se rechaza sin corromper la instancia.
WB12 medir p50/p95 de frame y snapshot, bytes/paginas mutadas y ocupacion de heaps.

Estos IDs pertenecen a docs/04-validation.md y deben ejecutarse en las tareas
correspondientes. No afirmar soporte de Waterbox a partir de WB01 solo.

## Arranque CD32 de peliculas reales (T66r5)

El parche derivado de MainForm captura identidad antes del reboot y MovieSession
la comprueba antes de Attach. BK2 y TASproj conservan NVRAM inicial y snapshot sin
consultar SaveRAM durable al reproducir. La prueba usa Client.Common/net48 real
y guest Waterbox, graba/abre ambos formatos desde PowerOn, SaveRAM y estado binario,
y compara A/V, ticks, NVRAM y snapshot completo tras replay y seek en otro proceso.
En Darwin el arnes selecciona Zwinder/TempFile real y un alias temporal al Zstd
ya instalado; no acredita el gestor Paged predeterminado ni la UI WinForms.
Esos limites del host y la conexion real de peliculas ADF/HDF se auditan en T80.

## Buffers de estados del host Darwin (T80a)

El parche derivado `0006-darwin-state-buffers-and-zstd.patch` conserva los bloques
logicos Paged de 4096 bytes (4092 de datos). MemoryBlock conserva su tamaño logico y
redondea solo la reserva y proteccion a la pagina host; Darwin usa MAP_ANON=0x1000.
El guard global de 4KiB de MemoryBlockUtils y el formato del sandbox permanecen intactos.
LibZstd pide `libzstd.dylib` en Darwin; requiere un codec instalado y accesible al
loader o en /opt/homebrew/lib o /usr/local/lib, sin instalarlo ni crear alias Linux. `tests/bizhawk-movies/test_host_states.py`
prueba Client.Common/net48 real: limites de pagina, captura fuera de orden,
invalidacion, vuelta del buffer circular y compresion Zwinder Memory/TempFile.
El historial se lee en un proceso nuevo contra bytes independientes. Es una prueba del host; T80 conserva
la aceptacion Waterbox, identidad de peliculas ADF/HDF y UI real.


## Inicio de peliculas con ADF, HDF y volumenes (T80b)

El transporte de MainForm/MovieSession valida las cabeceras y el SaveRAM antes de
Attach. PowerOn proyecta las bases originales; SaveRAM usa el blob seleccionado,
validado contra esas bases y sin padding final. La importacion real reconstruye
las bases selladas, tambien cuando TAS importa dos veces; no se reemplaza por un
hash adoptado. Grabar desde SaveRAM sin cambios (null) se normaliza a PowerOn.
Los estados conservan su validacion estricta. T80c transporta la base importada
canonica en MovieSaveRam, separada de Corestate, tambien en BK2/TAS que empiezan
desde estado; StartsFromSaveRam permanece desactivado. Esa base se captura al
importar, sin padding ni escrituras posteriores del guest. Antes de Attach se
comprueban cabeceras, blob, bases originales, envelope/checksum y campos wrapper;
despues se reconstruye la base sellada y se carga el snapshot por la ruta estricta,
sin override de identidad. No se añade un validador previo de la estructura interna
del snapshot Waterbox: un fallo de esa carga conserva la politica faulted existente.
STATE01 prueba solo la canonizacion host. WB09/WB03 requieren el guest compartido actual explicitado por
`P6_MEDIA_MOVIE_GUEST_RECEIPT`, replay/seek y otro proceso; sin el, quedan BLOCKED.


## HRTMon publico en BizHawk (T69c4)

`Cartridge=HrtMon` requiere A1200 y reinicio; por defecto queda Disabled. Freeze
es un boton grabable de nivel que usa commands bit0 de ABI2, incluso sin puertos
paralelos. Su flanco vive en P6IN dentro del guest, sin historial adicional C#.
El config hash incluye modo y SHA256 raw del builtin solo cuando esta activo.
La reserva base se conserva: el gate mide sbrk/mmap con margen doble y sealed,
sin memoria invisible. El blob builtin se registra mediante malloc y HRTMon tiene
1MiB mutable; no se supone que esos bytes esten sellados.
STATE01 compila/prueba configuracion host. El gate publico WB03/WB04/WB09 usa
comandos reales, snapshot held, BK2/TAS y seek en proceso nuevo; si falla el runtime
host conocido sigue BLOCKED y no se acredita por compilacion ni prueba nativa.

## Transporte Action Replay (T69g2)

`p6w_init_machine_cartridge(args, model, raw, bytes)` enlaza AR1/2/3/1200 entre
`p6_init` y `p6_load` mediante el binder publico. Comparte inicializador con
`p6w_init_machine` y conserva `p6w_init` y `P6W_LAYOUT` v1. El export nuevo siempre
valida el cartucho: modelo0 o ROM nula no seleccionan el camino sin cartucho.
Un fallo devuelve el error y desmonta el propietario antes de permitir otro init.

Los bytes del llamante solo deben vivir durante la llamada. El binder los copia
al blob del guest antes de `Seal`; no se duplica la ROM en el heap sellado.
Freeze conserva el nivel de ABI2 y el flanco del bridge. Los snapshots son los
del sandbox, con RAM, mapas y controles del cartucho; no se usa el codec nativo
como sustituto de la restauracion Waterbox. El test dedicado usa programas
propios y compara continuacion y snapshot completo en otro proceso.

La conexion publica de SyncSettings, proveedor de firmware e identidad del
wrapper corresponde a T69g3a; la puerta comercial con BK2/TAS es T69g3b.

## Action Replay en el wrapper publico (T69g3a)

`Cartridge` es una opcion de reinicio: Disabled=0 y HrtMon=2 conservan su valor;
Ar1=3, Ar2=4, Ar3=5 y Ar1200=6 son selecciones del frontend. Se traducen a los
modelos 1/2/3/1200 del binder, nunca a bits de `p6_config.flags`. Los flags
canonicos siguen siendo escritura=1 y HRTMon=2. Cada cartucho habilitado agrega
el boton Freeze por nivel al controlador.

AR1/2/3 requieren A500 con memoria y reloj de serie, 68000 exacto o compatible;
AR1200 requiere A1200/68EC020 de serie. Las restricciones del binder se validan
antes de abrir el guest, sin cambiar la maquina elegida. El proveedor de
EmuHawk pide la entrada Amiga AR1, AR2, AR3 o AR1200 correspondiente; los bytes
comerciales los aporta el usuario y no se distribuyen con el core.

`CartridgeFirmware` se conserva al resolver WHDLoad y volumenes y se copia al
propietario del core. Un reinicio del sandbox por StoreSaveRam conserva el
cartucho. El modelo y SHA256 de sus bytes forman parte de la identidad del
estado; el envelope rechaza otro cartucho antes de cargar el snapshot.

La prueba dirigida del wrapper reutiliza los programas propios de T69g2:
un corte held por modelo, restauracion en proceso nuevo y cinco unidades de
futuro con AV/PCM/ticks/Chip y snapshot completo. Esta puerta no sustituye
la prueba comercial de comandos, BK2/TAS y seek de T69g3b.

T69g3b valida los cuatro modelos con ROMs autorizadas desde ese proveedor:
el comando G de AR1/2/3 o R PC y X de AR1200 ejecuta un bucle original en Chip,
Freeze lo detiene y X lo reanuda. BK2 y TAS conservan el nivel de Freeze y las
teclas reales; replay, seek a un corte held y restauracion en otro proceso
repiten cada unidad de video/audio y el snapshot completo. Un modelo, ROM,
configuracion o estado Disabled distintos se rechazan antes de aplicar el
snapshot o adjuntar la pelicula. HRTMon conserva su identidad builtin y su
propia puerta. No hay compatibilidad prometida entre builds.
