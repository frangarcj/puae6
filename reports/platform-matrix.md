# Matriz de ISA, compiladores y sistemas (T25)

Fecha: 2026-10-10. Tarea T25, checks DET04, DET06, BUILD01, SAFE01, WB02, WB13, WB14, WB15.
Este informe solo marca **soportada** una fila cuando una ejecucion registrada (comando, build, hashes) coincide con la referencia; una fila
construida y no ejecutada, o ejecutada con diferencias, no es soportada. La parte generada (entre marcadores) sale de
`tests/portability/evidence.json` y `tests/portability/waterbox-evidence.json` con `tests/portability/render.py`; `tests/portability/test_portability.py`
falla si el informe, `plan/runners.json`, `plan/toolchains.json` y la evidencia dicen cosas distintas.

## Alcance declarado (cambio de alcance revisado)

Decision del usuario, 2026-10-10: aqui solo hay hardware Mac. La matriz declarada es **macOS ARM64 nativo + Linux ARM64 y Linux x86_64 en Docker
(este ultimo con `--platform linux/amd64`), cada uno con Clang y GCC, mas el guest ARM64 en Waterbox/MacHawk Darwin ARM64**. Windows x86_64 y los
runtimes Waterbox Linux/Windows x86_64 quedan **fuera del alcance declarado por decision del usuario** (no hay hardware ni runner Windows), no como
"pendientes de runner": no tienen evidencia ni soporte anunciado, y reabrirlos exige una nueva decision de alcance (`plan/runners.json`, estado
`out_of_scope`, con fecha y motivo).

Leer las filas x86_64 con cuidado: son **x86_64 traducido** por Docker Desktop sobre un Mac arm64 (Rosetta/QEMU), no hardware x86_64 nativo. Prueban
que el codigo compila con los compiladores x86_64 de Ubuntu 24.04 y que el binario x86_64 produce los mismos hashes; no prueban el comportamiento de
una CPU x86_64 real. Las filas Linux ARM64 son contenedores sobre la VM arm64 de Docker Desktop (kernel linuxkit), no una maquina Linux aparte.

<!-- matrix:begin -->
Generado por `tests/portability/render.py` desde `tests/portability/evidence.json` (fecha 2026-10-10); no editar a mano.

Corpus: 89 casos, SHA-256 del corpus (casos + ROMs) `8de6c7f33b50a88939d099f8b1021ffad502ea4911d64c1b14f149f21c5757b9`.
Referencia: fila `macos-arm64-appleclang` en O0. Hash de conjunto del hardware canonico `bbcfe41fe5fe2645b607aa9783fd915ea85d0f270ad17d1f18f6b1ae52d828f5`; hash de conjunto de A/V canonico `2e93b523d973cb2fce0b7018b8ff3c95c9baffff1cfb3b98df21010c73006b7f` (calculados sobre los hashes por caso de `evidence.json`).

### Filas ejecutadas

