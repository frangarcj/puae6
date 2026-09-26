# 00. Evidencia, procedencia y limites

Referencia local añadida por indicacion del usuario: ../MacHawk, inspeccionada
en HEAD 9b62757833c9bdd15bf43cef058c88bf91eb0ad7. docs/07-machawk.md registra
hallazgos de codigo ARM64 y plan/machawk.reference.json los fingerprints. Es la
referencia principal de integracion; las fuentes publicas siguientes son evidencia
comparativa. No se ejecutaron sus emuladores ni se modificaron sus archivos.

Consulta: 2026-09-26. Hechos observados y decisiones de diseno estan separados.
Las URLs master son referencias exploratorias, no inputs reproducibles de build.

## Estado local observado

Codexify selecciono /Users/frangar/Fun/uae. tree/list_directory devolvieron vacio;
get_project_doc no encontro instrucciones; git_status indico que no era un repo.
La maquina del conector es macOS ARM64 y su shell es zsh. No se ha inspeccionado
un supuesto framework preexistente: no habia contenido local que inspeccionar.

## Fuentes primarias consultadas

### S1. WinUAE 6.0.3: referencia reproducible

Repo: https://github.com/tonioni/WinUAE
Lista de tags 6: https://api.github.com/repos/tonioni/WinUAE/git/matching-refs/tags/6
Tag anotado: https://api.github.com/repos/tonioni/WinUAE/git/tags/88e3c2c6eac57d6dfc2ed688455a35e2e3a86a59

El tag 6030 apunta al objeto tag 88e3c2c6eac57d6dfc2ed688455a35e2e3a86a59,
que apunta al COMMIT 857f48fcb09399d4d95527e29a2fdeaedb41f827. El tag tiene fecha
2026-03-03 y no esta firmado segun la API. No confundir el SHA del tag con el commit.
El sitio oficial lista WinUAE 6.0.3: https://www.winuae.net/category/news/
Se fija esta version por ser una base 6.x verificable; no se necesita afirmar que
sea la ultima revision de desarrollo disponible.

### S2. WinUAE tiene una via Unix reciente

https://github.com/tonioni/WinUAE/blob/master/README_unix.md

El README consultado describe un port temprano macOS/Linux, od-unix, CMake,
C++17, SDL3/Qt opcionales y modo sin video cuando falta SDL3. No se comprobo su
pertenencia al tag 6030 ni su equivalencia al perfil determinista propuesto.
Decision: auditar commits concretos y reutilizar solo piezas host utiles mediante
parches con procedencia. NO incorporar master entero ni sus dependencias de UI.
Su mera existencia no demuestra que WinUAE sea ya un core reentrante por frames.

### S3. PUAE libretro existente

https://github.com/libretro/libretro-uae
https://github.com/libretro/libretro-uae/blob/master/README.md

El README consultado declara base WinUAE 5.3.1, commit upstream
51b7be351f359dc97f967f051da390283bc4b409. Ese NO es el commit del repo libretro.
El repo tiene integracion libretro, presets, flujo de medios y una modificacion
concreta del bucle CPU para devolver control por frame que conviene auditar.
Decision: referencia funcional Y arquitectonica para host/frame-control, no
sustituir la base WinUAE 6 por el arbol mezclado del port anterior. Fijar el
commit del repo al importarlo en T01 y portar solo deltas justificados.

### S4. Contrato libretro

https://docs.libretro.com/development/cores/developing-cores/

La documentacion requiere devolver el control por frame, al menos un input poll y
un callback de video por retro_run. El frontend controla sincronizacion/velocidad;
el motor no debe basarse en sleep ni tiempo de pared. El audio stereo tiene dos
int16 por frame de audio. El tamano anunciado de serializacion no puede crecer
entre load_game y unload_game. Una implementacion no soportada debe anunciar
serialize_size=0, no una falsa serializacion.

Estas reglas se convierten en tests de contrato, no solo comentarios.

### S5. BizHawk y su integracion UAE existente

Referencia inspeccionada (no afirmacion de ultima release):
1a140d98b8cc4a705db89d4738d76196c84044ea.

https://github.com/TASEmulators/BizHawk/blob/1a140d98b8cc4a705db89d4738d76196c84044ea/src/BizHawk.Emulation.Cores/Computers/Amiga/UAE.cs
https://github.com/TASEmulators/BizHawk/blob/1a140d98b8cc4a705db89d4738d76196c84044ea/waterbox/uae/bizhawk.c
https://github.com/TASEmulators/BizHawk/blob/1a140d98b8cc4a705db89d4738d76196c84044ea/waterbox/uae/Makefile

