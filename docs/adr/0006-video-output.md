# ADR 0006: salida de video del bridge

## Contexto

`p6_run_frame` debe entregar tras cada unidad un buffer con ancho, alto, stride, region activa, aspecto,
paridad de campo y cadencia (docs/02-contracts.md), sin asumir `stride = width * 4` ni una altura constante, y
con un limite del buffer demostrado, no supuesto. El renderizador de WinUAE 6.0.3 escribe en un unico buffer
XRGB8888 estatico de 1824 x 626 (`ports/portable/video_present.cpp`). El tamaño del raster lo decide
`compute_framesync()` (custom.cpp:1583-1794) a partir de las preferencias y de la temporizacion MEDIDA de la
pantalla, no de los registros de video del invitado. Mediciones y referencias de codigo: informe de investigacion de
T08b (reports/tasks/T08b.md).

## Decision

1. **Raster fijado por el preset**, no por el invitado: hires, lineas dobladas, `gfx_overscanmode` 3, sin centrado
   (`gfx_xcenter/ycenter` 0, sin ancho o alto extra), `gfx_iscanlines` 1 (los campos entrelazados se entretejen en
   un buffer), sin filtros ni scanlines. Resultado: 756 x 574 en PAL y 756 x 484 en NTSC para cualquier modo de
   video del invitado (lores, hires, planos, HAM, EHB, doble campo de juego, sprites, entrelazado). DIWSTRT,
   DIWSTOP y DDF solo mueven pixeles dentro del raster.
2. **Buffer constante por maquina y estandar**: `video_width x video_height` es 760 x 576 (PAL, y NTSC en la maquina
   ECS, ver "Haz programado") o 760 x 486 (NTSC del A500): el raster
   largo y 4 columnas mas, porque un invitado que alterna el entrelazado en cada unidad hace que las lineas NTSC (227/228
   relojes) se midan de 228 y el raster pase de 756 a 760. `active` es el raster que el renderizador dibujo en esa unidad, normalmente {0,0,756,574} / {0,0,756,484}
   y 576 / 486 filas si el invitado deja fijo el bit de campo largo sin entrelazar. Fuera de `active` todo byte, el
   relleno del stride incluido, es cero (lo garantiza el puerto; el probe lo comprueba en cada unidad).
3. **Aspecto = aspecto de pixel** del haz de la unidad (ver "Haz programado"): PAL 26:25, NTSC 43:50. El motor no tiene aspecto de pantalla (vive en od-win32,
   que no se compila); las constantes son las del libretro-uae fijado y coinciden con el calculo de primeros
   principios. La proporcion de imagen es `ancho*num : alto*den`.
4. **Paridad por filas del buffer**: PROGRESSIVE mientras el monitor no haya detectado entrelazado; despues EVEN
   para la unidad que reescribio las filas 0,2,4.. (campo largo de Amiga) y ODD para 1,3,5... Se lee de las banderas
   que el puerto captura al cerrar el pase, no de variables vivas, que al volver ya describen el campo siguiente.
5. **Cadencia**: fraccion entera por las banderas de la propia unidad y el haz que ejecuto (ver "Haz programado"): reloj
   del chipset partido por las CCK de la unidad. En el haz estandar (PAL 3546895/70824, campo largo 3546895/71051, entrelazado
   1418758/28375; NTSC 715909/11921, 1431818/23933, 2863636/47775). El espaciado exacto es `master_ticks`; nunca
   se usa el `vblank_hz` float del motor.
6. **Sin imagen**: si el renderizador no entrega una imagen nueva, o su raster no cabe en el buffer prometido, o se
   pidio sin `P6_RENDER_VIDEO`, `video` es NULL, `video_bytes` 0 y `P6_FRAME_NO_VIDEO`. La unidad avanza igual. La
   presentacion (contadores, traspaso) ocurre siempre, con o sin el flag, para que apagar el video no cambie el futuro.
7. **Un solo camino de presentacion**: `p6_bridge::present_unit()` es idempotente por unidad; lo llama `p6_run_frame`
   y el observador de STEP01 en el borde, asi que la traza compara la misma ruta que el bridge.
8. `p6_query_caps` publica el buffer real (760 x 576); `caps.flags` sigue en 0.

## Alternativas

- Informar el raster del motor tal cual (el ancho y alto de `video_*` cambiarian con el invitado): descartada; cada
  cambio obliga al frontend a rehacer texturas y da una unidad negra.
