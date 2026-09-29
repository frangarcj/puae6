# T06b2bg — Contenedor A500 y cadena ROM real

Parche0035 limita load_kickstart P6 a raw normalizado256/512 KiB y KICK de
1760*512 bytes con magic. Exige contenedor entero: sufijos, truncados y magic
incorrecto fallan. ELF/HUNK, GVP +8 y combinados1/2 MiB retornan UNSUPPORTED;
no llaman al loader ejecutable ni asignan bancos extendidos desde esta ruta.
La rama no-P6 se conserva. El checksum ya estricto se complementa con firma
11 11/11 14 y longitud big-endian en size-20, conforme al helper de rommgr.
read_kickstart mantiene su contrato de prefijos/lecturas, sin validacion global.

restore_roms rechaza requisitos conocidos cpu&15 (020/030 y hardware A4000)
con UNSUPPORTED antes de patch_kick; vuelve a proteger ROM, sin reiniciar ni
adaptar preferencias. No se deduce compatibilidad universal del nombre de
modelo. La politica y sus limites quedan registrados en ADR0001.

El reproducer build/t06b2bg-envelope-before ejecuta los cuerpos anteriores con
zfile/blob/checksum reales y metadata observada. Una ROM sintetica512 KiB valida
mas un byte termina status0/patches1; failure.log conserva el fallo de asercion.
La misma prueba con0035 exige READ_ERROR y cero parches. No usa ROM comercial.

Memory completo compila en CMake Debug/Release. Su prueba O0/O2 inspecciona nm
sin dead stripping y ahora exige tambien ausencia de read_executable_rom y
uae_restart, ademas de los imports host/fallback excluidos anteriormente.
Las53 regresiones existentes pasan (35 documentales): loader, checksum, origen,
CPU guard, normalizacion/keyring, zfile, texto y consultas ROM. GEN01 confirma39
outputs identicos en dos builds HOST limpios; HOST01 mantiene83 rutas y196
archivos/776 includes/332 ids; SRC01 verifica1134 archivos pristine.
Cola35 parches/21 archivos/884 lineas de delta;0035 añade43, sin nuevos archivos
upstream. El cambio atomico comprende parche+metadata, dos fixtures integrados,
runner, comprobacion de imports y decision; no incorpora refactorizaciones.

La integracion implementada por Luna pasa O0/O2 con ASan/UBSan; total54 tests
unicos junto a las53 regresiones. Extrae cuerpos reales de rommgr y memory y
comparte el fixture de lifecycle, sin reemplazar resolucion/descifrado/checksum.
Prueba raw256/512, byte/word swap con fixtures generados independientemente,
Cloanto con clave explicita, KICK exacto, truncados/sufijos, magic, longitud
embebida, checksum, formatos excluidos y flags CPU1/2/3/4/8. Comprueba bytes,
mask/tamaño/version, cero parches en error, preferencias A4000 intactas,
metadata CPU0 aceptada, recuperacion y disponibilidad de todos los handles.
Los fallos estructurales se resealan con un checksum independiente para que
la prueba no pase accidentalmente por rechazo de checksum. La metadata es
observada, sin matches SHA reales. Revision final y diff-check pasan.

No se afirma boot, frame, corpus comercial, content ID publico ni lifecycle
p6_load/reset/run. Metadata y servicios de maquina son fronteras observadas;
no se ejecuta memory_reset completo. La ROM rechazada puede haber escrito el
banco; la guarda CPU consume su estado causal, sin promesa de rollback. El motor
completo sigue pendiente de enlace y de las diez unidades con errores de la
ultima compilacion global; la presente tarea no la repite ni declara cerrada.
T06b2bh/T06c conservan estas puertas. Upstream pristine y MacHawk no se modifican.

T06b2bg DONE; T06b2bh desbloqueada. validate PASS.
