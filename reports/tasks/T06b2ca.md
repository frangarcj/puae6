# T06b2ca — apertura de assets ADF explícitos

El parche0046 conecta DISK_validate_filename con el zfile/blob real en modo rb,
aceptando solo el tipo ADF. Conserva leave_open/propiedad, cursor tras CRC y
protección de escritura; publica outputs únicamente tras éxito. Error de CRC
(incluido ENOMEM) cierra el stream y devuelve0 con errno, sin simular CRC válido.
La firma interna presupone outputs válidos y outfname distinto de fname_in con
capacidad MAX_DPATH. No se llama al resolvedor cfgfile retirado.

El build global sigue fallando en disk.cpp e inputdevice.cpp, como antes; log:
`build/t06b2ca-engine-failures.log`. La unidad disk mantiene sus15 errores de
servicios desktop. Esta tarea prueba apertura, no geometría del contenido,
inserción completa, DMA, medio arrancable, enlace ni snapshot. MED01/T12 y
Waterbox mantienen sus puertas. Un blob vacío puede abrirse: no es un ADF válido.

Continuación T06b2cb: auditoría Luna de disk identifica creación/rootblock/disk_date
(reloj host), saveimage/overlay/chmod, catálogo floppybridge_getsetprofile,
abrcheck XML y disk_prevnext(_name). Excluir cada feature con sus callers; en
drive_insert preservar lectura/pistas/DMA y evitar openwritefile para M1 rb.
No sustituir creación/escritura por retornos de éxito. inputrecord y persistencia
legacy requieren cierre antes de enlace.

Validación:44 tests únicos pasan (7 VFS,1 ROM,35 plan,1 disk). El probe disk
ejecuta cuerpo real y proveedores zfile/blob/CRC reales en O0/O2 ASan/UBSan:
bytes/EOF, CRC independiente, cursor, readonly, outputs opcionales, tipos/IDs
inválidos, máximo ID, agotamiento64+1 y fallos repetidos calloc/malloc recuperados.
Luna implementó el probe y realizó revisión independiente del caller/backend.
El fallo inicial del harness fue uae_prefs grande en pila ASan, corregido a static;
no exigió cambio de producción. El recuento del test del plan se ajustó a164/158.

GEN01:39 salidas idénticas en dos builds host; HOST01:83 rutas,196 archivos,
776 includes,332 identificadores; SRC01:1134 archivos WinUAE verificados.
Estas comprobaciones no certifican emulación. Cola46/27 archivos/1242 líneas.
.gitignore conserva las reglas de outputs ancladas; build/ ignorado y los inputs
versionados musl*/dist/config.mak siguen visibles. No cambios al repo MacHawk.