- Parchear `custom.cpp` para congelar `current_linear_*` (raster fijo): descartada; cambia `vblank_hz` y la
  sincronia de sonido, cuesta presupuesto de parches y no hace falta con un contenedor.
- Error y estado FAULTED cuando el modo no cabe: descartada; el estado de video del puerto no es pegajoso y el
  invitado puede volver a un modo valido. Se entrega sin imagen.
- Recortar la region activa a un rectangulo estatico: descartada; los margenes negros no son constantes.

## Evidencia

Investigacion de solo lectura con sonda propia sobre el motor real (PAL y NTSC, semillas 1 y 2, HAM, EHB, DPF,
sprites, entrelazado, LOF fijo, tramas largas y cortas). En el repositorio: `tests/step/test_step.py`
(`VideoMetadataTests`) comprueba geometria, aspecto, cadencia, paridad, relleno a cero, que apagar el video no
cambia el estado final ni los ticks, 40 unidades sin que nadie lea el audio, un invitado con campo largo fijo
(574 -> 576 filas dentro del mismo buffer) y un invitado cuyas tramas superan el buffer (se rechaza, la maquina
sigue, sin escrituras fuera). El mismo conjunto bajo ASan/UBSan es opt-in (`P6_TEST_SANITIZERS=1`).
`tests/host/test_a500_profile.py` ata cada preferencia de video al perfil.

## Contenido de la imagen (T08c)

Los programas de `tests/scenarios/a500/display.py` (un plano de bits con una sola fila repetida, barras de color
del Copper, campos entrelazados, cambios de modo por unidad) producen imagenes que un modelo analitico, escrito solo a
partir del programa, reproduce celda a celda en PAL y NTSC, lores y hires: la linea `26 + fila/2` (PAL) o `21 + fila/2`
(NTSC), la columna `76` como inicio de la ventana de DIWSTRT `$81`, colores OCS de 4 bits por canal. Hallazgos:
- Una fila del raster es mas ancha que la linea de haz en la que empieza: la columna 0 es el reloj de color 45,5 y cada
  columna un cuarto de reloj, asi que desde la columna 742 (reloj 4 de la linea siguiente) la fila ya muestra la linea
  siguiente. Un cambio de COLOR00 del Copper al inicio de la linea L+1 aparece en el extremo derecho de la fila de L.
- El entrelazado se entreteje como se documento: la unidad EVEN reescribe las filas pares y la ODD las impares
  (comprobado con un color de primer plano nuevo en cada unidad).
- Un invitado que alterna el entrelazado en cada unidad hace que el renderizador vuelva a medir el cuadro: PAL pasa a
  576 filas y NTSC a 760 x 486 (lineas de 228 relojes); de ahi el buffer de 760 columnas.

## Haz programado (T23f)

En una maquina ECS el invitado puede escribir BEAMCON0 y ejecutar unidades del otro estandar (o de un haz programable)
con la configuracion intacta (T23c lo midio: unidades NTSC anunciadas como PAL, y la imagen PAL perdida en un buffer
NTSC). Reglas adoptadas, todas sobre enteros que el motor ya tiene al volver la unidad (nada que fije el haz corre entre
el pase del renderizador y el retorno; `init_beamcon0` se aplica en vpos 0, antes del render de la linea 4):

- **Estandar del haz** `isntsc`: con Agnus ECS, bit PAL de `beamcon0` a 0 (la misma regla que `init_beamcon0`); con Agnus
  OCS, el de la configuracion (no hay BEAMCON0). Fija el aspecto (PAL 26:25, NTSC 43:50; un haz VARBEAMEN recibe el del
  bit PAL, sin afirmacion de hardware).
- **Cadencia** = `clock / CCK_unidad` con `CCK_unidad = (2*maxvpos + e) * (2*maxhpos_short + t) / 4`, `e` = 1 si entrelazado,
  2 si campo largo, 0 en otro caso (siempre 0 con VARBEAMEN, que no tiene campo largo: `maxvpos_long = -1`), `t` = 1 si
  el haz es NTSC y LOLDIS esta a 0 (la alternancia 227/228 de `linetoggle`). `clock` es el de la configuracion
  (3546895 o 3579545), el mismo en que se ancla el RTC; una maquina PAL con haz NTSC da asi 3546895/59605, no 3579545.
  Reduce a las constantes de arriba en los haces estandar. No se usa una medida de ticks (el borde de la unidad cae
  dentro de una instruccion) ni reloj de pared; el test compara el valor anunciado con el espaciado real de
  `master_ticks`.
