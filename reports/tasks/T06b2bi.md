# T06b2bi — Objeto rommgr y dispositivos excluidos

Parche0036 conserva getromname y su ABI legacy de buffer a cargo del caller.
Los sufijos enteros usan snprintf sobre64 bytes, suficientes para revision de
dos int32 y tamaño KiB; no se agrega alias general _stprintf ni conversion de
coma flotante/locale. Nombre y partnumber siguen siendo bytes opacos. Se evita
el wrap de size+1023 usando cociente/resto; para metadata ordinaria la salida es
la misma. Esto no convierte el destino legacy en un buffer publico acotado.

configure_rom P6 retorna0 para ArcadiaBIOS/GAME/ALG una vez encontrado el asset
instalado y antes de modificar prefs. No deriva archivos NVRAM/video del host.
El scanner Arcadia queda bajo su feature ARCADIA, sin stub ficticio. El perfil
actual la tiene apagada. No se habilitan dispositivos porque exista metadata.
La seleccion de otros perfiles y la API publica siguen siendo puertas posteriores.

La auditoria Luna inventaria callers getromdatabyname, romwarning y scanner
(rommgr), mas parser cfgfile: sus buffers individuales son MAX_DPATH. La
concatenacion de multiples nombres en romwarning tiene un limite propio previo
que no se resuelve aqui. La decision y limites constan en ADR0001 y el contrato
host. No se cambia upstream pristine ni MacHawk.

rommgr completo compila en CMake Debug/Release, frente a cinco errores previos.
El nuevo test_rommgr_source compila O0/O2 e inspecciona imports del objeto entero,
sin ocultarlos por dead stripping. Exige ausencia de _stprintf, scan_arcadia_rom,
fetch_nvrampath, fetch_videopath, addkeydir y target_load_keyfile. Sus depfiles no
incluyen threaddep ni headers Windows fuera del CCR puro ya autorizado. Quedan
imports reales como fetch_rompath/my_issamepath/gui_message/descramble_nordicpro
para el cierre del enlace y rutas legacy; no se afirma que el objeto sea un core.

Revalidacion global antes/despues en build/t06b2bh-engine-failures.log y
build/t06b2bi-engine-failures.log:10 ->9 unidades fallidas. Pendientes inputdevice,
disk, cfgfile, expansion, main, calc, ini, savestate y drawing. Compilar rommgr
no sustituye el cierre de servicios ni de validacion publica firmware/content ID.
T06b2bj conserva la puerta completa anterior; T06b2bh queda como agregado.

54 regresiones existentes pasan, incluidas35 del plan y cadena ROM real.
GEN01:39 outputs iguales en dos builds HOST limpios. HOST01:83 rutas y196
archivos/776 includes/332 ids. SRC01:1134 archivos pristine verificados.
Cola36 parches/21 archivos/917 lineas de delta (+33), sin archivo upstream nuevo.
El test de objeto completo y el probe de comportamiento de Luna añaden dos
comprobaciones:56 tests unicos en total. El probe extrae getromname/configure_rom
reales y pasa O0/O2 ASan/UBSan. Usa metadata/romlist/avisos observados; las rutas
host y dispositivos abortan si se invocan. Cubre nombre null/grupos/revision,
KiB redondeados/maxuint32, int32 extremos, partnumber largo y bytes opacos,
locales disponibles, KICK seleccionado, dispositivos excluidos sin mutacion y
metadata/path ausentes con/sin aviso. Revision raiz corrigio la cobertura de
path ausente: reinstala metadata KICK y cuenta consultas para distinguir ese
caso de un ID inexistente. No prueba firmware comercial, boot ni API publica.

T06b2bi DONE; T06b2bj desbloqueada. validate y git diff --check PASS.
