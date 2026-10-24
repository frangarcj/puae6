# Diagnóstico estructural de snapshots Waterbox (T40l)

Estado: probe preparado; causa de T40k todavía no acreditada.

T40k conserva su comparación exacta y sus dos fallos BK2/UAE. A/V coincidente
no demuestra igualdad del snapshot. Este probe independiente usa únicamente el
mini-guest público existente y el runtime Release verificado; no ejecuta EmuHawk.

Se conserva eviction activada, como el EmuHawk Debug del fallo. Dos buffers host
permanecen vivos. Tras avanzar el mismo mini-guest, una intervención restaura el
estado inicial de la misma instancia y cambia solamente el buffer de salida.
Los seis resultados originales deben permanecer idénticos. Todos los bytes del
snapshot se conservan en memoria y se comparan, incluidos stacks y threading.

Los límites reales del callback de escritura y PageInfo identifican secciones
y direcciones. No se deduce el layout Rust de PageAllocation ni de GuestThread.
Un cambio RWStack que sustituya un puntero al primer buffer por el mismo offset
del segundo acredita ese spill en este camino. Cambios en threading siguen sin
atribuir hasta demostrar layout/orden; resultados ambiguos no acreditan causas.

El resultado no corrige el runtime ni desbloquea T40k. Antes de cambiar interop,
pilas o serialización, registrar una ficha de corrección con reproducer y puerta
propia. No normalizar snapshots, excluir bytes ni cambiar hashes/comparadores.