| Fila | Plataforma | Ejecucion | Build (O0 / O2) | Hardware = ref (O0 / O2) | A/V = ref (O0 / O2) | `p6_headless` = ref (O0 / O2) | Kickstart 1.3 hw+A/V = ref (O0 / O2) | DET06 (O0 / O2) | Estados nativos entre filas (O0) | Estado |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `macos-arm64-appleclang` | macOS 26.7, arm64, Apple Clang | native on the build machine (Apple silicon, 16 KiB pages) | ok / ok | 89/89 / 89/89 | 89/89 / 89/89 | 4/4 / 4/4 | si / si | 89+89/89 / 89+89/89 | carga 16/16 de los de otras filas; los suyos los cargan 16/16; no prometido | **SOPORTADA** |
| `linux-arm64-clang` | Ubuntu 24.04 (container), arm64, Clang | container on the Docker Desktop arm64 VM of a macOS arm64 host (arm64 code on arm64 CPU) | ok / ok | 89/89 / 89/89 | 89/89 / 89/89 | 4/4 / 4/4 | si / si | 89+89/89 / 89+89/89 | carga 16/16 de los de otras filas; los suyos los cargan 16/16; no prometido | **SOPORTADA** |
| `linux-arm64-gcc` | Ubuntu 24.04 (container), arm64, GCC | container on the Docker Desktop arm64 VM of a macOS arm64 host (arm64 code on arm64 CPU) | ok / ok | 89/89 / 89/89 | 89/89 / 89/89 | 4/4 / 4/4 | si / si | 89+89/89 / 89+89/89 | carga 16/16 de los de otras filas; los suyos los cargan 16/16; no prometido | **SOPORTADA** |
| `linux-x86_64-clang` | Ubuntu 24.04 (container), x86_64, Clang (x86_64 traducido) | x86_64 translated in Docker Desktop on macOS arm64 (not x86_64 hardware) | ok / ok | 89/89 / 89/89 | 89/89 / 89/89 | 4/4 / 4/4 | si / si | 89+89/89 / 89+89/89 | carga 16/16 de los de otras filas; los suyos los cargan 16/16; no prometido | **SOPORTADA** |
| `linux-x86_64-gcc` | Ubuntu 24.04 (container), x86_64, GCC (x86_64 traducido) | x86_64 translated in Docker Desktop on macOS arm64 (not x86_64 hardware) | ok / ok | 89/89 / 89/89 | 89/89 / 89/89 | 4/4 / 4/4 | si / si | 89+89/89 / 89+89/89 | carga 16/16 de los de otras filas; los suyos los cargan 16/16; no prometido | **SOPORTADA** |

Las dos fracciones de DET06 son hardware+A/V iguales (de N casos) entre la variante `host` y la `baseline` de la misma fila.

### Ejecuciones de auditoria

Mismas construcciones con otras banderas o sanitizers; se comparan con la referencia como las anteriores. Un caso con error es un hallazgo del sanitizer o del proceso, no un hash distinto.

| Fila / variante | Banderas | Build | Hardware = ref | A/V = ref | Casos con error | Avisos (proyecto / motor) |
| --- | --- | --- | --- | --- | --- | --- |
| `linux-arm64-clang/san` | -O0 + ASan/UBSan | ok | 89/89 | 89/89 | 0 | 0 / 19 |
| `linux-arm64-clang/warn` | -O0 + audit warnings | ok | 89/89 | 89/89 | 0 | 1 / 22827 |
| `linux-arm64-gcc/O2nsa` | -O2 -g -fno-strict-aliasing | ok | 89/89 | 89/89 | 0 | 2 / 6 |
| `linux-arm64-gcc/O2wrap` | -O2 -g -fwrapv | ok | 89/89 | 89/89 | 0 | 2 / 6 |
| `linux-arm64-gcc/san` | -O0 + ASan/UBSan | ok | 89/89 | 89/89 | 0 | 0 / 6 |
| `linux-arm64-gcc/warn` | -O0 + audit warnings | ok | 89/89 | 89/89 | 0 | 3 / 22561 |
| `linux-x86_64-clang/san` | -O0 + ASan/UBSan | ok | 89/89 | 89/89 | 0 | 0 / 19 |
| `linux-x86_64-gcc/O2nsa` | -O2 -g -fno-strict-aliasing | ok | 89/89 | 89/89 | 0 | 2 / 6 |
| `linux-x86_64-gcc/O2wrap` | -O2 -g -fwrapv | ok | 89/89 | 89/89 | 0 | 2 / 6 |
| `linux-x86_64-gcc/san` | -O0 + ASan/UBSan | ok | 89/89 | 89/89 | 0 | 0 / 6 |
| `macos-arm64-appleclang/O2nsa` | -O2 -g -fno-strict-aliasing | ok | 89/89 | 89/89 | 0 | 0 / 19 |
| `macos-arm64-appleclang/O2wrap` | -O2 -g -fwrapv | ok | 89/89 | 89/89 | 0 | 0 / 19 |
| `macos-arm64-appleclang/san` | -O0 + ASan/UBSan | ok | 89/89 | 89/89 | 0 | 0 / 19 |

### Fila Waterbox (guest ARM64 en MacHawk Darwin)

