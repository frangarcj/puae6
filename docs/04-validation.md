# 04. Pruebas, CI y rendimiento

## Principio de prueba

Un plan validado no es un core validado. Compilar no es arrancar; arrancar no es
ser determinista; determinismo no demuestra exactitud del hardware. Los runners
guardan automaticamente build ID, perfil, host/ISA, toolchain, escenario y checks
cuando esos datos importan; no se rellena una ficha manual por cada ejecucion.
No hay benchmarks observados aun. Todos los umbrales de este documento son metas
propuestas que se ratifican tras la primera baseline reproducible.

## Runner de escenarios (a implementar en T04)

Contrato de entrada JSON versionado: scenario_id, profile/config normalizada,
firmware IDs, lista de medios, total de unidades de video, secuencia de entradas
por indice, eventos de reset/swap, checkpoints y modo de render/audio. Rechazar
campos desconocidos si pueden cambiar emulacion. Input pseudoaleatorio siempre
incluye algoritmo y semilla, no random del host.

Salida machine-readable: exit_code/status, core_build_id, config/media hashes,
frame/ticks, hash RAM/registros/eventos, hash video canonico y hash PCM previo a
volumen/overlays, muestras acumuladas, tiempo host separado y consumo de memoria.
Al primer desync imprimir primer boundary distinto y hashes por subsistema; guardar
ultimas entradas y checkpoints. Una suma final sola hace imposible depurar.

No comparar memoria cruda del proceso ni snapshots binarios entre direcciones,
compiladores o ISA. Hashes de maquina canonicos excluyen punteros, padding y
presentacion host; incluyen todo estado causal documentado. Un hash parcial sirve
para localizar, NO demuestra por si solo que el estado restante es correcto.

## Fixtures

Crear micro-ROMs/test programs propios con licencia explicita para CPU, CIA,
copper/blitter, DMA de audio, teclado/joystick, cambios de video y discos. Incluir
source + receta de assembler + pin + hash de salida. No guardar binarios de origen
incierto. La suite publica no depende de una Kickstart comercial no distribuible.

Suite privada local: firmware/juegos suministrados legalmente por el usuario y
referidos solo por hashes/IDs; no se suben a Git ni a artifacts publicos. Una ROM
libre probada no equivale a compatibilidad con todos los Kickstart. Cubrir despues
combinaciones reales A500/1.3, ECS y A1200 apropiadas cuando existan assets legales.

Corpus minimo antes de M4: 20 escenarios, incluyendo al menos 4 OCS, 4 ECS, 4 AGA,
PAL/NTSC, teclado/raton/joystick, dos de audio/DMA, dos de reset/swap y dos de
escritura/restauracion. Un mismo escenario puede cubrir varias dimensiones pero
no ocultar una categoria ausente. Juegos/demos son complemento, no el unico test.

## Catalogo de checks

| ID | Que demuestra | Criterio de aceptacion |
| --- | --- | --- |
| PLAN01 | Coherencia del plan | inspector y sus tests pasan; no demuestra emulacion |
| ENV01 | Entorno observado | comandos/versiones/permisos y runners ausentes registrados sin inferir ejecucion |
| BUILD01 | Build real | compilacion/enlace limpios del target declarado, con manifest y toolchain identificados |
| HARNESS01 | Infraestructura de pruebas | schema/ABI nativo/dispatcher probados; mocks etiquetados, tests ausentes fallan |
| ADR01 | Decision revisable | contexto, alternativas, evidencia, costo, riesgos y pasos de validacion concretos |
| SRC01 | Procedencia e integridad | commit real, hashes/licencias, pristine sin cambios |
| GEN01 | Codegen | dos builds limpios generan exactamente las mismas salidas |
| HOST01 | Frontera host | imports/simbolos/inclusions no autorizados hacen fallar build |
| ABI01 | FFI | sizeof/offsetof y canarios iguales al contrato C/C# |
| LIFE01 | Ciclo de vida | load fallido y 100 ciclos load/run/unload limpios; doble deinit seguro |
| STEP01 | Continuacion | N pasos frente a avance equivalente: mismo estado/ticks, sin perder eventos; al existir varios adaptadores, misma secuencia normalizada converge por la primitive comun |
| DET01 | Repeticion | 2 procesos nuevos, mismas entradas, 10000 boundaries: hashes iguales |
| DET02 | Restore | save N -> M -> load -> M igual, con N/M de distintos escenarios |
| DET03 | Instancia nueva | guardar A y restaurar B equivalente conserva continuacion |
| DET04 | ISA/compilador | matriz declarada igual por hardware y A/V canonicos por separado |
| DET05 | Silencio de presentacion | render/audio off temporalmente no cambia futuro |
| DET06 | Entorno host | hora, zona, locale, rutas y caches host distintos no alteran resultado |
| AV01 | Video | stride/limites/cambio de modo/PAL-NTSC/interlace sin overflow |
| AV02 | Audio | conteo/fases sin deriva acumulada; DMA, filtros y restore identicos |
| IN01 | Entrada | press/release, raton relativo, puertos, eventos y lag observado |
| MED01 | Medios RO | IDs, ejection/swap/errores y base inalterada |
| MED02 | COW | escritura/overlay/track state revierten; base y durable intactos |
| MED03 | HDF/WHDLoad | bloque/manifest reproductible, sin dependencia de FS host |
| STATE01 | Bound | serialize_size no crece; limites en todas las configs publicadas |
| STATE02 | Estado malo | truncado/tampered/otro build-ROM devuelve error sin corrupcion |
| LR01 | Contrato libretro | load/unload, input poll, un video por run, audio callback correcto |
| LR02 | Rewind/runahead | ramas especulativas no filtran writes/input; replay converge |
| REF01 | Referencia upstream | pruebas diferenciales sin diferencias no explicadas |
| SAFE01 | Memoria | sanitizers nativos sin errores nuevos, canarios y limites correctos |
| FUZZ01 | Entradas malformadas | config/playlist/estado/disco no causan OOB ni asignacion sin cota |
| PERF01 | Presupuesto | medidas repetibles por perfil/host, umbrales aprobados |
| UPD01 | Actualizacion | aplicar/regenerar/probar o conflicto explicito, nunca arreglo silencioso |
| PKG01 | Distribucion | artefacto, fuentes/parches/licencias y manifiesto reproducibles |

