# Referencias fijadas; no son el nuevo motor

Los commits y selecciones estan en `plan/upstream.lock.json`. Cada directorio
tiene un manifiesto hermano `*.manifest.json` con procedencia, seleccion,
modos Git y SHA-256 por archivo. WinUAE sigue separado en `upstream/winuae`.

| Directorio | Uso | Archivos |
| --- | --- | ---: |
| `puae` | Referencia publica de libretro/frame-control | 2493 |
| `libretro-api` | Solo la cabecera API con aviso MIT | 1 |
| `machawk` | Fuentes seleccionadas host/guest/managed y recetas locales | 362 |
| `machawk-uae` | UAE antiguo con ocho cambios locales fijados por hash | 1649 |
| `musl` | Fuente del submodulo local, no un sysroot construido | 2671 |
| `capsimg` | Dependencia del UAE antiguo; excluida del perfil inicial PUAE6 | 117 |

```sh
python3 tools/import_sources.py fetch --all
python3 tools/import_sources.py import --all
python3 tools/import_sources.py verify --all
```

`fetch` consulta la red solo para fuentes remotas y sus commits fijados; para
fuentes locales comprueba el HEAD sin escribir. `import` usa cache Git o lee
los blobs del checkout local, comprueba que el working tree seleccionado
coincida y admite unicamente los overlays fijados. `verify` no necesita Git,
red, caches ni MacHawk: usa los snapshots, manifiestos, lock y catalogo de
avisos `manifests/licenses.json`. Ningun comando modifica proyectos hermanos.

La seleccion MacHawk incluye WaterboxHost (tambien sus blobs de interop y
fuentes/generador), emulibc, recetas guest, shim UAE, fuentes del wrapper y
dependencias managed/transitivas seleccionadas. Incluye fuentes de ISOParser
y generadores/analyzers; excluye sus DLL precompiladas de `References/`.
El harness original todavia referencia esas DLL: T09/T20 deben generar sus
dependencias o adaptar el proyecto aislado. No ejecutar los scripts importados
como si fueran comandos PUAE6; algunos construyen/instalan sobre su checkout.

Cargo.lock, csproj y Directory.Packages.props conservan los descriptores de
paquetes externos. Los paquetes provisionados, hashes finales NuGet, LLVM,
imagen Docker y sysroot probado quedan para T09. Se conserva el SHA musl
local del lock; no se sustituye silenciosamente por el pin de otro core.
No se han copiado Assets, ROMs/juegos privados, outputs ni archivos untracked
de MacHawk. Los avisos de componentes heterogeneos se conservan por separado.

Git: `puae/` NO se versiona (113 MB, publica y fijada por commit; solo referencia
de lectura). Se conserva `puae.manifest.json`; para recuperarla:
`python3 tools/import_sources.py fetch --source puae_libretro_reference`, luego
`import` y `verify` con el mismo `--source`. `verify --all` falla mientras falte.
Las demas instantaneas si se versionan: `winuae` es entrada del build y las de
MacHawk provienen de un checkout local sucio que no debe modificarse.

Git: `musl/.gitignore` ignora su propio `dist/config.mak`, aunque upstream lo
versiona. Se ha incorporado ese archivo exacto con `git add -f`; el importador
no altera el indice Git ni modifica los `.gitignore` importados. Al reimportar
en otro checkout, conservar tambien ese archivo al preparar el commit.
