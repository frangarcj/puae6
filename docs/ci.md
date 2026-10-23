# CI de PUAE6

La puerta PR local se ejecuta con `python3 tools/ci/run.py`. Requiere el Mac
ARM64 de paginas de 16 KiB, herramientas instaladas y el guest/runtime preparados
con `python3 tools/build_waterbox.py --puae6`. No instala paquetes ni modifica
MacHawk. `--list` muestra la seleccion y devuelve `NOT_RUN`, nunca un PASS.

T39a registra PLAN01, BUILD01, seis casos de restore nativo OCS/ECS/AGA
(misma imagen y proceso nuevo), el wrapper con DMA/swap pendiente, poison del
entrelazado y los cambios de modo, y el guest Waterbox con WB13-WB15 reales.
Las suites compartidas por varios checks se ejecutan una vez y acreditan todos
ellos. La seleccion vive en `tools/check.py`; un check obligatorio ausente falla.
Las pruebas de `tests/ci` verifican infraestructura y no certifican el emulador.

Un proceso no aprueba por salir con cero: debe entregar conteos completos,
tests no vacios, ningun fallo, ninguna prueba omitida ni xfail. Un asset
obligatorio ausente bloquea antes de ejecutar; la receta publica no necesita
Kickstart ni juegos privados. Un timeout termina el grupo del worker y sus hijos.
No hay reintentos automaticos de crashes o desyncs.

El JSON final por stdout identifica revision, hash de los fuentes versionados
actuales (incluidos cambios locales), patchset, codegen, lock de toolchains,
compiladores, binarios nativos y Waterbox realmente presentes, caches de CMake,
runner y perfil (los caches se identifican por hash, sin volcar rutas locales).
Si los inputs cambian durante la ejecucion, falla. No enumera variables de entorno,
no recorre firmware sin versionar ni calcula su hash. Los diagnosticos sustituyen
rutas del proyecto, HOME, temporales y assets configurados. No guarda reportes.

Esta puerta es una seleccion PR local; no acredita CI remota ni sustituye el
corpus completo, la matriz de cinco filas, fuzz, clean-build, medidas y puertas
acumuladas nightly/release. Esa integracion y sus workflows pertenecen a T39b.
T39 conserva su aceptacion completa: recetas ejecutadas en los runners declarados
y evidencia remota ausente bloqueada. No publicar workflows ni configurar
secretos, runners o servicios sin autorizacion del usuario.

## Nightly y release (T39b)

`python3 tools/ci/nightly.py --lane L` ejecuta una de estas recetas; `--list`
solo enumera sus suites (`NOT_RUN`):

El interprete debe tener tambien Capstone, usado por las pruebas de instrucciones
FPU. En el Mac actual esta instalado para `/opt/homebrew/bin/python3.14`;
ese interprete ejecuta la lane funcional y sus workers. La receta no instala
dependencias ni considera una omision de Capstone como prueba superada.

- `clean`: dos codegens limpios comparados y build nativo desde un temporal vacio.
- `functional`: todos los modulos actuales fuera de las lanes especializadas,
  incluido firmware real, estados, medios, peliculas y Waterbox. Los inputs
  de `ASSETS` deben estar configurados como variables P6_ locales; no se copian,
  descargan ni publican. `P6_PACKAGE_ZIP` apunta a la preview producida por
  `python3 tools/package.py build`; se prueba el ZIP extraido, sin recompilarlo.
  Antes de preparar pruebas se exige que su manifiesto corresponda a la revision
  actual y que los fuentes esten limpios. Genera el paquete despues del ultimo
  commit de la candidata: un ZIP anterior no acredita los cambios actuales.
  Las pruebas A4000 y CD32 requieren ademas `P6_KICKSTART_A4000_ROM`,
  `P6_CD32_ROM_MAIN`, `P6_CD32_ROM_EXT` y `P6_CD32_GAME_CUE`. CDTV
  requiere `P6_CDTV_ROM_MAIN` (KS 1.3 ID6, 256 KiB),
  `P6_CDTV_ROM_EXT` (extendida v2.7 ID22, 256 KiB) y `P6_CDTV_BOOT_CUE`
  explicito para la fuente TM autorizada de su fixture (Cannon en las puertas
  C3a2/C3c/C3e). El TM de Brian usado por CD32 no acredita arranque CDTV.
  Los cartuchos reales requieren
  `P6_KICKSTART_A500_ROM`, `P6_ACTION_REPLAY1_ROM`, `P6_ACTION_REPLAY2_ROM`,
  `P6_ACTION_REPLAY3_ROM` y `P6_ACTION_REPLAY1200_ROM`; AR1200 usa tambien el
  Kickstart A1200 ya exigido. Los casos de medios reales tambien exigen
  `P6_CD_CUE_FILE` (Brian the Lion de 29 pistas y sus ficheros),
  `P6_CD_CCD_FILE` (descriptor con sus `.img`/`.sub`), `P6_CD_NRG_FILE`,
  `P6_CD_ISO_FILE` y `P6_CD_WAV_FILE` (PCM estereo 44,1 kHz/16 bits).
  `P6_CD_NRG_FILE` requiere DAO de una sesion para el lector por offset legacy.
  Puede generarse sin red ni software comercial con
  `tests.fixtures.nrg_etnf_cdi.converted_dao_fixture()` (CDI propia y CDI2Nero
  fijado, sin `-t`). El fixture ETNF global de dos sesiones usa otra politica
  y conserva sus pruebas especificas; no sirve como asset de este check DAO.
  ISO y WAV pueden ser datos propios compatibles con sus lectores; los casos
  opt-in conservan la validacion de formatos y contenido de sus tests.
  La ausencia de cualquiera
  bloquea antes de preparar imagenes, compilar o lanzar el motor.
  La receta fija `P6_MEDIA_MOVIE_GUEST_RECEIPT` al receipt canonico de este
  checkout (`build/waterbox/puae6/puae6-receipt.json`) para ejecutar BK2/TAS;
  no necesita otra copia ni admite un override heredado. Antes de Docker/codegen
  comprueba seleccion y bytes de fuentes, receta, arboles derivados, ELF/TLS y
  hashes de guest/runtime Release actuales. Un guest antiguo bloquea en ese
  preflight; la receta no lo reconstruye ni interpreta un receipt como playback.
  Una omission/xfail conserva el bloqueo.
