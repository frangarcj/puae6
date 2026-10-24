# ADR 0017: TPIDR_EL0 pertenece a Darwin

Estado: decidido e integrado mediante T28c.

## Decision

La copia Darwin ARM64 del runtime no escribe TPIDR_EL0, ni al preparar un hilo,
ni al entrar/salir del guest, ni durante syscalls/callbacks. El contexto guest
continua en x28, con los mismos marcos, registros no volatiles y offsets de ABI.
No se restaura un valor de CPU antiguo despues de una migracion del hilo.

El parche local `integration/waterbox-runtime/0002-darwin-tpidr.patch` se aplica
solo al copiar el runtime para aarch64-apple-darwin. El snapshot y MacHawk quedan
intactos. La receta verifica entradas, parche, binario generado y salida mediante
hashes; el recibo identifica el patchset y el inventario derivado.

## Motivo y prueba

[XNU fijado](https://github.com/apple-oss-distributions/xnu/blob/f6217f891ac0bb64f3d375211650a4c1ff8ca1ea/libsyscall/os/tsd.h)
lee CPU y cluster de los bits 0..11 y 12..19 de TPIDR_EL0. Cero es un valor
valido. El runtime de referencia lo interpreta como ausencia de inicializacion
y escribe un TIBptr; el interop tambien publica Context* como espejo legado.
La decision previa de no usar TLS del compilador en el guest no cubria estas
escrituras del runtime host.

Dos regresiones nativas ejercitan la preparacion con CPU0 y una entrada real del
interop. Restauran el registro antes de aserciones o asignaciones host. WB13,
WB14 y WB15 conservan su puerta de ejecucion real, callbacks y restauracion en
otro proceso; estas regresiones nativas no las sustituyen.

## Limite de la atribucion

La escritura es una infraccion ABI concreta; no demuestra por si sola la causa
del SIGBUS de malloc. El slot invalido del informe tampoco demuestra un x28
guest filtrado. Las puertas de CD, peliculas y HRTMon deben pasar completas con
el runtime corregido antes de cerrar sus tareas. No aumentar heaps, repetir
knobs ni ocultar fallos porque un control aislado pase.
