# T38: ensayo de una actualizacion upstream real (WinUAE 6030 -> 6.1.0 beta 17)

Resultado en una frase: la cola de 143 parches se reescribe sobre el candidato con 21 conflictos resueltos a mano, 4 parches retirados y 10 nuevos;
el candidato compila, enlaza, corre en nativo, libretro y dentro de Waterbox, es determinista y su audio, sus ticks y su chipset coinciden bit a bit con el pin
en los 28 escenarios del corpus, **pero** no guarda estado, 14 modulos de host fallan por fixtures atados a 6.0.3 y la CPU ve la fase de la interrupcion de
vblank de otra manera en 13 escenarios sin causa establecida. Adoptarlo exigiria las tareas propuestas del final. El producto no se movio: el pin 6.0.3 se
reconstruye con los mismos hashes (seccion 6).

Ficheros de este directorio: `candidate.json` (pin y alcance), `upd01-first-pass-report.{md,json}` (la herramienta de T37 sobre la cola vieja),
`conflicts.md` (parche a parche), `candidate-series/` (la cola resultante, 149 parches con hashes cerrados desde el snapshot del candidato),
`candidate-bases.json`, `new-patches.json`, `port-adaptation.patch` (cambios del puerto/bridge solo en el area de ensayo), `cross-version.json`,
`series-metrics.json`, `bench-comparison.md`, `results.json` (checks, diferencias clasificadas, tareas), `return-to-pins.json`, `logs/` (solo fallos y benchmark) y
los scripts del ensayo (`rebase_series.py`, `make_project.py`, `cross_version.py`, `series_metrics.py`, `bench_compare.py`).

## 1. Candidato

`8132fd219c808d950028de2b50f76e142460da50`, asunto `6100b17`, 2026-10-12, WinUAE 6.1.0 beta 17. No hay tag publicado despues de `6030`
(`git ls-remote`: el ultimo tag es 6030); WinUAE publica sus betas como commits de version (6100b1..6100b17). Se pidio un commit concreto y no master: se fetchea
por SHA completo con `--depth=1` y no se usa ninguna rama. 663 commits, 577 archivos, +345 869/-173 644; en el snapshot de 1134 archivos: 217 nuevos, 9 quitados, 359 cambiados.
Notas de version que tocan el perfil en `candidate.json`. Los datos descargados se trataron como no confiables: solo se leyeron objetos Git y se compilo lo que el proyecto compila.

## 2. Procedimiento y esfuerzo

1. `tools/update_upstream.py --old PIN --new CANDIDATO` (12 s): 143 parches = 6 clean, 3 rebased, 8 shifted, 10 semantic, 116 blocked. Exige aplicacion exacta; lo que no es
   limpio queda sin candidato (por diseno). Seis hallazgos de triage: `atonce.cpp` nuevo, cuatro avisos de licencia cambiados y uno renombrado (cabeceras iguales, solo hashes y nombre).
2. Reescritura con un 3-way de Git sobre un repositorio disperso de los 43 archivos que toca la cola (`rebase_series.py`): 143 commits (uno por parche). 122 se re-exportan solo con
   contexto distinto, 4 con el cuerpo cambiado sin marcadores (revisados a mano), 21 con marcadores resueltos leyendo los dos lados, 3 quedaron vacios (upstream ya los trae). ~25 min.
3. Copia del proyecto con el snapshot, el lock y la serie del candidato (`make_project.py`), `tools/materialize_upstream.py` (24 s, aplicacion exacta de los 149 parches), `tools/generate_cpu.py` (5 s),
   CMake. Siete rondas de compilacion/enlace (~40 min de trabajo, builds de 5-10 min cada una): 0900-0902 (codigo nuevo de upstream sin su guarda de perfil),
   0903-0907 (accesores de estado que leen variables que ya no existen), puerto (`my_existsfiledir`, simbolos que el bridge nombraba).
