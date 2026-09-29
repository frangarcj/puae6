# ADR 0004: presupuesto de archivos al cerrar lifecycle A500

T06b2w incorpora devices.cpp como archivo upstream distinto 16. La alarma de
docs/05-maintenance.md era 15 archivos/1500 lineas para M1, no un permiso para
ocultar adaptaciones en headers ni enlazar proveedores ficticios. Antes de este
cambio hay 16 parches sobre 15 archivos, 412 lineas de delta; el build real
todavia falla en 14 unidades (build/t06b2t-engine-failures.log).

Decision: aceptar este archivo adicional y revisar el coste de los siguientes
con evidencia de compilacion. Mantener 1500 lineas como alarma de revision, no
ampliar automaticamente el alcance funcional ni declarar cerrado M1. El parche
devices solo guarda llamadas de features excluidas y headers host; no mueve ni
reescribe registry, bucles o el orden de los dispositivos conservados.

Alternativas descartadas: un devices nuevo duplicaria el orden upstream; stubs
de threads/MCU/CD/RTG ocultarian dependencias; incluir sus fuentes introduciria
servicios host y dispositivos fuera de A500. Guardas locales mantienen visible
el delta en su caller y permiten compararlo durante la actualizacion upstream.

Fronteras revisadas: init_shm de od-win32/mman solo reconfigura Fast/Z3/RTG
(cero en este perfil), mientras memory_reset/cleanup conservan Chip/Slow/ROM.
native2amiga requiere la cola EXTER de FILESYS y threads; traps/rtarea pertenecen
a esa integracion excluida. device_func_* y blkdev gestionan CD/SCSI, no el ADF
de DISK. sampler captura audio host, ethernet es host, cpuboard_rethink atiende
aceleradoras. Keyboard mode 0 usa CIA; los tres MCU solo son necesarios en modos
positivos. memory, DISK, CIA, audio, input, blitter, dongle y rethink_uae_int se
conservan. graphics_leave/close_sound siguen siendo frontera A/V requerida.

Precondicion interna: prefs del perfil A500 validas, sin Fast/Z3/RTG, cpuboard,
CD, sampler, hostfs, red ni teclado MCU. T06c/T13 deben rechazarlas al validar
config publica; estos hooks void no son una API de negociacion de features.
Los imports MCU de CIA y los servicios de otras unidades siguen abiertos hasta
el cierre global; retirar su lifecycle aqui no demuestra el enlace del core.

Validacion: unidad entera e imports, orden/argumentos de callbacks mediante
boundaries observados, sanitizadores y regresiones cercanas. No sustituye un
reset real de la maquina ni un snapshot Waterbox. El recuento de delta se toma
de series.json/parches, no del arbol generado ni de lineas ocultas en macros.

T06b2aa incorpora audio.cpp como archivo 17: solo guarda el exporter sampleripper
y sus dos callers, no modifica sintesis ni DMA. El exporter escribe WAV al host
y depende de rutas/config de UI; esta fuera de M1. No se sustituye por un ripper
que devuelve exito. La alternativa de aliases TCHAR globales introduciria
escrituras host y no resolveria su exclusion. Se acepta este delta local; el
proveedor PCM se implementa en ports/portable y no copia el backend Win32.
PERIOD_MAX depende de ULONG_MAX aunque per es int: el aviso LP64 descubierto
en este build requiere revision con reproducer antes de cerrar audio.

T06b2ag incorpora softfloat/softfloat.cpp y softfloat-specialize.h como archivos
18/19. Clang rechaza float_raise declarado extern y definido static; se conserva
la declaracion y se hace inline externo, con definicion incluida por todos sus
callers. El include usa ahora el nombre exacto SOFTFLOAT-MACROS.H para hosts
sensibles a mayusculas. No cambia aritmetica ni tablas; cada cambio identifica
la derivacion PUAE6 y conserva los avisos originales. El presupuesto de lineas
sigue siendo 1500. Las dos unidades seleccionadas son necesarias para tan.

El wrapper Paula conserva conversion binary64/extended80/tan/binary64 de
fpp_native.cpp, pero usa un unico float_status local inicializado a precision80
y round_to_zero. Upstream configura un local que no usa, operando con fs global
que solo inicializa fp_init_native; depender de ese lifecycle requeriria FPU
fuera del perfil. La referencia del test usa el wrapper upstream con fs ya
inicializado: no se afirma equivalencia con su estado previo a inicializacion.
Se descarta tan/tanl porque cambiaria algoritmo, precision y redondeo.

