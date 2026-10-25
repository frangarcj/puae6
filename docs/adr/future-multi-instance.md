# T47. Dos maquinas alternadas y contextos nativos

## Decision

Posponer dos contextos del motor dentro de una misma imagen nativa. Para el caso
de uso definido aqui, adoptar la frontera existente de un proceso por maquina o
un sandbox Waterbox por maquina. Esto elige una arquitectura; no añade un gestor
de procesos ni acredita un escenario nuevo. Se conservan la ABI, los perfiles y
el rechazo de reentradas. El subbacklog de `plan/extensions/multi-instance.json`
es propuesto, no son tareas implementadas ni comandos disponibles.

Fuente examinada: WinUAE 6.0.3, tag6030, commit
`857f48fcb09399d4d95527e29a2fdeaedb41f827`, inventariado en
`manifests/upstream-files.json`, mas bridge, puertos y parches actuales del port.
No se modifica ni se toma un checkout distinto como autoridad.

## Caso que justifica la comparacion

Un editor de replay mantiene A como linea principal y B como rama especulativa.
Ambas cargan el mismo build, firmware y medios inmutables; B parte del estado de
A en una frontera publica. Se alternan A1/B1/A2/B2, con entradas diferentes;
se restaura A a una frontera anterior mientras B permanece detenida, se reproduce
la rama y se descarta B sin persistir sus escrituras. Como control adicional, dos
maquinas con configuraciones y medios distintos detectan contaminacion entre
CPU, reloj, callbacks y caches. No hay ejecucion de hardware simultanea ni red.

Para cada maquina, la referencia es su ejecucion aislada con los mismos paquetes
de entrada: CPU/chipset/RAM, video, PCM, ticks, eventos y overlays deben coincidir
en cada frontera. Una operacion sobre A no puede cambiar estado ni futuro de B.
No basta comparar la imagen final, compartir un resultado historico o cargar A
y B sucesivamente: los dos propietarios deben seguir vivos durante la alternancia.

| Alternativa | Aislamiento y coste a comprobar | Decision |
| --- | --- | --- |
| Dos procesos nativos | Cada proceso tiene sus globales/libc; hacen falta protocolo por unidades, copias A/V y transferencia de estados/medios. Medir arranque, memoria y coste IPC, sin reloj host dentro del motor. | Adoptar como primera opcion nativa del futuro probe. No existe aun un coordinador publico. |
| Dos sandboxes Waterbox en un proceso | Un guest por sandbox, heaps/TLS/contexto propios; activacion serial y rebinding de callbacks. Medir mapeo, snapshots y memoria de ambos. | Reutilizar la frontera existente, con pruebas ampliadas del caso. No equivale a dos instancias de la biblioteca nativa. |
| Dos imagenes de biblioteca nativa | Cargar dos veces la misma ruta puede devolver el mismo modulo; copiar el archivo no demuestra aislamiento de simbolos, dependencias o callbacks. Darwin y ELF no tienen el mismo contrato de carga. | Posponer; exigir prueba de loader y cierre de dependencias antes de considerarla alternativa portable. |
| Dos contextos explicitos en una imagen | ABI con handle y ownership por llamada, motor, tablas, bancos, caches, TLS, allocators y adaptadores. Evitar que un puntero al contexto seleccionado sobreviva al retorno. | Posponer: el caso no demuestra un beneficio que compense aun el delta upstream. No adoptar un cambio global de contexto mediante memcpy. |

## Inventario causal inicial

Este inventario identifica propietarios reales; no cuenta todos los globales ni
pretende que el inventario de estados sea un mecanismo de cambio de contexto.
Un campo reconstruible despues de restore puede seguir necesitando ownership
para que dos maquinas vivas no se lo sobrescriban.

