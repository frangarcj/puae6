# ADR 0018: TOC global virtual de CD multisesion

Estado: aceptada para el dispositivo privado; carga/restauracion y transportes
publicos multisesion conservan sus puertas pendientes.

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
ASan/UBSan. CdNrg conserva SingleOnly por defecto; el opt-in privado, identidad
y rebind de T66v3 no habilitan carga publica multisesion.
