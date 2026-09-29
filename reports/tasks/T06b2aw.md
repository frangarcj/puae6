# T06b2aw — Texto legacy byte/ASCII

Se implementa en ports/portable/tchar.h el subconjunto legacy puro requerido por
memory/rommgr/calc/cfgfile/inputdevice: longitud, copia, concatenacion, strncpy,
busqueda de caracter/subcadena y span. Son operaciones de bytes terminados en
NUL, con capacidad y no solapamiento a cargo del caller; strncpy conserva su
padding y ausencia de terminacion al truncar. No son validadores UTF-8 ni
sustituyen p6_tchar_copy, que sigue siendo acotado y transaccional.

Case-fold, comparacion case-insensitive y clasificacion decimal/hex son ASCII
explicito. Las comparaciones leen unsigned bytes; los bytes no ASCII permanecen
opacos. No se usa locale, ctype/strcasecmp del host ni normalizacion Unicode.
El cuerpo es comun a C y C++; las busquedas libc conservan las sobrecargas const
de C++. No se agregan aliases de formato, numeros ni acceso a archivos.

Luna audito los callers y la diferencia entre _stprintf/sprintf y snprintf,
asi como el parsing _tstof dependiente del locale. Esas fronteras siguen abiertas;
no habilitarlas silenciosamente para hacer desaparecer errores del compilador.
manifests/host-surface.json registra el subset y sus limites. No hay nuevos
parches upstream: la cola sigue en30 parches/20 archivos/670 lineas de delta.

Pruebas: C/C++ O0/O2 con ASan/UBSan,65536 pares de bytes para comparaciones,
dominio signed/unsigned mas EOF para clasificadores, evaluacion unica, prefijos,
NUL, UTF-8 opaco, copia/concat con centinelas, strncpy n=0..5 con padding y sin
terminador en truncacion, busquedas vacias/no encontradas y punteros mutables.
El test publico de copia validada conserva entradas invalidas/overflow/overlap.
Las pruebas varian locale dentro de sus procesos; no cambian el del motor.

El consumidor real extrae sin reescribir getromlistbyident/notcrc32/
getfrombydefaultname de rommgr.cpp, con tipos upstream y metadatos sinteticos.
Comprueba filtrado por modelo/tipo/version, orden por subversion, lista instalada
frente a todas las entradas, grupos excluidos y nombre default ASCII casefold
con tamaño minimo y CRC sentinel. No hay ROM bytes ni carga de firmware aqui.
El nombre directo se consulta con versiones no coincidentes porque el cuerpo
fijado sobreescribe ok=2 con ok=1 si coincide version; no se corrige esa conducta
en una tarea de texto. El primer probe lo detecto (build/t06b2aw-probes.log).
Otro fallo del harness esperaba el segundo NUL del padding en strrchr(...,0);
el contrato correcto devuelve el primer terminador (build/t06b2aw-text-test.log).

Verificacion:43 tests PASS (8 de host y35 del plan), incluidos texto, queries ROM,
sysdeps real y tabla readcpu completa comparada con HOST. CMake p6_portable_probe
y CTest portable-services Debug/Release PASS. GEN01:39 outputs iguales entre
dos builds HOST limpios. HOST01:83 rutas,196 archivos/776 includes/332 ids.
SRC01:1134 archivos pristine verificados. No se modifica upstream ni MacHawk.

Build global antes/despues conservado en build/t06b2av-engine-failures.log y
build/t06b2aw-engine-failures.log: siguen fallando11 unidades. Memory pasa de17
diagnosticos a5, todos _stprintf en busqueda alternativa de ROM. Rommgr muestra
ahora5 _stprintf y scan_arcadia_rom sin guarda. Calc muestra3 _stprintf y2
_tstof. Los recuentos anteriores rommgr/calc estaban cortados por error-limit,
no se presentan como medida exhaustiva de reduccion de errores. Resto: input,
disk, expansion, cfgfile, main, ini, savestate y drawing; enlace/servicios aun
pendientes. El gate global permanece intacto en T06b2ax y T06c/T07.

T06b2aw DONE; T06b2ax desbloqueada. validate y git diff --check PASS.
