# ADR 0018: TOC global virtual de CD multisesion

Estado: aceptada para el dispositivo y la fabrica nativa/libretro (T66v9);
el loader publico C#/Waterbox conserva su puerta pendiente.

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