| Fila | Check | Comando | Resultado (2026-10-10) |
| --- | --- | --- | --- |
| `waterbox-darwin-arm64` | WB02 | `python3 -m unittest discover -s tests/waterbox-smoke -p test_puae6_units.py` | 5 tests OK, 0 skips, 525 s: 10000 units twice in two processes equal p6_headless and the libretro core (ticks, image hash, PCM hash, parity, cadence, audio frames) with the same hardware snapshot digest each time; guard bands; render/audio off; limits |
| `waterbox-darwin-arm64` | WB13 | `python3 -m unittest discover -s tests/waterbox-smoke -p test_puae6_wb.py` | OK (10 tests in the file, 138 s, 0 skips): host callback crosses puae6.wbx with canaries in x19-x29/d8-d15/sp; x18, x28, musl TSD and errno intact; also in a restored sandbox |
| `waterbox-darwin-arm64` | WB14 | `python3 -m unittest discover -s tests/waterbox-smoke -p test_puae6_wb.py` | OK (same run): 16 KiB groups of a clean page and of Chip RAM dirty and restore as a whole on A500, A600 (1 MiB) and A1200 (2 MiB); a poke changes the next picture |
| `waterbox-darwin-arm64` | WB15 | `python3 -m unittest discover -s tests/waterbox-smoke -p test_puae6_wb.py` | OK (same run): a new process restores a snapshot of another (interlaced, buttons held, A600, A1200) and continues exactly; a damaged guest or one that is not the receipt's is refused |

### Filas fuera del alcance declarado

Cambio de alcance: 2026-10-10, user decision: the declared matrix is the Mac (native) and Docker Linux ARM64/x86_64 with Clang and GCC; there is no Windows hardware or runner. Ninguna tiene evidencia ni se declara soportada.

| Fila | Plataforma | Motivo |
| --- | --- | --- |
| `windows-x86_64` | Windows x86_64, native build + run | no Windows hardware or runner; out of the declared scope by user decision |
| `waterbox-linux-x86_64` | Linux x86_64, Waterbox runtime + x86_64 guest | outside the declared scope by user decision (no x86_64 Waterbox runtime or guest toolchain is announced; the ARM64 guest of the Darwin row does not run on it) |
| `waterbox-windows-x86_64` | Windows x86_64, Waterbox runtime + x86_64 guest | no Windows hardware or runner; out of the declared scope by user decision |
| `macos-x86_64` | macOS x86_64, native build + run | never part of the declared matrix (no Intel machine; Rosetta was not used as a runner) |

### Estados entre filas (no prometido)

Medido con 5 builds (O0) y 4 estados guardados con `p6_state_save` publico: estados con bytes identicos entre todas las filas: 4 de 4. Cargas cruzadas entre filas distintas: 80; aceptadas 80 (con digests iguales a los del que guardo: 80); rechazadas 0. Esto describe lo observado con estados nativos del ABI publico (bytes canonicos little-endian), no es una promesa: los estados entre builds, ISA o compiladores quedan **no prometido**, y los snapshots de memoria de Waterbox no se trasladan a ninguna otra fila ni se han probado en ella.
<!-- matrix:end -->

## Que compara cada hash

Todas las comparaciones usan `p6_state_probe` sobre un corpus de micro-ROMs propias del repositorio (`tests/portability/corpus.py`): CPU/CIA/Copper
del A500 en PAL y NTSC, memoria (Slow, Chip 1 MiB, Fast 8 MiB), A600/ECS y A1200/AGA, entrada (raton, joystick, teclas), programas de video del
A500/ECS/AGA, audio (4 canales, 44100/48000 Hz). Para cada caso, y unidad a unidad:

- **Hardware canonico**: los digest `cpu`, `scheduler`, `ram`, `rtc`, `cia`, `chipset` (con sus grupos `chip`), `audio`, `input` y `bridge` de
  `bridge/state_probe.cpp`, escritos campo a campo en little-endian de ancho fijo, sin punteros ni padding.
- **A/V canonico**: el digest `video` (filas del buffer), el hash de las muestras PCM de la unidad (`pcm`) y el de la imagen entregada (`picture`).

