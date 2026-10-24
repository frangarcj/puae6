# 01. Arquitectura y alcance

## Decision principal

Construir una biblioteca de emulacion compartida por tres targets: headless,
libretro y Waterbox. La primitive de ciclo de vida/configuracion/avance por frame
es comun y neutral al frontend. No convertir BizHawk en un frontend libretro ni
duplicar el control del scheduler en un adaptador Waterbox independiente.
No actualizar el PUAE viejo reemplazando archivos hasta que enlace. Se mantiene
la semantica WinUAE, portando solo deltas probados y aislando su dependencia del
host. S1-S8: docs/00-sources.md.

Dependencias permitidas, de arriba hacia abajo:

    RetroArch -> adapter/libretro ---+
                                    |
    CLI tests -> adapter/headless --+--> bridge/frame_step -> WinUAE + host port
                                    |
    BizHawk C# -> adapter/waterbox --+

El adaptador Waterbox se compila DENTRO del WBX. C# se comunica con el ABI del
sandbox, no llama retro_run ni carga la biblioteca libretro nativa. Los tres
adaptadores llaman la misma API p6_* y, en particular, la misma p6_run_frame.
Usan el mismo manifiesto de fuentes, parches y configuracion semantica; sus flags
de enlace y responsabilidades de presentacion son diferentes.

Esto no es una preferencia teorica. El MacHawk inspeccionado compila partes de
libretro-uae dentro de uae.wbx, pero bizhawk.c evita la ABI retro_* y llama
directamente a umain/m68k_go. A la vez, retro_run del core antiguo usa el mismo
m68k_go modificado para devolver control cuando unlockscr marca frame_end. PUAE6
debe conservar la primitive probada y eliminar el acoplamiento accidental: el
motor/bridge no incluye libretro.h y Waterbox no necesita compilar el adaptador
libretro completo.

El checkout principal de integracion es ../MacHawk, no master publico BizHawk.
Su ruta Waterbox Darwin ARM64 se audito como referencia; docs/07-machawk.md fija
los invariantes x18/x28, ELF AArch64 y paginas 4/16 KiB. Se prueba desde M0/M1.

