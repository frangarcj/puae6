# Preview Waterbox standalone

Preview FFI para macOS ARM64, páginas de 16 KiB y runtime .NET ARM64 compatible
con net8.0 instalado (roll-forward Major). No incluye SDK. No es un plugin EmuHawk;
esa integración y la reproducibilidad siguen pendientes. Estados ligados al build.

Extrae el ZIP completo conservando bin/, config/, licenses/ y source.tar.gz juntos:

```sh
dotnet bin/Ffi.dll pin
```

Extrae también source.tar.gz. El comprobador requiere Python 3.15 y otool de macOS:

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
incluyen paths CodeView: esta comparación no demuestra independencia de ruta.
`archive_payload_matches` compara los ocho productos con el ZIP indicado; no
afirma igualdad byte a byte del ZIP completo ni integración EmuHawk. El gate real
PKG01/BUILD01 de T40i requiere `P6_TEST_WATERBOX_REPRODUCIBLE=1`; sin ello queda BLOCKED.