Cada caso tiene un hash de hardware y otro de A/V **por separado**; ninguno mezcla ambos. Misma configuracion, frecuencia (44100/48000 Hz), estandar y
filtro en todas las filas. La fila de referencia es `macos-arm64-appleclang` en O0. Que dos filas den el mismo hash no demuestra que el hardware sea
correcto (eso es de las pruebas de cada subsistema): demuestra que ISA, compilador y sistema no lo cambian en el corpus. Un hash parcial localiza, no
demuestra el resto del estado (docs/04-validation.md).

Tambien se compara `p6_headless` (las lineas `unit N ticks=... hash=... audio=...`) en cuatro programas: son las lineas con las que WB02 compara el
guest de Waterbox, de modo que la igualdad entre filas ata el guest de la fila Darwin a las demas.

Un Kickstart 1.3 autorizado (`P6_KICKSTART_ROM`, leido donde esta, montado de solo lectura en los contenedores, nunca copiado ni hasheado y con sus
hashes sin registrar) arranca 150 unidades en el A500 como caso extra de cada ejecucion: ver `kickstart` en la evidencia (booleanos, no hashes).

## DET06: que se varia

La variante `host` ejecuta el mismo corpus con: `LANG`/`LC_ALL=tr_TR.UTF-8`, `LC_NUMERIC=de_DE.UTF-8`, `LC_CTYPE=ja_JP.UTF-8` (puede que el locale no
exista en la maquina; el resultado tampoco puede depender de eso), `TZ=Pacific/Kiritimati`, un **reloj desplazado +800 000 000 s** (2051, pasado el
`time_t` de 32 bits de 2038) mediante una biblioteca precargada (`tests/portability/faketime.c`; `evidence.json` registra `clock_shifted`, que el
programa de comprobacion vio el desplazamiento), otro directorio de trabajo y la ruta de las ROM con espacios, acentos, ideogramas y un simbolo
(`con espacios ñandú 日本語 ✓/roms`). No se varian las caches del anfitrion (no hay ninguna que el motor consulte) ni el reloj monotono.

## Hallazgos

1. **GCC -O0 no enlazaba** (build, no determinismo; corregido en T25a). Son funciones `static` que nadie llama (`expamem_map_uaeboard`, `add_rtarea_pointer`, los
   `*_ce030_cicheck`, `read_processor_time`) y que referencian simbolos de modulos que el perfil excluye; GCC las conserva a -O0 y Clang no (`nm` de
   `expansion.cpp.o`: GCC contiene `expamem_map_uaeboard`, Clang no). El motor no se toca. **Decision (T25a)**: `CMakeLists.txt` da el mismo tratamiento a todos los
   compiladores y enlazadores, eligiendo por el enlazador que acepta la bandera (`check_linker_flag`) y no por el sistema operativo: `-ffunction-sections
   -fdata-sections` con `--gc-sections` en GNU ld/gold/lld (ELF) y `-dead_strip` en ld64 (sin bandera de compilacion: GCC ni la implementa para Mach-O); el
   toolchain GCC ya no lleva banderas y cualquier compilador de cualquier sistema enlaza con el CMake por defecto. El codigo de las funciones vivas es el que
   genera el compilador en cualquier caso, y los hashes no cambian (ver abajo). Se descarto dar definiciones de error a esos simbolos: seria mantener una lista de
   simbolos de modulos fuera del perfil y cada una seria un stub; con la seccion por funcion el enlazador solo descarta lo inalcanzable y un simbolo que si se
   llama sigue siendo un error de enlace. **Reproducer**: `-DP6_GC_SECTIONS=OFF` con GCC y `-DCMAKE_BUILD_TYPE=Debug`, enlazar `p6_headless` o `p6_state_probe`;
   medido el 2026-10-11 con GCC 13.3 en Linux ARM64 y x86_64 (GNU ld): `undefined reference to` `uae_time()`, `jit_direct_compatible_memory` y `mmu030_cache_state`
   (18 y 16 referencias; la lista de T25 incluia ademas `rtarea_*`, `EXPANSION_bootcode` y `filesys_get_entry(int)`, que ya no aparecen con el motor actual) y con
   Homebrew GCC 15 en macOS (ld64, sin `-dead_strip`). **GCC -O2 sin las banderas si enlaza** (GCC 13.3 ARM64 y x86_64, `-O2 -g`, `-DP6_GC_SECTIONS=OFF`; y GCC 15 en
   macOS): a -O2 GCC descarta las `static` sin referencias; -O0 las conserva. Con el CMake por defecto (banderas puestas) se construyeron y
   enlazaron **todos** los ejecutables y la biblioteca libretro en el arbol -O0 de Linux ARM64 con GCC 13.3 y con Clang 18, y en macOS con Apple Clang
   (`ninja` sin objetivo); con Homebrew GCC 15 en macOS enlazan p6_headless, p6_state_probe y el nucleo libretro, y `p6_hdf_probe` y `p6_cycles_probe` no compilan
   (`#include <malloc/malloc.h>` del SDK de macOS 26 usa macros que GCC no entiende: es un fallo de compilacion de GCC con el SDK, sin relacion con el enlace). Una
   configuracion de variante que sustituia `CMAKE_CXX_FLAGS` (pierde los `_INIT` del toolchain) reprodujo el mismo fallo por accidente en T25; ya no hay `_INIT`
   que perder.
