# Preview Waterbox standalone

Preview FFI para macOS ARM64, páginas de 16 KiB y runtime .NET ARM64 compatible
con net8.0 instalado (roll-forward Major). No incluye SDK. No es un plugin EmuHawk;
la integración con EmuHawk se prueba por separado y no forma parte de este ZIP.
Estados ligados al build.

Cada candidata publicada lleva la revision PUAE6 y los SHA-256 exactos del guest
y del runtime en `manifest.json`; el nombre del ZIP incorpora la revision corta.
Usar juntos esos artefactos, las fuentes y el wrapper de la misma candidata.

Extrae el ZIP completo conservando bin/, config/, licenses/ y source.tar.gz juntos:

```sh
dotnet bin/Ffi.dll pin
```

Extrae también source.tar.gz. El comprobador se ha probado con Python 3.14
y requiere otool de macOS:

```sh
python3 -B source/tools/package_waterbox.py check --archive /ruta/preview.zip
```

El smoke usa una micro-ROM propia incluida, compara cuatro unidades tras restaurar
en el mismo sandbox y otro proceso y comprueba rechazo incompatible sin corrupción.
Elimina sus temporales. Otras sesiones: source/tests/ffi/Program.cs, con imágenes
autorizadas explícitas. Avisos en licenses/; enlaces del TAR nunca se extraen por el comprobador.

Para comparar dos reconstrucciones limpias de sus ocho productos standalone,
prepara primero los inputs oficiales actuales, el feed NuGet verificado y la caché
local de fuentes Cargo. Desde el checkout limpio, con las herramientas fijadas:

```sh
python3 -B tools/reproduce_waterbox.py --preflight
python3 -B tools/reproduce_waterbox.py --jobs 2 --archive /ruta/preview.zip
```

No descarga ni instala. Aísla y elimina objetos y salidas propias, conserva una
misma ruta entre ambas pasadas y no repite el smoke. El preflight solo genera el
HRTMon esperado en un temporal propio; nunca repara inputs oficiales. Los DLL
incluyen la revisión Git y paths CodeView: ambas pasadas requieren la misma
revisión y ruta; esta comparación no demuestra independencia de ruta.
`archive_payload_matches` compara los ocho productos con el ZIP indicado; no
afirma igualdad byte a byte del ZIP completo ni integración EmuHawk. El gate real
PKG01/BUILD01 de T40i requiere `P6_TEST_WATERBOX_REPRODUCIBLE=1`; sin ello queda BLOCKED.


## Integracion MacHawk solicitada

