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
se modifican. El alcance sigue siendo Darwin ARM64; no se anuncia GUI WinForms
macOS por la compilacion y las pruebas CLI.

La prueba dirigida de la aplicacion real compara BK2/TAS, A/V, estado final y
sandboxes aislados de UAE/PUAE6 con una micro-ROM propia y la referencia UAE
compilada desde fuentes (incluye la correccion setjmp ya validada). El
`uae.wbx.zst` local antiguo SHA `ba1bc2f601214b205132ad86325f6dbaf3e4c7780ac12d4a82b160683642c857`
carece de SetCDVolume y no carga con el wrapper actual; se conserva y no se
atribuye a ese binario el resultado de la referencia. El runtime nuevo pasa
ademas un smoke separado del Snes9x existente con save/restore y video+RAM.

La fusion no publica una preview PUAE6 automaticamente: el workflow existente
publica Windows y no prepara su guest Darwin ARM64. Hace falta conectar un
paquete de prueba del host macOS correspondiente; el ZIP standalone disponible
sigue siendo una alternativa FFI, no una distribucion completa de MacHawk.
