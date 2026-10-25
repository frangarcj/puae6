# T46: estudio de Waterbox Linux x86_64

Decisión: estudiar el runtime Linux x86_64 de la referencia MacHawk fijada,
con huésped ELF64 little endian x86_64 y páginas host/guest de 4096 bytes.
No se añade un perfil ni se anuncia soporte. Darwin ARM64 sigue siendo el
objetivo inicial implementado; sus puertas y sus páginas host de 16 KiB no
se trasladan a una categoría futura. T46 cierra la auditoría; T46a1/T46a2
completan **WB01 del mini-core C** en Linux nativo. WB02–WB12 y la integración
Amiga/managed de esa fila permanecen **BLOCKED**.

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
La captura incluye el parche de atomics x86_64. La receta
`tools/ci/linux_waterbox_probe.py` fija compilador, linker, archiver, Rust/target,
stdlib y Cargo.lock en `linux_wb01_inputs` del JSON; construye runtime y sysroot
musl C propios. Usa el interop.bin capturado y verificado, sin instalar NASM.
La fila Amiga futura necesita además su closure C++/builtins y pruebas propias.
`nightly-2026-08-01` en la fuente no fija los bytes del compilador; la receta usa
los binarios Rust preinstalados autenticados, sin pasar por el shim rustup.

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
recibos que vinculen esos binarios a la receta; no se ejecutaron.
El smoke/Makefile capturado es Darwin AArch64 y no sustituye este mini-core.

La [ejecución Linux nativa de WB01](https://github.com/frangarcj/puae6/actions/runs/40178369117)
carga el runtime construido desde las capturas y un ELF x86_64 propio por la
API C real. Inicializa/sella, comprueba SysV/GS, TLS, callback y syscall, límites,
dirty tracking de 4 KiB y restauración completa. Quince pasos y 23 snapshots
comparan contador, RAM, ROM, vídeo/PCM y futuro entre dos sandboxes vivos, con
una recreación. El rechazo de identidad usa el sobre del probe antes del load;
no acredita transaccionalidad de estados raw malformados del runtime.
Los hashes del runtime/guest/host realmente ejecutados figuran en el JSON.

`--inventory` observa herramientas; `--prepare` construye sólo runtime/sysroot;
`--smoke` ejecuta el mini-core. Los temporales de compilación del probe y sus
estados se eliminan; el caché útil queda en `build/linux-waterbox-wb01`.
No se instalaron herramientas globales ni se cambió MacHawk. Este WB01 tampoco
acredita la convención managed de un frontend Linux ni un perfil Amiga.
La propuesta WBX86-B conserva esa integración por probar. Antes de anunciarla
se debe portar el core con p6_run_frame y ejecutar WB02–WB12 con los oráculos
originales de docs/03-waterbox.md; no se heredan los PASS de Darwin.
