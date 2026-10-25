# T45: RTG software acotado, UAE Zorro III

## Decision y autoridad

Se elige la tarjeta **virtual UAE Zorro III**, `GFXBOARD_UAE_Z3 = 1`, con
una sola placa y **4 MiB de VRAM**. Es una extension propuesta para A4030,
CPU Normal, MMU0; no requiere implementar MMU ni cambiar CPU. No es Picasso II,
una GPU fisica, ni una promesa de compatibilidad con todos los controladores RTG.
El estudio no habilita PICASSO96, opciones, fuentes ni perfiles. Los IDs RTG-A..F
del JSON son propuestas; requieren fichas y puertas propias antes del port.

Autoridad: WinUAE6.0.3/tag6030, commit
`857f48fcb09399d4d95527e29a2fdeaedb41f827`, tagobj
`88e3c2c6eac57d6dfc2ed688455a35e2e3a86a59`. El snapshot local
`upstream/winuae` esta inventariado por `manifests/upstream-files.json`;
`plan/upstream.lock.json` fija su procedencia. Referencias siguientes: lineas
originales de ese pin, no resultados de ejecucion ni especificacion de hardware.

| Fuente local | Contrato observado y consecuencia |
| --- | --- |
| `include/gfxboard.h:68..70` | UAE Z2/Z3 se distinguen de GFXBOARD_HARDWARE. Elegir Z3 evita consumir la ventana Z2 de la Fast RAM existente. |
| `expansion.cpp:2123..2171,3796..3806` | Banco propio, Autoconfig Z3RTG; con 4 MiB poblados anuncia apertura fisica minima 16 MiB y subsize 4 MiB. No asignar 16 MiB de backing ni anunciar RAM inexistente. |
| `od-win32/winuae_msvc15/winuae_msvc.vcxproj:1201` | Compila `od-win32/picasso96_win.cpp`; no usar el `picasso96.cpp` generico antiguo como implementacion actual. |
| `od-win32/picasso96_win.cpp:2594..2620,6017..6220` | Instala uaegfx.card, listas de modos y vectores/traps Amiga; no necesita ROM de una tarjeta Cirrus. Inventariar tambien `autoconf`, `traps`, `native2amiga` y rtarea antes de enlazar. |
| `od-win32/picasso96_win.cpp:3080..3207` | SetGC cambia geometria; SetPanning conserva direccion, stride, offsets y superficie virtual. No basta con capturar el bitmap visible. |
| `od-win32/picasso96_win.cpp:1233..1298,6230..6247` | Vblank/hsync y render multihilo son caminos distintos. Mantener el primero ligado al tiempo emulado y no incorporar el hilo host. |
| `od-win32/picasso96_win.cpp:6474..6670` | restore_p96/finish reponen modo, paleta, flags y vectores; `HostAddress` se reconstruye. Es inventario inicial, no prueba de suficiencia del estado PUAE6. |
| `expansion.cpp:4045..4072,4074..4096` | save_pram devuelve TODO el backing; expansion guarda base de bus. Preservar tambien Autoconfig, VRAM oculta y mapa tras reset/restore. |

## Perfil propuesto y fronteras

Modos anunciados cerrados: **640x480, 800x600, 1024x768**, cada uno en CLUT8,
`RGBFB_R5G6B5PC` y `RGBFB_A8R8G8B8` (nombres/formatos en
`include/rtgmodes.h:7..14`). Son nueve modos propuestos,
no una lista de capacidades ya probadas. CLUT tiene 256 entradas RGB; RGB565PC
conserva su orden de bytes y ARGB32 su alpha no significativo. La conversion
software a XRGB8888 define alpha de salida fijo; no usa floats ni formato host.
No se anuncian planar, 15/24 bit, YUV, overlay, hardware cursor, 3D, multimonitor,
Z2, tarjetas fisicas ni modos enumerados desde el monitor anfitrion.

La superficie virtual se limita a 1024x768, stride positivo de hasta 4096 bytes;
los formatos usan 1/2/4 bytes por pixel. Comprobar con aritmetica ensanchada
`offset + (height-1)*stride + width*bpp <= VRAM`, asi como superficie virtual,
rectangulos, solapes y panning. No permitir wraparound, pitch menor que la fila,
base fuera de banco ni accesos a la parte no poblada de la apertura Z3.
La lista de modos y los limites forman parte de sync config/identidad, con
reinicio al cambiar placa/VRAM. RTG0 conserva su identidad y memoria actuales.

El backend upstream incluye `win32gfx.h`, `direct3d.h`, WriteWatch, cursores host,
render_pipe/CRITICAL_SECTION y enumeracion de modos (`picasso96_win.cpp:65..105,
797..864,2408..2464,5389..5409`). Se requiere una frontera software con equivalencia
observable: operaciones de blit/fill sobre memoria propia, tabla fija de modos,
paleta propia y refresco completo acotado, inicialmente sin dirty tracking host.
La lista `newmodes[MAX_PICASSO_MODES]` y las tablas de conversion upstream no
justifican reservas sin inventario: acotarlas a los nueve modos antes del port.
No portar Win32/D3D/OpenGL ni devolver exito ficticio a servicios excluidos.
Los vectores publicados deben corresponder a operaciones realmente implementadas;
RTG-A debe fijar sus respuestas de fallo antes de tratar peticiones invalidas.