- `matrix`: los dos corpus completos, cinco filas, O0/O2/sanitizers y entorno
  host alterado; comprueba los IDs de las imagenes locales fijadas, sin pulls/red.
- `sanitizers`: toda la suite fuzz; conserva semillas y conteos.
- `optimization`: prepara/valida todos los experimentos de flags y los cinco
  frontends normales/sanitizados; incluye equivalencia SIMD y guardas.
- `performance`: prepara y verifica los inputs antes de las 75 medidas de flags
  y las 150 medidas del frontend, y construye los probes nativo/libretro y C# Release
  antes de medir. Ejecuta tambien la comparacion CPU completa (135 ventanas) en
  `build/baselines/ci-cpu`, renovada para esta ejecucion; su TestCase solo consume
  esos datos y verifica identidad/cobertura, sin lanzar otra campaña. Despues ejecuta
  las suites de medicion y presupuesto.
  Se mantiene la politica ADVISORY existente; no transforma ruido en limite duro.

Los IDs de las imagenes de portabilidad estan fijados en
`plan/toolchains.json` (`portability_matrix.image_ids`); SIMD y el preflight
leen esa misma autoridad. Reconstruir una imagen requiere verificar su ID, ISA
y versiones y actualizar el pin: los resultados obtenidos con la imagen anterior
no acreditan el input nuevo. Una imagen fijada ausente sigue bloqueando la lane.

La seleccion acumula todos los `test_*.py`; las clases SIMD se reparten
explicitamente y un modulo nuevo sin clasificar falla. No se sustituyen tests
actuales por resultados guardados. Los datos intermedios de experimentos estan en
build/ mientras se validan; al cerrar la ejecucion se eliminan las medidas/logs.
No se modifica el ADR para ajustar decisiones a medidas nuevas.

El workflow local `.github/workflows/puae6.yml` ofrece PR/nightly/release manuales,
con checkout fijado a un commit y sin credenciales persistentes. Requiere runners
ya preparados: etiquetas `puae6` y `puae6-perf`, herramientas/caches offline,
referencia MacHawk de solo lectura y guest/runtime correspondientes. No instala
paquetes ni aprovisiona runners. Rendimiento espera a terminar los otros checks;
no compite con sanitizers. No hay upload de informes ni assets privados.

La lane funcional tambien exige la imagen RetroArch local fijada por ID e ISA
en `tests/libretro-host/docker/image.json`. Se comprueba antes del codegen y de
preparar pruebas, se incluye en la identidad y se vuelve a comprobar entre suites.
Una imagen ausente o distinta bloquea: el test no la construye ni descarga.
Docker ejecuta ese ID con `--pull=never --network=none`; los comandos de Disk
Control siguen por loopback dentro del mismo contenedor. El pin identifica una
imagen local existente, sin prometer que reconstruir su Dockerfile reproduzca
los mismos bytes.

La receta Waterbox acepta `--source-audit checkout|snapshot`. El modo por defecto
`checkout` verifica tambien HEAD, blobs y overlays de las referencias locales.
El modo explicito `snapshot` comprueba las capturas completas contra el lock y
sus manifiestos, incluidos modos, hashes y overlays, sin consultar los checkouts
originales. No hay fallback entre modos. Los receipts identifican la auditoria
realmente ejecutada; los antiguos conservan su alcance original. Ambos modos
compilan los mismos inputs. Este selector no proporciona el sysroot ni acredita
su reconstruccion o una ejecucion remota.