2. **Sin diferencias de hash** entre arm64 y x86_64, Apple Clang, Clang 18 y GCC 13, -O0 y -O2, en las 89 unidades de corpus ni en el Kickstart autorizado.
   La razon plausible, no demostrada aqui, es `-ffp-contract=off -fno-fast-math` del CMake raiz (CMakeLists.txt: sin FMA contraido el filtro RC de Paula
   y el intervalo de muestra dan los mismos bits), mas aritmetica entera de ancho fijo en el motor. T25a (2026-10-11) repitio las cinco filas con las banderas de seccion en `CMakeLists.txt`
   (-O0, -O2 y ASan/UBSan, 15 ejecuciones): **89/89** hashes de hardware y A/V iguales a los registrados en cada una, 4/4 lineas de `p6_headless` y DET06 89/89 en las
   variantes -O0 y -O2 (la variante sanitizada solo la linea base); resultado en `evidence.json`, clave `reruns.T25a`. Un cambio de enlazado no altera el codigo
   de las funciones vivas ni la maquina emulada.
3. **El guest de Waterbox es identico bit a bit con otra ruta de checkout**: reconstruido en este worktree, su SHA-256 (`4fde4dbe...d17b`) es el
   `guest_sha256` que `manifests/waterbox-memory.json` registro desde otro directorio, sin la ruta del checkout dentro del ELF.
4. **Estados**: los estados nativos del ABI publico guardados por las cinco construcciones tienen los mismos bytes y se cargan entre todas las filas
   (ver la tabla y la seccion de estados). Es una observacion con 4 estados en O0, no una promesa de compatibilidad.

## Estados entre filas: no prometido

Compatibilidad de estados = **no prometida** entre builds, ISA ni compiladores (AGENTS.md; docs/04-validation.md). T25 la mide sin deducirla del ABI:
`python3 tests/portability/run_matrix.py states save` y `states load` guardan 4 estados (A500 PAL, A500 NTSC Copper/IRQ, A1200 AGA, audio de 4
canales) con `p6_state_*` en cada una de las cinco construcciones O0 y cada construccion intenta cargar los de las demas; la carga acepta o rechaza y,
si acepta, se comparan los digest tras la carga con los del que guardo. Resultado en la tabla. Alcance: solo estados nativos canonicos del mismo
commit/parches/codegen en O0 y solo el corpus indicado. **No** se traslada ningun snapshot de memoria de Waterbox (el de la fila Darwin) a otra fila, ni
se ha probado: esos snapshots son memoria del guest ARM64 y no se prometen entre ISA.

## Auditoria de aliasing, shifts, endianness, desalineado y LLP64

