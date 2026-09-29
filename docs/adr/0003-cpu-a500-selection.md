# ADR 0003: seleccion compilada de CPU para A500

El perfil fijo solo genera CPUEMU_13; A500 usa la familia 14 contenida en esa
unidad. newcpu selecciona tambien otras familias mediante condiciones runtime,
pero cpu_prefetch.h no declara sus helpers sin CPUEMU_20/21/22/23. Activar esas
macros para satisfacer compilacion habilitaria motores ajenos al perfil.

Decision: P6 conserva el bloque de asignaciones CE68000 de set_x_funcs y su
estado comun x2/tracing; excluye los brazos de otros modelos y sus cachés.
La tabla cputbls conserva dimension 6x8 con una sola celda no nula, [0][4],
op_smalltbl_14. No se edita cpustbl/cpuemu generado. m68k_run selecciona la
implementacion CE original; su bucle e instrucciones no se modifican.

Los bloques de helpers 020/030/040 se excluyen en P6, conservando check_t0_trace,
reset_pipeline_state y la rama 68000 de fill_prefetch. Se preserva el reset de
campos de pipeline aunque no haya cache 020 activa: eliminar estado incidental
no es necesario para compilar. Las ramas no-P6 quedan disponibles intactas.
Disassembly sin DEBUGGER emite un diagnostico de no disponible, sin asignar un
buffer vacio. Dos formatos de diagnostico usan el ancho real de evt_t.

Precondicion interna: prefs A500 validas antes de construir tablas/asignar
accesos/ejecutar. Esta seleccion NO valida configuracion publica ni autoriza
llamar directamente m68k_run con prefs arbitrarias. T06c/T07/T13 deben rechazar
config no soportada en el lifecycle p6_* antes de llegar al motor; el ABI publico
aun no esta implementado. Tampoco se declara 68010 soportado porque existan
handlers13 generados.

Prueba: unidad entera Debug/Release y O0/O2, inspeccion de cputbls preprocesada,
ejecucion del fill_prefetch/check_t0_trace real con lecturas de memoria observadas,
sanitizadores y regresiones de handlers/CIA/IRQ. El enlace selectivo pertenece
solo al microtest. El objeto completo conserva referencias MMU/FPU en otros
helpers/reset: T06b2r debe excluir sus callers correctamente, nunca proporcionar
stubs de exito. Compilar este objeto no prueba enlace ni boot de una maquina.
