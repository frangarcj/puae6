# TLS de C++ en el guest Darwin ARM64

Decision propuesta T09b (BLOCKED): compilar C++ con `-femulated-tls` y enlazar el runtime emutls
pristine de compiler-rt LLVM 18.1.8, junto con sus cuatro headers internos.
Commit, tag, blobs y licencia quedan fijados en upstream.lock y el manifiesto.
Se conserva `thread_local` en el smoke; no se sustituye por un global.

El primer guest ejecutado fallo en smoke_init, PC 0x36f00018204, al guardar el
TLS en 0x1014. El desensamblado muestra `mrs x10, TPIDR_EL0; add x10,x10,#16;
str x11,[x10]`. El musl Darwin fijado obtiene el pthread desde `[x28]`;
TPIDR_EL0 transporta un espejo de Context, no el thread pointer Linux exigido
por el lowering local-exec de Clang. Reservar x28 no cambia ese lowering.

Caso original conservado en build/waterbox/tls-repro: guest.cpp, Makefile,
direct-tls.wbx, recibo con hashes y log de SIGSEGV. Dos hipotesis distinguibles:
el lowering Linux usa un thread pointer incompatible; o el thunk no preserva
el registro. La instruccion fallida y musl identifican la primera como causa
suficiente, aunque WB13 sigue siendo necesario para comprobar los thunks.

La implementacion upstream `compiler-rt/lib/builtins/emutls.c` usa
pthread_once/key_create/getspecific/setspecific. Las implementaciones musl
seleccionadas leen su pthread desde x28; tablas, indices y allocations quedan
en memoria guest capturada. No crea hilos del SO ni introduce TLS host.
Se conserva el runtime upstream completo, sin reimplementar su ABI ni devolver
exito desde stubs. La ruta se prueba con constructor/asignacion C++, avance,
restore en A/B y alternancia; compilacion por si sola no prueba la decision.

Esta decision obliga a comprobar el lowering de cualquier futura unidad con
TLS de compilador, incluidas dependencias precompiladas. WB13 auditara x18/x28
y assembly, incluyendo el unwinder. No se generaliza esta prueba a todo uso de
destructores thread_local ni a concurrencia, excluida del perfil inicial.

Segundo intento: añadir el flag a CXXFLAGS y compilar/enlazar emutls no cambio
el lowering final del build LTO. El ELF sigue sin __emutls_get_address y contiene
acceso directo a TPIDR_EL0; el bitcode preserva llvm.threadlocal.address.
La siguiente investigacion debe comprobar las opciones del backend en el enlace
LTO (common.mak usa CCFLAGS), no dar por validado el flag de la compilacion C++.
Tras dos intentos fallidos se conserva T09b blocked y se continua con T05.