4. Pruebas: REF01/DET01/host/estado/Waterbox/benchmark (seccion 4). Aparecieron dos regresiones reales del puerto que el 3-way habia dejado pasar: 0908 (el gancho
   de reinicio del contador quedo detras de un `set_cycles` nuevo de upstream: tras `p6_reset` el reloj de la placa fallaba) y 0909 (el nombre de un include en mayusculas que el
   puerto escribia y upstream renombro).
5. Retorno al pin y comprobaciones (seccion 6).

Tiempo total medido en reloj: ~2 h 40 min (la mitad esperando builds y suites con -j 3). Parches tocados: 24 con conflicto, 10 nuevos, 4 retirados; archivos de upstream tocados por la cola: 43 -> 41; lineas
de delta: +4365/-393 -> +4396/-391 (`series-metrics.json`). Ese recuento no demuestra mantenimiento: lo caro esta en lo que NO se hizo (seccion 7).

## 3. Cola resultante

Ver `conflicts.md`. Los cambios de semantica de upstream que importan al perfil: TOD de los CIA contado en la deteccion de hsync/vsync (b17), planificador de eventos
secundarios reescrito y blitter sin evento fijo (b13), deteccion de blanking vertical (b14), DMAL de Paula/Agnus (b16), Copper reimplementado (b13/b14), shifters AGA de 64 bits (b10),
`SPCFLAG_CALLBACK` y callbacks de hilo principal, y el reinicio del contador al principio del reset.
Upstream trae ya tres de nuestros parches (0061, 0096, 0145) y obliga a retirar un cuarto (0143): evidencia a favor de mantener `upstream_candidate` honesto.

## 4. Resultados por check

| check | resultado | nota |
|---|---|---|
| UPD01 | PASS | herramienta sobre el candidato real y sobre la serie reescrita (149 clean); `tests/upstream-update` |
| REF01 | PASS | T32b: 28 escenarios producto = referencia; 89 micro-ROM iguales tras 0908. `tests.reference.test_ref01` tiene 2 errores previos del producto (nombres de escenarios T32b) |
| GEN01 | PASS | 26 salidas identicas en dos builds limpios; 13 de 26 cambian respecto al pin (cpuemu_21, linetoscr_*) |
| WB03 | BLOCKED | el candidato no puede guardar estado (COPR) |
| WB04 | BLOCKED | idem |
| PERF01 | PARTIAL | nativo/libretro, 7 escenarios (incluye Kickstart 1.3+juego y 3.1+demo AGA): mediana de p50 -2.0 % (-5.3 % .. +2.4 %), maquina en uso; Waterbox sin medir |
| ADR01 | PASS | `docs/adr/0005-update-rehearsal.md` |

Otros resultados: DET01 (10 000 unidades, dos procesos) PASS. Waterbox: la guest compila (auditoria de imports incluida; 52 885 032 -> 53 168 064 bytes) y 60 unidades
en un sandbox real dan el mismo hash de imagen que el nativo (`b7532efeb0c7a983`); `tests/waterbox-smoke/test_puae6.py`: 5 de 20 pasan, 13 fallan solo porque el manifiesto de memoria es el de la guest
vieja, 2 por el A1000 (seccion 5). Suite de host: 54 de 69 en la primera pasada, 14 tras 0909 (`logs/host-suite-first-pass.txt`). Suite de estado, nivel `iter`: 2 de 6 modulos
(`logs/state-iter-tier.txt`). Kickstart real: dos escenarios del benchmark corren 5500 unidades por libretro sin fallo (no se comparo el resultado grafico con el pin).

## 5. Diferencias contra el pin

`results.json` las clasifica una por una. Resumen sobre los 28 escenarios y todos los limites de unidad (`cross-version.json`):

- Identicos: ticks maestros, PCM entregado, todos los digests de chipset (Copper, blitter, haz, Denise, Agnus, Paula).
- Cuadro: 756x574 -> 752x574 en OCS; en un patron de barras los pixeles son iguales desde la columna 4 en 572 de 574 filas (b17: centrado horizontal). Dos filas no se explican. El escenario entrelazado
  (`a500-hires-lace-fields`) solo conserva 317 filas iguales: ABIERTO.
