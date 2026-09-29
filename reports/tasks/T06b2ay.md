# T06b2ay — Busqueda Kickstart sin rutas alternativas

Parche0031 (+5 lineas) guarda fuera de P6 las rutas derivadas de start_path_data,
los nombres por defecto y read_rom_name_guess de get_kickstart_filehandle. P6
delega una sola vez en read_rom_name(p->romfile,false) y devuelve su resultado.
No toca prefs ni busca otro nombre tras un error. El brazo upstream no-P6 queda
intacto. No se agregan aliases _stprintf/sprintf para permitir buscar rutas host.

memory.cpp completo compila por primera vez: CMake Debug/Release y objeto O0/O2.
La inspeccion nm del objeto entero confirma read_rom_name conservado y ausencia
de start_path_data/read_rom_name_guess/rom_fopen/_stprintf; no usa dead stripping
para afirmar esa ausencia. El depfile solo permite el header CCR puro de
od-win32 ya autorizado por el manifiesto, sin otros headers Windows/threaddep.
La cola queda en31 parches/20 archivos/675 lineas de delta, sin archivo adicional.

El probe incluye memory.cpp entero y usa GC de secciones solo en su ejecutable
selectivo. read_rom_name es la frontera observada de prueba: comprueba el mismo
puntero/nombre y rw=false, retorna zfile/blob reales con cursor avanzado o un
error inyectado. No es proveedor de produccion ni pretende ejecutar rommgr.
Se prueban retorno exacto, prefs intactas, cien aperturas/cierres, lectura real
desde el cursor conservado, ausencia pese a otro asset kick.rom disponible,
IDs invalidos/largos, error EIO y agotamiento de los64 streams sin reintentar.
Bytes sinteticos arbitrarios de8 bytes: no son firmware valido ni bootable.
Luna reviso el diff y ese alcance sin detectar defectos.

Verificacion: probe O0/O2 ASan/UBSan PASS;48 regresiones zfile/texto/queries ROM/
plan PASS (35 documentales),49 tests unicos contando el nuevo probe. GEN01:
39 outputs iguales entre dos builds HOST limpios; HOST01:83 rutas,196 archivos/
776 includes/332 identificadores; SRC01:1134 archivos pristine verificados.
validate y git diff --check PASS. No se modifica upstream ni MacHawk.

Build global nuevo en build/t06b2ay-engine-failures.log:10 unidades fallan,
frente a11 antes. memory ya compila; quedan inputdevice, disk, rommgr, cfgfile,
expansion, main, calc, ini, savestate y drawing. Imports memory pendientes en
build/t06b2ay-memory-imports.txt. No se declara cerrado el enlace.

## Continuacion obligatoria: carga y error de ROM

La auditoria Luna distingue esta busqueda exterior de la resolucion interna:
read_rom_name aun consulta romlist y puede invocar read_rom(rd); rom_fopen2
normaliza byteswap/wordswap/interleaved y maneja otras variantes. Revisar esa
resolucion manteniendo transformaciones autorizadas y el content ID normalizado;
no presentar este probe como prueba de aliases ni descifrado Cloanto.

load_kickstart admite :AROS. Si falla, restore_roms llama al AROS embebido,
modifica RAM y sigue por patch_kick. Debe excluirse ese fallback en P6 y hacer
que init/reset/run propaguen un error causal, no solo notify_user/error_log.
restore_roms, memory_reset y los callbacks de devices son void: el bridge aun
no tiene implementado el limite que aborte correctamente esa cadena. Cambiar
el retorno interno sin comprobarlo en ese limite no resuelve el fallo.

read_kickstart rellena lecturas cortas, redondea el tamaño y no hace fallar el
retorno por checksum; read_kickstart_version no comprueba sus32 bytes. Se deben
validar tamaños/contenido/transforms y fallos de lectura antes de certificar
firmware. Probar una ROM sintetica con checksum, ausencia/truncado, rechazo de
fallback y estado posterior al error; no descargar ROMs comerciales ni alterar
hashes golden. Compilar memory NO cierra nada de esto. T06b2az empieza por esa
frontera y conserva la puerta global; T06c/T07 requieren init y frame reales.

T06b2ay DONE; T06b2az desbloqueada.
