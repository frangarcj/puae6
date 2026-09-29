# T06b2bc — Lector ROM sin reparacion silenciosa

Parche0033 hace efectivo el rechazo del lector Kickstart P6: el conteo real no
se redondea ni se repara con kickstart_fix_checksum. Acepta transferencias
completas de256/512 KiB;256 en buffer512 solo con espejo permitido. La consulta
de version exige los32 bytes y cierra el handle tambien en fallo, sin modificar
el estado causal de la carga. Un checksum solicitado incorrecto devuelve0 aunque
no exista exec.library. load_kickstart conserva READ_ERROR y restore_roms no
ejecuta patch_kick; las guardas CPU de T06b2ba siguen consumiendo ese resultado.

Checksum usa bytes logicos despues de descifrado. Se conservan el orden upstream
de espejo/descifrado y el salto de512 y limite256 KiB de KICK. Esto no habilita
A1000 ni valida todo contenedor posible. No se cambia la rama no-P6 del lector.

El checksum upstream promovia el primer byte a signed int antes de multiplicar
por2^24. UBSan falla con cuatro bytesFF en el reproducer
build/t06b2bc-checksum-before.cpp y su log. Se convierte ese operando a uae_u32,
sin sustituir el algoritmo end-around-carry. ADR0004 acepta rommgr.cpp como
archivo21, manteniendo la alarma1500. Cola33 parches/21 archivos/752 lineas de
delta;0033 añade22 lineas de delta (incluida la sustitucion aritmetica).

El probe memory existente ahora ejecuta los cuerpos reales del checksum junto
al loader y zfile/blob. Fixtures sinteticos256/512 usan suma independiente64-bit,
con high bytes/carries, corrupcion y all-zero sin exec.library. Diez longitudes
truncadas cubren cabecera, prefijo y limites256/512;80 consultas por longitud
detectan fugas de streams. Prueba espejo256, rechazo de medio segmento noalias,
KICK y recuperacion tras fallos. Las fronteras de metadata/dispositivos siguen
siendo observadas/abort, no proveedores de produccion. El test ampliado contra
memory anterior falla en la primera consulta de version vacia (antes devolvia
true), conservado en build/t06b2bc-loader-before y su log.

Luna implemento un probe independiente del checksum real, con suma64-bit como
oraculo, patronesFF/mixtos y corrupcion a256/512 KiB. Ambos probes pasan O0/O2
con ASan/UBSan. Su revision del cambio y del harness no encontro defectos dentro
del alcance; dejo explicitas las rutas de normalizacion aun no probadas.

memory.cpp completo compila Debug/Release. Revalidacion rommgr en
build/t06b2bc-rommgr-failures.log conserva exactamente los seis diagnosticos del
ultimo build global T06b2ay: cinco _stprintf y scan_arcadia_rom. No se afirma
compilacion completa de rommgr ni enlace del motor. GEN01:39 outputs iguales en
dos builds HOST limpios. HOST01:83 rutas,196 archivos/776 includes/332 ids.
SRC01:1134 archivos pristine verificados.52 tests PASS, incluidos35 documentales
y las regresiones loader/checksum/CPU guard/zfile/texto/queries ROM. validate y
git diff --check PASS. No se modifican upstream pristine ni MacHawk.

T06b2bd conserva la puerta global: validar contenedor entero (incluido exceso de
bytes), aliases/byte-word swap/interleaved, descifrado y errores de keyring,
estructura/perfil/content ID, init/reset/run publico y restantes servicios A500.
Estas pruebas no demuestran firmware arrancable ni compatibilidad comercial.
Un fallo puede escribir parte del banco antes de devolver error; se bloquea CPU,
no se promete rollback de carga. El avance de frame y Waterbox siguen pendientes.

T06b2bc DONE; T06b2bd desbloqueada. La tarea agregada T06b2bb mantiene sus puertas.