- A1000: el pin entrega 756x574 en cada unidad; el candidato no entrega cuadro en la unidad 13 y luego 752x576 (b17: "A1000 (No EHB) chipset option incorrectly selected OCS Agnus").
  Dentro de Waterbox la guest imprime el hash del buffer viejo en esa unidad y el nativo imprime 0: ajuste del puerto.
- CIA: digest distinto en 2 escenarios (TOD movido en b17). No se biseco.
- CPU: digest distinto en la unidad 1 de 13 escenarios (los 4 AGA, 4 de 5 A600, 5 de 19 A500), RAM distinta en 3 (4-7 bytes: el byte de pila del bucle interrumpido y los
  contadores de joystick que escribe el manejador de VERTB). Con el chipset igual, lo que cambia es cuando la CPU ve la interrupcion o los contadores. ABIERTO; hipotesis: arbitraje de bus/blanking de las betas 13-16.
- Los digests de scheduler/input/video difieren desde el limite 0 porque el layout de los accesores cambio (133 -> 125 bytes), no por la maquina.

Las diferencias ABIERTAS son 3 (cuadro entrelazado, dos filas del cuadro, fase de la CPU) y bloquean adoptar el candidato; el resto esta clasificado con su evidencia.

## 6. Retorno al pin y compatibilidad

- Ni `plan/upstream.lock.json`, ni `manifests/upstream-files.json`, ni `patches/winuae`, ni ningun archivo del producto cambio (`git status`: solo las zonas de T38). Nada se instalo ni se borro fuera de `build/`.
- Tras el ensayo, en el mismo worktree: `tools/update_upstream.py --old PIN --new PIN` = no-changes (143 clean), `python3 tools/generate_cpu.py --check` = 26 salidas identicas, `SRC01` 1134 archivos,
  `materialize_upstream` serie `9a8127bc...`; el arbol `build/upstream` y `build/generated` tienen el mismo hash que los del checkout principal del usuario (leidos, no tocados), y la guest Waterbox
  reconstruida da `79c3e8e3c2c2b738...`, el `guest_sha256` registrado en `manifests/waterbox-memory.json`. Detalle en `return-to-pins.json`.
- Estados y peliculas: ningun estado del pin se carga en el candidato ni se promete: la identidad del envelope (guest, config, hash de la serie) cambia y los cuerpos cambiaron de tamano (CHSL 245 -> 191 bytes).
  El build del pin se conserva para abrirlos (docs/05-maintenance.md, punto 8).

## 7. Lo que no se hizo y tareas propuestas

No se completo la adaptacion del estado nativo: el Copper, el haz, el DMAL, el planificador secundario y el blitter de 6100b17 exigen accesores y validadores nuevos; lo que se hizo (0903-0907 y el bridge del
proyecto de ensayo) solo llega a compilar y a mostrar donde falla. No se bisectaron las diferencias de CPU. No se midio Waterbox ni los tiempos de estado. Tareas propuestas (texto en `results.json`):
UPD-state-beam, UPD-state-copper, UPD-state-scheduler, UPD-state-dmal, UPD-host-fixtures, UPD-classify-cpu-phase, UPD-wbx-frame-hash, UPD-memory-manifest, UPD-licenses, UPD-sources, UPD-ref01-t32a.

## 8. Limites del ensayo

- Un solo candidato (el ultimo); no se probo una actualizacion por etapas (b1 ... b17), que repartiria los conflictos y permitiria bisecar.
- El 3-way es un auxiliar del ensayo, no una herramienta de producto; la herramienta de T37 sigue siendo el juez de la serie final. Una mejora posible de la herramienta: ofrecer el 3-way como
  propuesta de reescritura (hoy solo diagnostica).
- Medidas de rendimiento orientativas (la maquina estaba en uso, load1 hasta 19); sin umbrales.
- Alcance de plataforma: macOS ARM64 solo.
