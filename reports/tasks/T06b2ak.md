# T06b2ak — CPU halt conserva Paula

El enlace del probe revelo que cpu_halt y cpu_inreset se declaraban para todas
las CPUs, pero sus definiciones estaban dentro de CPUEMU_0. A500 selecciona
CPUEMU_13, y newcpu compilaba dejando cpu_halt sin proveedor. Parche0023 cierra
y reabre la guarda alrededor de esos dos helpers; no habilita CPUEMU_0/MMU ni
mueve opcodedebug/check_halt. Luna reviso el balance y la seleccion.

Con el helper real enlazado, build/t06b2ak-halt-before y su log reprodujeron la
perdida del estado de canal al entrar en halt: cpu_halt llamaba audio_deactivate,
que reinicia Paula, eventos y fase. Parche0024 guarda esa llamada fuera de P6.
No cambia haltloop ni lo sustituye, no pausa la entrega: mientras x_do_cycles
avanza chipset siguen existiendo Paula y PCM. ADR0002 registra la decision.

El probe incluye newcpu.cpp/audio.cpp reales y enlaza events, atomic, PCM,
lifecycle, GUI y diagnosticos reales. x_do_cycles tiene un forwarding de test
a do_cycles_normal/update_audio y una condicion de salida acotada; el bucle
real haltloop sigue siendo quien pide los pasos de8*CYCLE_UNIT. Es un microtest,
no ejecuta CPU CE ni el custom completo. IRQ/DMA no ejercitados abortan; el
evento pendiente de canal queda mas alla del intervalo y se comprueba su
deadline y estado, no su callback hardware.

Compara 1000 pasos de halt con control temporal para PAL/NTSC,44100/48000 y tres
salidas (BRK, MODE_CHANGE y cpu_halt_clear): PCM negativo exacto, fase, ciclos,
estado/deadline pendientes, no pausa, causa halt estable y SR mascara7.
cpu_inreset conserva supervisor/IRQ7/flag sin tocar fase. crash_auto_reset
se comprueba como solicitud observada hard=true/keyboard=false, cambio de
quit_program y flags; no se simula un reset de maquina exitoso.

CPU completo exporta ambos helpers y no importa cpu_halt/audio_deactivate ni
pause_sound. cpu_sleep_millis en este perfil no alcanza ningun sleep del host
con X86/PPC excluidos: no se añade shim ni se altera ese bucle por conveniencia.

Verificacion: probe halt/audio O0/O2 ASan/UBSan y unidad CPU entera con ambos
helpers exportados. CMake CPU/p6_portable/probe Debug/Release y CTest portable
PASS. GEN01:39 salidas identicas entre dos builds HOST limpios; HOST01:196
archivos/776 includes/332 identificadores, inventario83 rutas; SRC01:1134
archivos pristine verificados. Cola24 parches/19 archivos/529 lineas de delta.
Regresiones halt/audio, CPU status, prefetch/dump, PCM y plan:39 tests PASS,
35 documentales del plan. git diff --check y validate PASS.

Siguiente frontera T06b2al: compute_vsynctime mezcla clock de audio con host
pacing; debe notificar CHIPSET_CLOCK_PAL/NTSC fisico, conservando tasas de campo
nominales/raster. La auditoria Luna encontro target_adjust_vblank_hz en
compute_framesync y usos de vblank_hz en TOD/HSYNC: no fijar arbitrariamente
50/60Hz ni borrar half-line/interlace/LOF. Las variables vsynctimebase y
cputimebase pertenecen al pacing host; sus consumidores siguen pendientes.
Continuan las unidades sin compilar, enlace de maquina, frame y Waterbox.
