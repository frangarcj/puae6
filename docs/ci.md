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

- `clean`: dos codegens limpios comparados y build nativo desde un temporal vacio.
- `functional`: todos los modulos actuales fuera de las lanes especializadas,
  incluido firmware real, estados, medios, peliculas y Waterbox. Los once assets
  de `ASSETS` deben estar configurados como variables P6_ locales; no se copian,
  descargan ni publican. Una omission/xfail conserva el bloqueo.
- `matrix`: los dos corpus completos, cinco filas, O0/O2/sanitizers y entorno
  host alterado; comprueba los IDs de las imagenes locales fijadas, sin pulls/red.
- `sanitizers`: toda la suite fuzz; conserva semillas y conteos.
- `optimization`: prepara/valida todos los experimentos de flags y los cinco
  frontends normales/sanitizados; incluye equivalencia SIMD y guardas.
- `performance`: prepara y verifica los inputs antes de las 75 medidas de flags
  y las 150 medidas del frontend, y ejecuta las suites de medicion y presupuesto.
  Se mantiene la politica ADVISORY existente; no transforma ruido en limite duro.

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

No hay `origin` configurado en este checkout. Guardar este YAML y ejecutar lanes
locales no acredita ejecucion remota: T39b/T39 permanecen pendientes de esa puerta.
