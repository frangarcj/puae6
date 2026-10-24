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