## Estructura futura (no codigo ya existente)

    upstream/winuae/              snapshot limpio y de solo lectura para el agente
    references/puae/              referencia fijada, nunca fuente del nuevo motor
    references/bizhawk/           checkout fijado para prueba/integracion local
    patches/winuae/series.json    cola ordenada, una razon por parche
    patches/winuae/*.patch
    ports/portable/               superficie host comun, estrecha
    ports/native/                 acceso host autorizado por libretro/headless
    ports/waterbox/               memoria/VFS/relojes compatibles con sandbox
    bridge/                      ciclo de vida, prefs, frame_step neutral, API C
    adapters/libretro/           solo API libretro y UX
    adapters/headless/           runner de escenarios y hashes
    adapters/waterbox/           exportaciones WBX, sin tipos libretro
    integration/bizhawk/         cambios C# y receta WBX versionados
    config/                     esquema comun y perfiles
    manifests/                  fuentes y funciones host por perfil
    tools/                      importacion, codegen, checks y benchmarks
    tests/                      fixtures legales, contratos, integracion, regresiones
    build/                      materializacion parcheada; nunca fuente de verdad

## Tratamiento de upstream

Guardar el commit completo o un snapshot fiel con manifest de hashes. Usar una
unica estrategia (decidida: snapshot fiel/subtree importado, no submodulo editable
mas otra copia divergente). Los archivos no seleccionados no se compilan, pero
se conservan para procedencia/actualizaciones. Un manifiesto explicito selecciona
CPU, chipset y dispositivos. Nada de glob recursivo de todos los cpp.

La compilacion materializa una copia derivada en build/upstream y aplica parches
exactos, sin fuzz ni arreglos manuales dentro de build. Los generadores emiten en
build/generated. Las fuentes generadas correspondientes del snapshot no se
compilan simultaneamente. El generador es la autoridad de su familia de archivos.

Antes de crear shims, auditar od-unix del upstream actual a un SHA concreto. Tomar
solo implementaciones host compatibles cuya procedencia quede documentada. Si
requieren UI, hilos o cambios semanticos, no entran por conveniencia. La base sigue
siendo 6.0.3 hasta una actualizacion explicita y probada.

## Superficie host

Separar logging, configuracion, medios, asignacion de memoria, tiempo y entrega
A/V. Una pequena capa adapta estas operaciones a nativo o Waterbox.
No se virtualizan las operaciones del hardware emulado. CPU, blitter, copper,
Paula, CIA, RAM y DMA conservan accesos internos directos y las tablas de WinUAE.

Las dependencias de interfaz Windows/SDL/Qt, audio del SO, dispositivos fisicos,
red y plugins dinamicos no se enlazan en los perfiles iniciales. Si un simbolo
host es necesario, debe tener ficha: llamadores, semantica, backend y prueba. Una
funcion de dispositivo excluido no puede fingir que funciono: error not-supported.

Tipos Win32, TCHAR, HANDLE, LP64/LLP64, intrinsics, atomicos y rutas se resuelven
en la frontera mas estrecha posible. No crear un windows.h universal de mentirijillas.
Usar wrappers locales y adaptar los includes con parches pequenos. No reemplazar
masivamente los tipos/nombres del motor; preservar diffs upstream legibles.

## Instancias y concurrencia

Primera version: una instancia activa del motor por imagen nativa cargada. La API
rechaza otra con BUSY y no finge soportar multiples contextos. Waterbox aloja una
por sandbox; se prueba aislamiento con dos sandboxes alternados.
No transformar miles de globales en una clase. Inventariar, inicializar y restaurar
los globales que importan. Multiples instancias nativas son una fase independiente.

Un solo hilo ejecuta hardware. Callbacks de host no reentran. Sin hilos de audio,
renderizado, filesystem o CPU en el perfil determinista. El frontend decide cuando
llamar al siguiente frame; no se duerme dentro del motor.

## Avance por frame: referencia probada, port minimo a demostrar en 6.0.3

La referencia PUAE/MacHawk ya demuestra un patron concreto: unlockscr marca el
fin de frame y pone libretro_frame_end y el bit 0 de spcflags (docs/adr/0005 corrige la
atribucion de SPCFLAG_CHECK al fork MacHawk); el m68k_go modificado observa la peticion,
retorna al caller y en la llamada siguiente reanuda el scheduler. retro_run usa
esa ruta y el shim BizHawk la invoca directamente. Esto reduce el espacio de
diseno, pero NO demuestra que el mismo parche aplique sin cambios a WinUAE 6.0.3.

T07 compara ese delta fijado con el scheduler real de 6.0.3, porta la semantica
minima con nombres neutrales al frontend y demuestra donde esta el punto seguro,
que estado sobrevive y cual es el costo. p6_run_frame no reinicia la CPU ni
reimplementa retro_run. No usar longjmp desde vsync, excepciones para control de
flujo o corte a mitad de una operacion critica.

Solo si una diferencia concreta de 6.0.3 impide ese retorno pequeno y correcto,
T07 puede abrir la alternativa de corutina COOPERATIVA con pila propia soportada
por Waterbox. Debe documentar primero el fallo del mecanismo de referencia. No es
un hilo del SO; su pila/continuacion y ausencia de punteros host se prueban dentro
del smoke Waterbox antes de aceptarla. No elegir libco por costumbre ni
refactorizar toda la CPU para ganar pureza arquitectonica.

## Perfiles cerrados

| Perfil | Uso | Restricciones |
| --- | --- | --- |
| deterministic | Headless, libretro TAS, Waterbox | Interpreter; reloj emulado; un hilo; medios fijados; sin red; sin JIT; sin filesystem host |
| portable | Uso normal libretro | Mismo motor inicialmente; UX y presentacion adicionales no cambian ciclos |
| accelerated | Futuro nativo | JIT/optimizaciones con contrato separado; nunca anunciado como TAS por defecto |

Cualquier ajuste que cambie lo que ve la maquina forma parte de sync config:
CPU/chipset, RAM, ROM, PAL/NTSC, precision/caches, velocidad de disquete, RTC,
raton/autofire, drives y medios. El UI genera esta configuracion desde UN esquema.
Crop, overlays y volumen de salida son settings de presentacion, salvo que una
implementacion concreta demuestre influencia sobre el hardware y se reclasifique.
No renunciar a timing correcto para mejorar un benchmark sin cambiar perfil/ID.

## Alcance funcional

M1: A500/68000, OCS, PAL y NTSC, Chip/Slow RAM, Kickstart del usuario, ADF de solo
lectura, teclado, joystick y raton. Audio Paula stereo de salida, video software,
reset, avance exacto y snapshot Waterbox. No basta con una ventana negra.

M2: core libretro completo para ese perfil; A600/ECS (hecho en T23: segundo preset del mismo perfil, 1 MiB Chip, sin Gayle; tabla en docs/02-contracts.md) y A1200/68020/AGA (hecho en T24: tercer preset del mismo perfil, 68EC020 ciclo exacto, AGA, 2 MiB Chip, sin Gayle; tabla en docs/02-contracts.md, ADR 0010 para el 68020); RAM Fast
limitada por perfil (T49); estados nativos; replay en plataformas Tier 1. Sin FPU/MMU
ni aceleradoras en estos perfiles. El detalle exacto de clocks/caches se deriva
solo del preset upstream auditado, no de un valor inventado.

M3: escritura ADF reversible mediante overlay, multiples discos, HDF determinista
y WHDLoad usando un conjunto instalado reproducible. Para TAS no se monta una
carpeta del host ni se toman timestamps de archivos como reloj Amiga.
IPF/CHD/archivos comprimidos/CD-ROM requieren backend, licencia y tests propios;
no son requisitos ocultos para M1. CD32/CDTV/RTG y FPU/MMU se planifican tras M4.

## No objetivos

Paridad con toda la UI WinUAE; compatibilidad binaria con estados antiguos;
compatibilidad de peliculas entre cambios semanticos; todos los dispositivos
WinUAE; reescribir el runtime ARM64 ya existente en MacHawk; reemplazar silenciosamente UAE en
BizHawk; eliminar globales por estetica; optimizar antes de disponer de corpus.

## Riesgos que pueden bloquear el avance

R1 retorno por frame inseguro -> T07 y T09 antes de ampliar features.
R2 dependencias Windows ocultas -> manifiesto host y enlace estricto, no stubs exitosos.
R3 memoria fuera del snapshot -> inventario, veneno de caches y restore en instancia nueva.
R4 A/V dependiente de floats/host -> comparar builds; limitar promesas hasta resolver.
R5 parches grandes -> presupuesto, generadores reproducibles y ensayo de update.
R6 ruta MacHawk ARM64 aun no probada con PUAE6 -> WB01/WB13-WB15 reales; no marcar green por cross-build.
R7 escritura de medios fuera de rollback -> overlays dentro del estado y flush explicitamente separado.
R8 sobreampliacion -> no iniciar aceleradoras ni comodidades antes del gate M1.
