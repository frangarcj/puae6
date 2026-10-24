# TLS del compilador en el guest Darwin ARM64

Estado: decidido en T09b2. Sustituye la propuesta de T09b (emutls), que quedo
bloqueada y se descarta como ruta de trabajo.

## Decision

Un guest Waterbox Darwin ARM64 NO contiene TLS emitido por el compilador:
ni `thread_local`, ni `__thread`, ni `_Thread_local`. Los datos por hilo usan
`pthread_key_*` de musl, que llega al hilo del sandbox mediante `x28`; el smoke
lo hace asi desde T09b1 y sus valores sobreviven a snapshot, restore y sandbox
nuevo. El check WB16 falla la receta si el ELF final del guest contiene PT_TLS,
secciones SHF_TLS, simbolos STT_TLS o un `mrs/msr TPIDR_EL0` / `mrs TPIDRRO_EL0`
en segmentos ejecutables. emutls (`references/compiler-rt-emutls`) sigue fijado
como fallback documentado, pero NO se enlaza ni se prueba.

## Por que el TLS local-exec no puede funcionar aqui

El primer guest con `static thread_local` fallo en `smoke_init` (PC
0x36f00018204) en `mrs x10, TPIDR_EL0; add x10,x10,#16; str x11,[x10]` con la
direccion 0x1014; con `-femulated-tls` el fallo fue equivalente en 0x1012. Esas
direcciones no eran `Context*+16`. La sonda
`tests/waterbox-smoke/tls-negative/tpidr_probe.c` (nativa, manual, dependiente de
tiempos) lo explica: XNU reescribe TPIDR_EL0 en cada cambio de contexto. Con un
marcador escrito por el hilo, 200 de 200 lecturas posteriores difieren y el
registro contiene 0x1002..0x1004; un hilo nuevo arranca con ese mismo valor.
0x1004+16 = 0x1014 y 0x1002+16 = 0x1012: es el offset local-exec de AArch64.

Consecuencia: el "espejo" de Context que `interop_aarch64.s` escribe en
TPIDR_EL0 solo dura hasta que el kernel desaloja el hilo, y por eso
DARWIN_ARM64.md prohibe depender de el. No se puede arreglar en host ni en musl:
MacHawk no debe tocarse y ningun parche propio cambia el kernel. El musl
Darwin fijado obtiene su pthread desde `[x28]`, no de TPIDR_EL0, y ese es el
unico camino de TLS soportado.

## El motor no necesita TLS del compilador

`thread_local`, `__thread` y `_Thread_local` no aparecen en upstream/winuae, en
winuae/, ports/, adapters/ ni bridge/, ni en los guests uae, hatari y libcxx de
MacHawk. Sus C++ libsnes y bsnescore se compilan con `-fno-threadsafe-statics`.
MacHawk elimino incluso el TLS de MAME (commit 52276f7d4, "Remove TLS use in MAME"). La
unica aparicion era la sonda de nuestro smoke, que convirtio una incertidumbre
secundaria en puerta de T09b.

## Riesgo restante: TLS precompilado

Se inspeccionaron los objetos del sysroot fijado
(`llvm-ar x` + `llvm-objdump -d`). Solo `libc++abi.a(cxa_thread_atexit.cpp.o)`
accede a TPIDR_EL0 y contiene `.tbss`; se enlaza unicamente si algo referencia
`__cxa_thread_atexit`, es decir, un `thread_local` con destructor no trivial.
`__cxa_get_globals` (excepciones) usa `pthread_key`, o sea x28. `libc++.a`,
`libunwind.a`, `libc.a` y `libclang_rt.builtins` no tienen accesos. Esa
inspeccion no se repite en cada build: WB16 revisa el ELF FINAL, que es donde el
riesgo se materializa, incluidas dependencias precompiladas.

## Por que no emutls

La hipotesis del segundo intento se confirmo: `-femulated-tls` puesto solo en
CXXFLAGS nunca llega al backend LTO (`clang -###` anade `-plugin-opt=-emulated-tls`
solo si el flag esta en la linea de enlace, y common.mak enlaza con CCFLAGS).
Arreglarlo probablemente hara pasar la sonda, pero: anade un runtime de
compiler-rt que ningun guest de MacHawk usa, no cubre objetos precompilados como
libc++abi y seguiria necesitando WB16. Para reabrir esta decision haria falta que
una dependencia exija TLS de verdad: entonces se pasa el flag tambien en el
enlace, se prueba en Waterbox y se documenta una excepcion explicita en WB16.

## Consecuencias

- Caso fallido conservado: `tests/waterbox-smoke/tls-negative` (guest con
  `thread_local`). La receta lo compila como cualquier guest, comprueba que WB16 lo
  rechaza y guarda el motivo en el recibo. Nunca se ejecuta.
- WB13 ("TLS") significa TLS de musl via x28 a traves de callbacks, no TLS del
  compilador. No se generaliza a destructores de datos por hilo ni a concurrencia,
  fuera del perfil inicial.
- El perfil determinista sigue sin hilos del SO; un guest que necesite hilos o
  libco queda fuera de este invariante y exige una decision propia.