Usar el camino upstream `win32_rtgvblankrate=0`, host vsync desactivado y
`rtg_multithread=0`: vblank RTG se atiende desde el vsync emulado existente,
no desde el refresco del monitor. Su IRQ/espera debe probarse contra el pin;
no anunciar una frecuencia RTG independiente. CPU/chipset siguen avanzando
exclusivamente por `p6_run_frame`, tambien con RTG visible o render desactivado.

## Ownership y estados

El motor posee VRAM mutable, modo/paleta, direccion/stride, flags pendientes,
seleccion chipset/RTG y estado de interrupcion. Las escrituras/traps cambian
estado Amiga en su momento original; solo la entrega host de nueva geometria
se publica al finalizar una unidad, junto con sus pixeles. No posponer hardware
para acomodar la UI. Un descriptor de entrega pertenece al motor hasta la
siguiente llamada; ningun callback retiene punteros managed o de otro sandbox.

Se conserva el buffer chipset actual y se añade un buffer RTG software maximo
1024*768*4. El limite chipset 1824x626 (`ports/portable/video_present.h:35..48`)
no admite altura 768: exige capacidad/ABI y validacion explicitas del perfil RTG,
sin ampliar silenciosamente el perfil existente. Libretro negocia geometria
antes del callback de video y entrega un solo display seleccionado. Waterbox
copia desde guest a su IVideoProvider, revalida dimensiones/capacidad y guarda
los campos causales del wrapper. No requiere IGLTextureProvider. Ambos adaptadores
deben tolerar chipset→RTG→chipset, negro/display off y cambio de modo consecutivo.

Nativo: codec semantico versionado con VRAM 4 MiB completa, base/mapeo Z3,
modo actual/pendiente, Address/XYOffset/VirtualWidth/Height/stride/formato,
CLUT, SetGC/SetPanning/SetDisplay/SetSwitch, IRQ y fase de entrega. Inventariar
ademas los globales/listas/vectores de uaegfx; no serializar structs C++ ni
punteros host. Validar identidad, longitudes y rangos antes de mutar el estado;
rebind de callbacks y reconstruccion de HostAddress con la base nueva.

Waterbox: VRAM y estado mutable dentro del snapshot, nunca sealed/invisible.
Capturar inicialmente tambien el buffer de conversion; excluirlo exigiria una
puerta poison con pixeles actuales y futuro identicos, no basta llamarlo cache.
Restaurar a otra instancia/proceso, tras cambios de modo y con output desactivado,
debe conservar ticks, IRQ, RAM/VRAM completa, video y PCM. No hay compatibilidad
de estados/peliculas entre builds ni entre el codec nativo y Waterbox.

## Cotas propuestas, pendientes de medicion

| Incremento RTG sobre el perfil base | Bytes | Grupos Darwin 16 KiB |
| --- | ---: | ---: |
| VRAM 4 MiB completa, incluida la zona no visible | 4194304 | 256 |
| Un buffer XRGB8888 de 1024x768 | 3145728 | 192 |
| Tope de estado auxiliar RTG, paleta/listas/flags/seguimiento | 16384 | 1 |
| Total mutable adicional maximo propuesto | **7356416** | **449** |

Son 1796 paginas logicas 4 KiB; escribir un byte por grupo puede ensuciar los 449
grupos completos. Incremento de payload sin comprimir conservador: 7356416 bytes;
metadatos del formato/runtime y el sobre se cuentan aparte, no se supone ratio
Zstd ni tiempo de guardado. El buffer chipset, heaps existentes y temporales del
serializador no estan incluidos en ese incremento. Para un save nativo que
necesite copia completa, presupuestar otros 7356416 bytes de scratch transitorio.

Reserva inicial propuesta: añadir 14368 KiB al presupuesto mmap (doble del
incremento mutable, siguiendo el margen existente), sin tocar otros heaps ni
usar memoria invisible. No es un pico medido ni una garantia de que el port quepa:
el inventario debe demostrar que paleta/listas/traps/caches adicionales caben
en 16 KiB auxiliares. Si no caben, se bloquea esa vertical y se revisa la cota antes
de asignar; no crecer dinamicamente para aceptar cualquier modo. Medir committed,
pico, dirty groups, payload y tiempo de save/load en ambos frontends, incluyendo
VRAM entera sucia y dos sandboxes simultaneos. La receta actual no reserva nada
por este ADR.

## Puertas antes de anunciar soporte

RTG-A..F en `plan/extensions/rtg.json` fijan dependencias y oraculos propuestos.
Primero probes propios de Autoconfig/traps, patrones de VRAM y conversion con
valores esperados independientes calculados por formato, no screenshots de otro
emulador como unico oraculo. Despues integracion Amiga real con version y licencia
del sistema/driver Picasso96 fijadas y autorizadas: el uaegfx generado por WinUAE
no equivale al sistema Picasso96 completo ni autoriza redistribuirlo.

Ninguna puerta se declara pasada aqui. Antes de exponer una opcion se requieren
GEN01/SRC01 de la closure, AV01 de los nueve modos, AV02 de la continuidad PCM,
DET02/STATE01/STATE02 de cambios/VRAM y rechazos,
LR01 y WB03/WB13..16 reales, presupuestos PERF01, limites SAFE01 y matriz portable
con sanitizers.
El mismo ciclo de traps/modos y cortes se comparte entre productos; una prueba
nativa no sustituye la ejecucion dentro de Waterbox. No se necesitan firmware,
paquetes ni GPU host para cerrar este estudio.
