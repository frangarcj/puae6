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