Las variables floatx80_internal_* siguen siendo globals ordinarios del motor,
no alloc_invisible ni TLS; no se excluyen de snapshots. La ejecucion sigue el
contrato de un unico hilo/instancia y no introduce callbacks dentro del calculo.
El estado local evita arrastrar flags de excepcion del FPU, pero no promete
reentrancia general de SoftFloat. El snapshot real y los filtros completos
cross-ISA siguen teniendo su propia puerta; este probe solo prueba la tangente
y los coeficientes RC, no makefir ni toda la cadena de audio.

T06b2am incorpora custom.cpp como archivo 20. La cola anterior tiene 24 parches
sobre 19 archivos y 529 lineas de delta. Se acepta una guarda local de negociacion
host en compute_framesync y de bases de reloj host en compute_vsynctime, mas la
notificacion del reloj fisico Paula. Copiar estos calculos a otro scheduler o
simular un monitor esconderia dependencias y permitiria variar CIA/TOD con el
refresco del host. Se conserva el selector de tasas nominales calculadas desde
geometria y reloj de chipset, sin redondear a 50/60; interlace/LOF/half-line y el
resto del raster no se reescriben. La alarma de 1500 lineas sigue vigente.

En P6, devices_update_sound recibe CHIPSET_CLOCK_PAL/NTSC y syncadjust=1;
devices_update_sync conserva svpos con su ajuste de campo. Las bases temporales
del host no se fabrican ni inicializan con constantes: eliminar sus consumidores
y cerrar custom completo sigue pendiente. Esta decision no valida un frame ni
el pipeline de dibujo; pruebas extraen los cuerpos reales con fronteras observadas.

T06b2bc incorpora rommgr.cpp como archivo21 por una correccion aritmetica de
checksum: el primer byte de cada word se promovia a int antes de multiplicar
por2^24, con overflow signed para valores>=128. El reproducer ejecuta el cuerpo
real con cuatro bytesFF y UBSan; la correccion convierte el primer operando a
uae_u32, manteniendo suma end-around-carry y orden big-endian. No se sustituye el
checksum por una implementacion paralela ni se desactiva el sanitizador.
Se acepta este archivo adicional; la alarma de1500 lineas permanece vigente.

El perfil A500 de read_kickstart exige transferencias completas de256/512 KiB
(256 en buffer512 solo si se permite espejo) o un prefijo exacto de consulta.
No repara checksum ni redondea lecturas incompletas. El checksum solicitado se
comprueba sobre bytes logicos despues del descifrado, sin usar exec.library como
condicion para validarlo. Se conservan el orden de espejo/descifrado y el salto
de512 bytes y limite256 KiB del formato KICK. La consulta de version exige32
bytes, cierra el stream en fallo y no modifica el resultado causal de carga.

Esto prueba transferencia y checksum; no demuestra firmware arrancable ni
compatibilidad del modelo. Formas de contenedor completas, aliases, descifrado,
content ID y configuracion publica siguen pendientes en T06b2bd/T06c. Un error
puede dejar bytes parcialmente escritos en el banco; las guardas de T06b2ba
impiden ejecutar CPU con ese resultado. No se promete rollback de la carga.
La prueba con datos sinteticos y las fronteras observadas no valida reset/frame
completo. Compilar memory no sustituye el cierre de rommgr ni del motor.

T06b2bk acepta include/statusline.h como archivo22 de la cola. Su declaracion
createstatusline(HWND,int) expone un tipo Windows a disk/input/cfgfile/drawing,
aunque todos los callers y la implementacion estan en od-win32 no seleccionado.
Se guarda esa declaracion fuera de P6, sin inventar HWND ni retirar las funciones
HUD genericas. drawing.cpp ya pertenece a la cola por resolucion de generados.
Seleccionar sus brazos sincronos existentes conserva algoritmos RGA/Denise y
elimina el lifecycle del worker; no se emulan semaforos. La alarma1500 sigue
vigente, y objeto/render completo mantiene su puerta si aparecen otros errores.

T06b2bo acepta main.cpp como archivo23:39 parches y1090 líneas de delta (+19).
La inclusión threaddep no tiene consumidores y queda fuera de P6; se excluye el
lanzador desktop completo, sin copiar inicialización ni ofrecer bootstrap falso.
Mantener main de escritorio arrastraría UI/configuración host y un segundo punto
de entrada a m68k_go; eliminar toda la unidad perdería globals/prefs/PRNG/reset
que usa el motor. El futuro bridge debe realizar su lifecycle real con errores.

