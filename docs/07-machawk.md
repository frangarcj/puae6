# 07. Referencia principal: ../MacHawk y Waterbox Darwin ARM64

Actualizacion solicitada por el usuario durante la planificacion: usar
`../MacHawk/` como referencia BizHawk. Este documento y sus tareas actualizadas
sustituyen cualquier supuesto anterior de empezar Waterbox solo en x86_64.
El arbol BizHawk publico consultado sigue siendo referencia secundaria, no el
checkout de integracion principal.

## Inspeccion realizada, no ejecucion de emuladores

Se leyeron archivos de codigo/documentacion y estado Git en ../MacHawk, sin
modificarlos. No se ejecutaron builds, harnesses, ROMs ni juegos. No se leyeron,
copiaron ni calcularon hashes de assets comerciales presentes en ese directorio.

MacHawk HEAD observado: `9b62757833c9bdd15bf43cef058c88bf91eb0ad7`.
El origin configurado es TASEmulators/BizHawk, pero NO se verifico que dicho commit
local pueda descargarse desde ese remoto. No sustituir el checkout por un clone
de master ni asumir que el nombre del remoto reproduce sus cambios.

Submodulo UAE en `waterbox/uae/libretro-uae`:
`ccecb1ead642c1bbe391308b88a7ffa9478b918d`, con cambios locales en
ocho fuentes: akiko.c, blkdev_cdimage.c, cd32_fmv.c, expansion.c, filesys.c,
hardfile.c, include/cd32_fmv.h y zfile_archive.c bajo sources/src.
Son cambios del usuario y se conservan. No reset, checkout, clean, stash automatico
ni commit de esos cambios. Leer una referencia sucia no autoriza modificarla.

Musl local: `2063abc4e16c84218757b1db10d3cdf9f36ef3f8`, sin cambios reportados en
la inspeccion. La documentacion DARWIN_ARM64 menciona otro pin para un guest
Hatari: no usar ese ejemplo como sustituto automatico de la receta real de PUAE6.
Rust esta fijado por `waterbox/waterboxhost/rust-toolchain.toml` a
`nightly-2026-08-01`; no se verifico su instalacion/disponibilidad en esta sesion.

`plan/machawk.reference.json` guarda HEADs, SHA-256 de trece archivos seleccionados,
SHA-256 de cada fuente UAE modificada y del diff de ese submodulo. El fingerprint
es una referencia de inspeccion, NO un lock completo de todos los inputs de build.
T01 debe capturar los inputs exactos seleccionados del runtime/toolchain, con
procedencia y sin ROMs, para producir builds reproducibles.

## Archivos que debe leer el implementador

- waterbox/waterboxhost/DARWIN_ARM64.md: invariantes ARM64/16 KiB y packaging.
- waterbox/common.mak: selector WBX_TARGET_ARCH y flags target.
- waterbox/linkscript_aarch64.T: layout ELF especifico del guest.
- waterbox/waterboxhost/src/context/interop_aarch64.s: contexto/TLS x28.
- waterbox/waterboxhost/src/context/thunks.rs: rutas de interop del host.
- waterbox/waterboxhost/src/memory_block/pal.rs y pageblock.rs: mapeos y alineacion.
- waterbox/uae/Makefile y bizhawk.c: integracion UAE actual, no nuevo motor.
- waterbox/uae/libretro-uae/libretro/libretro-core.c y libretro-glue.c: frontend
  antiguo y punto donde se solicita el fin de frame.
- waterbox/uae/libretro-uae/sources/src/newcpu.c: m68k_go(may_quit,resume) usado
  tanto por retro_run como por el shim BizHawk.
- src/BizHawk.Emulation.Cores/Computers/Amiga/UAE.cs: wrapper y estado extra.
- src/BizHawk.Emulation.Cores/Waterbox/WaterboxCore.cs: ABI/ciclo de vida del host.
- waterbox/waterboxhost/uae-cd32-headless/Program.cs y whdload-regression.sh:
  infraestructura de prueba local que se debe auditar antes de reutilizar.

Las rutas de esta lista son RELATIVAS A MacHawk. No escribirlas como si existieran
bajo uae. Crear adaptaciones y recetas en integration/bizhawk dentro de uae; usar
copias de build/output separadas. No sobrescribir Assets/dll/uae.wbx.zst ni output
del checkout local con scripts de instalacion automaticos.

## Relacion libretro / Waterbox comprobada localmente

El Makefile observado define CORE_DIR=libretro-uae, EMU=sources/src y
LIBRETRO=libretro, compila libretro-core.c, libretro-glue.c y otros helpers con
__LIBRETRO__ y WATERBOX. Sin embargo, BizHawk no usa retro_run como su ABI.
bizhawk.c exporta Init/FrameAdvance/GetMemoryAreas; Init prepara callbacks, llama
umain y hace el primer m68k_go(1,0), mientras FrameAdvance aplica input/eventos y
reanuda con m68k_go(1,1).

