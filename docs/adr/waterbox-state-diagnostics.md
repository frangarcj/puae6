# Diagnóstico estructural de snapshots Waterbox (T40l)

Estado: probe ejecutado; el mini-guest no reproduce la divergencia de T40k.

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

El mini-guest aprobado es `62199dd9…`, producido por la receta `8b151b30…`
y lock `f5c6e023…` del commit `0713ffa1b62fa55518ac17f3d33087b3b4adbbcf`.
El probe lee ambos blobs Git por sus SHA completos sin ejecutarlos; verifica las
secciones causales del lock, todas las fuentes/wrappers/manifiestos del recibo
y ambos inventarios sysroot. La receta y el lock actuales se identifican por
separado antes/después. El runtime actual tiene su propia validación de recibo,
fuentes, patchset, herramientas, arquitectura y bytes; no se fija al antiguo
productor del mini-guest ni se le atribuye aquella receta por herencia del recibo.

La ejecución real dirigida conserva snapshots idénticos en los ocho pares:
dos saves consecutivos, dos instancias y la intervención sobre el buffer. Los
cinco fixtures y la ejecución real pasan; runtime y guest permanecen idénticos
antes/después y los temporales se eliminan. Esto sólo descarta la reproducción
por este recorrido del mini-guest, sin acreditar una causa para UAE.

`elf.rs` del runtime ya marca invisibles las pilas de entrada principal y
alternativa (`mark_invisible(layout.main_thread/alt_thread)`); el probe no cambia
esa política. Marcar RWStack dirty no implica que toda pila entre en el snapshot.
Antes de intervenir sobre registros, pilas o threading hay que localizar la
diferencia de UAE en su sección y página reales. El resultado negativo no cubre
pilas de hilos virtuales ni el recorrido de ejecución del guest antiguo.