uaerandomizeseed P6 delega en uaesetrandseed con la semilla explícita, incluido
cero que ya normaliza a1: contrato fijado en T05e, sin reloj local de fallback.
Los dos globals del PRNG permanecen en memoria ordinaria. La limpieza sin
AUTOCONFIG usa los arrays actuales de RAM/RTG en vez de tres campos inexistentes;
no habilita expansión ni sustituye el rechazo de configuración en la API pública.
La alarma1500 sigue vigente; se prueba conservación de Chip/Slow y otros campos.

T06b2bq acepta expansion.cpp y cfgfile.cpp como archivos24/25. Tras retirar el
include threaddep aparecen39 errores de catálogos/hooks fuera de A500. P6 excluye
periféricos/acceleradoras del catálogo, conserva slot0 sin dispositivo y subtype
nulo porque existen accesos directos del motor; mantiene AutoConfig genérico y
metadata memoryboards. Chip/Slow no proceden de ese catálogo. Se guardan hooks
FILESYS sin proveedor. No se rellenan funciones init con éxito ficticio.

El catálogo reducido exige adaptar get_unit_expansion_rom a su terminador real,
y excluir el exporter de mounts legacy que indexaba directamente el array.
write_filesys_config P6 avisa explícitamente si recibe montajes no soportados;
no exporta ni indexa. El rechazo público causal sigue pendiente: el void legacy
no sustituye un status p6_*. Acelerar compilación sin estas fronteras dejaría OOB.
No se habilitan SCSI/CD/red ni se arrastra su implementación para resolver enlaces.

T06b2by acepta savestate.cpp como archivo26. Se conserva por sus globals y helpers
compartidos, según state_policy; retirar la unidad perdería soporte usado por el
motor. El delta separa quick-slot de escritorio y export CD fuera del perfil,
acota el texto de versión y corrige tipos de printf. No copia un serializer ni
habilita CD/host paths para satisfacer el enlace. La alarma1500 sigue vigente.
Los imports legacy restantes requieren su cierre propio; compilar esta unidad
no aprueba estados nativos, rewind ni snapshots Waterbox.

Cola tras T06b2by:45 parches/26 archivos/1203 líneas de delta (+16).

T06b2ca acepta disk.cpp como archivo27:46 parches/27 archivos/1242 líneas de
delta (+39). DISK_validate_filename es el caller real de apertura para el drive;
sustituir el resolvedor desktop por un stub global ocultaría configuración de
rutas. Se adapta solo esta frontera a IDs de assets ADF inmutables, sin cambiar
lectura de pistas ni DMA. Se rechazan tipos ROM/key y errores de CRC, conservando
propiedad del stream y protección de escritura. La alarma1500 sigue vigente.
La compilación completa de disk, validación geométrica e inserción conservan sus
puertas en la continuación; este cambio no anuncia un disquete arrancable.

Cola tras T06b2ca: 46 parches/27 archivos/1242 lineas de delta.

T06b2cc mantiene disk.cpp como archivo 27 (sin archivos nuevos): 47 parches/27
archivos/1289 lineas de delta (+47). Son 14 regiones P6_PORTABLE que excluyen,
con sus callers, la creacion de ADF (fecha del reloj host y escritura),
overlay/save-image y proteccion por atributos del host, el XML de bootblocks y
el cambio de disco por nombre, mas la region FLOPPYBRIDGE del catalogo de perfiles
y el rechazo de ejecutables en DISK_validate_filename. Alternativas descartadas:
proveer gettimeofday/_wunlink/_tfopen o un disk_date fijo (ocultaria una
dependencia del reloj y de rutas del host y produciria imagenes con fecha
inventada), y dejar el codigo sin excluir para que el enlace lo descubra (el
compilador ya prueba que no compila). Se conserva lectura de pistas, DMA,
registros y temporizacion; wrprot es siempre true con el overlay fuera. La
alarma de 1500 lineas sigue vigente con 211 de margen.
Limites: SAVESTATE sigue sin definirse (gate T15), imports target_addtorecent,
fullpath, console_out y gui_ask_disk siguen abiertos en T06b2cb, y
inputdevice.cpp aun llama a disk_prevnext. No se anuncia un disquete arrancable.

Cola tras T06b2cc: 47 parches/27 archivos/1289 lineas de delta.

