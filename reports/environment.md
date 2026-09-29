# T00: entorno observado (corregido durante T01a)

Fecha: 2026-09-26. Checkout: `/Users/frangar/Fun/uae`, inicialmente limpio.
Inventario local, sin instalar paquetes, arrancar maquinas/contenedores ni
ejecutar builds o emuladores. No existe todavia un core PUAE6 ejecutable.
Los estados de runners estan en `plan/runners.json`; describen disponibilidad,
no resultados de compatibilidad o determinismo. Revision MacHawk delegada a Luna.

## Host y herramientas

Comandos ejecutados desde uae. Salida resumida; exit 0 salvo indicacion.
Las rutas de la tabla son las resueltas por `shutil.which`, excepto LLVM explicito.

| Comando | Ruta / resultado observado |
| --- | --- |
| `uname -sm`; `sw_vers` | Darwin arm64; macOS 26.7, build 25G229 |
| `sysctl -n hw.pagesize` | 16384 bytes |
| `python3 --version` | `/opt/homebrew/bin/python3`: 3.14.7 |
| `git --version` | `/usr/bin/git`: 2.50.1 (Apple Git-155) |
| `cmake --version` | `/opt/homebrew/bin/cmake`: 4.3.2 |
| `ninja --version` | `/opt/homebrew/bin/ninja`: 1.13.2 |
| `clang --version`; `clang++ --version` | `/usr/bin/`: Apple Clang 21.0.0 (clang-2100.1.1.101), target arm64-apple-darwin25.6.0 |
| `gcc --version`; `g++ --version` | `/usr/bin/`: los mismos Apple Clang, NO GCC independiente |
| `xcode-select -p` | `/Applications/Xcode.app/Contents/Developer` |
| `xcodebuild -version`; `xcrun --show-sdk-version` | Xcode 26.6 (17F113); SDK 26.5 |
| `/opt/homebrew/opt/llvm/bin/clang --version` | Homebrew Clang 22.1.8, target arm64-apple-darwin25.6.0 |
| `ld.lld --version` | `/opt/homebrew/bin/ld.lld`: Homebrew LLD 22.1.8 |
| `make --version` | `/usr/bin/make`: GNU Make 3.81 |
| `pkg-config --version`; `zstd --version` | `/opt/homebrew/bin/`: 0.29.2; 1.5.7 |
| `rustc --version`; `cargo --version` | `/opt/homebrew/bin/`: 1.95.0 (59807616e); 1.95.0 (f2d3ce0bd) |
| `rustup toolchain list` | No ejecutado: `shutil.which('rustup')` devuelve None |
| `dotnet --info` | `/opt/homebrew/bin/dotnet`: SDK 10.0.400, MSBuild 18.9.6; runtime 10.0.11, osx-arm64 |
| `docker --version`; `docker context show` | `/usr/local/bin/docker`: 29.8.0 (88096ef); desktop-linux |
| `docker version --format '{{json .Server}}'` | exit 1, Server null: socket `/Users/frangar/.docker/run/docker.sock` inexistente |
| `qemu-system-x86_64 --version` | `/opt/homebrew/bin/qemu-system-x86_64`: 11.0.3; no VM iniciada |

`dotnet --info` lista solo Microsoft.NETCore.App y Microsoft.AspNetCore.App
10.0.11, sin otras arquitecturas ni workloads. No se encontro otro dotnet en
`~/.dotnet/dotnet`, `/usr/local/share/dotnet/dotnet`,
`/usr/local/share/dotnet/x64/dotnet` ni `/opt/homebrew/opt/dotnet@8/bin/dotnet`.
No se inspeccionaron discos completos buscando instalaciones alternativas.

`llvm-ar` no esta en PATH, pero `llvm-ar`, `llvm-objdump`, `llvm-readelf` y
`clang++` son ejecutables bajo `/opt/homebrew/opt/llvm/bin`. LLVM 18 no se
encontro en `/opt/homebrew/opt/llvm@18/bin/clang`. LLVM 22 no se declara
sustituto validado de LLVM 18.1.8, citado para la regresion Snes9x en
`DARWIN_ARM64.md`; esa mencion no fija por si sola la toolchain PUAE6.

## MacHawk: referencia preservada y ruta Waterbox

Se consultaron `git -C ../MacHawk rev-parse HEAD`, `git status --short`,
`git submodule status` y los HEAD/status de UAE y musl. Coinciden con el lock:

- MacHawk: `9b62757833c9bdd15bf43cef058c88bf91eb0ad7`.
- UAE: `ccecb1ead642c1bbe391308b88a7ffa9478b918d`.
- musl: `2063abc4e16c84218757b1db10d3cdf9f36ef3f8`.

Se recalcularon con Python/hashlib los SHA-256 de las rutas enumeradas en
`plan/machawk.reference.json`: coinciden los 13 archivos seleccionados,
las 8 fuentes UAE modificadas y el diff registrado. El checkout tambien tiene
cambios ajenos en `.vscode/settings.json`, neocd/vice y archivos sin seguimiento.
Entre estos ultimos esta `../MacHawk/Assets/dll/uae.wbx.zst`: no sobrescribirlo.
No se modificaron ni limpiaron. No se leyeron ni copiaron assets comerciales.