WB01-WB12 estan definidos exactamente en docs/03-waterbox.md. Son checks de
runtime Waterbox, no aliases de tests nativos. Todo check futuro empieza como
NOT_IMPLEMENTED y falla si lo selecciona una puerta obligatoria.
WB13-WB15, definidos en docs/07-machawk.md, añaden preservacion x18/x28/TLS,
granularidad de memoria 4/16 KiB y carga/restauracion del guest MacHawk ARM64.

Los IDs agrupan suites que crecen por etapas. Cada ejecucion identifica tarea,
perfil y assertion_set exacto. Por ejemplo T11 valida streaming de AV02, T15 añade
restore de audio y T32 exige ambas partes; T29 valida HDF de MED03 y T30 añade
WHDLoad. T02 prueba el scanner HOST01; T06 añade imports del binario real. El
scope se fija antes de implementar y debe cubrir TODA la aceptacion de su ficha.
T08 prueba AV01 con programas cuya imagen se predice desde el propio programa (nunca desde una captura del motor)
y guarda el hash SHA-256 de los pixeles activos solo despues de esa comprobacion; el arranque con un Kickstart
autorizado es un escenario aparte que queda BLOCKED sin imagen y no se extrapola del micro-ROM.
T07 prueba STEP01 en bridge/headless; T16 lo amplia al adaptador libretro y T19
al guest Waterbox. Ningun adaptador puede aprobarlo con un scheduler alternativo.
No se puede presentar PASS de un subconjunto como PASS del conjunto completo.
T32/T39/T41 ejecutan la union acumulada sin omisiones para perfiles anunciados.
Los manifests del dispatcher registran esta cobertura y fallan si falta una
assertion requerida por la puerta, aunque otros tests del mismo ID hayan pasado.

## Metodos para errores de estado

Escoger N alrededor de DMA, raster, motor de disco, CIA, pulsacion sostenida y
cambio de medio. Probar N+M sin restore y tras 100 restores alternados. Restablecer
en instancia nueva y con buffers de host distintos; poison de caches excluidas.
Guardar/cargar antes y despues de un reset y ambos campos entrelazados.

Fuzzer de estados nunca necesita ROM comercial: usa el micro-ROM y estado semilla.
Validar cabeceras, longitudes, conteos, integer overflow y rechazo transaccional.
No usar checksums como sustituto de bounds checking.

## Referencia de exactitud

Construir la referencia WinUAE del MISMO commit, con configuracion y medios
iguales y, si hace falta, un parche de instrumentacion separado/no semantico.
Comparar CPU, memory writes, registros de chipset, IRQ/DMA y ticks en microtests;
normalizar la presentacion antes de comparar imagen/PCM. Registrar toda diferencia.

Igualar WinUAE prueba fidelidad al upstream elegido, no exactitud universal de una
Amiga fisica. Donde haya tests de hardware o bugs conocidos, documentar su
procedencia. No usar PUAE viejo como unico oraculo de timing para WinUAE 6.

## Matriz de CI propuesta

