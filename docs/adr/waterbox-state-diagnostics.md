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

T40m ejecuta una localización de UAE dentro del EXE original: sólo BK2, ambos
cores vivos y doce frames alternos por pase, con RomLoader/MovieSession y las
comparaciones A/V originales. Un Stream compositivo conserva cada Write y
WriteByte de SaveStateBinary; el mapa procede del mismo WaterboxHost por el
campo protegido `_exe` verificado. El decoder T40l valida el prefijo runtime;
las diecinueve escrituras finales son los 69 bytes originales de WaterboxCore y
UAE, también comparados y nombrados. SHA/tamaño siguen cubriendo el estado entero.
Esta observación no sustituye los cuatro casos ni el comparador intacto de T40k.
Datos ambiguos bloquean.

La diferencia real está en dos bytes de páginas RW ordinarias: el byte 73 de
`__exbuf` y el byte 73 del primer `jmp_buf` de `s_try_stack`. Los símbolos del ELF
real y `cpummu.c` identifican ambos buffers; el segundo se copia del primero por
`__pushtry`. Iniciales y guardados consecutivos son idénticos; la cola managed y
threading también coinciden. No procede corregir padding Rust ni limpiar pilas.

El ensamblador del ELF real confirma `stp x27,x28,[x0,#64]` en `setjmp` y el
`ldp` correspondiente en `longjmp`. El slot de x28 ocupa los bytes 72..79. El
interop Waterbox pone `Context*` del host en ese registro reservado; `-ffixed-x28`
no cambia el ensamblador de musl. Por ello debe revisarse el ABI de setjmp/longjmp
del guest, manteniendo el contexto vigente al saltar y evitando persistir aquel
puntero host. La corrección requiere un test de salto real y restauración en otro
sandbox, además del escenario original. No basta con ocultar el slot al comparar
ni parchear datos de estado. MacHawk y su binario original permanecen intactos.
