# T06b2bk — Denise por su ruta sincrona

Parche0037 fija ENABLE_MULTITHREADED_DENISE=0 en P6. Excluye threaddep, worker,
cola concurrente/read-write counters, semaforos, gui_sem y esperas de init/free/
flush. Las ocho entradas conservan sin cambios sus brazos sincronos existentes:
updatelinedata, llamada Denise y actualizacion de overlap cuando corresponde.
No hay nuevo scheduler, semaforos ficticios ni reescritura de algoritmos raster.

Se conserva temp_line/this_line y thread_debug_lock, que pertenece al protocolo
de lock/unlock del vidbuffer, no a una API de hilos. drawing_init conserva tablas,
buffers y reset; drawing_free solo liberaba el worker, ausente en este perfil.
La auditoria Luna identifica usos externos de gui_sem solo en GTK no seleccionado.
start_draw_denise/end_draw_denise y el orden de llamadas de custom no se modifican.

include/statusline.h guarda fuera de P6 la declaracion createstatusline(HWND,int),
cuya implementacion/callers son Windows no seleccionados. No inventa HWND ni
retira el HUD generico. ADR0004 acepta este archivo22; drawing ya estaba en la
cola. Son37 parches/22 archivos/989 lineas de delta (+72); alarma1500 vigente.

El test_drawing_source preprocesa la unidad completa y analiza tokens pertenecientes
a drawing, sin borrar cuerpos de funciones: no hay worker/sem/start/sleep/queue
concurrente, ni dependencias threaddep o Windows fuera del CCR puro autorizado.
Compila tambien statusline.h con sysconfig/sysdeps P6. Esto prueba seleccion de
codigo y cabeceras, NO objeto/enlace ni rendering.

La compilacion completa CMake Debug/Release sigue fallando despues de superar
threaddep; logs build/t06b2bk-drawing-debug.log y -release.log. El comando directo
clang++ -std=c++17 -Iports/portable -Ibuild/generated -Ibuild/upstream/include
-Ibuild/upstream -ferror-limit=0 -fsyntax-only build/upstream/drawing.cpp conserva
el inventario completo en build/t06b2bk-drawing-all-errors.log:218 diagnosticos,
22 en drawing y196 en linetoscr_aga_fm0/fm1/fm2.205 son color_regs_aga ausente;
resto HUD xred/green/bluecolors/statusline y debug_dma/heatmap/sprite_mask/dma_rec.
Son superficies antes ocultas por el include threaddep, no hardware sustituido.
No se editan generados ni se habilita AGA para esconder errores. T06b2bl conserva
el cierre completo y las otras ocho unidades pendientes del ultimo build global.

Diferencia upstream documentada: quick_strobe sincrono pasa endpos como tercer
argumento, el worker pasaba vpos. Este cambio conserva la conducta sincrona;
la precision debe investigarse con reproducer raster antes de certificarla.
No se deduce fidelidad de pixels/frame/boot/Waterbox del probe de dispatch.

GEN01:39 outputs iguales entre dos builds HOST limpios. HOST01:83 rutas y196
archivos/776 includes/332 ids. SRC01:1134 archivos pristine verificados.
El probe Luna pasa O0/O2 con ASan/UBSan y enlace estricto: extrae estructura y
cuerpos reales de init/free, ocho wrappers/flush, updatelinedata, quick_denise_rga
y update_overlapped_cycles. Usa el tipo RGA de custom.h; render inferior y otras
inicializaciones son fronteras observadas, no proveedores de produccion.
La revision raiz extrae tambien la seleccion macro real y refuerza metadata
antes de cada callback, cruce real del ring y rechazo de linecnt distinto.
Verifica filtros RGA 0x1fe/0x3f, registros 0x20/0x40, sprwrite pendiente,
strobe(endpos), finalseg/no-finalseg, store/restore, flush y orden del lifecycle.
Un multithread_enabled=1 no habilita worker en P6. No hay implementaciones de
thread/sem en el probe: una referencia a ellas falla al compilar/enlazar.

40 regresiones cercanas pasan (custom OCS/VSync/timing/CIA, CPU ROM guard y35
pruebas documentales),42 tests unicos contando los dos nuevos. Permanecen los
warnings anteriores de custom; los errores del renderer completo se conservan
como evidencia de puerta abierta. No se afirma un objeto drawing compilado.
Upstream pristine y MacHawk permanecen intactos.

T06b2bk DONE; T06b2bl desbloqueada. validate y git diff --check PASS.
