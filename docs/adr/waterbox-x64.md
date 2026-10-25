# Waterbox x86_64: un carril separado para la preview Windows

La descarga usada por los testers es el ZIP Windows de EmuHawk. El guest
Darwin ARM64 no puede ejecutarse alli. Se añade un perfil ELF x86_64/4KiB
sin sustituir el perfil inicial ARM64/16KiB ni su runtime, sysroot o recibos.

`python3 tools/build_waterbox_x64.py` autentifica solo inputs locales.
`--build --jobs 2` compila exclusivamente el guest en `build/waterbox-x64`,
tras preparar los arboles actuales de motor/codegen y HRTMon. Reutiliza la
seleccion de fuentes/imports del motor y autentifica el cierre manual.
No instala, descarga, ejecuta el guest ni construye un runtime.

El sysroot x86_64 proviene del cache de Hatari del MacHawk de referencia,
que se lee sin modificar. Su inventario completo queda fijado, junto a las
herramientas y las plantillas musl. Los wrappers se regeneran en la salida
propia y fuerzan el triple x86_64-linux-gnu. Las fuentes adyacentes observadas
son musl 2063abc4e16c84218757b1db10d3cdf9f36ef3f8 y LLVM
3b5b5c1ec4a3095ab096dd780e84d7ab81f3d7ff (18.1.8).

Esta identificacion local NO prueba que el sysroot sea reproducible desde
esas fuentes. Falta capturar/reproducir su receta completa antes de afirmar
procedencia de release. El perfil y recibo conservan `publication_ready=false`.
La comprobacion de ELF rechaza otro ISA, paginas invalidas, W+X, TLS
estructural y simbolos no resueltos. El desensamblado rechaza TLS FS/GS salvo
la lectura GS:0x18 explicita del contexto sandbox de musl Waterbox.
La ejecucion real, excepciones C++, callbacks y restauracion en runtimes
Linux/Windows son puertas separadas; compilar nunca las da por superadas.

El probe ABI x86_64 devuelve 15 palabras, sin reutilizar los 30 slots AAPCS64:
GS:0x18 antes/despues [0,1], retorno callback [2], canarios RBX/RBP/R12..R15
[3..8], SP antes/despues [9,10], clave pthread [11,12] y errno [13,14]. Los
canarios son B003/B005/B012/B013/B014/B015; el callback recibe 11/22/33/44/55/66
hex en RDI/RSI/RDX/RCX/R8/R9. La pila queda alineada a 16 antes del call.
SysV no tiene registros SIMD preservados por el callee. `p6w_identity` conserva
el layout de tres palabras contexto/clave/errno para la entrada reentrante.
La rama AAPCS64 original permanece identica. El check optativo
`P6_TEST_X64_PROBE_COMPILE=1` compila solo este archivo para ambos ISA; la
ejecucion real del probe con callback y restauracion sigue siendo otra puerta.

La receta deriva dos alineaciones de .lrodata/.ldata a4KiB con hashes de
base/resultado; no modifica el linkscript capturado. El wrapper static omite
el interprete ELF y fija el LLD real. No hay TLS estructural ni accesos FS;
GS:0x18 es la unica lectura segmentada permitida. _DYNAMIC local/undefined
con valor0 es la conversion de una referencia weak oculta segun
[ELF gABI5.4](https://gabi.xinuos.com/elf/05-symtab.html#symbol-visibility),
no una peticion de binding externo; globales sin resolver siguen rechazados.
El objeto audio.cpp x64 llama a floorf para la fase del resampler (upstream
linea2436), por lo que se permite esa funcion libm del sysroot estatico.

La reconstruccion offline preparada en T83d se invoca con:

```sh
python3 tools/build_waterbox_x64_sysroot.py --llvm-source build/source-cache/llvm-emutls --output build/waterbox-x64-sysroot-rebuilt
```

Sin `--execute` verifica fuentes/herramientas y prepara el comando (salida3,
`NOT_RUN`); solo `--execute` inicia la compilacion. Un worktree puede leer las
capturas de otro checkout mediante `--source-root /ruta/uae`, si su lock coincide.
La imagen amd64 existente queda fijada por ID y hashes/versiones de herramientas.
No hay red, descargas ni instalaciones: musl/MacHawk/LLVM se montan readonly.
La derivacion aplica los parches getcwd/atomics capturados, reproduce VERSION
1.2.0-git y usa GNU ld explicitamente en el wrapper local porque falta LLD en
esa imagen. Las cuatro fases reconstruyen builtins/unwind/libc++/libc++abi.
Un inventario distinto del cache historico es una identidad nueva valida, sin
modificar el pin del producto: exige recompilar el guest con ese derivado.
El recibo conserva `publication_ready=false` y `runtime_verified=false`; la
receta no sustituye las puertas de ejecucion ni afirma igualdad binaria historica.

La reconstruccion offline con GNU ld fijado produce un sysroot distinto del cache
historico. Se selecciona explicitamente su inventario nuevo y se reconstruye el
guest con ese derivado; no se declara equivalencia binaria con la receta antigua.
El ELF final conserva las paginas de4KiB, no admite TLS estructural/imports
inesperados y solo permite GS:0x18 para contexto. El cierre de fuentes usa la
captura musl completa, sus parches y los scripts LLVM ya congelados. La ejecucion
real Windows y la publicacion siguen siendo puertas separadas.

Windows MapViewOfFileEx requiere una base multiplo de la granularidad de
asignacion ([contrato Microsoft](https://learn.microsoft.com/en-us/windows/win32/api/memoryapi/nf-memoryapi-mapviewoffileex)).
El primer job real falla con1132 antes de ejecutar el guest: LLD omite el
segmento inicial vacio y el runtime toma como base el minimo PHDR no nulo,
base+4KiB. Un enlace propio minimo reproduce ese layout; --image-base no lo
corrige. Se añade una seccion explicita de un byte de solo lectura en la base
64KiB del sandbox. Es un ancla de layout, no una cabecera ELF simulada. Se
conservan paginas de4KiB y segmentos RX/R/RW separados, sin tocar el runtime.
El validador repite el minimo PHDR usado por el loader y rechaza la base vieja;
el reproducer real enlaza ambas variantes y conserva el control negativo.
