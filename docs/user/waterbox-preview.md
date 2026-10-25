# Preview Waterbox standalone

Preview FFI para macOS ARM64, páginas de 16 KiB y runtime .NET ARM64 compatible
con net8.0 instalado (roll-forward Major). No incluye SDK. No es un plugin EmuHawk;
la integración con EmuHawk se prueba por separado y no forma parte de este ZIP.
Estados ligados al build.

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

La [preview PUAE6](https://github.com/frangarcj/BizHawk/releases/tag/puae6-preview-e4739224)
es la descarga para los probadores. El PR #12 esta fusionado en main. Extraer la preview PUAE6 Windows x64 completa en una
carpeta nueva e iniciar EmuHawk.exe con los requisitos habituales de BizHawk
(.NET Framework 4.8 y Visual C++ x64). No requiere SDK, Python ni compilador.
Elegir PUAE6 entre los cores preferidos de Amiga, configurar una Kickstart propia
y autorizada y abrir el contenido. UAE conserva la seleccion predeterminada.

Los ajustes, exportacion HDF, Soft/Hard Reset, estados y herramientas generales
se usan desde los menus existentes. Conservar PUAE6/ junto al programa: contiene
fuentes y licencias, incluida la captura musl source-x64.tar.gz que se extrae
junto con source.tar.gz. El paquete no incluye Kickstart ni juegos comerciales.
Los otros guests proceden de la release fijada hatari-preview-56ea1841 y conservan
su aviso CAPSImg y el EmuTOS libre.

La candidata ejecuta en Windows real la ABI de quince slots, callback/reentrada,
restauracion exacta del estado completo en otro sandbox, comparacion A/V y cuatro
pruebas CHD nativas. La prueba GUI abre contenido propio, reconoce PUAE6 en la
ventana visible y la cierra normalmente. Solo esa configuracion temporal usa el
fallback de audio Dummy de BizHawk porque el runner no tiene dispositivo; no
cambia la configuracion distribuida ni elimina la comparacion del PCM emulado.

Estados y peliculas dependen del build, configuracion y medios; no son
intercambiables con UAE ni otros builds. TINF, ETN2 multisesion, waiting_blits y
el caso CU06 pendiente siguen como extras. JIT, MMU, RTG y CD32 FMV no estan
habilitados en esta preview.
