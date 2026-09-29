# 06. Ejecucion por una IA con poco contexto

## Que hay ahora

Documentacion, un backlog JSON y un inspector/validador del plan en Python standard
library. No hay motor ni toolchain instalada por este paquete. La entrega actual
termina con la validacion de esos archivos, no con un core probado.

La referencia de integracion elegida por el usuario es ../MacHawk. Leer
docs/07-machawk.md antes de T00/T09: Waterbox Darwin ARM64 es objetivo inicial;
no esperar a T46 para probarlo ni usar el runtime publico x86_64 en su lugar.

## Comandos disponibles del marco

    python3 tools/plan.py validate
    python3 tools/plan.py next
    python3 tools/plan.py show T00
    python3 -m unittest discover -s tests -p 'test_plan.py' -v
    python3 tools/plan.py validate --release

El ultimo comando DEBE fallar en esta entrega: las tareas del producto estan
pending y hay pins por completar. Nunca interpretar ese fallo esperado como un
motivo para marcar el backlog entero done. El inspector no ejecuta los comandos
de las tareas ni juzga por si solo si el emulador funciona.

## Ciclo de iteracion

Trabajar como una sucesion de verticales cortas:

1. elegir una tarea preparada y el riesgo concreto que elimina;
2. implementar el cambio minimo que produzca una observacion util;
3. ejecutar sus checks y una regresion cercana;
4. revisar el diff, marcar done y pasar a la siguiente vertical.

Git + tests son el historial normal. No escribir un informe por rutina. Si tras
dos o tres tareas consecutivas solo hay documentos y ninguna prueba/probe/binario
ejecutable nuevo, revisar la descomposicion y adelantar validacion practica.

## Orden recomendado

T00-T04 preparan entorno, fuentes, inventarios, codegen y contrato de pruebas.
T09 sigue inmediatamente despues: prueba Waterbox de riesgo con mini-core, aunque
su ID numerico sea mayor. T05-T08 construyen la minima ruta A500 nativa; T07 parte
explicitamente del frame-boundary ya probado por el PUAE fijado y lo neutraliza.
T10-T13 completan entrada/audio/medios/reset. T26 introduce memoria/fuzz temprano.
T16 pasa a M1 como smoke libretro y gate de paridad sobre la misma p6_run_frame.
Solo despues T19-T22 cierran la primera ruta Amiga Waterbox REAL. T14-T15 y
T17-T18 completan estados nativos y el producto libretro M2. No ampliar a ECS/AGA
antes de esos gates.
T23-T25 amplian perfiles y plataformas; T27-T31 medios/UX; T32-T41 comparacion,
rendimiento, mantenimiento y distribucion. El JSON contiene el orden de prioridad
exacto y las dependencias reales; no ejecutar algo bloqueado por seguir numeros.
T42-T47 son lineas posteriores al release, no funciones prometidas para el MVP.

Cada tarea incluye dependencias, lecturas, zonas editables, acciones, checks,
aceptacion y motivo para parar. `next` devuelve la primera preparada por el orden
del backlog. Se puede elegir otra preparada realmente independiente; nunca saltar
una dependencia usando mocks ni cambiar el status para desbloquearla.

## Comandos futuros: contrato, NO implementados aun

T00 registra las herramientas y acuerda rutas de runners sin instalar nada a ciegas.
T01 crea importador/verificador de fuentes fijadas y dependencias externas.
T02/T03 crean source manifest, aplicador de parches y generadores host.
T04 crea `tools/check.py --check ID` y el runner headless de escenarios. Cada ID
no implementado devuelve error; su self-test no sustituye la prueba real.
T06 crea los presets CMake native-debug/native-release y el target p6_headless.
T09 crea la receta `tools/build_waterbox.py --smoke` conectada al runtime real.
T16 crea el adaptador libretro minimo sobre bridge/frame_step, sin scheduler propio.
T19 amplia Waterbox para `--core`; usa la misma p6_run_frame y no enlaza
adapters/libretro ni llama retro_run.
T33 crea `tools/bench.py --scenario ID --backend native|waterbox --output PATH`.
T37 crea `tools/update_upstream.py --old OLD_SHA --new NEW_SHA --workdir PATH`.

