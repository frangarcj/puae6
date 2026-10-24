# ADR 0002: IRQ de CPU en el hilo propietario

El contrato p6_* admite un unico hilo de ejecucion, sin reentrada ni workers.
Los callers de safe_interrupt_set del perfil A500 son CIA y custom: ya ejecutan
hardware en ese hilo. Consultar is_mainthread de Win32 o devolver true desde un
shim ocultaria esa precondicion; no hay runtime de threads que deba inicializarse.

Decision: P6 conserva directamente el cuerpo upstream sincrono de
safe_interrupt_set. Se excluye por preprocesador la cola de productores externos
de esa funcion. No cambia INTREQ_INT (CE/compatible), INTREQ_0 (fast) ni su
comprobacion del bit ya solicitado. No introducir callbacks de frontend como
productores IRQ; los adaptadores encolan entradas en el boundary neutral.
El consumo de uae_int_requested/check_uae_int_request se conserva. El cambio no
autoriza otros perfiles, CP_DRACO/CP_CASABLANCA ni workers de dispositivos.

atomic_and y atomic_or deben devolver el valor PREVIO. El consumidor de IRQ
depende de ello para vaciar y observar pendientes. Se implementan con builtins
Clang/GCC de 32 bits secuencialmente consistentes, exigidos lock-free por
static_assert; no cambian layout de uae_atomic ni precisan mutex/libatomic.
Esto conserva semantica atomica incluso en las operaciones de flags existentes,
sin anunciar que el motor sea concurrente. No se implementan primitivas sin
callers soportados ni se copian los servicios POSIX/Win32.

threaddep se incluye solo con WITH_THREADED_CPU. statusline y su unico clear se
excluyen en P6 porque limpian UI; el resto del reset queda intacto.

Validacion: test extrae automaticamente las funciones IRQ del newcpu materializado
y las ejecuta con el proveedor real y capturas INTREQ/doint. Comprueba ambos
niveles, ramas CE/compatible/fast, old-value y flags con ASan/UBSan. El preprocesado
completo verifica includes y ausencia de is_mainthread; nm inspecciona el proveedor
completo. Esta prueba no afirma que newcpu entero compile: la seleccion de
modelos/cache aun tiene errores, reproducidos en build/t06b2l-cpu-diagnostics.log.
T06b2p conserva esa puerta junto al enlace futuro y pruebas del scheduler completo.

T06b2ak: halt y reset son solicitudes comunes, no una familia CPUEMU. El enlace
del probe revelo cpu_halt/cpu_inreset bajo la guarda CPUEMU_0, aunque los llama
A500 CPUEMU_13. Parche0023 cierra/reabre esa guarda alrededor de ambos helpers;
no habilita otra CPU ni cambia opcodedebug/check_halt/loops MMU. cpu_inreset
conserva SPCFLAG_CPUINRESET, supervisor, mascara7 y MakeSR.

La CPU detenida sigue llamando x_do_cycles(8*CYCLE_UNIT) desde haltloop. Su
audio_deactivate reiniciaba Paula (zerostate, eventos, fase y buffers), mezclando
un estado de CPU con una pausa de presentacion. Parche0024 excluye esa llamada
solo en P6; no la sustituye por pause_sound: mientras avanza chipset sigue
generandose/entregandose PCM. Un frontend que deja de pedir frames deja de
avanzar tiempo, y la pausa explicita de entrega tiene su propio contrato.
No se altera haltloop ni se crea otro scheduler. cpu_sleep_millis no llega a
sleep del SO con WITH_X86/WITH_PPC excluidos; no se implementa un shim ficticio.

Reproducer: build/t06b2ak-halt-before y su log (cola0023 sin0024) fallan al
comparar el estado de canal inmediatamente despues del halt. El probe ejecuta
CPU/audio/events reales, con forwarding de x_do_cycles al scheduler real y
salida acotada por BRK/MODE_CHANGE o cpu_halt_clear. Compara PCM/fase/ciclos y
deadline pendiente con control temporal. IRQ/DMA fuera del intervalo abortan;
reset automatico se observa como solicitud, no simula un reset de maquina.
La ruta de frame y el custom completo siguen pendientes; esto no prueba DMA
ni ejecucion de instrucciones durante halt, que precisamente no deben avanzar.

T06b2ba: el resultado causal de cargar ROM pertenece a memory, en almacenamiento
ordinario por imagen/sandbox, y se consulta mediante p6_memory_rom_status.
Init/cleanup dejan UNLOADED; solo una carga efectiva reemplaza el resultado.
Reset sin recarga, consulta de version y vaciado de diagnosticos no lo borran.
El perfil portable rechaza :AROS: no enlaza su ROM embebida ni la usa como fallback.
restore_roms devuelve el fallo sin ejecutar patch_kick. LOAD_OK significa que
termino el cargador legacy; no certifica firmware, checksum ni perfil soportado.

m68k_go rechaza un resultado distinto de LOAD_OK antes de alterar su estado de
ejecucion. Si custom_reset provoca un fallo de carga, abandona el bucle antes de
m68k_reset2 (lectura de vectores) y conserva el teardown normal. No se introducen
excepciones, longjmp, callbacks host ni otro scheduler. El bridge debera validar
firmware y consultar el resultado tras load/reset y antes de run; esta consulta
interna no sustituye los errores de la ABI publica ni demuestra su integracion.

Los probes ejecutan los cuerpos reales de loader/restore/lifecycle y m68k_go,
con zfile/blob reales y fronteras de dispositivos observadas o que abortan ante
uso inesperado. Datos sinteticos prueban flujo de control, no firmware valido.
La persistencia en un reset completo de maquina y la propagacion p6_* end-to-end
siguen siendo puertas de T06b2bb/T06c, junto al enlace del motor completo.
