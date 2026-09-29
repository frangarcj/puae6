# T06b2ag — tangente Paula independiente del FPU

audio_math.cpp conserva binary64 -> float64_to_floatx80 -> floatx80_tan ->
floatx80_to_float64 -> binary64. Copia bits con memcpy, exige binary64 y usa
precision80/round_to_zero en un estado local. El wrapper fpp_native original
configura un local que no usa y depende de fs inicializado por fp_init_native.
La equivalencia comprobada es con ese upstream inicializado, no con fs previo
a init. Luna reviso esa dependencia y la clausura de fuentes/licencias.

Se seleccionan softfloat.cpp y softfloat_fpsp.cpp, sin fpp_native, decimal ni
FPUEMU/WITH_SOFTFLOAT. El grafo CMake tiene 35 objetos de motor. Parche0021
corrige el conflicto extern/static de float_raise mediante inline externo y
el nombre exacto SOFTFLOAT-MACROS.H. La primera compilacion pristine mostro
ambos problemas; no se cambian operaciones ni tablas para resolverlos.
Se conservan avisos y hashes de los seis archivos retenidos en licenses.json.
ADR0004 revisa estado/alcance y el presupuesto: 21 parches, 19 archivos,
509 lineas añadidas/eliminadas. La alarma de 1500 lineas sigue vigente.

El probe extrae literalmente los helpers de palabras, softfloat_tan y
rc_calculate_a0 del upstream pristine como referencia. Compara bits de los
seis coeficientes A500 (6200/7000/20000 Hz, tasas44100/48000), bypass Nyquist,
4096 entradas de tangente entre 1/64 y1.5 con ambos signos, ceros con signo,
subnormales, minimo normal, infinitos y NaNs. Repite con cuatro modos de
redondeo host contra referencia FE_TONEAREST e intercala otras llamadas para
detectar dependencia del estado previo. Todo con O0/O2 y ASan/UBSan.
Usa audio.cpp real con descarte de secciones ajenas; no proveedores ficticios
de hardware ni ejecucion de CPU/DMA. No es un oraculo independiente de FPSP.

Checks:
- unittest audio_math/PCM/plan/native_cmake: 40 PASS (35 documentales del plan).
- CMake Debug/Release: audio.cpp, ambas unidades SoftFloat, p6_portable y probe
  compilan; CTest portable-services PASS en ambos presets.
- Imports de unidades completas y depfiles comprobados por test: sin tan/libm
  host ni backend FPU; include sensible a mayusculas tratado como error.
- GEN01: 39 salidas identicas en dos builds HOST limpios.
- HOST01: 196 archivos, 776 includes, 332 identificadores; inventario83 rutas.
- SRC01: 1134 archivos pristine verificados offline; git diff --check PASS.

Limites: solo macOS ARM64 ejecutado. No valida makefir, filtros completos,
set_audio, audio_deactivate del halt, reloj de custom ni snapshot Waterbox.
Los globals internos SoftFloat permanecen memoria ordinaria y se conserva
el contrato monohilo; no se promete reentrancia. T06b2ah conserva estas puertas
y la compilacion global; los 12 fallos del ultimo build global T06b2aa siguen
pendientes, no se ha vuelto a afirmar ni comprobar enlace de maquina completa.
Los avisos PERIOD_MAX de audio permanecen documentados en T06b2ac.