Estado del build nativo (verificado en T06c; existe, pero no arranca ninguna maquina):

    cmake --preset native-debug          # ASan/UBSan
    cmake --build --preset native-debug  # motor + capa portable + p6_headless
    ctest --preset native-debug --output-on-failure
    ./build/native-debug/p6_headless --identity
    cmake --preset native-release && cmake --build --preset native-release && ctest --preset native-release

    python3 tests/host/run_parallel.py                 # suite de host en paralelo (~2 min)
    python3 -m unittest discover -s tests/codegen -t . # puertas de enlace, imports, avisos, includes
    P6_TEST_COMPILERS=1 python3 -m unittest tests.codegen.test_compilers   # matriz Apple clang/LLVM/GCC

`p6_headless --identity` imprime perfil, commit de WinUAE, serie de parches y capacidades;
`boot: not implemented` es cierto hasta T08 (el bridge ya avanza unidades, T07, pero no entrega video ni audio).

STEP01 (T07c) usa un arbol propio con el gancho de prueba del parche 0062, que ningun preset define:

    python3 -m unittest tests.step.test_step                    # arbol build/step-trace, ~1 min tras la primera compilacion
    P6_TEST_MUTANTS=1 python3 -m unittest tests.step.test_step.MutantTests   # cinco mutantes del mecanismo + control
    python3 -m unittest tests.host.test_bridge_lifecycle        # init/load/unload sobre el motor real (T07b)
    P6_TEST_SANITIZERS=1 python3 -m unittest tests.host.test_bridge_lifecycle   # lo mismo bajo ASan/UBSan

`tools/check.py --check STEP01` sigue respondiendo NOT_IMPLEMENTED: el dispatcher de T04 solo conoce HARNESS01 y
sus tests lo fijan. La evidencia de STEP01 son los modulos anteriores.

Secuencia de build nativo despues de T06 (contrato original):

    cmake --preset native-debug
    cmake --build --preset native-debug
    ctest --preset native-debug --output-on-failure
    python3 tools/check.py --check STEP01

La IA debe verificar estos comandos en el checkout cuando existan, no limitarse
a documentarlos. Si los adapta a la toolchain fijada, cambia el contrato y los
checks en el mismo cambio. Los comandos no llevan descargas ocultas ni permisos
globales. Un comando cuyo binario no existe no se marca como 'paso manual aprobado'.

## Plantilla de trabajo de una tarea

Entrada: tarea Txx desbloqueada + documentos read + source SHA + config/fixtures.
Salida: cambio minimo + test/check + diff entendible.

Antes: leer archivos reales y definir la condicion precisa de exito. Durante: no
cruzar touch ni mezclar optimizaciones. Despues: ejecutar pruebas, mostrar diff y
actualizar status al superar aceptacion. Crear `reports/tasks/Txx.md` solo si hay
un bloqueo o contexto importante que Git/tests no cuentan bien.

Un mock tiene nombre y proposito limitados: ABI transport o parser, nunca exactitud
Amiga ni save/restore real. Las pruebas de juego requieren que haya instrucciones
68k ejecutadas, cambios de memoria/hardware y salida controlada, no solo pixels.

## Reglas para escalar sin paralizar todo

Faltan ROMs: ejecutar corpus libre; bloquear compatibilidad que requiere esas ROMs.
Falta runner x86_64: documentar build y bloquear runtime Waterbox; no inferir exito.
No aplica parche: guardar reject y leer upstream; no parchear build a mano.
Desync: minimizar escenario, primer tick/frame y subsistema; no regenerar golden.
Rendimiento malo: medir en release igual y localizar; no quitar precision.
Demasiado scope: dividir tarea conservando gate; no meter una reescritura entera.
ABI/layout dudoso: test C/C# en ambos sentidos antes de emular.

## Decisiones y revisiones

Una decision que cambia perfil, compatibilidad, codigo de CPU, formato de estado,
pila de corutina, memoria invisible, JIT o runtime requiere ADR en docs/adr.
Mantenerlo corto: problema, decision, motivo, consecuencias y como se valida.

No pedir confirmacion para cada archivo o paso ya especificado. Si el bloqueo no
se puede resolver dentro del contrato, guardar una nota breve y continuar por una tarea
independiente. No prometer trabajo en segundo plano ni un resultado inexistente.

## Definicion global de terminado

Todos los criterios de T41; fuentes fijadas; ninguna prueba critica skipped;
Waterbox real y libretro real en plataformas anunciadas; estados y medios con
rollback; medidas publicadas; actualizacion ensayada; licencias/paquetes/instrucciones.
No basta con que el codigo compile ni con completar la herramienta plan.py.
