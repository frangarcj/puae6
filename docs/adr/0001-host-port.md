# ADR 0001: frontera host estrecha sobre WinUAE 6.0.3

Estado: frontera y contratos host propuestos; implementacion pendiente T03-T07.
HOST01 comprueba el inventario estatico, no certifica enlace ni Waterbox.

Base: `857f48fcb09399d4d95527e29a2fdeaedb41f827`, snapshot verificado con
`python3 tools/import_sources.py verify --all`. Las rutas siguientes son relativas
a `upstream/winuae`, salvo indicacion contraria. `source-manifest.json` separa
unidades seleccionadas, productos generados e inputs textuales. La seleccion
requiere parches: no se puede compilar el snapshot intacto con esa lista.

## Decision y evidencia

Conservar CPU/chipset/scheduler; adaptar servicios host individualmente. No copiar
`od-win32/sysconfig.h`: habilita hilos, dispositivos y librerias fuera del perfil.
Tampoco usar `UAE_MINI`/`CUSTOM_SIMPLE` como atajo: `cia.cpp::cia_wait_pre` cambia
con `CUSTOM_SIMPLE`. El punto de partida es `cfgfile.cpp::bip_a500`, config 0,
llamado por `built_in_prefs`, con `set_68000_compa` y `built_in_chipset_prefs`.

`include/sysdeps.h` presupone TCHAR y reconoce ARM mediante macros distintas de
`__aarch64__`; resolver tipos y deteccion con parches acotados, no un falso Win32.
`main.cpp::uaerandomizeseed` toma `getlocaltime` si seed=0: el bridge debe fijar
una semilla reproducible. No eliminar el PRNG interno del hardware.
`events.cpp` consulta scanline/reloj host; `audio.cpp` entrega buffers a
`finish_sound_buffer`; `memory.cpp`/`disk.cpp` usan zfile para ROMs/medios.
`manifests/host-surface.json` registra contratos por grupo: cada simbolo hereda
semantica, backend nativo/Waterbox y prueba prevista. `audit --json` reproduce
las unidades que contienen cada identificador; no confundir ocurrencia con una
llamada alcanzable en runtime ni pruebas previstas con pruebas ya ejecutadas.

