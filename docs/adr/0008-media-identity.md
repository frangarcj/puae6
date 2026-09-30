# ADR 0008: medios y firmware por identidad

## Contexto

`p6_load` recibe el firmware y una lista de imagenes de disco con un `content_id` de 32 bytes. El contrato
(docs/02-contracts.md) dice que un path del host no es identidad ni parte del estado y que un medio ausente o distinto
es un error. El motor de WinUAE, en cambio, abre disquetes por nombre a traves de `zfile`, no valida tamaño ni formato
(una imagen de 901120 bytes con la marca `UAE-1ADF` y 0xFFFF pistas desborda la tabla de pistas y termina en SIGSEGV; una
de 4 MiB corrompe la estructura de la unidad en silencio), y `default_prefs` deja `df0.adf`..`df3.adf` como nombres de
disco. Mediciones y referencias de codigo: informe de investigacion de T12 (resumen en reports/tasks/T12a.md).

## Decision

1. **El bridge es el unico guardian.** `validate_media` (bridge/media.cpp) corre antes de tocar el motor y rechaza, por este
   orden y sin efecto alguno sobre la instancia: mas de 16 medios (CAPACITY), array nulo (BAD_ARGUMENT), tipo distinto de
   ADF (UNSUPPORTED), flags no nulos, imagen ausente y ids duplicados (BAD_ARGUMENT), tamaño distinto de 901120 bytes o
   cabecera de ADF extendido / ejecutable Amiga (WRONG_MEDIA) y, al final, un `content_id` que no es el SHA-256 de los
   bytes (WRONG_MEDIA). El hash se calcula solo para listas que ya pasaron las comprobaciones baratas.
2. **Identidad = SHA-256 de los 901120 bytes crudos.** Para el ADF la forma normalizada es la imagen; un ADZ o DMS futuro
   se identificaria por los bytes descomprimidos. El firmware se identifica por el SHA-256 de su buffer, calculado en la
   carga; no cambia la ABI. `Session` guarda `firmware_sha256`, `media_count` y los ids en el orden del array para la
   cabecera de estado de T14 (`media_manifest_hash`).
3. **Nombre logico = `media-` + 64 hex minusculas** (70 caracteres, sin punto). Sin punto no hay extension reconocible, asi
   que el motor no puede tratar la imagen como PC/ST/IMG. `p6_blob_register` copia la imagen al almacen de blobs del
   motor (id valido: alfanumerico, `_`, `-`, un punto simple; nunca rutas ni URI). Nunca existe un path del host.
4. **Sin nombres implicitos.** `build_preferences` vacia `floppyslots[0..3].df` (upstream pone `df0.adf`..`df3.adf`) y
   fija `forcedwriteprotect` en las cuatro unidades; `check_preset` exige ambas cosas y el perfil las declara como
   `override`. El motor ya fuerza proteccion contra escritura por tres vias (rama P6 de `DISK_validate_filename`, blob sin
   `data/allocsize` que devuelve EROFS y `drive_write_data` que sale si la unidad esta protegida); la cuarta es defensa en
   profundidad y hace visible la decision.
5. **Vida util.** Los buffers del llamante deben vivir hasta `p6_unload` (contrato ya escrito), pero el bridge no guarda
   punteros: el motor trabaja sobre su copia (14,4 MB para 16 discos, que cuentan para heap y snapshot de Waterbox, T19).
   Una carga que falla libera lo registrado (`release_engine` limpia los blobs); un fallo de validacion no registra nada.
6. **Capacidad.** `p6_caps.max_media = P6_MAX_MEDIA (16)`. El bit `P6_CAP_MEDIA_EVENTS` sigue apagado: los eventos de
   mount/eject son T12b. El almacen tiene 32 assets, de sobra para firmware + 16 discos.

## Limites

- Solo ADF de doble densidad de 901120 bytes. ADF extendido (`UAE-1ADF`/`UAE--ADF`), HD, ST/IMG, IPF, SCP, ADZ y DMS quedan
  fuera del MVP hasta acotar `read_header_ext2` (parche opcional 0063 del informe) o añadir una biblioteca.
- Dos discos con los mismos bytes no son dos medios (id duplicado): el llamante cambia de disco por indice, no por copia.
- El motor sigue aceptando imagenes de otros tamaños si alguien las registrara fuera del bridge; la barrera esta en `p6_load`.
- El disco de arranque (montado antes del primer frame) y los cambios en caliente llegan con T12b.

## Alternativas descartadas

- Confiar en el `content_id` del llamante: no detecta "disco equivocado" en la carga y deja pasar un ADF extendido hostil.
- Reutilizar SHA-1/CRC32 del motor: la identidad del contrato es SHA-256 y los tests de Python ya la usan.
- Pasar por el motor un path virtual con extension `.adf`: reabre la rama PC-format y el reconocimiento por extension.