Se leyeron `waterbox/waterboxhost/DARWIN_ARM64.md`, `run-macos-smoke.sh`,
`rust-toolchain.toml`, `waterbox/common.mak`, la receta UAE, `global.json` y
`Waterbox.UaeCd32Headless.csproj`. La ruta inicial sigue siendo host Darwin ARM64
y guest ELF AArch64 con x18/x28 reservados, paginas logicas de 4 KiB y host
de 16 KiB; debe fijar `WBX_TARGET_ARCH=aarch64` y `WBX_HOST_PAGE_SIZE=16384`.

La receta declara Rust `nightly-2026-08-01`; ni `~/.cargo/bin/rustup` ni
`~/.rustup/toolchains` existen. El Rust Homebrew observado no demuestra ese pin.
El smoke exige expresamente Microsoft.NETCore.App 8.x antes de ejecutar; con
el dotnet observado esa comprobacion fallaria. `global.json` permite SDK
`latestMajor`, pero eso no aporta el runtime 8. El harness UAE apunta a net8.0.

Correccion tras la indicacion del usuario: **la ausencia de runtime 8 no es un
bloqueo universal de Waterbox**. `../MacHawk/waterbox/vice/README.md:1539`
documenta `DOTNET_ROLL_FORWARD=Major dotnet run --no-build -c Debug -- <mode>`
desde `vice-smoke` para usar el runtime mas nuevo. El preflight rigido pertenece
a `run-macos-smoke.sh`; T09 debe elegir la invocacion apropiada del harness
aislado, sin instalar .NET 8 por esa comprobacion de inventario solamente.

El mismo README (seccion Building, linea 57) compila el guest con
`docker run --rm --platform linux/arm64`, imagen local `machawk-wbx:arm64`
(Clang 18), `WBX_TARGET_ARCH=aarch64`, `WBX_HOST_PAGE_SIZE=16384` y
`SYSROOT=/work/musl-sysroot-aarch64-darwin`. Docker sirve para guest/toolchain;
el host Rust puede construirse localmente con la receta bootstrap existente.
El socket ausente fue una observacion puntual de T00, no evidencia de que la
receta no funcione. El usuario confirma que Waterbox funciona en MacHawk;
esta correccion se basa en su indicacion y las recetas leidas, sin ejecutar
el runtime ni escribir outputs alli. Los mounts de la receta se adaptaran a
copias bajo uae. El pin/toolchain efectivo Rust sigue pendiente de capturar.

El script amplio requiere Docker y puede descargar fuentes/NuGet e imagen
Ubuntu 24.04, ejecutar cargo test, varios cores y builds managed. No se ejecuto.
La receta `RUSTC_BOOTSTRAP=1 cargo test` tampoco se ejecuto; no se cambiaron
variables globales ni el pin Rust para sortear estas diferencias.

## Caches y permisos

Inventario por existencia/nombres (`Path.exists/iterdir`, `find`, `du -sh`), sin
cargar binarios ni certificar sus inputs:

- `~/.cargo/registry` existe; `~/.cargo/git` no. `~/.nuget/packages` contiene
  147 entradas. `~/Library/Caches/Homebrew` existe. Su completitud offline
  no se ha verificado.
- `../MacHawk/waterbox/waterboxhost/target` ocupa aproximadamente 2.0 GiB.
  Contiene sysroots `musl-sysroot-aarch64-darwin` (20 MiB) y
  `musl-waterbox-aarch64-darwin` (26 MiB), `debug/libwaterboxhost.dylib`,
  `obj/uae-aarch64-darwin/{debug,release}/uae.wbx` y variantes adicionales.
  Son candidatos para auditoria, no outputs PUAE6 ni un build lock completo.
- `os.access('.', R_OK/W_OK/X_OK)` devuelve true en uae. La lectura de las
  fuentes MacHawk ha funcionado; solo esa lectura esta autorizada. Outputs
  futuros deben quedar separados bajo uae, sin sobrescribir MacHawk.
- No se han probado permisos MAP_JIT, mprotect, callbacks, snapshots ni
  restauracion en sandbox. Siguen pendientes de T09, incluidos WB13-WB15.
- QEMU x86_64 y Wine estan en PATH y existe
  `/Library/Apple/usr/libexec/oah/libRosettaRuntime`. No acreditan un runner
  Linux/Windows/macOS x86_64; no se inicio VM, Wine, Rosetta ni conexion remota.
  `qemu-x86_64` y `qemu-aarch64` no estan en PATH.

## Resultado de checks y continuacion

PLAN01: `python3 tools/plan.py validate` pasa; los 35 tests de
`python3 -m unittest discover -s tests -p 'test_plan.py' -v` pasan sin skips.
ENV01: inventario revisado con comandos, versiones, permisos, carencias y
responsables por runner. Es un check documental; `tools/check.py` aun no existe.

T00 puede cerrarse: herramientas nativas inventariadas y referencia Waterbox
disponible con receta Docker/roll-forward, aun no validada para PUAE6. T01 permite importar
fuentes fijadas y crear su verificador offline. T09 no puede aprobar su puerta
sin resolver la receta/toolchain y ejecutar su mini-core dentro de Waterbox.
El propietario del entorno es responsable de provisionar herramientas/maquinas
ausentes con autorizacion; el implementador debe preparar y probar la receta
aislada. Ningun resultado de emulacion, cross-build o runtime se infiere aqui.
