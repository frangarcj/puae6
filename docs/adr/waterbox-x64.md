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
