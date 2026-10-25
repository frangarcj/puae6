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
