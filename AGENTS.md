# Instrucciones para ejecutar PUAE6

Este directorio contiene un port en desarrollo y su plan de tareas. Responder en
castellano. Prioridades: correccion/determinismo, mantenimiento, rendimiento y
portabilidad. No sacrificar una prioridad silenciosamente para aprobar otra.

El usuario selecciono ../MacHawk como referencia BizHawk. Su lectura esta
autorizada; no su modificacion, limpieza ni instalacion de nuevos binarios encima.
Leer docs/07-machawk.md: Waterbox Darwin ARM64 es objetivo inicial, no trabajo
futuro de T46. Preservar los cambios locales identificados en el fingerprint.

## Protocolo obligatorio por sesion

1. Leer README.md, este archivo y la ficha elegida con `python3 tools/plan.py show ID`.
2. Leer todos los documentos `read` de la ficha y el codigo real antes de editarlo.
3. Consultar estado local y `python3 tools/plan.py validate`. No tocar trabajo ajeno.
4. Elegir una tarea por responsable con dependencias DONE; consultar tools/plan.py next.
   En task force respetar max_in_progress del plan y usar worktrees aislados.
5. Anunciar en una frase el resultado util que se busca, archivos y check principal.
6. Implementar la vertical mas pequena que deje algo ejecutable o comprobable.
   Añadir/ajustar el test que capture el comportamiento; no crear ceremonia si un
   check existente ya prueba exactamente el cambio.
7. Durante implementacion ejecutar el check dirigido del comportamiento cambiado y
   sus regresiones cercanas. La union acumulada de checks, matriz completa y medidas
   extensas se ejecutan al integrar una candidata, sin repetirlas por cada cambio.
   Repetir una bateria solo por fallo, cambio relevante o incertidumbre concreta.
   Los checks y la aceptacion de cada ficha siguen siendo obligatorios antes de done.
   Git diff/status y la
   salida de tests son el registro normal. No guardar informes ni evidencias historicas;
   los datos temporales de una comprobacion pertenecen a build/ y se eliminan al terminar.
8. Marcar done cuando checks y aceptacion pasan y ejecutar validate. Explicar un bloqueo
   brevemente en blocked_reason de la ficha. Conservar decisiones utiles en codigo/ADR,
   sin informes por tarea.
9. Mostrar diff y explicar resultado, limites y siguiente tarea desbloqueada. No hacer
   push, publicar releases ni modificar proyectos hermanos sin peticion del usuario.

Estados permitidos: pending, in_progress, blocked, done. El limite de tareas
in_progress lo fija max_in_progress (una por defecto; cuatro en la task force
autorizada el 2026-10-19). El coordinador actualiza el plan, reparte propietarios,
integra commits y serializa compilaciones pesadas; los agentes no editan metadata
compartida ni ejecutan baterias completas por su cuenta. Un bloqueo no cuenta como
tarea completada. No marcar done porque
compila o muestra una pantalla: deben pasar los checks/aceptacion de la ficha.

## Tamaño y alcance

Una tarea tiene un objetivo comprobable. Objetivo orientativo por cambio manual:
1-5 archivos y menos de 400 lineas logicas, salvo importacion/codigo generado.
Si no cabe, dividir su ficha en subtareas con IDs nuevos, dependencias explicitas y
la misma puerta de salida; no ampliar el alcance ni borrar checks. No fragmentar
artificialmente una correccion que necesite ser atomica.

Solo editar rutas `touch` de la ficha y su estado. Si hace falta otra ruta,
registrar motivo y dividir o ampliar la ficha explicitamente antes del cambio.
Los glob patterns describen zonas permitidas, no autorizan reescribirlas enteras.

## Prohibiciones arquitectonicas

- No editar upstream/winuae directamente. Cambios al motor mediante patches/winuae.
- No mezclar refactorizacion cosmetica y actualizacion upstream.
- No editar a mano cpuemu*, cpustbl*, tablas o salidas de generadores.
- No copiar el motor antiguo de PUAE dentro del nuevo para resolver errores de enlace.
- No simular Win32 completo; implementar solo la superficie host inventariada.
- No convertir todos los globales en objetos. Una instancia nativa activa por imagen
  cargada es una limitacion aceptada; una instancia por sandbox Waterbox.
- No longjmp a traves de C++, no hilos del SO como atajo para retro_run.
- No sleep/reloj de pared/entropia host para avanzar hardware emulado.
- No callbacks host por instruccion ni acceso a RAM. CPU, DMA y RAM siguen dentro del motor.
- Tras T07, ningun adaptador llama directamente m68k_go ni mantiene otro scheduler:
  headless, libretro y Waterbox avanzan exclusivamente mediante p6_run_frame.
- No JIT/red/PPC/FS del host en el perfil deterministic; no declarar netplay/runahead
  funcionales hasta superar sus escenarios especificos.
- No mover memoria a alloc_invisible solo porque el snapshot es grande.
- No cambiar hashes golden ni tolerancias para hacer pasar una regresion inexplicada.
- No hacer stubs que devuelvan exito para dispositivos soportados. Desactivar la feature
  en build/config o devolver un error claro de no soportado.
- No prometer compatibilidad de estados o peliculas entre builds diferentes.

## Bloqueos

Tras dos intentos locales razonados sin resolver un mismo fallo, conservar el reproducer
como test y anotar dos hipotesis verificables en la ficha; marcar blocked y elegir otra tarea
realmente independiente. No inventar una funcion upstream ni cambiar flags de CPU
al azar. Un problema de ciclo, generador, ABI, aliasing, JIT o snapshot requiere
un reproducer y revision de la decision correspondiente.

Sin runner Waterbox: escribir y validar lo que sea posible, pero su puerta queda
BLOCKED. Un build x86_64 cruzado o ejecutar una biblioteca nativa en macOS no
sustituye una ejecucion dentro de Waterbox/BizHawk.

## Versiones, dependencias y seguridad del entorno

Los SHA de plan/upstream.lock.json son la autoridad; los null son pendientes
visibles. No usar latest/master como input de una release. No instalar paquetes,
alterar configuracion global de Git, borrar directorios o cambiar maquinas sin
necesidad explicita y autorizacion apropiada. No descargar ROMs comerciales.

El runner del plan solo comprueba coherencia documental; NO prueba el emulador.
Los comandos futuros de docs/06-execution.md no deben anunciarse como existentes.