T06b2cd incorpora inputdevice.cpp como archivo 28: 48 parches/28 archivos/1346
lineas de delta (+57). Son 19 regiones P6_PORTABLE y tres ediciones locales.
Excluye, con sus callers, el escritor/lector de configuracion textual, el
lenguaje de macros de eventos custom (ShellExecute, parser cfgfile), el texto de
scripts jport custom, y guarda RTG, el test del depurador, volumen/mute de
escritorio, cambio de disco por nombre y el autoswitch con reloj de pared. La
configuracion llega como estructura del bridge, no como texto. Un dato causal:
al excluir solo definiciones, el compilador no detecta los callers de funciones
declaradas en cabeceras; el enlace de prueba (dylib con -undefined error) si, y
se uso para cerrarlos. No se usa alias silencioso: los callers se guardan.

Frontera de texto. T06b2aw dejo abiertos _stprintf y _tstol/_tcstol (auditoria
Luna). Aqui se cierra solo lo necesario y de forma acotada, en ports/portable y
no en parches: _stprintf pasa a ser un snprintf C++ cuyo tamano sale del tipo
del array (un destino puntero no compila, siempre termina la cadena, informa la
truncacion por el retorno y elimina una secuencia UTF-8 incompleta), y las
conversiones enteras son ASCII, sin locale, con saturacion definida y ERANGE,
contrastadas con strtol de libc en locale C. Alternativa descartada: adaptar
cada sitio de _stprintf con parches locales (8+ lineas en un archivo caliente
que cambia en cada update) o excluir la asignacion de puertos por nombre (T10
necesita decidirla). Quedan fuera scanf, %f/%g/%e (locale numerico) y _tstof.
gethidvals2 usa el parseo acotado: mismo id para valores reales, y valores
mayores que INT_MAX o negativos informan desconocido (-1) en vez de los bits sin
signo (probado contra sscanf). La alarma de 1500 lineas sigue vigente con 154 de
margen.
Limites: los imports de dispositivos fisicos (inputdevicefunc_*, input_get_default_*)
son T10; las acciones AKS de escritorio quedan compiladas y sin resolver para
T06c; el aviso upstream de dst[i].custom (array probado como puntero) no se
adapta aqui.

Cola tras T06b2cd: 48 parches/28 archivos/1346 lineas de delta.

T06b2cf anade el parche 0049 sobre cia.cpp (archivo ya en la cola): 49 parches/28
archivos/1367 lineas de delta (+21); margen de 133 sobre la alarma de 1500. Son
siete regiones que excluyen del CIA A500 lo que la puerta de imports (T06b2ce)
mostro que aun nombraba: Akiko (CD32), los adaptadores SCSI por puerto paralelo,
el gancho de depuracion de grabacion de entrada y la lectura/escritura de la RAM
del reloj en un fichero resuelto por el sistema de ficheros del host. El puerto
paralelo conserva sus lineas de joystick e impresora. Alternativa descartada:
definir `parallel_port_scsi = false` y un `parallel_port_scsi_read` que aborte en
el port; el simbolo existiria solo para que el enlace pasase, ocultando que la
rama no debe existir. Limite: la RAM del reloj no se persiste en M1 y la config
publica debe rechazar `rtcfile`; sin ese rechazo la opcion se ignoraria en
silencio.

Cola tras T06b2cf: 49 parches/28 archivos/1367 lineas de delta.

T06b2cg supera la alarma: 53 parches/28 archivos/1647 lineas de delta (+280 en
cuatro parches: 0050 newcpu 51, 0051 memory/custom 76, 0052 input/disk/savestate/
rommgr 93, 0053 main/expansion/cfgfile 60). No hay archivos nuevos. Coste y
motivo: la puerta de imports (T06b2ce) enseno que 10 unidades seguian nombrando
funciones excluidas (grabacion de entrada, aceleradoras, RTG/hostfs/trampas,
bancos Gayle/Gary, proteccion de memoria del host, arranque UAE, cartuchos);
cerrar 59 simbolos exige guardar unos 100 sitios de llamada. Todos siguen el
mismo patron, `#ifndef P6_PORTABLE` con el texto upstream intacto (salvo tres
sustituciones exactas: get_mem_bank_real por get_mem_bank, sin memwatch;
need_uae_boot_rom sin area de traps; expamem_z3hack sin memoria de aceleradora),
asi que un update los reaplica de forma mecanica y la puerta detecta cualquier
regresion.

