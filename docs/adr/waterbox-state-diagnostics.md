# Diagnóstico estructural de snapshots Waterbox (T40l)

Estado actual: corrección ABI integrada en los guests derivados; pasan los cuatro casos originales de coexistencia y las restauraciones en sandbox/proceso nuevo. El diagnóstico siguiente conserva la causa y la decisión, sin excluir bytes del estado.

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

## Decisión ABI de T40n, ejecutada en el oráculo

El overlay es exclusivo de `src/setjmp/waterbox_aarch64`: cambiar juntos el
escritor a `stp x27,xzr` y el lector a `ldp x27,xzr`, conservando el contexto x28
vigente. No cambia ningún slot, tamaño, SP, registro restante ni el retorno
`longjmp(env,0) == 1`. El AArch64 genérico y los saves de x28 del interop host
mantienen su contrato. Sólo ignorar el slot al leer dejaría el puntero variable
en el estado; sólo cambiar el escritor rompería el lector.

El oráculo debe mantener vivo el frame C entre setjmp y longjmp, verificar el
slot escrito cero y en ese mismo frame introducir un canario para comprobar que
el lector conserva x28/TLS. Los canarios WB13 cubren los otros registros. Ningún
salto atraviesa un frame C++ ni vuelve a un frame que ya retornó. Después se
comprueba el snapshot completo y su restauración en otro sandbox/proceso.

Esta decisión no acredita sigsetjmp: falta una implementación específica para
waterbox_aarch64 y la variante AArch64 genérica usa otro tamaño de `__jmp_buf`.
Tampoco acredita otros caminos de señales o corutinas. Sysroot y guest derivados
requieren identidad nueva y las puertas de integración originales antes de cerrar
T40o y T40k; el overlay del oráculo no cambia todavía los productos.

La prueba real del overlay pasa: diez saltos en dos sandboxes y dos en un
proceso nuevo, con retornos 0→1 y 7, registros y TLS intactos. Un contador
capturado obliga a observar la restauración 4→2→4 en el segundo sandbox y
0→2→4 en el proceso nuevo. Los estados completos coinciden entre instancias,
y el futuro difiere del baseline; una carga que no hiciera nada no pasaría.
El guest original se rechaza antes de saltar al detectar su slot no canónico.
Los inputs se verifican antes/después y los outputs temporales se eliminan.
La integración de T40o usa un sysroot derivado autenticado y guests construidos desde fuentes, con wrappers propios. El empaquetador y el preflight de reproducibilidad exigen esa procedencia. La base, el runtime y la referencia MacHawk permanecen intactos; los estados siguen sin promesa de compatibilidad entre builds.

## Derivación de libc y metadatos del ensamblador

La base conserva objetos ensamblados con LLVM 18. El compilador local fijado
es LLVM 23. Al recompilar las dos fuentes originales, las instrucciones, tamaños
y atributos coinciden; sólo cambia la etiqueta local STT_NOTYPE `$x.0` a `$x`
en `.text+0`, de tamaño cero. Cambian también el orden de la tabla de strings
y sus offsets en la tabla de símbolos.

La comparación base/fuente actual debe identificar exactamente esa diferencia:
conservar todos los demás símbolos y atributos, comprobar el mismo conjunto de
strings salvo aquel token y rechazar cualquier cambio de código o relocación.
La comparación entre original y parcheado con el compilador actual sigue siendo
estricta sobre los metadatos y sólo admite la instrucción acordada. No se edita
ningún ELF ni se normalizan snapshots. La base y el índice completo del archivo
se conservan como inputs; el derivado tendrá identidad propia.

## Nueva localizacion de la candidata MacHawk (T82h)

El gate real vuelve a fallar en TASproj/UAE tras pasar BK2. El diagnostico
conserva los cuatro casos y cada byte del comparador original. CaptureLegacy
registra las escrituras y PageInfo del estado final; solo exporta el par ante
un mismatch y el transporte se elimina al terminar. No se normaliza el estado.

La ejecucion identifica paginas guest RW: __exbuf en0x36f0279bc00 y
s_try_stack en0x36f0279c580, con diferencias en los offsets0,8,40 y65.
Los simbolos del ELF real identifican ambos objetos; el ASM setjmp guarda
x19,x20,x24 yx27 en esos slots. La cola managed y el resto de secciones
coinciden. x28 sigue canonico; no se trata del slot corregido porT40n.

El interop Darwin actual conserva los GPR del host en su pila, pero entra al
guest sin inicializar x19..x27 ni x29. La decision a probar es dar valores
canonicos a esos registros no argumentales al entrar, manteniendo intactos
los argumentos, x18, x28 y la restauracion del host/callbacks/hilos. Hace
falta un control con canarios host distintos, restauracion en sandbox/proceso
nuevo y el reproducer original antes de cambiar el runtime de producto.
Los d8..d15 tambien se capturan por setjmp, pero este diagnostico no acredita
un fallo alli ni autoriza limpiarlos sin preservar los valores del host.
