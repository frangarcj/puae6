# ADR 0018: TOC global virtual de CD multisesion

Estado: aceptada para dispositivo, fabrica nativa/libretro y loader publico
C#/Waterbox, incluidas sus puertas de restauracion en proceso nuevo.

## Contexto y fuentes

El frontend consume `sys_command_cd_toc` y el TOC de `sys_command_info`, sin
seleccion de sesion. El snapshot WinUAE/PUAE no proporciona un oraculo multisesion
fiable: sus grupos DAO sobrescriben la tabla global.

[READ_TOC_EX oficial, formato 0](https://learn.microsoft.com/en-us/windows-hardware/drivers/ddi/ntddcdrm/ns-ntddcdrm-_cdrom_read_toc_ex)
describe todas las sesiones y el leadout de la ultima sesion completa para AA.
[ECMA-168, segunda edicion, anexo C.3](https://www.ecma-international.org/wp-content/uploads/ECMA-168_2nd_edition_december_1994.pdf#page=128)
mantiene la numeracion de pistas entre sesiones. Son fundamento de esta politica
virtual; no prueban el comportamiento de una unidad CD32/CDTV real.

## Decision

`akiko_toc` proyecta todas las pistas globales 1..N en el `cd_toc_head` existente,
con INDEX01 absolutos y el A2 final como leadout. Conserva el orden y campos
legacy, incluido CONTROL agregado y los bytes de discos de cero/una sesion
explicita. Sus A0/A1/A2 agregados son un resumen virtual, no Q de un lead-in.

Las consultas son puras: no seleccionan sesion, mueven cursor o leen fuentes.
`akiko_session_toc` sigue devolviendo la tabla explicita por ordinal; consultarla
no cambia el resultado global. Su firstaddress es INDEX00, no Session Info MMC.
Los subcanales almacenados siguen prevaleciendo sobre Q sintetizado.

Los LBAs no se compactan. El hueco entre A2 e INDEX00 siguiente es Range, sin
inventar payload ni tiempos fijos. El dispositivo describe todos los sectores
de un burst multisesion antes de leer: un cruce de hueco no hace IO y conserva
salida y posicion. No se añade ese recorrido al camino de una sola sesion.

## Limites y comprobacion

Session Info (formato 1) y FullTOC fisico (formato 2) no estan implementados en
esta API. El segundo necesita Q de los lead-in, que este resumen no puede
reconstruir. No se añaden stubs ni seleccion emulada. Quedan sin acreditar la
seleccion, TOC y comportamiento de huecos del hardware CD32/CDTV real.

MED01 prueba TOC numerico, comparacion legacy byte exacta, consultas independientes,
lecturas de ambas sesiones y rechazo sin IO por el dispositivo real, con
ASan/UBSan. CdNrg conserva SingleOnly por defecto; CdImage usa el opt-in
ExplicitSessions, alias de valor 1 de ExplicitPrivate, con la identidad y rebind
de T66v3 intactos. LR01 comprueba load/serialize/restore frio y futuro exacto;
la fixture no acredita boot de aplicacion ni el comportamiento de hardware.

## ETNF global Canonical150 privado (T66w1)

Decision implementada y comprobada en los componentes privados de T66w1.

[CDI2Nero f43561d](https://github.com/sizious/cdi2nero/tree/f43561d27e9a75061171f0e69a872c9439ccf898)
es un productor GPL fijado: en modo TAO emite un grupo ETNF/SINF por sesion,
copia `start_lba` global y omite los pregaps del payload. Sus writers CUES/DAO
permiten contrastar esas coordenadas usando la misma CDI original. Con pregap
150, `start_lba` coincide con INDEX01 en LBA; no es el sector compacto de
VCDImager y no se le suman 150 sectores por pista.

El perfil privado `EtnfGlobalCanonical150` distingue esa normalizacion de DAO,
ETNF compacto y ETN2. Solo admite varios grupos ETNF/SINF puros y requiere una
politica de propietario e identidad propias. La politica anterior (1)
continua rechazandolo antes de leer payload; T66w2 añade CanonicalSessions (3)
en la fabrica nativa/libretro, manteniendo las identidades. TINF y ETN2 multisesion conservan
su puerta pendiente.

ETNF no conserva la longitud original del pregap ni el CUE0 fisico. Este perfil
declara pregaps sinteticos de 150 sectores y una frontera virtual firmada
`cue0 = INDEX01 - 150`: inicialmente -150; las siguientes son el INDEX00
canonico. El Track inicial conserva el recorte legacy a LBA 0. La frontera no
afirma el comienzo fisico del lead-in ni recupera Q original. DAO mantiene su
CUE0 suministrado, y el validador de Session no cambia.

Los datos determinan A2 y los LBAs globales se conservan. El intervalo hasta
INDEX00 de la sesion siguiente sigue siendo Range sin IO. El TOC global y el Q
sintetico pertenecen a la politica virtual de esta ADR; FullTOC fisico sigue
sin implementarse. La fixture original, el productor fijado y los tests de
metadata/propietario hacen reproducible el contrato; esta puerta privada no
acredita los loaders publicos ni restauracion Waterbox.

T66w3 implementa la normalizacion host DiscSystem con tabla inmutable y proveedor
disperso. Su marca Canonical150 distingue el origen sintetico; los bytes/Q/SBI
viajan completos en V3 y conservan precedencia sin resintesis del guest. El
dominio managed P6ETGM2448 distingue este perfil de DAO, cuyos IDs no cambian.
Esta puerta del host tampoco sustituye el snapshot publico Waterbox.
T66w4 comprueba ese snapshot por XML, CD directo y catalogo mixto, con Q/SBI,
lease, SaveRAM, futuro exacto y negativos de identidad, sin reconstruir TOC fisico.

## Productor original Nero ETN2: contrato parcial, sin habilitar formatos

El [instalador oficial Nero Burning ROM 2025 27.0.1000](https://ecloud.nero.com/index.php/s/pBxLJzgPaNtAJBi)
contiene codigo original inspeccionable sin ejecutarlo ni instalarlo. Su SHA-256
es `3fbc9dd234eb1dec0d5a51bd2c9df2dfad0144c5cdb3d1d353ba91c9f2618f94`
(275434744 bytes). Dentro del contenedor PE/7z,
`NeroInstaller/NeroBurningROM.zip` tiene SHA-256
`9fffbea66c939f3933cd4c1efe5b6e44491f21865a35035e8eb75a10611ebd52`.
Los componentes siguientes son PE32 x86, version 27.0.1.3, bajo el directorio
`Nero Burning ROM/` de ese ZIP:

| Componente | SHA-256 |
| --- | --- |
| `uimage.dll` | `59522845afe8a67110b5934cb8583b1e172994f948420b67c2abcbbbd77ba623` |
| `NeroAPIEngine.dll` | `5c09c57a441d34af2a75d82e74acce412c1bbeb6d31af7645849a7f060039879` |
| `uImageGen.dll` | `5d6523cf6dc558667ea3546a9b893d2b0cf4e1ee413aaf4586d3e92e1133587c` |

Las direcciones siguientes son VA con base de imagen `0x10000000`; restar esa
base da el RVA. Son puntos de codigo privado, no nombres de funciones deducidos
del simbolo exportado mas cercano que muestra el desensamblador.

En `uimage.dll`, `0x10005150..0x100052aa` construye la cabecera ETN2,
longitud `32 * numero_de_entradas`, y escribe cada registro de 32 bytes mediante
el mismo sink que recibe el payload. Comprueba el numero de bytes escritos.
Sea `T` la estructura interna de pista de 40 bytes copiada por esa rutina:

| Offset del registro ETN2 | Escritura observada desde T |
| --- | --- |
| 0 | BE64 de `T+0` |
| 8 | BE64 de `T+8` |
| 16 | BE32 de la palabra sin signo de `T+16` |
| 20 | BE32 de `T+24` |
| 24 | BE32 del byte sin signo de `T+28` |
| 28 | BE32 de `T+32` |

El inicio de pista `0x10004599..0x1000467a` obtiene la posicion del sink por
`0x10007b87` y la guarda en `T+0`. El final de pista
`0x100046e4..0x10004785` guarda en `T+8` la posicion final menos la inicial.
Esto atribuye los dos primeros campos a posiciones/longitudes del archivo;
todavia no identifica su frontera como INDEX00 o INDEX01. `T+24` recibe el
cursor del driver en `+0x48` (`0x10004632..0x10004637`), y la escritura de
payload `0x1000467d..0x100046e1` incrementa ese cursor con las unidades recibidas.
`T+32` procede de sumar una tabla del descriptor de entrada: contador en `+0x2c`
y elementos de 32 bits desde `+0x34`, mediante `0x10002e22`. No se ha atribuido
el significado de esa tabla ni del byte que llega a `T+28`. Por tanto, el
escritor no demuestra que los ocho bytes finales sean siempre reservas cero.
La restriccion del perfil canonico actual sigue siendo una restriccion de ese
perfil, no una descripcion general de Nero.

En este camino del footer hay una tabla ETN2 para toda la coleccion de pistas.
Despues, `0x1000548e..0x1000553d` recorre otra coleccion del driver (`+0x4c`)
y escribe un chunk SINF de cuatro bytes por entrada. Esta organizacion permite
un ETN2 seguido de varios SINF; no debe imponerse el emparejamiento de un
ETNF/SINF por sesion del productor CDI2Nero. Tampoco demuestra que todos los
caminos de Nero emitan ese footer.

La globalidad del cursor entre sesiones sigue siendo una hipotesis: la rutina
`0x10004583` cambia el campo de sesion y pone a cero su contador de pistas,
sin borrar `+0x48`; `0x10005a0f` si borra ese cursor y las colecciones.
Las ramas `0x10004815..0x100048dc` tambien lo ajustan segun el tipo de medio.
Falta demostrar la secuencia de llamadas efectiva para dos sesiones propias y
contrastar los campos con su TOC original. Estos hechos no fijan pregaps,
CUE0/A2 fisicos ni Q, ni autorizan trasladar Canonical150 de ETNF a ETN2.

TINF esta identificado como **lector**, no como productor: en
`NeroAPIEngine.dll`, `0x10069033` lee cabeceras y
`0x1006a0c7..0x1006a153` selecciona registros TINF de 12 bytes, ETNF de 20 o
ETN2 de 32. La conversion `0x1006dfbb` amplia los offsets y longitudes BE32
de los dos primeros formatos a 64 bits sin ajustar pregap en esa conversion.
No establece la semantica INDEX00/INDEX01 del offset. `uImageGen.dll`,
`0x10001f82` y `0x100025c2..0x100026d4`, localiza TINF y recorre sus registros
para formatear texto; esa ruta no es un escritor NRG.

Decision: conservar los rechazos TINF/ETN2 multisesion y las identidades actuales.
El siguiente contrato requiere una salida propia del productor autenticado,
con configuracion y TOC independientes que distingan los significados pendientes;
una etiqueta, un literal o esta lectura estatica parcial no sustituyen ese
oraculo. No se incorpora codigo ni binarios propietarios al proyecto y esta
decision no cierra T66.

## Limite de las referencias PUAE5 y WinUAE 6

Los parsers `parsenrg` de [PUAE5 fijado](https://github.com/libretro/libretro-uae/blob/6536174a80d74e6c325aaa5390ff091fac8761d0/sources/src/blkdev_cdimage.c#L2000)
y [WinUAE 6.0.3](https://github.com/tonioni/WinUAE/blob/857f48fcb09399d4d95527e29a2fdeaedb41f827/blkdev_cdimage.cpp#L1972)
no contienen una rama TINF. Ambos leen ETN2 solamente mientras `gotsession`
es falso y fijan el numero de pistas con el primer SINF; el parser de la
referencia MacHawk conserva esas mismas condiciones. No reconstruyen una tabla
completa de sesiones ETN2 y no sirven de oraculo para esa normalizacion.
Esto permite afirmar soporte ETN2 de una sesion, no soporte multisesion completo;
no excluye que algun archivo multisesion pueda abrirse parcialmente.

TINF y ETN2 multisesion completo amplian la paridad con esas referencias.
Su implementacion requiere el contrato independiente descrito arriba. Este
limite de la referencia permite separar su implementacion de la primera entrega:
el usuario aprueba aplazar TINF a T66x1 y ETN2 multisesion a T66x2, ambos despues
de T41. Siguen pendientes, con las cinco puertas originales de T66 y el contrato
independiente requerido; no se atribuye exito a esas variantes. T66 conserva
ISO/CUE/CCD/MDS/CHD, NRG DAO y ETNF global multisesion, ETNF/ETN2 de una sesion,
CDDA, NVRAM, frontends y arranque/restauracion CD32. Se mantienen los rechazos de
las variantes aplazadas y sus regresiones.