Ninguna bandera de CPU ni de aliasing se ha cambiado; cada comprobacion es una medicion con reproducer.

- **Aliasing y desbordamiento con signo**: construcciones -O2 con `-fno-strict-aliasing` (O2nsa) y con `-fwrapv` (O2wrap) en Apple Clang (macOS),
  GCC en Linux ARM64 y GCC en Linux x86_64, frente al -O2 por defecto (Clang/GCC aplican strict-aliasing a -O2): ver la tabla de auditoria; cualquier
  hash distinto seria un reproducer de UB, y no lo hay (89/89 en hardware y A/V en las seis). No prueba ausencia de UB, solo que
  el corpus no depende de esas dos suposiciones del compilador.
- **UB en ejecucion**: ASan+UBSan (`san`, `-fno-sanitize-recover=all`: un hallazgo detiene el proceso) sobre las 89 ejecuciones del corpus. Cubre
  desplazamientos, desbordamiento con signo, alineacion de punteros, limites de arrays y accesos fuera de rango en cada fila listada en la tabla de
  auditoria. No cubre rutas que el corpus no ejecuta (escritura de disquete con overlay, `p6_media_*`), que tienen sus propios tests con sanitizers
  (`tests/media-cow`, SAFE01 de T26/T24).
- **Avisos** (construccion Debug con `-Wall -Wextra -Wshift-count-negative -Wshift-count-overflow -Wshift-negative-value -Wcast-align -Wstrict-aliasing -Wundef`
  en Clang 18 y GCC 13, ARM64): el codigo propio (bridge, ports, adapters) produce 1 aviso con Clang (`-Wunused-const-variable`) y 3 con GCC
  (grupo `-Wextra`), y ninguno de desplazamiento, alineacion o aliasing; el motor upstream produce miles (22 827 con Clang, 22 561 con GCC),
  casi todos `-Wunused-*`. Los relevantes para portabilidad: `-Wcast-align` con Clang (1051 conversiones que aumentan la alineacion en codigo
  upstream, inocuas en arm64/x86_64 y a considerar en ISAs de alineacion estricta, Tier 2; GCC no avisa en estas ISAs), `-Wundef` (327) y
  `-Wmissing-field-initializers`. `-Wstrict-aliasing` de GCC solo informa con optimizacion, y esta construccion es -O0: la comparacion de aliasing
  es la de las construcciones O2nsa. Los avisos del motor con las banderas del proyecto estan inventariados en `manifests/engine-warnings.json`.
- **Revision de `bridge/` y `ports/portable`**: sin `htole`/`bswap`/macros de endianness: el estado serializa campo a campo en little-endian de ancho fijo
  (`bridge/state*.cpp`, `ports/portable/state_io`), los floats del estado pasan por `memcpy`, y la memoria del Amiga (big-endian) la accede el motor por
  sus bancos. Los `reinterpret_cast` del proyecto son 5 sitios (2 en `state_upstream.cpp`, 3 en `video_present.cpp`) sobre el buffer de video de `uint32_t`
  con `stride` multiplo de 4, y las vistas de `ports/portable/media/overlay.h` sobre un bloque de almacenamiento: sus desplazamientos
  (`slot_of` 0, `sector` 56320, `medium` 63360, `data` 66880, `raw_words` 1 869 120, `raw_bits` 2 073 664) son multiplos de la alineacion de
  cada tipo si el bloque lo esta (T25a lo comprueba en compilacion con `static_assert` sobre los desplazamientos, en C y en C++).
- **LLP64**: no ejecutable aqui (no hay Windows). Revision: `uae_u32`/`uae_s64` son tipos de ancho fijo (`upstream/winuae/include/uae/types.h`); el
  codigo propio usa `long` solo en `ports/portable/tchar.h` (`p6_tcstol`, con `unsigned long limit`, acotado), y `PERIOD_MAX` (ULONG_MAX) guardado
  en un `int` da -1/-2 en LP64 igual que el `long` de 32 bits de Windows (nota de `manifests/engine-warnings.json`). Las ABIs de `p6.h` usan enteros
  de ancho fijo (`tests/contracts/layout_probe.c`). Sin ejecutar LLP64 no se declara soportado.
