# ADR 0005 (update-rehearsal): ensayo de una actualizacion upstream real, WinUAE 6030 -> 6.1.0 beta 17

El numero 0005 ya lo usa `0005-frame-boundary.md`; este documento conserva el nombre que fija la ficha de T38
(`docs/adr/0005-update-rehearsal.md`) y no sustituye al otro.

## Contexto

`docs/05-maintenance.md` exige, antes de M4, ensayar una actualizacion real a un candidato fijado y evaluado, y prohibe afirmar
mantenimiento demostrado por contar lineas de parches o con un mock. El usuario decidio no mover el commit fijado (6.0.3,
`857f48fcb09399d4d95527e29a2fdeaedb41f827`) por ahora: el ensayo se hace en un area descartable (`build/update/` del worktree), no cambia
`plan/upstream.lock.json`, `manifests/upstream-files.json`, `patches/winuae` ni nada del producto, y deja sus resultados en
`reports/update-rehearsal/`. La herramienta es la de T37 (`tools/update_upstream.py`).

## Decision

1. Candidato: el commit `8132fd219c808d950028de2b50f76e142460da50` (`6100b17`, 2026-10-12, WinUAE 6.1.0 beta 17). No existe ningun tag
   publicado despues de `6030`; WinUAE publica sus betas como commits cuyo asunto es la version, y este es el ultimo. Se fija por SHA completo
   (nunca master): `git fetch --depth=1 <repo> <sha>` a un repositorio desnudo bajo `build/update/`, blobs leidos de los objetos Git como
   hace `tools/import_sources.py`. Alcance: 663 commits, 577 archivos, +345 869/-173 644 lineas; 217 archivos nuevos, 9 quitados, 359
   cambiados en el snapshot de 1134. `reports/update-rehearsal/candidate.json` guarda SHA, fechas, alcance y las notas de b10, b13, b14, b16 y b17 que
   tocan el perfil.
2. Metodo en tres pasos, cada uno con su evidencia:
   a. `tools/update_upstream.py` clasifica la cola (143 parches): 6 clean, 3 rebased, 8 shifted, 10 semantic, 116 blocked por compartir fichero.
   b. La reescritura se hace con un `git rebase --onto` de 3 vias sobre un repositorio disperso de los 43 archivos que la cola toca
      (`reports/update-rehearsal/rebase_series.py`). Git propone; una persona lee los dos lados de cada marcador y resuelve. Los 4 parches cuyo
      cuerpo cambio sin marcadores se revisaron por diff. La serie resultante vuelve a aplicar EXACTA (sin offset ni fuzz) con el mismo `git apply`
      de `materialize_upstream`, y `update_upstream.py --old NEW --new NEW` da 149 parches clean.
   c. El candidato se construye y prueba en una copia del proyecto (`reports/update-rehearsal/make_project.py`) con su propia serie, sus propios
      `build/upstream` y `build/generated` y su propio lock; el producto no se toca.
3. Los fallos de compilacion o enlace que produce codigo nuevo de upstream, y los accesores de estado que leen variables que ya no existen,
   se corrigen SOLO en el area de ensayo como parches nuevos (0900..0909, `new-patches.json`), cada uno con su motivo. Los de
   accesores de estado no se completan: se dejan documentados con tarea propuesta (ver abajo). Un parche que upstream ya trae o cuya premisa
   cambio sale de la cola (0061, 0096, 0143, 0145).
4. La diferencia con el pin se mide con el mismo corpus de T32 (28 escenarios en tres maquinas) y se clasifica (`results.json`): upstream
   previsto, atribuido por nota de version, adaptacion del puerto, o abierta. Una diferencia abierta bloquea adoptar el candidato; no bloquea
   el ensayo, que su objetivo es descubrirlas.
5. Retorno: el producto no se altero; el conjunto de pins anterior se materializa, genera y construye igual que antes (mismos hashes, ver
   `return-to-pins.json`).

## Alternativas descartadas

- Esperar al siguiente tag (`6040` o `6.1.0`): no existe, y el ensayo seria un mock. Un tag mas viejo no tendria diferencia de interes.
- Usar el modo `--new-source` de la herramienta y resolver a mano cada parche con `git apply --reject`: la herramienta exige aplicacion exacta
  y marca como conflicto lo que Git fusiona sin marcadores; con 116 parches bloqueados no se puede avanzar parche a parche. El 3-way no sustituye
  a la herramienta (que sigue siendo el juez de la serie final) sino al proceso manual de reescritura.
- Ablar los parches de estado para compilar el candidato: es el atajo de "desactivar la feature"; la regla del plan lo permite solo con error
  claro, y aqui el estado nativo es una promesa del producto. Se adaptaron los accesores hasta donde compila y se declara incompleto.
- Aceptar el candidato en el producto: el usuario decidio no mover el pin.

## Evidencia (resumen; el detalle esta en `reports/update-rehearsal/rehearsal.md`)

- Cola: 143 -> 149 parches, 43 -> 41 archivos tocados, +4365/-393 -> +4396/-391 lineas (`series-metrics.json`). Los archivos calientes siguen siendo
  `custom.cpp` (29 -> 32 parches) y `newcpu.cpp` (20 -> 22).