Alternativas descartadas. (1) Un modulo del port con definiciones "no soportado"
de esos simbolos: ahorraria unas 250 lineas, pero convierte una opcion mal
configurada en un aborto en mitad de la emulacion en lugar de un rechazo previo, y
oculta las dependencias que la puerta existe para mostrar (misma razon que en
0049). (2) Macros por funcion (CPUBOARD, GAYLE, INPUTREC...): es mejor a largo
plazo, pues permitirian reactivar cada una en M2 y despues, pero el numero de
ediciones es el mismo y ahora se ignorarian casi todas; las guardas actuales se
renombran de forma mecanica cuando una funcion vuelva. (3) Dejar los simbolos sin
resolver y proporcionarlos en el enlace: rechazado por AGENTS.md.

Consecuencia: la lista de opciones que la config publica debe rechazar o
normalizar pasa de dispersa a un manifiesto verificable,
manifests/config-invariants.json (T13). Sin ese rechazo, varias opciones se
ignorarian en silencio (rtcfile, cs_ide = -1 normalizado, cpuboard, cache JIT...).
Revisar el presupuesto en M2, cuando A600/A1200 reactiven parte de estas regiones.

Cola tras T06cg: 53 parches/28 archivos/1647 lineas de delta.

T06ca incorpora gencpu.cpp como archivo 29: 54 parches/29 archivos/1664 lineas de
delta (+17). Es un parche a una HERRAMIENTA HOST, no al motor: main() acepta
numeros de modelo por argumento (validados antes de crear ningun fichero) y, sin
argumentos, emite todo como upstream. El receta pide los modelos 13 y 14. Efecto:
cpustbl.cpp pasa de 73328 a 3198 lineas, cputbl.h de 69295 a 6229, y desaparecen
11859 simbolos sin resolver; cpuemu_13.cpp y las tablas 13/14 salen identicas byte
a byte. La puerta de imports baja de 12018 a 159.

Alternativas descartadas. (1) `CPUEMU_68000_ONLY`: bandera oficial de upstream que
gencpu ya emite, pero `struct regstruct` pierde los campos de MMU/cache (cacr,
mmu_enabled, tcr...) que newcpu.cpp y newcpu_common.cpp siguen usando, asi que no
compila; upstream no la mantiene de forma consistente. (2) Dejar la tabla completa
y confiar en `-dead_strip`: dependeria del enlazador (una dylib exporta todo, y ELF y
Mach-O difieren en si informan indefinidos de secciones descartadas) y ocultaria la
dependencia que la puerta existe para mostrar. (3) Editar cpustbl.cpp o definir los
manejadores: prohibido, la salida generada no se toca. Consecuencia para M2: el
receta debera pedir tambien los modelos de 68020/AGA; es cambiar un argumento, no
un parche. El delta de 1500 lineas sigue superado (ADR anterior).

Cola tras T06ca: 54 parches/29 archivos/1664 lineas de delta.

T06cb (parches 0055-0057) deja 57 parches/30 archivos/1868 lineas (+204); keybuf.cpp es
el archivo 30. 0055 saca del enlace la maquinaria de ficheros .uss de savestate.cpp
(dos lineas por region): T15 reutilizara las funciones por dispositivo de upstream con
E/S en memoria y su propio envelope, asi que aqui se excluye en vez de fingir un
serializador; las variables y primitivas que usan otras unidades se conservan y
savestate_check devuelve false porque atender peticiones sin restaurador dejaria
savestate_state atascado. 0056 guarda los servicios de escritorio (acciones AKS de
GUI/pantalla/volumen/RTG/captura/estado, lista reciente y OSD, rutas del host, tabla
de mensajes, reloj de pared) en ocho unidades; las acciones internas de la maquina
(reset, eject, cambio de puertos, IRQ7, refresco, quit/restart) se conservan. 0057
retira el teclado en pantalla, pegar, la pausa del escritorio y la correccion de
distribucion de teclado de Windows.

Decisiones a revisar. La identificacion de ROM por nombre de fichero queda excluida:
aceptaba contenido sin hash, y la identidad del firmware pasa a depender solo de
CRC/SHA-1. Los ids logicos de firmware se comparan con igualdad exacta. Alternativa
descartada: variables constantes (pause_emulation = 0, key_swap_hack = 0) y no-ops de
las acciones de escritorio; definirlas ocultaria que esas rutas no existen y dejaria a
un cambio futuro de la config publica activarlas sin que nada lo notase. La puerta de
imports pasa de 155 a 29 simbolos (solo entrada y video, con proveedores reales en
T06cd/T06ce). El presupuesto de 1500 lineas sigue superado; se revisara en M2.
