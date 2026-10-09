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
WB13-WB16, definidos en docs/07-machawk.md, añaden preservacion x18/x28 y del TLS de
musl, granularidad de memoria 4/16 KiB, carga/restauracion del guest MacHawk ARM64
y ausencia de TLS del compilador en el ELF final.

Los IDs agrupan suites que crecen por etapas. Cada ejecucion identifica tarea,
perfil y assertion_set exacto. Por ejemplo T11 valida streaming de AV02, T15 añade
restore de audio y T32 exige ambas partes; T29 valida HDF de MED03 y T30 añade
WHDLoad. T02 prueba el scanner HOST01; T06 añade imports del binario real. El
scope se fija antes de implementar y debe cubrir TODA la aceptacion de su ficha.
T08 prueba AV01 con programas cuya imagen se predice desde el propio programa (nunca desde una captura del motor)
y guarda el hash SHA-256 de los pixeles activos solo despues de esa comprobacion; el arranque con un Kickstart
autorizado es un escenario aparte que queda BLOCKED sin imagen y no se extrapola del micro-ROM.
T23 repite AV01 en el A600/ECS (preset dentro del perfil deterministic-a500) con los mismos metodos: los programas y la imagen que cada uno
debe producir estan en `tests/scenarios/ecs/ecs.py` (identificacion VPOSR/DENISEID, 1 MiB de Chip, SuperHires, DIWHIGH, BEAMCON0, VARBEAMEN y
productividad, PAL y NTSC; los hashes de repetibilidad en `ecs-baselines.json`), los ejecuta `tests/step/test_ecs.py`
(`python3 -m unittest tests.step.test_step tests.step.test_display tests.step.test_ecs`) y `tests/step/test_kickstart_a600.py` es el arranque
opt-in con el Kickstart 2.05 autorizado (`P6_KICKSTART_A600_ZIP` + `P6_WB205_ZIP`, leidos en memoria, sin copia ni hash). Cada programa y estandar
tiene su escenario `tests/scenarios/ecs/ecs-<programa>-<pal|ntsc>.json` (38 ficheros, `machine` A600, 1 MiB Chip, sin Slow; el firmware es el SHA-256
del micro-ROM que ensambla ecs.py, no un Kickstart) que `tests/scenarios/test_schema.py` valida y compara con lo que el generador produce. AV02 repite
la suite de audio en el A600 (`AudioA600Tests`); DET02/STATE01 corren la tabla A500 mas 14 escenarios A600 con punteros DMA sobre 512 KiB y el
Kickstart 2.05 opt-in; WB03 y LR01 se ejecutan con ambas maquinas. Medido, no afirmado de un hardware: en SuperHires el motor pinta el borde en negro
en lugar de COLOR00 (el modelo no describe lo que cae fuera de la ventana) y en NTSC la imagen SHRES de las lineas impares queda 2 columnas a la izquierda.
T24 repite AV01/AV02/DET02/WB03/SAFE01 en el A1200 (68EC020 ciclo exacto + AGA, tercer preset del mismo perfil; tabla en docs/02-contracts.md) con los mismos metodos. Donde estan los checks:
CPU 020, `tests/host/test_cpu_020.py` (micro-ROM de instrucciones 68020 con valores calculados en Python desde el M68020UM, en el preset real del A1200; la misma imagen falla en un 68000) y
`tests/host/test_a1200_profile.py` (el perfil `config/profiles/a1200-aga.json` campo a campo contra `built_in_prefs` upstream); GEN01 comprueba que `cpuemu_21.cpp` generado es identico al del upstream y que el 68000 no cambia.
Maquina y video: `tests/step/test_a1200.py` (Alice/Lisa, 2 MiB de Chip sin alias, instrucciones 020, VBR) y `tests/step/test_aga.py` con `tests/scenarios/aga/aga.py` (15 programas AGA: 8 planos y 256 registros de
24 bits, BPLCON4, HAM8, FMODE 0/1/3 con DDFSTRT/DDFSTOP coherentes, barras de Copper de 24 bits, sprites de 16/32/64 bits; la imagen que cada uno debe producir sale del propio programa; 30 escenarios JSON `aga-<programa>-<pal|ntsc>.json`
que `tests/scenarios/test_schema.py` valida y compara con el generador; los hashes de repetibilidad en `aga-baselines.json`; cero fuera de `active` en cada unidad; `MeasuredTests` fija las dos medidas que no son una afirmacion de hardware).
Firmware autorizado, opt-in: `tests/step/test_kickstart_a1200.py` (`P6_KICKSTART_A1200_ROM`, y `P6_AGA_ADF` para la demo; el DMS `P6_AGA_DMS` lo abre el nucleo libretro). AV02: `AudioA1200Tests` en `tests/audio/test_audio.py`.
DET02/STATE01: `tests/state/test_det02_a1200.py` (185 pruebas: las del A500 que tienen sentido en un 020, cache/pila maestra/T0/marcos de excepcion/instrucciones/espera de DMA de audio, AGA 8 planos/FMODE/FMODE alternando/sprites de 64 bits/paleta de Copper/HAM8/hires, disco, y con
firmware el Kickstart 3.1 y la demo a mitad de la carga) y `tests/state/test_state_a1200.py` (layout, cota, estados de otras maquinas INCOMPATIBLE, rewind); la cota por maquina se recalcula (A500 3 608 513, A600 3 608 482, A1200 5 890 716 B) y no se reutiliza la del A500.
WB03: `tests/waterbox-state/test_wrapper_machine.py` y `test_wrapper_state.py` en las tres maquinas, mas `tests/waterbox-smoke` (`--machine a1200`) y `tests/waterbox-smoke/test_puae6_memory.py` (presupuesto medido por maquina). LR01: `tests/libretro-host` (sesion A1200 == `p6_headless --machine a1200` unidad por unidad,
cadencia BEAMCON0, estados de otras maquinas rechazados, RetroArch real con `puae6_machine=A1200`). FUZZ01 (T17) lleva el A1200 y sus casi-aciertos en `normalize_config` y en el fuzz de opciones. SAFE01 para T24 (`python3 tools/check.py --check SAFE01 --task T24`) ejecuta los canarios, el ciclo de vida y los
invitados de video bajo ASan/UBSan: `VideoGuestSanitizerTests` (A500), `EcsGuestSanitizerTests` (A600) y `AgaGuestSanitizerTests` (A1200: los 15 programas AGA en PAL y NTSC, que ejercitan tambien el 020). El transitorio de FMODE tras una carga (hallazgo de T24c) se busca con `tests/state/test_fmode_transient.py` (151 pruebas, ~85 s; parte de
`run_parallel.py --suite state`): un FMODE en vuelo en la frontera y sprites/planos justo despues; no diverge y el motivo esta en ADR 0009 (punto 9). Lo que no se prueba: Kickstart 3.0, Fast RAM, FPU, Gayle/IDE, otros 020, la interfaz de EmuHawk.
Medido, no afirmado de un hardware: un sprite aparece un pixel lores a la derecha del pixel de plano de la misma coordenada y un DDFSTRT que no cae en una unidad de fetch empieza la imagen en la unidad siguiente.
MED02 (T27a, `tests/media-cow/`): `test_overlay.py` prueba el almacen (base nunca escrita, tamaño fijo, todo-o-nada, pool lleno rechazado y contado, indice reconstruible,
ASan/UBSan) y `test_med02.py` corre el micro-ROM de `med02.py` (un trackdisk en miniatura: escribe pistas AmigaDOS por DMA, sobrescribe una, escribe la otra cara, aleja la cabeza
y lee de vuelta) por el bridge y el motor reales con las palabras MFM codificadas por el modelo independiente de `tests/scenarios/disk/disk.py`: lo exportado es la base con
los sectores del invitado, lo leido por el invitado decodifica a ellos, el hash de la base no cambia nunca ni el del disco que no se toco, un estado a mitad quita las
escrituras posteriores (mismo proceso y proceso nuevo, y el estado guardado tras cargar es el cargado, byte a byte), la cota (STATE01) incluye el pool entero y no crece, un
estado con el pool casi lleno hace que el drive informe el disco protegido, y un overlay imposible (sector repetido, medio fuera del manifiesto, mas sectores que el pool,
cortado, con bytes de mas) se rechaza sin tocar la maquina. Sin el flag el disco sigue protegido como en MED01. `test_med02_raw.py` repite el esquema con un invitado que escribe una vuelta sin ningun sector (sync propio 0x8914 y un patron): lo
que lee despues de alejar la cabeza es lo escrito (salvo el salto de bits del indice, que el oraculo conoce), el export dice que no puede (UNSUPPORTED) y el estado lo trae o lo
quita, y escribir la pista como AmigaDOS sustituye la copia cruda.
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
locales fijados, pruebas WB13-WB16 y contrato de paginas 4/16 KiB. Añadir build y
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