El [PR #11](https://github.com/frangarcj/BizHawk/pull/11), fusionado en `main`,
integra las fuentes del wrapper en BizHawk.Emulation.Cores, junto con registro,
carga de medios y peliculas. Incluye el ZIP verificado con su fuente completa,
una preparacion offline y el pin del guest; no se instala el DLL standalone.

```sh
python3 waterbox/puae6/prepare.py
dotnet build src/BizHawk.Client.EmuHawk/BizHawk.Client.EmuHawk.csproj -c Release -p:InstallGitHooks=false
```

Seleccionar PUAE6 en los cores preferidos de Amiga. UAE conserva la prioridad
predeterminada y su identidad. El checkout local original y sus submodulos no
se modifican. Darwin ARM64 conserva su carril FFI; la preview de producto para
los probadores utiliza EmuHawk Windows x64. No se anuncia GUI WinForms macOS por compilar o
ejecutar pruebas CLI.

En Config > Cores > PUAE6, `Machine profile` aplica de una vez los defaults de
cada maquina y los seis presets del esquema. A4040 Normal incluye 68040 y FPU
interna; A4060 Normal incluye 68060 y FPU interna. Los valores normales aparecen
sin resaltar y cada override en negrita; restablecer una fila recupera el valor
del perfil. El selector no se serializa ni cambia por si mismo estados o peliculas:
solo escribe los Sync Settings concretos que ya formaban su identidad.

La prueba dirigida de la aplicacion real compara BK2/TAS, A/V, estado final y
sandboxes aislados de UAE/PUAE6 con una micro-ROM propia y la referencia UAE
compilada desde fuentes (incluye la correccion setjmp ya validada). El
`uae.wbx.zst` local antiguo SHA `ba1bc2f601214b205132ad86325f6dbaf3e4c7780ac12d4a82b160683642c857`
carece de SetCDVolume y no carga con el wrapper actual; se conserva y no se
atribuye a ese binario el resultado de la referencia. El runtime nuevo pasa
ademas un smoke separado del Snes9x existente con save/restore y video+RAM.

Las releases que usan los probadores publican EmuHawk para Windows x64. La
integracion de PUAE6 debe incluir un guest ELF x64 y su pin de compilacion,
ademas del lector CHD para Windows. El guest Darwin ARM64 no sirve para ese ZIP;
su paquete standalone sigue siendo una alternativa FFI separada.


## Menus e infraestructura de pruebas

El PR #12 conecta PUAE6 con los menus Amiga del EmuHawk existente: ajustes,
exportacion HDF, icono, configuracion de cores y Soft/Hard Reset distintos.
Los menus generales permanecen en BizHawk. La ventana Eto bajo
`waterbox/waterboxhost/eto-puae6-integration-smoke` es un banco de pruebas,
no el frontend de producto; el usuario excluye portar alli TAStudio/Lua/RAM Watch.
Las pruebas Cocoa acreditan solo ese banco con los ensamblados reales.
No acreditan que WinForms EmuHawk funcione como GUI macOS.

El [PR #14](https://github.com/frangarcj/BizHawk/pull/14), fusionado en
`main`, actualiza esa integracion con el perfil CD32 FMV, el guest x64
`0e1ac382f1481c874b22b39dc198a6862a4380b00f3c87a471e0243c5f832da1`
y el paquete Darwin anterior. La opcion `Fmv` solicita la entrada de firmware
`CD32FMV`; Freeze sigue apareciendo solo para cartuchos freezer.

El gate de coexistencia de la candidata pasa los cuatro casos BK2/TAS con
UAE y PUAE6: imagen, audio, estado completo y aislamiento. El runtime Darwin
inicializa los registros no argumentales al entrar al guest para evitar que
setjmp guarde valores del host. El oraculo conserva el control negativo anterior,
los callbacks con reentrada y la restauracion en sandbox/proceso nuevo. No se
excluye ningun byte del comparador.

El constructor reconoce macOS ARM64 y Windows x64, comprueba la ISA y el SHA
fijado en el ensamblado antes de adquirir firmware o medios y vuelve a comprobar
el guest antes de crear Waterbox. El carril Windows usa un guest x64 distinto y
el sysroot reconstruido desde fuentes fijadas; compilar el guest no sustituye su ejecucion en Windows.


## Descarga de EmuHawk Windows con PUAE6

La [preview PUAE6 actual](https://github.com/frangarcj/BizHawk/releases/tag/puae6-preview-9b50966e)
es la descarga para los probadores. El [PR #22](https://github.com/frangarcj/BizHawk/pull/22)
esta fusionado en `main` como `9b50966e`; incorpora los perfiles de maquina sobre
la base CPU/uaescsi del PR #21. Extraer la preview PUAE6 Windows x64 completa en una
carpeta nueva e iniciar EmuHawk.exe con los requisitos habituales de BizHawk
(.NET Framework 4.8 y Visual C++ x64). No requiere SDK, Python ni compilador.
Elegir PUAE6 entre los cores preferidos de Amiga, configurar una Kickstart propia
y autorizada y abrir el contenido. UAE conserva la seleccion predeterminada.

Los ajustes de PUAE6 vuelven a presentar, como el core UAE antiguo, una sola
opcion `Floppy disk drives` de 0 a 4. Cero desconecta DF0..DF3; los demas valores
conectan ese numero de unidades y montan como maximo ese numero de imagenes
iniciales. El ajuste separado de la primera preview queda oculto solo para poder
leer configuraciones guardadas. El wrapper entrega audio sincronico a 44100 Hz
desde el primer frame, tambien al reiniciar la salida de sonido tras mover o
redimensionar la ventana de EmuHawk.

El ajuste de reinicio `CD controller` ofrece `Auto`, `None`, `Cd32` e `Ide`.
`Auto` conserva CD32 al abrir solo un CD, pero al combinar CD con ADF, HDF,
un volumen ZIP/directorio o WHDLoad conserva una maquina A600, A1200 o A4000
y usa su IDE. `Cd32` exige la maquina CD32 y `Ide` una maquina compatible.
`None` tambien puede usarse con el ajuste de reinicio `uaescsi.device`, que
ofrece `Disabled`, `Auto` y `Enabled`. `Auto` no hace nada cuando no hay CD y,
si hay un catalogo CD con controlador `None`, lo expone por el dispositivo
Amiga sin cambiar `HDF controller`; asi un HDF de arranque y varios HDF de
juegos pueden seguir en `Uae` mientras el CD queda en uaescsi. `Enabled` exige
contenido CD y `None`, y muestra un error ante otra topologia; `Disabled` con
CD y `None` tambien se rechaza porque el medio quedaria sin controlador. Una eleccion incompatible se detiene antes
de abrir el medio y muestra el motivo, sin cambiar silenciosamente de maquina.
Estados, BK2 y TAS vuelven a abrir la misma topologia; si cambia el CD, ADF,
HDF o volumen se rechaza la carga sin tocar la sesion. UAE sigue siendo el core
predeterminado y sus estados y peliculas no se convierten a PUAE6.

El ajuste de reinicio `RTG memory` ofrece `None` y `MB4`. Los 4 MiB habilitan
una sola tarjeta UAE Zorro III con maquina A4000/030 o A4000/040 y exactitud de
CPU `Normal`; la salida conserva un lienzo fijo 1024x768 al cambiar entre chipset
y RTG. Para un HDF preparado para 68040, seleccionar A4000/040, CPU 68040 y FPU
68040. La MMU permanece deshabilitada y no es necesaria para RTG. Otra capacidad,
maquina o exactitud se rechaza antes de arrancar. `None` mantiene el raster normal
del chipset.

Para CD32 FMV, seleccionar maquina `CD32` y cartucho `Fmv`, y aportar en el
gestor de firmware una ROM propia autorizada bajo `CD32FMV`. Se reconocen los
dumps raw conocidos r40.22/r40.30 de 256 KiB y el contenedor r40.30 de 512 KiB
formado por dos mitades identicas. La descarga no incluye ROM del cartucho ni
juegos. El ZIP Windows tiene SHA-256
`1bd646514b3cc7652366df63d08212a986154c6693c3ad5075813593675aa94e`.

Los ajustes, exportacion HDF, Soft/Hard Reset, estados y herramientas generales
se usan desde los menus existentes. Conservar PUAE6/ junto al programa: contiene
fuentes y licencias, incluida la captura musl source-x64.tar.gz que se extrae
junto con source.tar.gz. El paquete no incluye Kickstart ni juegos comerciales.
Los lectores IPF de Hatari y UAE usan el decoder MAME/BSD-3-Clause incluido, no
CAPSImg; el EmuTOS libre se conserva.

La candidata ejecuta en Windows real la ABI de quince slots, callback/reentrada,
restauracion exacta del estado completo en otro sandbox, comparacion A/V, cuatro
pruebas CHD nativas y sandboxes A4000/030, LC040 y 68040 con FPU. Las consultas
arquitectonicas de CPU/MMU del 68030 funcionan con la traduccion MMU deshabilitada;
LC040 sin FPU entrega la excepcion Line-F correcta y 040+FPU conserva sus consultas.
El mismo programa propio mide mas trabajo con X8 que con X2 sin usar reloj de pared.
La prueba RTG usa A4000/040, CPU/FPU 68040, multiplicador X8 y RTG4 con lienzo
1024x768. La prueba GUI abre
contenido propio, reconoce PUAE6 en la ventana visible y la cierra normalmente.
Solo esa configuracion temporal usa el fallback de audio Dummy de BizHawk porque
el runner no tiene dispositivo; no cambia la configuracion distribuida ni
elimina la comparacion del PCM emulado. La ejecucion dirigida
[41293783929](https://github.com/frangarcj/BizHawk/actions/runs/41293783929)
supera todas esas etapas sobre el merge probado `d54cb741`. Las matrices de
`main` de [solucion](https://github.com/frangarcj/BizHawk/actions/runs/41291831919)
y [Waterbox](https://github.com/frangarcj/BizHawk/actions/runs/41291832037)
terminan correctamente sobre el mismo merge; la release PUAE6 apunta a ese
contenido probado. El guest x64 es
`0f9f99dfcf462277538755b356ef65c7dd06dd6b046b72f95bbb8e5e15e81087`.
El paquete Darwin ARM64 separado de esta candidata fija el guest
`ae086e70e6b1396c16bb57b04c1fba1dcb981842b119c442546cb12d03476c5b`;
su archivo tiene SHA-256
`1080ccb0f01229906d57d35da01015406ac756557af079cddbb7e830bf5e3c8e`,
su `manifest.json` registra revision, runtime y hashes exactos, y supera la
comprobacion instalada. Sigue siendo un paquete FFI, no un frontend WinForms
para macOS.

Estados y peliculas dependen del build, configuracion y medios; no son
intercambiables con UAE ni otros builds. TINF, ETN2 multisesion, waiting_blits y
el caso CU06 pendiente siguen como extras. JIT y MMU no estan habilitados; RTG
queda limitado a los perfiles A4000/030 o A4000/040 de 4 MiB descritos arriba. CD32 FMV si esta
habilitado; Cannon Fodder supera las puertas reales nativa, libretro y Waterbox.
La sintesis Layer II conserva las muestras silenciosas de baja amplitud sin
cambiar el reloj ni el buffer causal, y CD32/CDTV publican la actividad optica en
el indicador de unidad. Esto no generaliza compatibilidad a todo el catalogo ni
promete estados entre builds.

## Preview historica A4000/040 T96

La preview de [la release `puae6-preview-157855f9`](https://github.com/frangarcj/BizHawk/releases/tag/puae6-preview-157855f9),
integrada mediante el [PR #20](https://github.com/frangarcj/BizHawk/pull/20),
fija el guest Windows x64
`fc5ceecd3ad85f7a65ae12f67a76f7d062a4f9c9c5d3e7f7a70502a2b138f48e`
y selecciona en su smoke A4000/040, CPU/FPU 68040, multiplicador X8 y RTG 4 MiB.
El ZIP Windows `BizHawk-PUAE6-preview-windows-x64-157855f9.zip` tiene SHA-256
`7a1da49322036131bcc6ff8ce810ffa57d21ce76a0bbdd1f6991d92f23e4ff4e`.
El ZIP Darwin ARM64 separado
`puae6-waterbox-macos-arm64-preview-1bb68201.zip` tiene SHA-256
`39bddac35f70a16f2e554b0fdb3eeded776783d46db7e9ab8cc29a3fc0b5637e`,
fija el guest `4d9d8cf0936a943d8b38503eb22eb8761bf067566991780563a42406da0c8b38`
y supera el smoke instalado de restauracion. La auditoria del ZIP no encuentra
ROM, HDF, ADF, IPF ni el medio privado. El gate `puae6_only` de Windows real y
las dos matrices de `main` pasan sobre los commits exactos registrados arriba.