UAE.cs deriva de WaterboxCore; su metadato de port declara 5.0.0. Usa uae.wbx,
firmware/medios de solo lectura y protege las disqueteras contra escritura.
El shim C exporta Init/FrameAdvance/GetMemoryAreas y usa m68k_go y estado PUAE.
Esto es referencia concreta de integracion, NO una ABI general reutilizable tal cual.
El submodulo TASEmulators/libretro-uae esta fijado en
ccecb1ead642c1bbe391308b88a7ffa9478b918d en ese checkout BizHawk.

La inspeccion local de ../MacHawk confirmo el detalle que condiciona la nueva
arquitectura: waterbox/uae/Makefile usa CORE_DIR=libretro-uae y compila
libretro-core.c/libretro-glue.c con __LIBRETRO__, pero bizhawk.c no llama
retro_run. Inicializa con umain()+m68k_go(1,0) y cada FrameAdvance reanuda con
m68k_go(1,1). En el core viejo, unlockscr() marca libretro_frame_end y
SPCFLAG_CHECK; m68k_go retorna al detectar ese boundary. Por tanto, la semantica
de frame-step ya tiene una referencia probada, mientras que la ABI libretro y la
ABI Waterbox siguen siendo adaptadores distintos.

### S6. Ciclo de vida y estado C# Waterbox

https://github.com/TASEmulators/BizHawk/blob/1a140d98b8cc4a705db89d4738d76196c84044ea/src/BizHawk.Emulation.Cores/Waterbox/WaterboxCore.cs

Se inspecciono el archivo, incluyendo PreInit, PostInit, FrameAdvance y los metodos
de estado. PostInit obtiene areas y llama Seal. FrameAdvance pasa buffers C#
fijados temporalmente. SaveStateBinary guarda snapshot y ciertos contadores C#;
SaveStateBinaryInternal/LoadStateBinaryInternal son puntos para el estado extra.
LoadStateBinary limpia el callback de input y exige reponer punteros nativos.
No basta con que la RAM Amiga este dentro del sandbox.

### S7. Clasificacion de memoria Waterbox

https://github.com/TASEmulators/BizHawk/blob/1a140d98b8cc4a705db89d4738d76196c84044ea/waterbox/emulibc/emulibc.h

alloc_sealed permite asignar/escribir durante init y despues queda inmutable;
alloc_invisible excluye memoria de los estados; alloc_plain se guarda normalmente.
Estas asignaciones no se liberan individualmente. Debe demostrarse la seguridad
antes de excluir una cache o un framebuffer; no asumir que es puro output.

### S8. Toolchain Waterbox

https://raw.githubusercontent.com/TASEmulators/BizHawk/master/waterbox/readme.txt
https://github.com/TASEmulators/BizHawk/blob/1a140d98b8cc4a705db89d4738d76196c84044ea/waterbox/common.mak

El readme explica musl modificado, emulibc, libco, libcxx y pasos Linux/WSL.
common.mak usa sysroot propio, enlace estatico, script de enlace especifico y
builtins x86_64. El pin inspeccionado no es una toolchain macOS ARM64 general.
NO establecer como objetivo inicial portar el runtime Waterbox a otra ISA.
MacHawk u otro fork del usuario requiere una auditoria y pin independientes.

### S9. Costo de snapshots

https://tasvideos.org/Bizhawk/Doom

La documentacion del core Doom explica problemas de estados grandes al cambiar
mucha memoria. Es motivacion para medir paginas sucias/tamano/latencia; no demuestra
el consumo que tendra PUAE6. No se han medido tiempos, memoria ni FPS de PUAE6.

## Que falta comprobar (tareas, no conclusiones)

No se ha clonado/compilado el commit WinUAE fijado, auditado todos sus globales,
verificado su licencia archivo por archivo, ejecutado firmware ni probado un WBX.
T00-T09 resuelven acceso a runners, importacion, dependencias, generacion de CPU,
linkado y viabilidad de avance/snapshot. Los presupuestos de rendimiento del plan
son objetivos propuestos, nunca benchmarks observados.

No hay garantias previas de determinismo entre compiladores/ISA, precision identica
de filtros de audio o cobertura de savestate upstream. Se publicara solo la matriz
que pase los tests. Los pins de dependencias pendientes no pueden llegar a una release.