- 24 parches con marcadores de conflicto (21 resueltos a mano, 3 absorbidos por upstream), 1 obsoleto por el renombrado de un archivo
  (0143), 10 parches nuevos (0900..0909).
- Codegen: 26 salidas reproducibles (GEN01 pasa en el candidato); 13 de 26 cambian (cpuemu_21 y los 12 `linetoscr_*`).
- Construye: nativo (native-release, state-opt, bench-release, reference-opt), libretro y la guest Waterbox (auditoria de imports incluida);
  60 unidades en un sandbox Waterbox real dan la misma imagen que el nativo. Dos escenarios con Kickstart real (1.3 + juego, 3.1 + demo AGA)
  corren 5500 unidades por libretro sin fallo.
- Pruebas: REF01 sobre el corpus de 28 escenarios (T32b) y sobre los 89 micro-ROM: producto = referencia del candidato (el de los micro-ROM
  encontro una regresion del puerto, 0908). DET01 (10 000 unidades, dos procesos): igual. Suite de host: 54 de 69 modulos pasan en la primera
  pasada; uno de los 15 fallos era un problema real del puerto (0909), 14 siguen siendo fixtures atados a 6.0.3.
- Contra el pin (28 escenarios): ticks maestros, PCM y todos los digests de chipset son identicos; difieren la geometria del cuadro (borde), el CIA
  en 2 escenarios y la fase de la CPU en 13 (abierto). Detalle y clase de cada diferencia en `results.json`.
- Estado nativo: el candidato NO guarda estado (`COPR: the strobe of the Copper is out of range`); los estados del pin no se cargan en el
  layout nuevo, ni se prometio (cada build tiene su identidad). WB03 y WB04: BLOCKED.
- Rendimiento (orientativo, maquina en uso): el runner nativo cambia -2.0 % en mediana de p50 (-5.3 % .. +2.4 %) en 7 escenarios.

## Costes y riesgos

- Tiempo del ensayo, medido en reloj: reescribir la cola con sus 21 conflictos, ~25 min; siete rondas de compilacion y enlace hasta un candidato que
  enlaza, ~40 min; el resto (pruebas, guest Waterbox, benchmark, comparacion con el pin) fue esperar a builds y suites de 5-30 min con -j 3; en total
  unas 2 h 40 min. Pero la actualizacion NO esta terminada: las tareas propuestas son de dias de trabajo (accesores del estado nativo de Copper, beam,
  DMAL, planificador y blitter, mas sus validadores; 14 modulos de host con fixtures atados a 6.0.3; clasificacion de la fase de la CPU).
- El presupuesto de delta (15 archivos / 1500 lineas manuales, alarma de revision) ya se habia superado antes (ADR 0004); la actualizacion no lo
  empeora (41 archivos, ~4400 lineas anadidas) pero la deuda esta en lo que depende de internos de upstream: los accesores de estado nativo
  (bridge/state_*.cpp, patches 0078, 0094-0100, 0103, 0108) leen variables que upstream renombra o rehace en cada beta.
- Riesgo de adoptar sin clasificar: la diferencia de fase de la CPU (13 de 28 escenarios, RAM en 3) es desconocida hasta que se bisecte o se lea el
  codigo nuevo de arbitraje de bus y deteccion de blanking.
- Riesgo del metodo: el 3-way de Git puede fusionar en silencio algo semanticamente mal (p. ej. la recarga de `cputimebase`, el gancho de reinicio
  del contador que quedaba detras de un `set_cycles` nuevo, 0908). Solo el corpus y los tests por parche lo detectaron; contar lineas de parche no.

## Como se valida

- `python3 -m unittest discover -s tests/upstream-update -p 'test_*.py'` (UPD01: herramienta con un mini upstream, coherencia del registro del
  ensayo, serie candidata cerrada por hashes desde el snapshot del candidato, producto sin mover).
- `python3 tools/update_upstream.py --old <pin> --new <pin> --workdir build/update/x` = no-changes y `python3 tools/generate_cpu.py --check`.
- Para repetir el ensayo: `rebase_series.py init/export`, `make_project.py`, y luego `tools/materialize_upstream.py`, `tools/generate_cpu.py`,
  cmake y los checks de `results.json` dentro del proyecto de ensayo.

## Tareas propuestas (no hechas; ver `rehearsal.md`)

UPD-state-beam, UPD-state-copper, UPD-state-scheduler, UPD-state-dmal (accesores y validadores de bridge/state_*.cpp para el layout 6100b17),
UPD-host-fixtures (14 modulos de tests/host), UPD-classify-cpu-phase (la diferencia de fase de la CPU), UPD-wbx-frame-hash (la guest
imprime el hash de un cuadro no entregado), UPD-memory-manifest, UPD-licenses (hashes de aviso de 5 archivos), UPD-sources (`atonce.cpp`),
UPD-ref01-t32a (error previo del producto, ver `results.json`). Texto de cada una en `reports/update-rehearsal/results.json`.
