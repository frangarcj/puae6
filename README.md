# PUAE6: marco de ejecucion del proyecto

Estado: PLAN DE IMPLEMENTACION. No existe todavia un core ejecutable.
Fecha de investigacion: 2026-09-26.
Directorio solicitado: /Users/frangar/Fun/uae.
Nombre de trabajo: PUAE6; no supone aprobacion de marca ni incorporacion a proyectos upstream.

## Objetivo

Crear un motor Amiga derivado de WinUAE 6, facil de actualizar, con dos productos hermanos:

- un core libretro nativo, portable y sin dependencias de interfaz grafica;
- un core para BizHawk/Waterbox, determinista y apropiado para TAS.

Base inicial fijada: WinUAE 6.0.3, tag 6030, commit
`857f48fcb09399d4d95527e29a2fdeaedb41f827`.
No sustituirlo por master. Los pins de referencia y los pendientes estan en
`plan/upstream.lock.json`. Ver fuentes y observaciones en `docs/00-evidence.md`.

Referencia principal BizHawk: `../MacHawk/`, seleccionada por el usuario e
inspeccionada en HEAD `9b62757833c9bdd15bf43cef058c88bf91eb0ad7`. Su Waterbox
Darwin ARM64 es objetivo inicial, no una fase futura. Ver `docs/07-machawk.md`
y `plan/machawk.reference.json`; los cambios locales de MacHawk no se modifican.

## Empezar aqui

1. Leer `AGENTS.md`, `docs/01-architecture.md` y `docs/07-machawk.md`.
2. Ejecutar `python3 tools/plan.py validate`.
3. Ejecutar `python3 tools/plan.py next` y despues `python3 tools/plan.py show T00`.
4. Ejecutar SOLO una tarea desbloqueada y dejar una vertical pequena funcionando.
5. No interpretar una prueba del plan como una prueba del emulador.

Los comandos anteriores son herramientas de este paquete. Los comandos CMake,
CTest, de escenarios y de Waterbox descritos en las tareas son CONTRATOS A
IMPLEMENTAR; no existen hasta completar su tarea de bootstrap. No crear scripts
que devuelvan exito sin ejecutar las comprobaciones descritas.

## Forma de trabajo: iteraciones rapidas, arquitectura estable

El plan es una guia ejecutable, no un proceso de auditoria. Priorizar ciclos cortos:
implementar algo observable, probarlo, mirar el diff y continuar. Git y los tests
son el registro habitual. `reports/tasks` queda para bloqueos, decisiones que no
se entienden leyendo el cambio, rendimiento y release; no hay informe obligatorio
por cada tarea terminada.

La velocidad no permite romper las fronteras que hacen mantenible el port: WinUAE
permanece importado limpio, los cambios al motor son parches pequenos, p6_run_frame
es comun a todos los frontends y Waterbox/libretro no duplican scheduler. Si una
tarea produce solo documentos durante demasiado tiempo, dividirla o adelantar un
probe ejecutable que valide el riesgo real.

## Documentos normativos

| Documento | Contenido |
| --- | --- |
| `AGENTS.md` | Protocolo de trabajo para una IA con poco contexto |
| `docs/00-evidence.md` | Que se inspecciono, pins y limites de la investigacion |
| `docs/01-architecture.md` | Capas, alcance, perfiles y decisiones cerradas |
| `docs/02-contracts.md` | ABI, ciclo de vida, tiempo, entrada, medios y estados |
| `docs/03-waterbox.md` | Port real, memoria, C#, snapshots y compatibilidad TAS |
| `docs/04-validation.md` | Escenarios, oraculos, CI y presupuestos medibles |
| `docs/05-maintenance.md` | Importacion, parches, codegen, actualizaciones y releases |
| `docs/06-execution.md` | Orden de ejecucion, comandos futuros y escalado de bloqueos |
| `docs/07-machawk.md` | Referencia local, ARM64, registros reservados y paginas de 16 KiB |
| `plan/tasks.json` | Backlog autoritativo, dependencias y criterios por tarea |
| `plan/upstream.lock.json` | Fuentes fijadas y dependencias que aun deben fijarse |
| `plan/machawk.reference.json` | Fingerprint de archivos inspeccionados y cambios locales UAE |

## Decisiones que no debe reabrir cada agente

WinUAE es el upstream del motor; PUAE existente es referencia funcional Y de la
frontera de ejecucion por frame. El port MacHawk observado compila fuentes de
libretro-uae pero BizHawk no actua como frontend libretro: su shim Waterbox llama
directamente a la misma primitive de avance que usa retro_run. PUAE6 conserva esa
idea con una API neutral comun; no copia el arbol viejo ni hace que BizHawk llame
a retro_run. Conservar el arbol upstream sin editar y aplicar una cola pequena de
parches al arbol de build. No reescribir CPU/chipset, no convertir todos los
globales en objetos, no construir un falso Win32 general y no colocar callbacks
en cada acceso a RAM.

Un hilo de emulacion, avance por unidad de video y reloj emulado. Interpretador
primero. JIT, PPC, red y dispositivos fisicos no forman parte del perfil Waterbox
inicial. Libretro ARM64 y el guest Waterbox ARM64 se compilan y prueban por
separado; se reutiliza la ruta Darwin ARM64 existente de MacHawk, con pruebas
reales de x18/x28, paginas 4/16 KiB y restauracion en sandbox nuevo.

Estados nativos: serializacion semantica versionada. Estados Waterbox: snapshot
del sandbox mas estado relevante del wrapper C#. No son intercambiables. No se
promete compatibilidad con estados antiguos de PUAE/WinUAE/BizHawk.

## Hitos del producto

- M0: fuentes verificadas, toolchain y pruebas honestas; ninguna compatibilidad anunciada.
- M1: A500 PAL/NTSC arranca sobre la primitive comun; smoke libretro, headless y
  Waterbox ARM64 convergen, con snapshot real en Waterbox.
- M2: libretro completo para ese perfil, ECS/AGA, estados y replay multiplataforma.
- M3: medios con escritura reversible, HDF y flujo WHDLoad reproducible; corpus y rendimiento.
- M4: ensayo de actualizacion upstream, paquetes y decision de release basada en pruebas.
- Posterior: CD32/CDTV, RTG, FPU/MMU y aceleracion nativa, cada uno con su propia puerta de pruebas.

## Estado observado al iniciar

Codexify no listaba uae, pero acepto el directorio indicado directamente. Estaba
vacio al iniciar la planificacion; no habia AGENTS.md, codigo ni tests. Despues se
inicializo este repositorio Git local para trabajar con cambios pequenos. Aun no
se han clonado/importado fuentes, instalado toolchains ni construido un emulador.

No hay ROMs Kickstart ni juegos comerciales en este paquete. Los fixtures de CI
deben tener licencia/procedencia comprobada. Un escenario que necesita un asset
no disponible queda BLOCKED/SKIP explicito, nunca PASS.