| Familia y fuentes | Riesgo para una sola imagen con dos maquinas |
| --- | --- |
| `bridge/session.h`, `bridge/lifecycle.cpp` | Una `Session session`, `stage`, referencia a config y hook de reinicio. `Guard` usa un bool para rechazar una llamada anidada con BUSY; no es un mutex. Un segundo init secuencial devuelve BAD_STATE, no crea un handle nuevo. |
| `newcpu.cpp`, `include/newcpu.h`, `include/options.h` | `regs`, prefs y tablas `cpufunctbl*` globales; handlers generados y funciones de acceso dependen de ellos. Separar tablas inmutables probadas de seleccion/punteros por CPU. Cambiar emisores, nunca outputs a mano. |
| `custom.cpp`, `events.cpp`, `memory.cpp`, `audio.cpp` | Tablas de eventos y handlers, fase del haz, bancos/baseaddr, filtros y buffers. Incluir CIA, disco/IDE y CD: no convertir solo registros CPU y RAM. |
| `bridge/state.cpp`, `manifests/state-inventory.json` | `g_context` temporal de Validate/Apply y planes de medios; separar scratch del propietario persistente. Una carga rechazada no puede tocar a la otra maquina. |
| `ports/portable/blob_io.c`, `rtc_time.cpp`, `pcm_output.cpp`, `video_present.cpp`, `sound_lifecycle.cpp`, `drive_sound.cpp` | Blobs/overlays, reloj, PCM, raster y estado mecanico tienen almacenamiento compartido por imagen. Los buffers prestados y callbacks necesitan vida util y dueno explicitos. |
| `ports/portable/alloc.h`, emulibc y compiler-rt emutls capturados | malloc/free de libc, errno, pthread keys y TLS de excepciones no se vuelven por-instancia al pasar un handle. Auditar constructor/destructor, fallo parcial y liberacion cruzada; un heap nuevo por contexto no es un requisito asumido. |
| `adapters/libretro/libretro_core.cpp`, `adapters/waterbox/puae6_guest.cpp`, `cd_source.cpp` | Sesion libretro, bandeja, mapper/OSD, bool loaded y catalogos guest. Cambiar solo el bridge no vuelve multiinstancia a libretro ni a su ABI sin handle. |
| Runtime Waterbox `src/context`, `integration/bizhawk/smoke/Native.cs` | Activacion/Context y TLS guest dependen del sandbox. En ARM64 preservar x18/x28 y el TPIDR host; no usar TLS nativo por hilo como sustituto de dos contextos alternados en ese mismo hilo. |

Los snapshots nativos semanticos y Waterbox no se intercambian. No se trasladan
punteros host con el estado. Firmware/medios pueden compartirse solo como bytes
inmutables con ownership documentado; overlays y persistencia quedan separados.
No se mueve estado a alloc_invisible para abaratar el segundo propietario.

## Coste y umbral para reconsiderar

Procesos y sandboxes conservan el motor sin conversion masiva y ya tienen fronteras
de aislamiento. Sus costes de memoria, IPC/activacion y snapshot deben medirse en
el caso A/B antes de decidir que son insuficientes; aqui no hay cifras medidas.
Contextos explicitos afectan todos los accesos globales transitivos, generador,
imports, serializacion y adaptadores. El mantenimiento debe medir archivos/hunks
tocados, conflictos y regeneracion al actualizar el pin, ademas del rendimiento.
Un puntero global al contexto actual seria seleccion serial, no reentrancia, y
tambien exige demostrar que callbacks, caches y TLS no lo retienen.

Reabrir la decision solo si MI-D demuestra un presupuesto concreto incumplido
por las dos fronteras de aislamiento y un probe pequeno identifica la ventaja
del contexto explicito sin violar determinismo ni generar un delta inmantenible.
No se presupone que eliminar BUSY, añadir hilos o duplicar el scheduler resuelva
el problema. Cada maquina continua exclusivamente mediante `p6_run_frame`.

## Pruebas existentes y puerta futura

`tests/state/test_probe.py` compara procesos y recargas; no prueba dos propietarios
nativos vivos. `tests/waterbox-state/test_wrapper_state.py` ya contiene restore a
segundo/tercer sandbox y turnos, rechazos y proceso nuevo; no acredita contextos
nativos. Estas rutas son controles reutilizables, sin afirmar una ejecucion nueva.

MI-A..MI-E fijan oraculos de alternancia, fallo de carga, restore, caches y coste.
Una futura implementacion exige DET02/STATE01/LIFE01, WB03 para sandboxes y las
regresiones de ABI, sanitizer, presupuestos y actualizacion correspondientes.
Mientras solo exista este estudio, ninguna puerta runtime nueva queda satisfecha.
