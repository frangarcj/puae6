# T06b2be — ROM y claves explicitas

Parche0034 hace que read_rom_name P6 resuelva solo el ID solicitado, sin consultar
romlist/read_rom para sustituir o reensamblar otro asset. Conserva rom_fopen2:
byte swap, word swap y reconocimiento de interleaved siguen siendo del motor.
Fallos de buffers temporales, transferencia o publicacion retornan NULL; una
transformacion detectada no puede caer al original si falta memoria para su
stream normalizado. La validacion de todo contenedor raw/KICK permanece abierta.

Las claves tienen ahora una entrada explicita: load_keyring limpia el anterior
y carga solo su path no vacio, como ID blob; NULL/vacio descarga. Se excluyen
addkeydir y todos sus callers P6, sin implementar un buscador vacio ni consultar
paths/target_load_keyfile. addkeyfile comprueba alloc y transferencia completa;
addkey rechaza longitud no positiva, conserva dedup y ownership de cuatro slots.
La API sigue devolviendo conteo de claves. El bridge publico aun debe conectar
registro/desregistro y distinguir motivos de error; esto no implementa p6_load.

AMIROMTYPE1 exige cabecera completa y payload256/512 KiB exacto antes de asignar.
Lee a scratch, descifra, comprueba checksum y publica con zfile_fopen_data usando
el ID original. No ignora el error del decoder ni entrega tmp.rom vacio. Cada
clave candidata requiere checksum, ademas de firma o SHA reconocido. Una clave
con prefijo plausible y contenido incorrecto permite probar la siguiente; el
ultimo XOR fallido se deshace tambien. Los blobs originales son de solo lectura.
Esto no convierte checksum en prueba de firmware arrancable o compatibilidad.

El reproducer build/t06b2be-decode-before.cpp extrae read_rom_name anterior,
con apertura zfile real y un decoder observado que retorna0. Una cabecera
AMIROMTYPE1 con payload256 KiB aun produce stream: la asercion falla en el log
homonimo. El cuerpo parcheado pasa con las mismas fronteras en el caso after.
Es una prueba del control de publicacion; no se anuncia ese callback de fallo
como descifrador real. La preparacion inicial del harness corrigio la firma de
read_rom para usar exactamente la declaracion del header (romdata*,bool).

Regresiones existentes:52 tests PASS (35 del plan), incluyendo memory loader,
checksum, CPU guard, zfile, texto y queries ROM. Memory incluye ahora frontera
addkeydir que aborta si alguien reintroduce su llamada; nm del objeto completo
tambien exige ausencia del import. CMake memory Debug/Release PASS. Compilacion
rommgr queda con5 errores frente a6: cuatro _stprintf y scan_arcadia_rom, todos
ya existentes; se elimina el _stprintf del buscador de claves. Evidencia en
build/t06b2be-engine-failures.log. No se declara cerrado ese objeto ni el motor.

GEN01:39 outputs identicos entre dos builds HOST limpios. HOST01:83 rutas,
196 archivos/776 includes/332 ids. SRC01:1134 archivos pristine verificados.
Cola34 parches/21 archivos/841 lineas de delta;0034 añade89, sin archivo adicional.
ADR0001 registra la decision de assets/claves explicitos y lifecycle pendiente.
La prueba nueva de Luna pasa O0/O2 ASan/UBSan: total53 tests unicos. Extrae cuerpos
reales de transformaciones, keyring, decoder, checksums y queries SHA; zfile/blob
y CRC/SHA son reales. Usa metadata vacia explicita (no valida matches SHA conocidos)
y una entrada romlist que coincide con el ID: read_rom/my_issamepath/addkeydir
abortarian si se invocaran. Fixtures256/512 prueban raw, byte/word swap, Cloanto,
clave ausente, incorrecta plausible sola y seguida de correcta, checksum corrupto,
payloads mal dimensionados y KICK intacto. Comprueba ID publicado y bytes encoded
originales sin cambios. Lifecycle cubre dedup, cuatro slots, overflow y descarga.

OOM se inyecta por separado en scratch y en el zfile P6 real compilado como unidad
independiente. Fallan owner y data del resultado para swap y Cloanto; exige NULL,
recuperacion al desactivar fallo y disponibilidad simultanea de64 handles blob.
No simula fallo de fread del proveedor inmutable, ni prueba interleaved1 MiB,
resolucion de metadata SHA conocida o el encadenamiento completo con memory_reset.
Los tests zfile existentes conservan su cobertura de ownership/asignaciones.
El harness inicial corrigio IDs con separadores, que el contrato blob rechaza;
las claves usan P6_BLOB_KEY. No se cambio produccion para admitir esos IDs.
Revision final, validate y git diff --check PASS. Upstream pristine y MacHawk
permanecen intactos. Los logs de las ejecuciones correctas son temporales.

T06b2bf conserva estructura/modelo/content ID y validacion de contenedores enteros,
incluido exceso de bytes raw/KICK, cierre de servicios y propagacion publica.
read_rom_file legacy sigue fuera de la ruta explicita, no es una API portable
alternativa validada. No se afirma boot, frame, ROM comercial ni Waterbox.

T06b2be DONE; T06b2bf desbloqueada. T06b2bd conserva su puerta global original.