La salida por frame del arbol fijado esta en el camino libretro: unlockscr guarda
metadatos de dibujo, pone libretro_frame_end=1 y activa set_special(1); el fork MacHawk activa
SPCFLAG_CHECK (docs/adr/0005). newcpu.c
comprueba ese flag despues del run_func, lo limpia y retorna sin reiniciar la CPU.
retro_run usa esa misma reanudacion. Por tanto el port viejo prueba una primitive
de continuacion util, no que Waterbox deba convertirse en frontend libretro.

Los SHA de Makefile, bizhawk.c y UAE.cs reconsultados durante esta revision siguen
coincidiendo con plan/machawk.reference.json. La conclusion anterior no depende
de los nuevos archivos no rastreados observados en MacHawk y no se modifico el
checkout hermano.

## Invariantes obligatorios del guest Darwin ARM64

El common.mak inspeccionado tiene WBX_TARGET_ARCH por defecto x86_64. Para el
nuevo target local elegir EXPLICITAMENTE aarch64, no confiar en que uname lo haga.
Su rama AArch64 usa `--target=aarch64-linux-gnu -ffixed-x18 -ffixed-x28`, builtins
AArch64 y linkscript_aarch64.T. El host es Darwin; el guest sigue siendo ELF de la
toolchain Waterbox, no una dylib Mach-O renombrada.

x18 esta reservado para Darwin. x28 transporta Context/TLS del sandbox. Reservar
ambos registros en TODA unidad de codigo del guest, incluidas dependencias y
codigo generado compilado para target. Los generadores ejecutados en el host no
heredan estos flags por accidente. Auditar assembly, intrinsics y posibles
corutinas por separado: un archivo ensamblador no respeta -ffixed-x28 magicamente.
TPIDR_EL0 es un espejo de compatibilidad; no introducir dependencias nuevas que
omitan el contexto x28.

Waterbox conserva paginas logicas de 4 KiB y su formato; Darwin agrupa cuatro en
paginas fisicas de 16 KiB segun el contrato local. Build guest de esta ruta:
WBX_HOST_PAGE_SIZE=16384, layout compatible y protecciones coherentes por grupo.
No usar el default 4096 de common.mak sin fijarlo. El runtime debe consultar/validar
el tamaño real del host y fallar ante un caso no soportado, no asumir silenciosamente.

No mezclar dentro del mismo grupo fisico datos sealed/invisible/mutables con
permisos incompatibles. Revisar alineacion de heaps, ELF, mmap y protecciones.
El costo de dirty tracking no es necesariamente de una sola pagina logica: una
escritura puede afectar el grupo completo de cuatro. Medir costo por pagina fisica
y logica de forma separada o dejar claro cual se esta contando.

El runtime documenta thunks ejecutables con MAP_JIT, alternancia de proteccion
mediante pthread_jit_write_protect_np e invalidacion de icache. Estos thunks de
interop son infraestructura WaterboxHost; NO significan habilitar el JIT de CPU
Amiga. PUAE6 deterministic sigue usando interpreter. No quitar las restricciones
de memoria ejecutable del host para hacer pasar el build.

## Build y empaquetado locales

Usar la receta ARM64 local y sus parches musl, no la receta x86_64 upstream sin
ajustes. Auditar versiones de Clang/LLVM/libc++/musl/Rust y hashes de sysroot; la
mencion LLVM 18.1.8 en la documentacion es contexto de regresiones existentes, no
prueba de que todas las dependencias PUAE6 ya esten resueltas.

El runtime local documenta `libwaterboxhost.dylib` junto al ejecutable managed y
los guests bajo el directorio dll esperado por PreInit. Preservar ese contrato en
un paquete aislado PUAE6. No depender de un fallback de paths ni de un WBX antiguo
que exista en el checkout. Registrar el hash del archivo REALMENTE cargado.

Las herramientas run-macos-smoke.sh y cargo test ya existen en MacHawk. La primera
puede descargar dependencias/crear outputs y hacer builds amplios; NO se ejecuta
automaticamente como parte de este plan. T00 inventaria Docker, Xcode/Clang,
Rust/Cargo, .NET y caches, y T09 ejecuta la ruta minima apropiada con inputs y
outputs autorizados. No instalar ni actualizar globalmente por iniciativa propia.

## Aprovechar el wrapper y harness existentes sin sobreafirmar

UAE.cs local contiene soporte mas amplio que el extracto upstream inicialmente
consultado: opciones CD32/CDTV, HDF/WHDLoad y hooks de estado extra. En torno a
las lineas 642-679 guarda edges de botones, drive/slot, cuatro slots y disco CD;
al restaurar reconfigura imagen/volumen. Son ejemplos que deben revisarse para
PUAE6, no una razon para reusar identidad o formato de estado del core viejo.

