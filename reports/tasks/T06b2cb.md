# T06b2cb — cierre de la compilacion del motor A500

Resultado: los 33 objetos seleccionados (28 unidades WinUAE + 5 generadas)
compilan con contratos host comprobados y ninguna familia de imports tiene ya a esta
tarea como dueña. La compilacion no es enlace: el enlace y el gate completo son T06c
y T06.

## Evidencia por paso de la ficha

| Paso | Estado | Evidencia |
|---|---|---|
| 1 drawing (HUD/debug, OCS/AGA) | compila; precision NO certificada | T06b2bm; los 30 helpers de 7/8 planos no tienen llamadores (preprocesado) y sus 33 avisos estan en engine-warnings.json como `dead_under_profile`. La diferencia `quick_strobe` sync(endpos) frente a worker(vpos) sigue sin reproducer raster: pasa a T07/T08. |
| 2 ROM explicita sin AROS | hecho | T06b2ba–bi (lectura exacta, claves explicitas, contenedor A500, rommgr). 0052 rechaza ademas el descrambling de cartuchos. |
| 3 imports host de custom | hecho | T06b2ao–au; la puerta de imports no lista nada de custom.cpp como dueño T06b2cb. |
| 4 dividir por fronteras | hecho | subtareas T06b2bk…cg. |
| 5 depfiles/preprocesado/imports vs lista | hecho | `test_engine_imports` (12018 simbolos clasificados, dueño y disposicion), `test_engine_includes` (33 unidades: solo ports/portable, generados y el arbol WinUAE; de od-win32 unicamente machdep/m68k.{cpp,h}). |
| 6 avisos del renderer y de las demas unidades | hecho | `manifests/engine-warnings.json` + `test_engine_warnings`: 52 avisos en 14 entradas con disposicion escrita; sin tocar el generador. El reproducer de `PERIOD_MAX` (LP64) esta en el mismo test. |
| 7 launcher main, semilla explicita | parcial | main.cpp compila sin launcher (T06b2bo). Cablear la semilla en el ciclo de vida PUBLICO y no resembrar en restore requiere la API publica: T06c/T13. |
| 8 expansion con catalogo reducido | parcial | compila (T06b2bq, 0053). Rechazar cpuboard/mounts/RAM no admitida en la frontera publica: `manifests/config-invariants.json` (T13). Los cuatro avisos de expansion estan clasificados (uno `revisit`). |
| 9 cfgfile, callers reales | hecho | parser/exporter excluidos (T06b2bu), callers de custom/inputdevice/dispositivos cerrados en 0048–0053; calc/ini sin dependencias (`test_native_cmake`). |
| 10 savestate | hecho para compilar | T06b2by; callers de persistencia/replay cerrados en 0052. El serializer nativo real y el restore conservan la puerta T15 (familias `state_*` de la puerta). |
| 11 disk.cpp | hecho | T06b2cc; imports de disk pendientes (target_addtorecent, fullpath, console_out, gui_ask_disk) estan en la puerta con dueño T06c. |

## Lo que queda abierto, con dueño (medido por la puerta)

12018 simbolos sin resolver: 11859 manejadores `op_` de otros modelos de CPU (T06c
decide como seleccionar la tabla generada sin editarla), 79 de estado (T15), 20 de
dispositivos logicos de entrada (T10), 15 de presentacion (T08) y 44 de servicios
de escritorio, rutas, texto y tiempo (T06c). Las guardas dependen de invariantes de
configuracion que nadie impone todavia (`config-invariants.json`, T13).

## Como se mantiene

Tres puertas sin emulador: imports (`link-imports.json`), avisos
(`engine-warnings.json`) e includes. Un update de WinUAE que añada una dependencia,
un aviso o una cabecera fuera de los arboles permitidos falla en ellas.
