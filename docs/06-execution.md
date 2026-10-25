# 06. Ejecucion del proyecto

PUAE6 tiene core nativo headless/libretro, guest Waterbox Darwin ARM64 e
integracion C# comprobados. El alcance de la primera entrega, los productos
locales y los limites estan en [las notas](user/release-notes.md); el plan conserva
los extras pendientes. No interpretar la coherencia del plan como emulacion probada.

## Empezar una tarea

1. Leer README.md, AGENTS.md y la ficha con `python3 tools/plan.py show ID`.
2. Ejecutar `python3 tools/plan.py validate` y `python3 tools/plan.py next`.
3. Leer las rutas read y el codigo real; elegir dependencias terminadas y respetar touch.
4. Implementar una vertical comprobable y ejecutar su check dirigido mas las regresiones cercanas.
5. Revisar diff/resultados, cerrar la ficha cuando pase su aceptacion y validar el plan.

La task force autorizada admite cuatro tareas en progreso, una por responsable,
con worktrees aislados; el coordinador integra metadata y serializa builds pesados.
Durante desarrollo no se repite la matriz/campana completa por cada cambio. El
cierre integra la cobertura acumulada y repite lo afectado por un cambio, fallo o
incertidumbre concreta; conserva la revision y el alcance de cada resultado.

## Comandos disponibles

```sh
python3 tools/plan.py validate
python3 tools/plan.py next
python3 tools/plan.py show T41
python3 tools/check.py --task T41 --check PLAN01
python3 -m unittest tests.test_plan tests.contracts.test_dispatcher
python3 tools/plan.py validate --release
```

El dispatcher ejecuta solo las suites declaradas para cada tarea/check/perfil.
Una combinacion ausente devuelve NOT_IMPLEMENTED. Un runner, asset u opt-in
necesario ausente queda BLOCKED; un mock o un test del propio runner no acredita
emulacion. Para builds, escenarios y variables requeridas, usar [testing.md](testing.md),
[ci.md](ci.md) y las guias de producto. No ejecutar automaticamente una lane completa
si ya esta acreditada y no hay un cambio causal.

`validate --release` comprueba tareas obligatorias y pins pendientes; puede pasar
con extras opcionales abiertos. No ejecuta las suites ni certifica emulacion,
instalacion, rendimiento o compatibilidad. Las tareas posteriores a T41 siguen
siendo trabajo real aunque no bloqueen esa primera entrega.

## Fuentes, fixtures y entorno

El motor WinUAE fijado permanece pristine; las correcciones van en patches/winuae
y se materializan en build/. Los generados se producen con su receta, no se editan.
La referencia MacHawk es solo lectura y se preserva su fingerprint. Waterbox
Darwin ARM64 forma parte del objetivo inicial: no se sustituye su ejecucion por
una biblioteca nativa ni por un build cruzado. Ver [07-machawk.md](07-machawk.md).

Micro-ROMs y fixtures propios acreditan su escenario concreto. Firmware/juegos
comerciales autorizados se usan localmente y nunca se publican. Un arranque con
micro-ROM no acredita cada Kickstart comercial. No instalar paquetes, modificar
configuracion global ni tocar proyectos hermanos por iniciativa propia.

## Registro y bloqueos

Git diff/status y salida de tests son el registro habitual. Los datos temporales
van en build/ y se eliminan; conservar solo codigo, tests, fixtures legales,
recetas, dependencias necesarias y decisiones utiles. No guardar reportes por tarea.

Tras dos intentos razonados sobre el mismo fallo: conservar el reproducer como
test, anotar dos hipotesis verificables, marcar blocked y elegir otra tarea
independiente. No ampliar arenas, alterar flags CPU o cambiar golden para ocultar
una divergencia. Una reduccion de alcance se declara expresamente; no equivale
a completar la funcion excluida. Publicar, hacer push o reemplazar una instalacion
requiere la instruccion correspondiente del usuario.
