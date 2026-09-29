# T06b2ac — amplitudes signed Paula

0020 cambia dos lineas de audio.cpp: FINISH_DATA multiplica por 2/4 en lugar de
desplazar un int negativo, y samplexx_sinc_handler multiplica sinc_output_state
por 131072 en lugar de desplazarlo 17 bits. C++17 no define esos desplazamientos
negativos. Las multiplicaciones caben en int en el dominio Paula comprobado:
sample signed8 por volumen 0..64 produce -8192..8128; sinc escala ese estado
a -1073741824..1065353216. FINISH_DATA produce amplitudes signed16 antes del
volumen de salida 0..32768, cuyo producto tambien cabe en int. No se cambia
redondeo, clipping, tablas, formato, tiempos ni filtros. Otros dispositivos y
configuraciones arbitrarias no quedan autorizados por esta prueba.

Reproducers originales fallan en ambos shifts con -8192, guardados en
build/t06b2ac-{finish,sinc}-before.log y ejecutable t06b2ac-scale-before.
El probe persistente tests/host/audio_scale_probe.cpp cubre:

- FINISH_DATA: todos los enteros del rango signed de cada ancho 14..18 y cinco
  volumenes (0,1,16384,32767,32768), comparados con division/multiplicacion int64
  independiente; comprueba ambos canales y redondeo negativo.
- Mezclador stereo real: todos los samples -128..127 y volumenes 0..64; canales
  simultaneos al minimo producen -32768 por lado, sin UB.
- Sinc: 25 pares de niveles incluidos extremos, todas las edades 0..2048 y
  tablas sin filtro/A500 LED off/on, frente a acumulacion de referencia int64.
  Esto ejercita escalado y steps aislados, no certifica cualquier historia de
  la cola sinc, filtros completos, DMA ni determinismo entre arquitecturas.

O0/O2 ASan/UBSan PASS; se conserva el probe de transporte PCM y orden/drains.
Regresiones PCM/devices/plan: 37 tests PASS (35 son del plan documental).
audio.cpp entero y p6_portable compilan CMake Debug/Release. GEN01: 39 outputs
identicos en dos builds HOST. HOST01: 191 archivos, 766 includes, 332 identificadores.
La cola tiene 20 parches, 17 archivos y 503 lineas de delta. No se ocultan warnings
de PERIOD_MAX, ni se afirma que el core completo compile/enlace.

## Auditoria PERIOD_MAX

Luna reviso todos los usos: per es int; ULONG_MAX se convierte a -1 y
ULONG_MAX-1 a -2 en los toolchains probados. El reproducer nativo LP64 confirma
comparaciones sentinel true. Un objeto COFF compile-only con target
x86_64-pc-windows-msvc confirma por static_assert los mismos valores LLP64.
Fuentes/artefactos: build/t06b2ac-period-repro.cpp y
build/t06b2ac-period-cross.cpp/.obj. No es ejecucion Windows ni prueba completa
de audio en LLP64. La promocion unsigned preserva esas comparaciones; no hay
evidencia de divergencia que justifique alterar periodos en esta correccion.
La conversion out-of-range sigue siendo implementation-defined en C++17 y
produce avisos LP64. Se conserva visible; una normalizacion futura debe cubrir
reset/restore/interpolacion y consumidores, sin cambiar constantes a ciegas.

T06b2ad conserva lifecycle, validacion de tasa/config, softfloat/filtros, enlace
de audio y las restantes unidades. Ultimo build completo T06b2aa: 12 unidades
fallan; no se repite porque estas dos lineas no cambian sus dependencias.
Caches previas: build/{upstream,generated}-before-t06b2ac.