El repositorio publico `frangarcj/puae6` contiene los fuentes y workflows. Guardar
workflows o ejecutar lanes locales no acredita ejecucion remota: T39b/T39
conservan pendientes sus puertas no ejecutadas en los runners declarados.

## Matriz nativa publica

El workflow manual `native-linux.yml` ejecuta las cinco filas declaradas del
proyecto, cada una en O0, O2 con assertions y ASan/UBSan: 15 jobs, con cuatro
simultaneos, dos compiladores por job y un limite de 45 minutos por job.
El selector manual `variants` usa `all` por defecto; `san` ejecuta solo las cinco
filas con sanitizers para comprobar una correccion sin repetir O0/O2.

| Fila | Runner estandar | Compilador |
|---|---|---|
| macOS ARM64 | `macos-15` | Apple Clang |
| Linux ARM64 | `ubuntu-24.04-arm` | Clang |
| Linux ARM64 | `ubuntu-24.04-arm` | GCC |
| Linux x86_64 | `ubuntu-24.04` | Clang |
| Linux x86_64 | `ubuntu-24.04` | GCC |

`python3 tools/ci/linux_native.py --row FILA --opt O0|O2|san` comprueba el SO e
ISA reales antes de compilar y selecciona su toolchain existente. Sin argumentos
conserva Linux x86_64/Clang/O2. Las herramientas deben estar preinstaladas: no
instala paquetes ni usa Docker, MacHawk, secretos, caches de GitHub o artifacts.
El repositorio es publico por instruccion del usuario; estos runners son estandar.

Las micro-ROM MIT propias `boot.s` e `input_all.s` prueban headless/libretro y
restore nativo respectivamente. `boot.s` evita depender de los puertos distintos
por defecto de cada frontend. Guardan/restauran en procesos nuevos y comparan
futuro hardware/AV/PCM y fullstate exactos. Los estados temporales se eliminan al
cerrar; revision, fila, flags, fuentes, patchset, codegen, compiladores y binarios
salen por stdout. ASan/UBSan detienen el job ante hallazgos; LSan esta activo en
Linux y no esta disponible en Darwin ARM64. Ademas, dos ciclos de carga/avance/
descarga en la misma biblioteca comparan el futuro y detectan caches no liberadas
o no reconstruidas. La falta de herramientas, salida
incompleta o un fallo termina el job. `fail-fast: false` permite ver las demas
combinaciones sin ocultar el fallo.

El selector manual `gate` conserva `native` por defecto. `corpus` ejecuta solo
la puerta nueva de corpus, con los mismos quince jobs y variantes: construye una
vez probe/headless, genera las 89 entradas publicas y las 28 diferenciales, y
reutiliza `row_driver.py`, `faketime.c`, sus goldens y el comparador de T25.
Cada corpus corre completo en baseline y host: locale, TZ, rutas con caracteres
especiales y tres relojes civiles desplazados realmente; el monotono se conserva.
Comprueba todos los hashes hardware/AV, unidades, errores y headless, sin cambiar
ningun golden. Las ROMs y medios son generados propios, sin firmware comercial.
El formato JSON de las referencias sigue en version 1; `probe_schema=2` identifica
la observacion completa, incluidos `config_changed` y `config_changed_flags`.
El probe anuncia esa version una vez por proceso y se comprueba tambien por caso.
Una migracion de esquema debe explicar el cambio de bytes y verificar todas las
observaciones anteriores antes de renovar hashes; el comparador usa siempre los
campos completos actuales.

`--gate corpus` exige revision limpia y registra/recomprueba tambien recetas,
generadores, fixtures y goldens Python. Salida, corpus y biblioteca de reloj
son temporales y se eliminan incluso ante fallo. ASan/UBSan siguen activos en
`san` mediante `-fno-sanitize-recover=all`, aunque el `row_driver` no hereda
`UBSAN_OPTIONS`; ese driver existente desactiva LSan para el corpus. El gate `native`
previo conserva LSan Linux activo. Escribir esta receta no acredita su ejecucion
remota ni sustituye las lanes locales originales.

El selector `native` ejecuta pruebas nativas por combinacion y no acredita la
comparacion completa entre ISA de los corpus. El selector `corpus` compara ambos
goldens por fila cuando se ejecuta realmente. Ninguno acredita Waterbox Darwin,
las lanes acumuladas o la release.
Windows y macOS Intel aun no tienen un port anunciado en la matriz del proyecto.

La comprobacion SRC01 de avisos incluye dependencias vendorizadas declaradas en
el lock, ademas de los snapshots: para MAME/IPF valida offline el pin, PROVENANCE,
todos los archivos derivados/originales, shims y licencia, sin rutas ajenas,
symlinks ni archivos extra. Una fuente desconocida o un hash distinto falla.
El productor CDI2Nero es solo un fixture de tests y conserva su verificacion
independiente; no se enlaza en el producto.
