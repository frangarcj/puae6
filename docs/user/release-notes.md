# PUAE6: preview de perfiles MacHawk T102

Decision: **READY para el alcance de preview declarado**. La prerelease
[`puae6-preview-9b50966e`](https://github.com/frangarcj/BizHawk/releases/tag/puae6-preview-9b50966e)
corresponde al merge MacHawk `9b50966e10101b98b75e772e7b000924f2835bf0`
del [PR #22](https://github.com/frangarcj/BizHawk/pull/22).

El archivo `BizHawk-PUAE6-preview-windows-x64-9b50966e.zip` mide
123548753 bytes y tiene SHA-256
`1bd646514b3cc7652366df63d08212a986154c6693c3ad5075813593675aa94e`.
El asset descargado de la release coincide byte a byte con el paquete auditado;
no contiene Kickstart, juegos, HDF ni los medios privados de prueba.

MacHawk ofrece 16 perfiles completos, incluidos los seis presets del esquema.
A4040 Normal y A4060 Normal aplican CPU, FPU y compatibilidad coherentes. El
PropertyGrid muestra en fuente normal los valores del perfil y en negrita solo
los overrides; cada fila puede volver a su baseline. El selector es virtual:
las configuraciones existentes se infieren desde sus valores concretos y no se
añade identidad serializada ni se cambian ABI, guest, estados o peliculas.

La matriz de la candidata paso
[Waterbox completo](https://github.com/frangarcj/BizHawk/actions/runs/41297886001)
y [solucion con analizadores](https://github.com/frangarcj/BizHawk/actions/runs/41297886077).
Sobre el merge pasaron la
[solucion Linux/Windows](https://github.com/frangarcj/BizHawk/actions/runs/41299992665)
y el [gate Windows real](https://github.com/frangarcj/BizHawk/actions/runs/41300012208),
que compila EmuHawk Release y comprueba perfiles/overrides, ABI, RomLoader,
restore entre sandboxes y CHD antes de producir el ZIP publicado.

MMU, JIT, PPC y red permanecen fuera del perfil determinista. Las restantes
diferencias funcionales se inventarian por separado; no se presentan como
regresiones de esta vertical de interfaz.

# PUAE6: preview CPU/uaescsi T101

Decision: **READY para el alcance de preview declarado**. La prerelease
[`puae6-preview-d54cb741`](https://github.com/frangarcj/BizHawk/releases/tag/puae6-preview-d54cb741)
corresponde al merge MacHawk `d54cb7416cd8396d03335b950c902b5957f624c9`
del [PR #21](https://github.com/frangarcj/BizHawk/pull/21).

## Paquetes publicados

| Producto | Archivo | SHA-256 |
| --- | --- | --- |
| EmuHawk Windows x64 | `BizHawk-PUAE6-preview-windows-x64-d54cb741.zip` | `d4be7754bf1a4516587be2ce2b2860a184069e38c630f545cac44ae337ca8712` |
| Waterbox standalone macOS ARM64 | `puae6-waterbox-macos-arm64-preview-d8efc52f.zip` | `1080ccb0f01229906d57d35da01015406ac756557af079cddbb7e830bf5e3c8e` |

El ZIP Windows contiene el guest x64
`0f9f99dfcf462277538755b356ef65c7dd06dd6b046b72f95bbb8e5e15e81087`,
el lector CHD, las fuentes `source.tar.gz`
`5d6a53df58a312372f7e419663ddf3aff874f6bf2095f1c31dce962d7808e727`
y `source-x64.tar.gz`
`0d5878b5720f76d4a269e5183ff5264896f5445f6966474710a0a52ba3f89b3a`,
licencias y avisos. El paquete Darwin fija el guest
`ae086e70e6b1396c16bb57b04c1fba1dcb981842b119c442546cb12d03476c5b`.
Los dos assets descargados de la release coinciden byte a byte con los paquetes
auditados. No contienen Kickstart, juegos, HDF ni los medios privados de prueba.

## Cambios y comprobaciones

Las consultas arquitectonicas del 68030 ya funcionan con traduccion MMU
deshabilitada; LC040 sin FPU recibe la excepcion Line-F correcta y 040+FPU
conserva sus consultas. El presupuesto determinista de CPU distingue X2 y X8.
`uaescsi.device` ofrece `Disabled`, `Auto` y `Enabled`: Auto sin CD se resuelve
a deshabilitado y con CD en una topologia compatible se habilita sin cambiar la
maquina ni el controlador elegidos.

Las matrices integradas de
[solucion](https://github.com/frangarcj/BizHawk/actions/runs/41291831919) y
[Waterbox](https://github.com/frangarcj/BizHawk/actions/runs/41291832037)
terminan correctamente sobre el merge. La puerta
[Windows real](https://github.com/frangarcj/BizHawk/actions/runs/41293783929)
compila EmuHawk Release y comprueba consultas 030/LC040/040+FPU, multiplicador,
politica uaescsi, ABI/reentrada, restauracion entre sandboxes, CHD y apertura y
cierre de la GUI antes de generar el artefacto publicado.

## Limites

JIT y MMU permanecen deshabilitados. SysInfo 3.24 puede mostrar campos nulos o
incompletos con LC040 sin FPU aunque esa combinacion ya no se cuelga; para esa
utilidad antigua se recomienda 040 con FPU. Estados y peliculas dependen del
build, configuracion y medios y no son intercambiables con UAE ni otros builds.

## Entrega historica inicial

Decision: **READY para el alcance declarado abajo**. Los paquetes quedan locales
para revision; esta decision no publica una release ni modifica MacHawk.

## Paquetes

| Producto | Archivo | Fuentes declaradas |
| --- | --- | --- |
| Nativo macOS ARM64 | [puae6-native-macos-arm64-preview-445d20ff.zip](../../build/packages/puae6-native-macos-arm64-preview-445d20ff.zip) | `445d20ff` |
| Nativo Linux ARM64 | [puae6-native-linux-arm64-preview-445d20ff.zip](../../build/packages/puae6-native-linux-arm64-preview-445d20ff.zip) | `445d20ff` |
| Nativo Linux x86_64 | [puae6-native-linux-x86_64-preview-445d20ff.zip](../../build/packages/puae6-native-linux-x86_64-preview-445d20ff.zip) | `445d20ff` |
| Waterbox macOS ARM64 | [puae6-waterbox-macos-arm64-preview-8b7bea71.zip](../../build/packages/puae6-waterbox-macos-arm64-preview-8b7bea71.zip) | `8b7bea71` |

Cada archivo contiene su manifiesto, fuentes/parches, recetas y licencias. Los
nativos conservan `445d20ff`: el cambio final es del wrapper C#, sin cambios en
motor/bridge nativo. Waterbox incluye el wrapper actualizado en `8b7bea71`.
Estas notas y el cierre final del plan son posteriores y no se atribuyen al
contenido de los ZIP anteriores.

SHA-256:

```text
d81877f47376b37ef448fad8ef28cfe18197ff1be0f4ba0ae15e9b4e0eb19b85  puae6-native-macos-arm64-preview-445d20ff.zip
b14f1b72f17d6a52fe45addbeebb18ff36f6ca363fe675428d435920f9197ba4  puae6-native-linux-arm64-preview-445d20ff.zip
7556cf17d07a49339dbbdee0c167ab65f11214f2eb2603c2784ae6fee0048692  puae6-native-linux-x86_64-preview-445d20ff.zip
33a5dd455cc28ed99cc74eef9c28a109fbf408d2b96f58a414664910e99d20f3  puae6-waterbox-macos-arm64-preview-8b7bea71.zip
```

## Alcance

Core libretro nativo para macOS ARM64 y Linux ARM64/x86_64; Waterbox standalone
Darwin ARM64 con fuentes de integracion BizHawk. El ZIP Waterbox no es un plugin
para copiar en EmuHawk. Su instalacion y el uso de la integracion estan descritos
en [la guia nativa](native-preview.md) y [la guia Waterbox](waterbox-preview.md).
La ejecucion CLI del EmuHawk completo y la coexistencia de UAE/PUAE6 estan
comprobadas; no se anuncia una GUI WinForms completa en macOS.

Perfiles Amiga OCS/ECS/AGA, memoria configurable, 68000/010/020 y perfiles
030/040/060 Normal sin MMU, FPU en combinaciones admitidas, disquetes/contenedores,
HDF/IDE/volumenes/WHDLoad, CD32 y CDTV con los limites de [los contratos](../02-contracts.md).
El inventario [de paridad con PUAE5](../parity-puae5.md) explicita cada opcion,
equivalencia parcial y exclusion. No es una promesa de compatibilidad universal.
ROMs y juegos comerciales se aportan por separado y no forman parte de los ZIP.

## Cambio final y comprobaciones

Las peliculas BK2/TAS desde un estado transportan por separado sus bases
ADF/HDF/volumen importadas, sin confundirlas con escrituras posteriores. El
preflight comprueba blob, identidad, envelope/checksum y campos wrapper antes de
Attach; la carga posterior mantiene identidad estricta. No se añade validacion
previa del interior del snapshot Waterbox. Los modos PowerOn/SaveRAM y las
peliculas CD32 conservan sus regresiones dirigidas, incluidos seek, cambio de CD
y proceso nuevo. Las bases, cabeceras y envelopes incorrectos se rechazan sin
mutar la sesion en los casos comprobados.

El cierre conserva la cobertura funcional acumulada, corpus y comparaciones,
sanitizers/malformed inputs, matriz y presupuestos ya verificados, con su revision
original. El delta final de peliculas se comprueba por separado; no se presenta
toda la cobertura como una ejecucion unica de un solo SHA. No cambian CPU,
chipset, guest, runtime, codecs de disco ni caminos medidos de avance/rendimiento.
La retencion de la base importada añade su blob canonico a la memoria managed
hasta cerrar el core; no ocupa un heap adicional del guest ni avanza hardware.

Los cuatro paquetes tienen instalacion aislada y fuentes exactas comprobadas.
Los binarios nativos y guest/runtime conservan su prueba limpia por identidad;
el wrapper nuevo se recompila dos veces desde limpio en rutas fijas. Dos montajes
del ZIP Waterbox completo son identicos. El campo de esquema de preview
`binary_reproducibility_verified` permanece falso; las comparaciones reales no
cambian ese contrato del manifiesto ni prometen independencia de rutas/SDK.

## Limites y recuperacion

No se promete compatibilidad de estados/peliculas entre builds ni entre el core
UAE antiguo y PUAE6. Se conserva el Waterbox `445d20ff` anterior para reproducir
las peliculas con su distribucion original. Ante un estado de otra identidad,
reabrir el contenido y usar la distribucion correspondiente; no forzar la carga.
Los medios base no se sobrescriben: persistencia mediante exportacion/SaveRAM
explicita, con escrituras reversibles durante replay y rewind.

TINF y ETN2 multisesion completo quedan como extras T66x1/T66x2 posteriores,
segun decision expresa del usuario; permanecen rechazados. Tambien siguen
pendientes CU06 denormal y waiting_blits. WB01 Linux adicional pasa con un
mini-core C; no anuncia soporte Amiga/BizHawk en ese runtime ni cambia los ZIP.
Los estudios de JIT, RTG y multiples instancias y el subplan FMV estan cerrados:
definen decisiones y puertas futuras, sin habilitar esas capacidades. T44
terminado es un estudio MMU, no soporte MMU. Los arranques comerciales acreditados
no se extrapolan a todos los juegos; CDTV se comprueba con un programa propio y
firmware autorizado.