El harness uae-cd32-headless admite STATE_TEST, STATE_SAVE y STATE_LOAD. La ruta
inspeccionada de STATE_TEST compara firmas de video durante 60 frames. Tambien
recoge audio, pero ese fragmento no demuestra comparacion exacta del PCM ni de
scheduler/registros/RAM/overlay. No equivale por si solo a DET02/WB03 completos.

La nueva adaptacion debe fallar con exit code no cero ante mismatch o cobertura
incompleta. No basta con imprimir MISMATCH. Añadir hashes canonicos hardware/PCM,
restauracion entre procesos/sandboxes, media IDs y caso corrupto. Reutilizar la
infraestructura de lanzamiento y captura, no heredar sin auditoria un criterio
de exito basado solo en video. No se ha ejecutado ese harness en esta sesion.

## Secuencia revisada

T00: inventario local MacHawk ARM64, herramientas/caches y permisos; no partir de
un hipotetico runner Linux obligatorio para la primera prueba.
T01: fijar snapshot de referencia local y cambios relevantes sin tocar MacHawk.
T02: auditar WinUAE-host y MacHawk-host/guest ABI, y clasificar el delta PUAE
frame-control frente a glue frontend-only.
T09: mini-core real en Waterbox Darwin ARM64, con los registros y paginas anteriores;
probar save/load y nueva instancia antes de ampliar emulacion.
T07: portar primero la semantica frame_end/m68k_go fijada a una p6_run_frame
neutral; corutina solo ante incompatibilidad demostrada en WinUAE 6.0.3.
T16: smoke libretro M1 sobre la misma p6_run_frame, sin scheduler alternativo.
T19: A500 WinUAE6 real dentro del guest ARM64 y libwaterboxhost.dylib local,
sin llamar retro_run ni enlazar el adaptador libretro.
T20-T22: wrapper PUAE6 independiente y pruebas de pelicula/snapshots basadas en
la infraestructura MacHawk auditada.
T25/T39: añadir runtime Linux/Windows x86_64 como matriz separada para portabilidad.
T36: optimizar dirty pages sin violar granularidad de 16 KiB ni historia causal.
T46: queda para OTROS runtimes/futuros forks, no para aplazar MacHawk ARM64.

## Gates adicionales ARM64

WB13: compilar y ejecutar probe que cruza callbacks preservando x18/x28/TLS;
verificar tambien las unidades assembly y dependencias del guest.
WB14: escrituras en los cuatro offsets de un grupo 16 KiB, snapshot, mutacion,
restore y comparacion; caso negativo de protecciones incompatibles y limites.
WB15: cargar el guest ARM64 correcto con dylib/managed runtime registrados y
restaurar en sandbox/proceso nuevo; una carga accidental x86_64 o del UAE anterior
es fallo, no fallback.

WB13-WB15 se añaden a WB01, WB03, WB04 y WB07, no los sustituyen. Que este codigo
exista demuestra una ruta a probar, NO que PUAE6 este ya portado ni que estos tests
hayan pasado. La primera prueba real sigue siendo una dependencia obligatoria.

## Referencia adicional: alternativa futura a CAPSImg para IPF

Indicada por el usuario: [frangarcj/BizHawk#1](https://github.com/frangarcj/BizHawk/issues/1),
"UAE: replace capsimg (non-commercial SPS licence) with a port of MAME's BSD
IPF reader". Leida mediante `gh issue view` el 2026-09-26: abierta, sin
comentarios, actualizada el 2026-09-05. Se registra como propuesta del fork,
no como implementacion ni prueba de compatibilidad.

La incidencia propone el lector `src/lib/formats/ipf_dsk.cpp` / `ipf_dsk.h`
de MAME, descrito alli como BSD-3-Clause. Antes de importarlo hay que fijar
commit, verificar avisos/dependencias y desacoplar `floppy_image`. La adaptacion
debe entregar MFM y timing a la capa de disquete; comprobar revisiones de
encoder, gaps, weak bits y protecciones con corpus de procedencia autorizada.
La equivalencia con CAPSImg no esta demostrada. No se han descargado imagenes
de disco ni se ha verificado aqui la licencia del corpus sugerido por la issue.

Aplicacion al plan: T02 mantiene CAPSImg fuera de la seleccion inicial y T12
conserva su alcance de medios soportados. IPF necesita una tarea propia de
backend/licencia/estado/corpus antes de habilitarse. El decoder y su estado
causal deben vivir dentro del guest y del snapshot; no usar una DLL host como
atajo. Probar save/load y restauracion en sandbox nuevo, ademas de lecturas.

La observacion de la issue sobre retirar libc++ al eliminar CAPSImg se refiere
al UAE antiguo de ese fork. PUAE6 parte de WinUAE6 en C++: T02/T09 deben derivar
sus dependencias reales, sin eliminar libc++ por analogia. La referencia local
CAPSImg ya capturada conserva sus avisos y su exclusion de los paquetes hasta
la revision correspondiente; citar una alternativa no cambia sus condiciones.
