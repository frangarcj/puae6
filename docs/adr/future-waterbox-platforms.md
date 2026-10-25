# T46: estudio de Waterbox Linux x86_64

Decisión: estudiar el runtime Linux x86_64 de la referencia MacHawk fijada,
con huésped ELF64 little endian x86_64 y páginas host/guest de 4096 bytes.
No se añade un perfil ni se anuncia soporte. Darwin ARM64 sigue siendo el
objetivo inicial implementado; sus puertas y sus páginas host de 16 KiB no
se trasladan a una categoría futura. Este estudio satisface la aceptación
documental de T46; WB01 del candidato permanece **BLOCKED**.

La autoridad es `plan/upstream.lock.json`: source_id `machawk`, commit
`9b62757833c9bdd15bf43cef058c88bf91eb0ad7`, más la captura
`references/machawk.manifest.json`. La captura incluye modificaciones locales
identificadas: el commit por sí solo no describe esos bytes. Los SHA y rutas
relevantes comprobados contra los manifests están en
`plan/extensions/waterbox-platforms.json`. El musl disponible es la captura
`waterbox_musl_darwin`, commit `22440a465d6f7d3241e73bdf990c2b8026022625`;
a pesar de su nombre contiene `arch/waterbox` x86_64 y su setjmp/longjmp.
No hay que descargar una rama móvil ni modificar el proyecto hermano.

## Runtime, loader y ABI

`WaterboxHostPlatform` selecciona `libwaterboxhost.so` en Linux;
`OSTailoredCode.LinuxLLManager` usa dlopen RTLD_NOW/dlsym. El artefacto debe
ser ELF64LE x86_64 y acreditar cierre de bibliotecas, exports C y versiones
antes de cargarlo. El runtime parsea el ELF guest y monta sus segmentos,
pero `Host::new` no comprueba e_machine antes de ejecutar `_start`:
el futuro preflight debe rechazar guest de otra ISA sin activarlo.

El PAL Linux usa memfd_create, mmap de dirección fija, mprotect y SIGSEGV.
La ruta de dirty tracking examina REG_ERR del ucontext x86_64 y conserva
el handler anterior; hay que probar faltas legítimas y rechazos. El código
exige guest de 4 KiB y, fuera de macOS, igualdad con la página host. La
agrupación de cuatro páginas guest en una host de 16 KiB es específica de
macOS. Linux de 16/64 KiB queda bloqueado; no basta cambiar una constante.

El interop x86_64 fija su tabla en 0x35f00000000 y el linkscript del guest
parte de 0x36f00000000. Emite thunks ejecutables y reserva 64 callbacks.
Usa SysV64; el adaptador managed Unix usa la convención nativa, mientras
que el adaptador MS/SysV corresponde a Windows. TLS usa GS, arch_prctl y
Context en GS:0x18. El mini-oráculo debe verificar alineación de pila,
registros SysV preservados, TLS/errno, entradas/salidas, callback y syscall,
reentrada controlada y restauración de GS. Los registros ARM x18/x28 y
su overlay setjmp no son una receta x86_64. Los thunks de interop tampoco
autorizan habilitar el JIT del emulador.

## Sysroot y snapshot

`common.mak` tiene selección x86_64, linker estático y linkscript específico;
`arch/waterbox` llama al syscall bridge, no al kernel Linux directamente.
La captura incluye el parche de atomics x86_64. Falta autenticar una receta
x86_64 completa: compilador, linker, archiver, NASM, Rust/target, dependencias
Cargo, cabeceras, libc/libc++/builtins y el inventario del sysroot resultante.
`nightly-2026-08-01` en la fuente no fija los bytes del compilador. Ni el
sysroot AArch64 derivado actual ni el Rust ARM64 instalado cubren esta fila.

El snapshot serializa FS, program_break, ELF, páginas y hilos. Linux protege
RWStack para dirty tracking; macOS tiene otra política. El interop guarda
una dirección de retorno host en pila guest: esto exige el oráculo, no
justifica descartar bytes. Hay que conservar la política de eviction
explícita, estados completos exactos y restauración en la misma instancia,
otra instancia y otro proceso. No se promete compatibilidad entre builds
ni entre arquitecturas. El hash mismatch de MemoryBlock sólo se imprime
en la referencia; WB11 requiere rechazo íntegro antes de mutar el core.

## Primer recorrido comprobable

Se observaron, sólo como datos, `../MacHawk/Assets/dll/libwaterboxhost.so`
y su copia en output: ELF64LE x86_64 ET_DYN, 682848 bytes, SHA
`d1d2a05cb64cb68cf3c56afe53e1ed9ec6c1a7516a95907e769b8779097299db`.
Existen outputs ViceSmoke Linux y un guest Hatari x86_64 locales. No hay
recibos que los vinculen a esta receta/captura ni un resultado WB01 de
PUAE6 acreditado. No se ejecutaron; no son sustitutos del mini-core.
El smoke/Makefile capturado construye Darwin AArch64. Las filas Docker
nativas existentes tampoco acreditan un runner Waterbox Linux.

Primero se debe registrar una tarea de procedencia y receta aislada, con
runner Linux x86_64 real de 4 KiB autenticado. Después WB01 debe cargar el
mini-core público por la API C real, inicializar/sellar/avanzar y demostrar
ABI, snapshot y dos sandboxes. Un ELF malformado/ISA distinta, falta de
exports, fallo de mapeo o recibo alterado debe fallar sin PASS. Sólo tras
WB01 se porta el core con el scheduler p6_run_frame y se ejecutan WB02–WB12
con los oráculos originales de docs/03-waterbox.md. El subbacklog del JSON
expresa ese orden y las puertas bloqueadas; no son tareas ya ejecutadas.
No se instalaron herramientas, compilaron imágenes ni cambiaron perfiles,
runtime o MacHawk durante el estudio.