- **Big-endian, 32 bits, ARMv7**: Tier 2, fuera de la matriz; no se prueban.

## Reproduccion

```sh
# Imagenes (una por ISA): Ubuntu 24.04 con Clang 18 y GCC 13
docker build --platform linux/arm64 -t puae6-portability:arm64 -f tests/portability/Dockerfile tests/portability
docker build --platform linux/amd64 -t puae6-portability:amd64 -f tests/portability/Dockerfile tests/portability
# Una fila y una optimizacion: construye, ejecuta el corpus (baseline y host) y deja el resultado en build/portability/<fila>/
P6_KICKSTART_ROM=/ruta/al/Kickstart-1.3.rom python3 tests/portability/run_matrix.py exec linux-x86_64-gcc O2 --kickstart
# Comparar todo lo ejecutado con la referencia y escribir tests/portability/evidence.json
python3 tests/portability/run_matrix.py report --write
# Pruebas de coherencia (sin Docker) y, con P6_TEST_PORTABILITY=1, ejecucion de las filas que esta maquina puede ejecutar
python3 -m unittest tests.portability.test_portability
P6_TEST_PORTABILITY=1 python3 -m unittest tests.portability.test_portability
# Fila Waterbox (macOS arm64, MacHawk solo lectura)
python3 tools/build_waterbox.py --puae6
python3 -m unittest discover -s tests/waterbox-smoke -p test_puae6_units.py     # WB02 (10000 unidades, ~9 min)
python3 -m unittest discover -s tests/waterbox-smoke -p test_puae6_wb.py        # WB13, WB14, WB15
```

## Checks de la ficha

| Check | Como se cubre | Resultado |
| --- | --- | --- |
| DET04 | hashes de hardware y A/V por separado, 5 filas x O0/O2, contra la referencia | ver tabla: 89/89 en las dos columnas en las filas soportadas |
| DET06 | variante `host` de cada fila (locale, TZ, reloj +800 Ms, directorio, ruta de ROM) contra su `baseline` | ver tabla |
| BUILD01 | construccion limpia de `p6_state_probe` y `p6_headless` por fila con su toolchain (`cmake/toolchains`), versiones de compilador en la evidencia | ver tabla |
| SAFE01 | ASan+UBSan sobre el corpus por fila (tabla de auditoria) y, en macOS, `python3 tools/check.py --check SAFE01 --task T24` | ver el cierre en `reports/tasks/T25.md` |
| WB02, WB13, WB14, WB15 | guest ARM64 en Waterbox/MacHawk Darwin, repetidos en este worktree | ver la fila Waterbox |

`tools/check.py` registra desde T25a ('DET04','T25') y ('DET06','T25') sobre `tests.portability.test_portability`: las pruebas de coherencia de documentos y evidencia
corren siempre y las que construyen y ejecutan las filas son opt-in (`P6_TEST_PORTABILITY=1`; las filas Linux necesitan Docker). Sin la variable esas pruebas se
omiten y el despachador devuelve `BLOCKED` (nunca `PASS`): `python3 tools/check.py --check DET04 --task T25`. Con ella, el mismo comando construye y ejecuta las filas
que esta maquina puede ejecutar (las demas, p. ej. las de Docker con el daemon parado, son subpruebas omitidas: `BLOCKED` hasta que corran las cinco) y compara cada hash con `evidence.json` (`P6_TEST_PORTABILITY=1 python3 tools/check.py --check DET06 --task T25`, horas con las filas x86_64).

## Limites

- Los hashes comparan 89 casos de micro-ROM mas un Kickstart 1.3; no son una prueba de exactitud del hardware ni de compatibilidad con otros firmwares.
- Ninguna fila usa JIT, red, PPC ni sistema de ficheros del anfitrion (perfil `deterministic`).
- Solo se compara un commit (el de esta rama); los hashes no se garantizan entre versiones.
- Windows x86_64, Waterbox Linux x86_64 y Waterbox Windows x86_64: fuera de alcance (ver arriba).
