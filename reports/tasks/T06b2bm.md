# T06b2bm — Renderer OCS/ECS

Parche0038 permite compilar drawing.cpp completo sin AGA ni DEBUGGER. Guarda
consumidores de almacenamiento AGA, decodificadores, selección/includes generados,
lookup DPF y copia de registros; elimina placeholders de un elemento. Excluye
HUD host sin callers y guarda referencias al depurador. Conserva ramas OCS/ECS,
HAM6, genlock, NTSC, colisiones y almacenamiento de seis planos. No edita upstream
ni generados. Cola:38 parches/22 archivos/1071 líneas de delta (+82), bajo alarma1500.

Compilan los objetos completos CMake native-debug (sanitizers) y native-release;
test_drawing_source compila además O0/O2 sin GC de enlace, revisa imports y
preprocesado/depfiles: sin worker/threaddep, HUD Windows, consumidores AGA ni
includes linetoscr_aga. Permanecen los ocho includes common/OCS/ECS correspondientes.
Los servicios video/save-state/etc. externos siguen pendientes de enlace.

El probe Luna ejecuta update_color, setlasthamcolor, decode_ham_pixel_fast y
bpldat_docopy reales, extraídos de pristine y parcheado en binarios separados.
Referencia con almacenamiento AGA, siempre aga_mode=false; P6 sin ese almacenamiento.
Comprueba 32 registros, valores representativos, ecs/genlock, callback COLOR00
observado, HAM6 por transición independiente y copia completa de seis planos.
En OCS copia32 incluso si denise_bplfmode64 cambia; no copia64. Comprueba máscara
legacy y flags/trigger offset. La expectativa inicial del probe sobre wide era
incorrecta y se corrigió leyendo ambas ramas reales; no se alteró producción.

Ejecuta también lts_ecs_n_ilores_dlores_b1 del generado real: tres píxeles de
bitplane, bordes/blank, cursor, colisiones y centinelas. La tabla de colores y
bplcoltable son entradas observadas; no prueba su construcción ni un frame.
Referencia/P6 producen observaciones iguales en O0/O2 con ASan/UBSan. No se deduce
precisión de todas las rutinas/selector ni validación pública de perfiles inválidos.

Comandos/resultados:
- python3 -m unittest tests.host.test_drawing_source tests.host.test_drawing_sync
  tests.host.test_custom_ocs tests.host.test_custom_vsync tests.host.test_custom_timing
  tests.host.test_custom_cia tests.host.test_cpu_rom_guard tests.test_plan:42 PASS.
- python3 -m unittest tests.host.test_drawing_ocs -v:1 PASS (Luna).
- cmake --build --preset native-debug/native-release --target
  CMakeFiles/p6_engine.dir/build/upstream/drawing.cpp.o:PASS ambos.
- GEN01:39 salidas iguales entre dos builds HOST limpios.
- HOST01:83 rutas;196 archivos/776 includes/332 identificadores.
- SRC01:1134 archivos pristine verificados. validate/diff --check PASS.

Se conservan diagnósticos en build/t06b2bm-drawing-debug.log y -release.log:
47 warnings,45 de helpers generados de siete/ocho planos con MAX_PLANES=6. El test
sobre el preprocesado completo exige que los30 nombres aparezcan solo una vez,
en su definición: no hay callers ni selección en la unidad OCS/ECS. Los otros
dos warnings son comparación u8<256 y switch. No se silencian; T06b2bn conserva
su revisión y generación por features con reproducer, sin ampliar arrays.

Build global con --target p6_engine -- -k 0 sigue fallando:9 ->8 unidades,
registradas en build/t06b2bm-engine-failures.log: inputdevice,disk,cfgfile,expansion,
main,calc,ini,savestate. No hay core enlazado, boot ni ejecución Waterbox nueva.
T06b2bn mantiene esas puertas, validación pública y precisión quick_strobe.

T06b2bm DONE; T06b2bn desbloqueada. MacHawk permanece intacto.