- **Alto activo**: ya era el del pase (`info.height`), no de la configuracion.
- **Buffer**: se anuncia al cargar y no cambia en marcha. Una configuracion NTSC en la maquina ECS reserva 576 filas
  (la altura PAL); el A500 NTSC conserva 486 porque no puede salir de su estandar. Coste: solo el numero que se
  informa (`video_height`, `video_bytes`); el array estatico del puerto ya tiene 626 filas, no cambia ningun pixel,
  estado ni hash del A500. Alternativa descartada: buffer variable por unidad (obliga al frontend a rehacer texturas y
  rompe "el maximo se anuncia al load"). Los rasters de mas de 576 filas o 760 columnas siguen sin entregarse.
- **Unidad de transicion**: la unidad en la que cambia BEAMCON0 se describe con el haz nuevo aunque en parte corrio con el
  anterior (el cambio se aplica antes del render): un error de una unidad, determinista.
- **Cero fuera de `active` tambien en la transicion** (T23g). Las primeras lineas de la geometria nueva (p. ej. productividad + SuperHires,
  HTOTAL 150) se dibujan mientras el motor aun tiene el ancho del modo anterior y podian pasar de la anchura entregada dentro de la fila de
  1824 pixeles del array del puerto. `unlockscr()` limpia al final de cada pase la cola `[w, 1824)` de las filas `[0, h)` y las filas
  `[h, asignadas)` del buffer canonico (no del auxiliar): unos 74 us por unidad (0,2 %), sin ensanchar el buffer ni escribir fuera del array.
  La garantia (todo byte fuera de `active` vale cero) vale en cada unidad de cada programa ECS de haz, PAL y NTSC; los hashes del A500 y ECS no cambian.
- **Adaptadores**. libretro anuncia en el load la tasa nominal del estandar configurado (`retro_get_system_av_info`, buffer
  maximo 760 x 576 de `caps`) y, en la frontera de `retro_run` en que una unidad llega con otra tasa, llama una vez a
  `RETRO_ENVIRONMENT_SET_SYSTEM_AV_INFO` (fps nuevo, con la geometria y el aspecto de esa misma unidad); un cambio solo de
  aspecto o de tamano va por `SET_GEOMETRY`. Banda muerta del 1 %: el campo largo y el entrelazado mueven la tasa de un haz
  estandar un 0,3 % (50,08 / 49,92 / 50,00 Hz) y un frontend que reinicia audio y video por eso lo haria cada vez que un
  invitado alterna el entrelazado; PAL<->NTSC son 19 %. Un frontend que rechaza el cambio no recibe la misma peticion otra
  vez. El adaptador de BizHawk relee cadencia y aspecto en cada unidad con imagen (sin cambios) y su `BufferHeight` inicial
  es el del buffer del load (576, salvo A500 NTSC).

## Costo y riesgos

- Sin parches nuevos del motor. `p6_run_frame` descarta el PCM tras cada unidad hasta T11: sin ello la cola se llena y
  la decima llamada falla.
- `P6_VIDEO_FRAME_DISCARD` es inalcanzable en este perfil (solo lo levantan rutas de lapiz optico, DMA de depuracion,
  grabacion y monitores especiales): un cambio de raster se ve como UNA unidad negra o a medias, deterministica.
- Al entrar en entrelazado, las unidades 1-3 se dibujan progresivas, la 4 lleva las banderas pero pixeles de cuadro
  completo y la 5 es la primera con campo (media negra): determinista, pero el frontend la vera.
- Estado a inventariar en T15: `denise_accurate_mode` y `denise_*_extra_*` se derivan al recalcular el modo; un
  estado restaurado en un proceso nuevo que se salte `init_hz` dibujaria negro. El buffer de pixeles es historia (la
  trama entretejida) y forma parte del estado.
- El Kickstart real puede dejar LOF activo tras una pantalla entrelazada (episodios de 576 filas): sin medir hasta
  que exista una ROM autorizada.

## Validacion

`python3 -m unittest tests.step.test_step` (arbol `build/step-trace`), `tests.host.test_a500_profile`,
`tests.host.test_bridge_caps`. AV01 completo (pixeles con contenido, PAL/NTSC, lores/hires, entrelazado) es T08c.