Tier 1 de producto: Linux x86_64 (Clang y GCC), Windows x86_64 (toolchain fijada),
macOS ARM64 (Apple Clang) y Linux ARM64 (runner real o claramente identificado).
Native/headless y contrato libretro en cada plataforma publicada. El primer gate
puede usar Linux x86_64 + macOS ARM64; no anunciar el resto hasta probarlo.

Tier 1 Waterbox inicial: guest AArch64 en MacHawk Darwin ARM64 con sysroot/runtime
locales fijados, pruebas WB13-WB15 y contrato de paginas 4/16 KiB. Añadir build y
ejecucion Linux x86_64 y Windows x86_64 como filas independientes antes de
anunciarlas. La toolchain guest puede ejecutarse en contenedor; el runtime y su
rendimiento se validan en el host real, no se deducen del cross-build.

Tier 2: builds/test experimentales 32-bit/ARMv7/big-endian y otros frontends. No
presuponer soporte por usar uint32_t; hay que probar sizeof, alineacion, endian,
size_t y conversiones LP64/LLP64. No bloquear M1 intentando cubrir cada consola.

PR rapida: integridad de plan/pins/parches, compile/link del perfil minimo, tests
unitarios, micro-ROMs, DET02 y LR01; smoke Waterbox desde el primer WBX funcional.
PR de motor/estados: añadir DET01/03, WB02-08 y diff de paginas/tamaño de estado.
Nightly/release: corpus completo 10000+ frames, cross-build, fuzz, memory/leaks,
movie seek, perf y ensayo de actualizacion. El cache no sustituye build limpio.

ASan/UBSan/LSan se usan en nativo donde la toolchain lo permita. No forzarlos dentro
de Waterbox sin soporte validado; alli se usan canarios, debug runtime y checks de
memoria del host. Warnings propios pueden ser error; warnings heredados se
inventarian sin esconderlos todos con -w. No -Ofast/-ffast-math en deterministic.
No -march=native en binarios distribuidos; baselines de ISA y dispatch explicitos.

## Protocolo de rendimiento

Escenarios fijos: A500 OCS con copper/blitter; ECS; A1200 AGA/020 con carga CPU;
Paula+DMA; actividad de disco; secuencia de save/load/seek. Perfil y ROM/media hash
identicos. No comparar interpreter accurate con JIT/inexacto y llamar a la diferencia
una optimizacion del mismo producto.

Por runner dedicado, documentar CPU/OS/toolchain/flags/energia y carga. Warmup de
300 unidades propuesto; medir 10000; repetir al menos cinco veces. Conservar datos
brutos y reportar mediana, p95 y dispersión de coste por frame. Medir primero el
motor headless sin vsync, luego libretro y Waterbox como capas separadas.

Metricas: tiempo de emulacion, coste de adaptador/conversion/copia, frames/s sin
limitar, RSS/peak, asignaciones por frame, bytes copiados, dirty pages donde el
runtime permita medirlas, tamaño de snapshot, p50/p95 save/load y seek/sec.
Si el runtime no expone paginas sucias, registrar bytes del estado e instrumentacion
alternativa; no inventar el contador. Cache warmth se documenta en ambos casos.

Objetivos iniciales a ratificar con baseline:
- presupuesto p95 de emulacion <= 70% de la duracion de la unidad de video en la
  maquina de referencia elegida para cada perfil publicado;
- regresion sostenida > 5% de tiempo o > 10% de tamaño/coste de estado exige
  investigacion y aprobacion explicita; repetir para descartar ruido;
- ruta estable sin alloc/free por frame en bridge/adaptadores, sin imponer una
  reescritura del allocator upstream sin medirlo;
- ninguna optimizacion puede cambiar hashes del perfil deterministic.

No son garantias para todo hardware ni resultados ya medidos. La baseline aprobada
fija presupuesto por perfil/host; no se rebaja en el mismo cambio que lo incumple.
Performance se mide en release fijada, sanitizers en un job distinto.

## Orden de optimizacion

1. Medir y separar motor, interfaz, snapshots e I/O.
2. Eliminar copies/formateo/logging/allocs evitables fuera de bucles CPU.
3. Reducir dirty pages y memoria redundante con pruebas de reconstruccion.
4. SIMD opcional de pixel/audio con referencia escalar y output bit-exacto.
5. Ajustes compiler/LTO/PGO sobre escenarios representativos, reproducibles.
6. JIT nativo solo tras ADR, perfil diferente, paridad/invalidacion/cache y mantenimiento.

No optimizar primero añadiendo callbacks por acceso, threads, JIT o saltos de
emulacion. La copia de salida puede ser necesaria para lifetime de buffers; no
llamar zero-copy a retener un puntero host invalido tras devolver FrameAdvance.