En 6030 no existen `od-unix/`, `CMakeLists.txt` ni `README_unix.md`.
La auditoria adicional fija [WinUAE 7d84eae](https://github.com/tonioni/WinUAE/tree/7d84eae9aecaf98bcfee1e5cf2c765d2fbfe17b4/od-unix),
solo referencia leida el 2026-09-26, nunca entrada del build.
`path_expand.*` puede aportar utilidades tras revisar API/licencia por archivo;
Qt, SDL3, pthreads, señales, `nanosleep`, relojes host, mman, red y dispositivos
fisicos no son una capa Waterbox reutilizable. No importar el directorio entero.
Cache de auditoria prescindible: `build/unix-audit/<sha>/SHA256SUMS` (98 archivos).
SHA256 de CMake: `314d74141c6cfc1388cf7574843dfe08795ab960986a63e7257b26b417b81634`;
README: `bc80549af862911a70de9b44b4a66fb4bb3ddc118c10f2409bf2efba8b43282e`.

PUAE fijado: `references/puae/sources/src/newcpu.c` y `libretro/libretro-glue.c`
muestran retorno por `libretro_frame_end`/`SPCFLAG_CHECK`, marcado en `unlockscr`.
MacHawk `references/machawk/waterbox/uae/bizhawk.c::FrameAdvance` llama
`m68k_go(1, 1)` directamente. T07 portara esa semantica mediante `p6_run_frame`;
no heredara el frontend entero, sus callbacks ni `sched_yield`.
Procedencia y avisos: `plan/upstream.lock.json` y `manifests/licenses.json`.

La receta UAE antigua de MacHawk SI enlaza CAPSImg y C++; excluir CAPSImg es una
decision PUAE6, no una propiedad de aquella receta. Ver issue y alcance IPF en
`docs/07-machawk.md`. WinUAE6 sigue siendo C++ al retirar CAPSImg.
Waterbox Darwin inicial: ELF AArch64, x18/x28 reservados, paginas logicas 4096 y
host 16384; fijar `WBX_HOST_PAGE_SIZE=16384`. `common.mak` tiene defaults x86/4096
que no sirven sin configurar. T09 debe ejecutar WB13-WB15 en el sandbox real.

## Comprobaciones y limite actual

`python3 tools/check_host_surface.py inventory` valida pin, hashes, rutas y que
todas las unidades seleccionadas se auditan, sin duplicar las generadas.
`python3 tools/check_host_surface.py audit` recorre includes transitivos (union
de ramas, tambien `<headers>` locales), valida sus fronteras y detecta simbolos
sin contrato en todos los archivos alcanzados, mediante las familias observadas,
operaciones prohibidas del scanner y APIs `extern` de headers declarados.
No acepta dependencias nuevas dentro de esa cobertura ni contratos sin prueba.
`python3 tools/check_host_surface.py scan <archivos...>` detecta dependencias
host conocidas, incluso en ramas inactivas. `scan` es local; `audit` recorre el
grafo. Ninguno expande macros ni resuelve tipos, punteros a funcion o todas las
declaraciones C++. Rechazan includes mediante macros para evitar silencio.
El snapshot original contiene dependencias prohibidas y debe dar errores.
T06 debe contrastar contratos con depfiles/preprocesado/simbolos y enlace estricto;
una dependencia nueva o sin clasificar impide aprobar el build. Los tests
sinteticos prueban el inspector, nunca el emulador.

## Decisiones concretas para el primer perfil

`built_in_prefs(0,0,compa=0)` activa CPU/memoria/blitter cycle exact.
`newcpu.cpp::build_cpufunctbl` elige lvl0/mode4, `op_smalltbl_14`, cuyos handlers
genera la familia `cpuemu_13.cpp`. No existe `cpuemu_14.cpp`. La tabla dispatch
tambien referencia otras familias. Ademas, en `cpustbl.cpp` pristine el `#endif`
CPUEMU_13 precede a la tabla14: definir solo esa macro no prueba cierre de tablas.
T03 debe reproducir la generacion y ajustar generador/dispatch si procede; nunca
editar manualmente cpustbl/cpuemu. El caso minimo es `cputbls[0][4]` mas las
referencias de `cputbls[6][8]` y ese limite de guard (newcpu:1858, cpustbl:16249).

Denise: `drawing_init` arranca hilo y semaforos independientemente de
`multithread_enabled`; `drawing_free` espera al worker. El port debe conservar
el orden RGA en modo sincrono y quitar todo ese lifecycle, no simular semaforos.
`gui_data.powerled` alimenta el filtro Paula: conservar ese estado aunque no haya GUI.

El preset Slow512K activa RTC (`has_expansion_with_rtc`). `clock_bget` y
`rtc.cpp` consultan `time/localtime`; usar epoca y calendario de sync config mas
tiempo emulado. No desactivar RTC ni TOD para ocultar el problema. TOD normal
permanece; `tod_hack` de sincronizacion al host queda rechazado.

ROM: conservar CRC/SHA de `crc32.cpp`, mapa/overlay y transformaciones autorizadas.
Falta/ROM invalida debe fallar: `load_kickstart_replacement` carga AROS embebido
y modifica RAM; es distinto de `ersatz_init` y ninguno es fallback M1 aprobado.
ADF: denegar `r+b` y permitir reintento `rb`; mantener mecanica y write-protect.
La sospecha inicial de XML en toda insercion queda descartada: `drive_insert`
llama `DISK_examine_image(...,false,...)`; `abrcheck` requiere deepcheck=true.
Excluir esa opcion de reconocimiento profundo, sin quitar examen de geometria.

Retener `savestate.cpp` inicialmente: define `savestate_state`, los helpers
`restore_*_func` y `savestate_check`, que tiene caller fuera de SAVESTATE en
`m68k_go`. Aislar persistencia/rewind y dispositivos excluidos mediante parches;
apagar la macro no permite omitir la unidad. Esto no habilita una API de estados
nativos ni compatibilidad con estados WinUAE; esas puertas permanecen en T15.

`dongle.cpp` es logica interna requerida por CIA/input/devices, transparente con
dongle=0; se incluye. El preset usa keyboard_mode=0, pero devices inicializa
los tres MCU incluso asi: excluir MCU requiere guards en lifecycle/CIA y rechazo
de modos >0, conservando la ruta serial upstream modo0. No poner init ficticios.

T06b2be: P6 read_rom_name abre solo el ID de asset solicitado; no consulta romlist
para sustituir/reensamblar otra entrada. Conserva las transformaciones locales
rom_fopen2, pero un fallo de memoria/lectura/publicacion falla cerrado. No se
retorna el original si se detecto una transformacion y no se pudo publicarla.
Se excluyen addkeydir y sus callers P6, no se implementa un buscador ficticio.
load_keyring limpia el anterior y carga solo path si es un ID explicito no vacio;
NULL/vacio limpia. Su retorno sigue siendo conteo, no una nueva API publica.
El futuro bridge debe invocarlo con el asset de clave y liberar al descargar;
los hooks publicos init/load/unload aun deben integrar ese lifecycle.

AMIROMTYPE1 admite payload A500 exacto256/512 KiB; lectura, asignacion, descifrado
y checksum deben terminar antes de publicar zfile_fopen_data con el ID original.
Cada clave candidata requiere checksum, ademas de firma o SHA reconocido; una
clave que solo produce prefijo plausible no impide probar la siguiente.
En P6 se deshace tambien el ultimo XOR fallido. El buffer original registrado
en blob nunca se modifica. addkeyfile no registra lecturas parciales ni OOM;
addkey rechaza longitudes no positivas y conserva ownership/dedup/capacidad.

Las pruebas de normalizacion usan cuerpos reales, zfile/blob y CRC/SHA reales,
con datos sinteticos y fallos de asignacion controlados. No certifican ROM
arrancable, modelo, contenedor raw/KICK completo, content ID publico ni boot.
read_rom_file legacy sigue fuera de la ruta de asset explicito, no es una API
portable alternativa autorizada. Cierre rommgr y lifecycle publico permanecen
obligatorios en T06b2bf/T06c, antes de anunciar carga del core funcional.

## T06b2bg: contenedor principal A500 completo

La carga principal P6 acepta solo streams normalizados de 256/512 KiB o discos
KICK de exactamente 1760*512 bytes con magic KICK. Rechaza ELF/HUNK, GVP +8 y
combinados de 1/2 MiB como UNSUPPORTED sin invocar el cargador ejecutable ni
asignar bancos extendidos desde esa ruta. Otros tamaños/magic incorrectos son
READ_ERROR. Se conserva la rama legacy no-P6; read_kickstart mantiene su contrato
de consulta de prefijos y no se convierte en validador global de formatos.

Tras el checksum, la carga exige la firma 11 11/11 14 y longitud big-endian en
size-20, igual que kickstart_checksum_more_do del upstream fijado. La metadata
A500 describe ROMs de 256/512 KiB, pero no contiene los bytes comerciales: esta
regla es una politica estructural del perfil inicial, no una prueba de corpus.
Requisitos conocidos cpu&15 (020/030 o hardware A4000) devuelven UNSUPPORTED
antes de patch_kick, sin reiniciar ni adaptar preferencias. No se convierte el
nombre/modelo comercial en una lista universal de compatibilidad.

La prueba integrada usa los cuerpos reales de resolucion, normalizacion,
keyring, descifrado, carga y checksum con zfile/blob reales. Metadata y servicios
de maquina siguen siendo fronteras observadas; los fixtures no son firmware
arrancable. La validacion publica de modelo, content ID normalizado y lifecycle
p6_load/reset/run conserva su puerta en T06b2bh/T06c. Las ROMs extendidas y otros
perfiles requieren sus propias puertas; tampoco se certifican aqui memory_reset,
boot, frame o Waterbox. Un fallo puede dejar bytes en el banco: la guarda CPU
consume el estado causal, sin prometer rollback del buffer.

## T06b2bi: cierre del objeto rommgr

getromname conserva la ABI legacy sin capacidad explicita: los callers del motor
(cfgfile y scanner) proporcionan MAX_DPATH y metadata interna. P6 formatea solo
revision/tamaño con snprintf sobre un temporal de64 bytes; ambos enteros de32
bits caben sin truncacion. Los formatos %d/%u no usan agrupacion, coma flotante
ni flags localizados; nombre/partnumber se concatenan como bytes opacos. No se
agrega un alias _stprintf ni se presenta esta funcion como API publica acotada.
El tamaño en KiB se redondea por cociente/resto para evitar wrap de size+1023.

Arcadia/ALG no forman parte del perfil A500. configure_rom rechaza sus tipos
antes de mutar preferencias y excluye las rutas NVRAM/video derivadas del host.
scan_single_rom solo invoca scan_arcadia_rom cuando ARCADIA esta definido. No se
implementa un scanner ficticio ni se declara soporte porque exista metadata.
Los demas dispositivos y la validacion publica de perfiles conservan sus puertas;
esta correccion no convierte configure_rom ni el scanner legacy en alternativas
validadas de p6_load. El cuerpo no-P6 mantiene el comportamiento anterior.

La auditoria de callers incluye getromdatabyname, romwarning y scan_single_rom_file
(rommgr), ademas del parser cfgfile. Cada buffer individual es MAX_DPATH; la
concatenacion de varios nombres en romwarning tiene un limite previo no resuelto
por este cambio. Capacidad de mensajes agregados y APIs publicas deben validarse
en su propia frontera, sin confundir el temporal numerico acotado con esa garantia.

## T06b2bk: despacho Denise sin worker

P6 fija ENABLE_MULTITHREADED_DENISE=0 en drawing.cpp y compila los brazos
sincronos ya existentes de las ocho entradas de cola. Conserva updatelinedata,
quick_denise_rga, actualización de ciclos solapados, draw/start/end y orden del
llamador. Excluye worker, cola concurrente, semaforos, gui_sem usado por GTK no
seleccionado y esperas de init/free/flush. No se reemplaza un semaforo por exito
ficticio ni se añade otro scheduler. drawing_free solo limpiaba el worker, por
lo que en P6 no tiene recurso de ese lifecycle que liberar. drawing_init sigue
inicializando tablas, buffers y estado de dibujo.

thread_debug_lock se conserva: protege la secuencia lógica de lock/unlock del
vidbuffer, no es un servicio de hilos. La prueba de despacho usa fronteras
observadas para las funciones que dibujan, y ejecuta los cuerpos RGA/overlap
reales; no certifica pixels, frame ni pipeline completo. El preprocesado completo
prueba ausencia de codigo worker, no enlace: el siguiente gate debe cerrar HUD,
debug y seleccion OCS/AGA que impedían compilar tras retirar threaddep.

La ruta síncrona fijada de denise_handle_quick_strobe_queue pasa endpos como
tercer argumento de denise_handle_quick_strobe; el worker pasaba vpos. Se conserva
la conducta síncrona y su prueba explícita; el gate de precisión debe revisar
esta diferencia con un reproducer raster antes de afirmar fidelidad de strobes.
No se corrige el algoritmo por intuición en una tarea de lifecycle host.

## T06b2bm: renderer OCS/ECS sin almacenamiento AGA

Parche0038 guarda decodificadores, dispatch e includes generados AGA, lookup
DPF y copias de registros con AGA; elimina placeholders de un elemento que no
representaban hardware. No añade color_regs_aga ni amplía MAX_PLANES. Conserva
las ramas OCS/ECS, HAM6, genlock y NTSC. El HUD host local sin callers queda fuera
de P6; las referencias al depurador quedan bajo DEBUGGER. No se simula su éxito.

Los objetos completos Debug/Release y O0/O2 prueban compilación y frontera de
imports. La prueba ejecutable compara colores/HAM/copia de seis planos contra
los cuerpos pristine en otro binario con almacenamiento AGA pero aga_mode=false.
Una rutina ECS generada real comprueba píxeles, colisiones, cursor y centinelas.
No certifica selector completo, raster ni frame; p6_load debe rechazar AGA en su
futura frontera pública, no confiar en estos brazos OCS ante un perfil inválido.

linetoscr_common aún define 30 helpers de siete/ocho planos y emite avisos de
bounds con MAX_PLANES=6. El preprocesado completo prueba que cada nombre aparece
solo en su definición, sin callers ni selección; no se silencian los avisos ni
se editan generados. Los dos avisos restantes (comparación u8<256 y switch) son
anteriores. El cierre de generación por features y la precisión raster permanecen
pendientes junto con la diferencia quick_strobe documentada en T06b2bk.

## T06b2bo: main como soporte de biblioteca

P6 excluye CLI y lanzador desktop desde DONT_PARSE_CMDLINE hasta real_main/main,
incluidos start_program/do_start_program y leave_program. No hay nueva rutina
vacía: la entrada p6_* pendiente deberá inicializar y liberar recursos reales.
Se conservan globals, helpers de texto/prefs y uae_reset/quit/restart intactos.
Sus imports target/console y las consultas de aceleradoras/RTG en fixup_prefs
siguen pendientes de frontera/enlace; compilar no demuestra esos servicios.
El caller legacy de usage en cfgfile debe excluirse junto con su parser CLI.

uaerandomizeseed ya no importa getlocaltime en P6. Semilla0 significa1 según
uaesetrandseed y el contrato T05e; no cambia algoritmo, contador de llamadas ni
reseed al restaurar. La prueba compara referencia con entrada normalizada y
P6 con entrada original, además de valores conocidos y continuación de ambos
globals. No certifica snapshot Waterbox ni serialización nativa del PRNG.

## T06b2bq: catálogo de expansión del perfil fijo

P6 no registra controladoras, red, CD ni aceleradoras fuera de A500. expansionroms
contiene solo terminador; cpuboards conserva la opción0 y dummy_sub nulo exigidos
por accesos directos a [0][0]. Se mantienen parser AutoConfig, tablas RAM y
memoryboards. Los callbacks y helpers del catálogo excluido no se sustituyen.
Los callers FILESYS sin definición también quedan bajo esa feature.

get_unit_expansion_rom recorre el catálogo real hasta el terminador antes de
resolver índices de controladora. write_filesys_config P6 no exporta mounts;
mountitems!=0 emite warning explícito. Esto evita los índices directos legacy
sobre el catálogo reducido; no constituye la validación causal de p6_load ni
una API de persistencia. Entradas públicas aún deben rechazar RAM/periféricos
fuera del perfil y exigir cpuboard_type/subtype0 antes de helpers internos.

El sufijo numérico usa temporal16, sin alias global _stprintf. Las copias legacy
siguen requiriendo nombre final<=127 bytes (aci.name), incluyendo sufijo; no es
una garantía para textos arbitrarios. La prueba compara observaciones del cuerpo
real con pristine en O0/O2, con callbacks de prueba y autoconfig profundo abortable.
Se prueban cero/uno/255 y el límite127; no se certifica AutoConfig de una máquina.

## T06b2bs: preset sin bancos heredados

buildin_default_prefs repetía memset del primer banco Fast y Z3 en cada vuelta,
dejando datos de la configuración anterior en los demás. Parche0041 indexa ambos
arrays y borra cada ramboard completo, como expresa el bucle original. Es una
corrección de estado candidata a upstream, aplicada también fuera de P6; no cambia
el perfil ni convierte memoria no soportada en una configuración válida pública.

La comparación parte tanto de bancos vacíos como de todos los slots poblados.
El caso previo debe fallar por RAM residual; la referencia limpia conserva su
resultado. Los cuerpos de los presets y del chipset son reales; selección de ROM
y callbacks target/config son fronteras observadas de la prueba, no servicios
implementados. Compilar ese probe no acredita cfgfile completo, boot ni frame.

La auditoría de cfgfile sin su include IDE en una copia temporal obtiene 224 errores
completos, registrados en build/t06b2br-cfgfile-probe. La mayoría son formatter y
parser TCHAR; además hay linkage static/extern, separador FSDB y dos APIs ATA
usadas al parsear geometría. El constructor de presets no presenta errores en
ese diagnóstico. No se elimina ide.h en producción: excluir parsing/export de
host requiere resolver sus callers reales custom/inputdevice, sin éxito ficticio.

## T06b2bu: configuración de biblioteca

Parche0042 excluye de P6 el parser/exporter/CLI de escritorio en cfgfile, así como
la migración de nombres de adaptadores de red. Conserva copy/discard, limpieza de
mounts, addbcromtype, compatibilidad, default_prefs, todos los builders builtin y
config_check_vsync/set_config_changed. No reemplaza funciones excluidas con
retornos exitosos. Sus callers restantes deben cerrarse según feature antes del
enlace: siguen siendo errores de enlace visibles hasta la continuación.

default_prefs deja de construir default_file, caché de texto que el exporter usa
para omitir opciones iguales al valor inicial. Los valores de prefs se conservan;
la configuración pública futura debe validar el perfil explícito. Esta decisión
no autoriza modelos ni periféricos adicionales por conservar sus builders.

error_log conserva la lista y su ciclo de vida, pero usa vsnprintf con va_copy:
el retorno C99 es longitud requerida y no el indicador de truncado negativo de
MSVC. Reintenta con capacidad exacta para mensajes largos. Errores de formato y
asignación solo emiten diagnóstico, nunca se convierten en éxito causal del motor.
Las pruebas de mensajes y la compilación de toda la unidad no prueban boot/frame.

## T06b2bw: callers y selección tras retirar configuración desktop

devices_free ya no intenta liberar temp_lines en P6: la lista privada y todos
sus productores se excluyeron en0042. fixup_prefs conserva sus fixups y deja de
crear la caché de exportación. Se guardan los callers, sin sustituir funciones.

calc/ini salen únicamente de selection.upstream_units. Sus fuentes pristine y
audit_sources permanecen. Los33 preprocesados seleccionados (incluidos5 generados)
no contienen usos de sus28 APIs fuera de declaraciones; los objetos disponibles
tampoco las importan. Una prueba comprueba esa frontera y el grafo CMake sigue
validándose contra el manifiesto. No es prueba de enlace de todo el motor.

ini pertenecía a geometría hardfile/FILESYS y calc al parser de fórmulas excluido;
ninguno ejecuta hardware A500. Rutas de medios VFS y comandos de input siguen
necesitando implementaciones o guardas propias. No se elimina estado de máquina
para resolver esas referencias pendientes.

## T06b2by: soporte compartido de estados

Parche0044 conserva savestate.cpp y sus globals/helpers. En P6 usa snprintf con
capacidad100 para la versión (tres int caben), excluye el exporter de chunks CD
y la función quick-slot que construía rutas/nombres desktop. La futura API de
estados será neutral al frontend; no hay quick-slot que aparente guardar.
Los callers legacy de quick en inputdevice necesitan su guarda posterior.

Parche0045 corrige cuatro formatos de diagnóstico según sus tipos reales:
size_t con %zu, uae_u32 con %u y diferencia de punteros con %td. Es candidato
upstream, sin envío; no cambia bytes serializados ni avances de hardware.

La prueba de cabecera ejecuta su prefijo real y writers reales, con save_chunk
observado; no ejecuta todo save_state_internal ni certifica el contenedor ASF.
El header upstream tiene1000 bytes y no valida descripciones arbitrarias: la
validación del serializer público sigue en T15. Replay, restore CD/modelos,
configuración, rutas, screenshots y otros imports permanecen pendientes de
enlace/exclusión. Ninguna prueba aquí acredita un restore nativo ni Waterbox.
