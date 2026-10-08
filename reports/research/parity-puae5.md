# Paridad PUAE6 frente a PUAE5 (libretro-uae): inventario, estado y tareas nuevas

Investigacion de solo lectura. Fecha del plan: 2026-10-08 (T24a in_progress). Nada del repo se ha editado.

## 0. Fuentes y salvedades

- Referencia pedida: `references/machawk-uae` (fork TASEmulators de libretro-uae, README "Based on WinUAE 5.0.0", commit ccecb1e).
  Todas las citas `core.c:N` / `core.h:N` son de `references/machawk-uae/libretro/libretro-core.c|h` salvo que se diga otra cosa.
- Existe ademas `references/puae` (libretro-uae master 6536174, "Based on WinUAE 5.3.1"). Es MAS NUEVO. Diferencias medidas:
  1. opcion extra `puae_use_whdload_buttonwait` (puae/core.c:1281, defecto enabled);
  2. `valid_extensions` termina en `|/` (lanzar directorios) (puae/core.c:5216);
  3. `uae_kickstarts[]` añade A1000 KS1.1 NTSC/PAL (puae/core.h:27-42, no hay modelo A1000);
  4. `puae_cpu_compatibility` defecto `exact` en x86_64 (machawk-uae: `memory`) y nota "(DMA/Memory) se fuerza a (Full) con 68000" (puae/core.c:1101,1111);
  5. los presets se generan con `preset_opt` y `cscompa[]` pero con los mismos valores de CPU/chipset/RAM que machawk-uae (puae/core.c:6150-6320);
  6. machawk-uae conserva tres opciones de compatibilidad ya retiradas en puae: `puae_zoom_mode`, `puae_zoom_mode_crop`, `puae_mapper_zoom_mode_toggle` (marcadores "deprecated", core.c:1397,1438,2234).
  La paridad "al menos todo lo de PUAE5" debe usar la UNION: lo de machawk-uae + `buttonwait` + lanzar directorio.
- Fuente de verdad PUAE6: `plan/tasks.json` (252 fichas), `docs/02-contracts.md` ("Maquinas y capacidades (T23)", lineas 283-319),
  `docs/01-architecture.md` (perfiles, 130-160), `AGENTS.md`, `config/schema/options.json`, `adapters/libretro/*`, `bridge/include/p6.h`.
- Hechos verificados que condicionan todo:
  - PUAE5 NO compila JIT: `retrodep/sysconfig.h:12` `//#define JIT` y `Makefile.common` tiene el bloque JIT comentado. Tampoco BSDSOCKET (l.55),
    A2065, SANA2, PICASSO96 (l.53), PPC, x86 bridge. Compila: DRIVESOUND, FILESYS, AGA, CD32, CDTV, PARALLEL_PORT, SERIAL_PORT, SCSIEMU, UAESERIAL,
    FPUEMU, MMUEMU/FULLMMU, CPUEMU_0/11/13/20-25/31-35/40/50, ACTION_REPLAY, CAPS (dlopen), FDI2RAW, ARCADIA, SAVESTATE, A2091, NCR, GFXBOARD, A_ZIP/A_LHA/A_DMS,
    CHD y MPEG2 (Makefile:313,316). => JIT, red y RTG NO son huecos de paridad: no hay conflicto con el perfil deterministic.
  - PUAE5 monta HDF con controlador `uae0` (`hardfile2=rw,DH%d:...,uae0`, core.c:5832-5844, 6916, 7015) = placa autoconfig de filesys/uaehf, NO Gayle IDE.
    Por tanto el HDF de PUAE5 funciona en TODOS los modelos con KS>=2.x/3.x sin Gayle. "Gayle IDE" no es requisito de paridad estricta; si lo es el
    montaje por `uae0`/filesys (y el preset A600/A1200 de PUAE5 lleva Gayle real por `chipset_compatible=A600/A1200`, core.c:6185-6215).
  - El A600 de PUAE6 NO es el A600 de PUAE5. PUAE5: KS3.1 A600 (`kick40063.A600`), 2M Chip + 8M Fast, con Gayle (core.c:6185-6194, 6065).
    PUAE6: KS2.05, 1 MiB Chip, sin Fast, sin Gayle (docs/02-contracts.md:289-302). `config/schema/options.json` ya lo declara (por eso la clave es `puae6_machine`).
  - PUAE6 es SOLO ciclo-exacto (68000 CE, T24a añade 68020 CE): `bridge/config.cpp:159` fija `gfx_framerate=1`, el perfil fija `cpu_cycle_exact=1`,
    `blitter_cycle_exact=1`, `immediate_blits=0`, `waiting_blits=0` (config/profiles/a500-ocs.json:234-243).
  - El raster de PUAE6 es canonico fijo: Hires, linea doble, overscan 3, 756x574 PAL / 484 NTSC (bridge/config.cpp:156-171); formato de pixel unico XRGB8888
    (libretro_core.cpp:1059).
  - La ABI solo conoce 2 tipos de puerto (JOYSTICK, MOUSE), 3 botones y un solo raton en ports[0] (bridge/include/p6.h:90-99; input_map.h:30-31).
  - `retro_get_memory_data` devuelve NULL, no hay SET_MEMORY_MAPS, ni LED, ni SUPPORT_ACHIEVEMENTS (libretro_core.cpp:1048-1049,1142-1143; cabecera l.30).
  - No hay ruta de `rom.key` (ROMs cifradas de Amiga Forever, `amiga-os-130.rom` etc.) ni en `p6.h` ni en `adapters/` (grep vacio); `firmware.h` lista nombres Amiga Forever
    pero un fichero cifrado se rechazaria por contenido. ADR 0001:123 menciona AMIROMTYPE1 en el motor, sin API de clave en la ABI.

## 1. Inventario PUAE5 y estado en PUAE6

Leyenda: HECHO (tarea/commit) | PLANIFICADO (tarea, oblig./FUTURE) | FALTA | CONFLICTO. "P##" = tarea nueva propuesta en la seccion 3.

### 1a. Modelos y presets

Todos los presets de PUAE5 salen de `emu_config()` (core.c:6108-6305; `chipmem 1=0.5M,2=1M,4=2M; bogomem 2=0.5M; fastmem 8=8M`). KS = `emu_config_string("kickstart")` core.c:6060-6075.

| Modelo (opcion `puae_model`, core.c:758) | CPU / FPU / MMU | Chipset (`chipset_compatible`) | Chip | Slow | Fast | Kickstart / extras | PUAE6 |
|---|---|---|---|---|---|---|---|
| A500 (l.6152) | 68000 | OCS (A500) | 0.5M | 0.5M | 0 | 1.3 `kick34005.A500` | HECHO (T08; preset A500 identico) |
| A500OG (l.6163) | 68000 | OCS (A500) | 0.5M | 0 | 0 | 1.3 | FALTA -> P01 (ABI fija 512 KiB Slow, options.json "unsupported bogomem_size") |
| A500PLUS (l.6174) | 68000 | ECS (A500+) | 1M | 0 | 0 | 2.04 `kick37175.A500` | FALTA -> P01. Hardware cercano al A600 de PUAE6 (T23) pero con KS2.04 y su tabla `cs_*` propia |
| A600 (l.6185) | 68000 | ECS (A600) + Gayle | 2M | 0 | 8M | **3.1** `kick40063.A600` | DIFERENTE. PUAE6 A600 = 1M, KS2.05, sin Gayle (T23, HECHO). Equivalente de PUAE5: P02 (Fast+2M Chip) + P03 (KS3.1) + P08 (Gayle) |
| A1200OG (l.6207) | 68020 (24-bit) | AGA (A1200) + Gayle | 2M | 0 | 0 | 3.1 `kick40068.A1200` | PLANIFICADO T24 (oblig.): A1200 = 68EC020 CE, AGA, 2M, SIN Gayle, waiting_blits=0 (T24a ficha, decision 2026-10-08). Diferencia con PUAE5: Gayle (P08) |
| A1200 (l.6196) | 68020 | AGA | 2M | 0 | 8M | 3.1 | T24 + P02 (Fast) |
| A2000OG (l.6229) | 68000 | OCS (A2000) | 0.5M | 0.5M | 0 | 1.3 | FALTA -> P01 |
| A2000 (l.6218) | 68000 | `ecs_agnus` (A2000) | 1M | 0 | 0 | 3.1 A600 `kick40063.A600` | FALTA -> P01 (+P03) |
| A4030 (l.6240) | 68030, FPU 68882, MMU 68030, 32-bit | AGA (A4000) | 2M | 0 | 8M | 3.1 `kick40068.A4000` | FALTA -> P06 (+P05 FPU, P07 MMU) ; T44 FUTURE hoy |
| A4040 (l.6254) | 68040, FPU 68040, MMU 68040, 32-bit | AGA (A4000) | 2M | 0 | 8M | 3.1 A4000 | idem |
| CDTV (l.6267) | 68000 | `ecs_agnus` (CDTV), sin floppy | 1M | 0 | 0 | 1.3 + ext CDTV `kick34005.CDTV` + NVRAM | FALTA -> P18/P19 (T43 FUTURE hoy) |
| CD32 (l.6279) | 68020 | AGA (CD32), sin floppy | 2M | 0 | 0 | 3.1 CD32 (`kick40060.CD32` + `.ext`, o 1 MiB combinada) | FALTA -> P18 |
| CD32FR (l.6291) | 68020 | AGA (CD32) | 2M | 0 | 8M | idem | FALTA -> P18 + P02 |
| auto (l.758,6330-6340) | segun contenido: FD -> `puae_model_fd` (A500), HD -> `puae_model_hd` (A1200), CD -> `puae_model_cd` (CD32) | | | | | opciones `puae_model_fd/hd/cd` (l.798/820/838), `puae_model_options_display` (784) | FALTA -> P29 (depende de P01/T24/P15/P18) |
| Presets personalizados `puae_libretro_<modelo>.uae` en saves (core.c:6113-6141), global `puae_libretro_global.uae` (7366-7380), por contenido `<nombre>.uae` (7381-7400) | | | | | | | PLANIFICADO parcial: T31 "importador .uae allowlist" (oblig.). Ver CONFLICTO C9 |

Tags de nombre de fichero (solo con modelo `auto`; busca en TODA la ruta; el primero que casa gana, en este orden), core.c:6483-6545:
`(A4030)|(030)` -> A4030; `(A4040)|(040)` -> A4040; `(A1200OG)|(A1200NF)` -> A1200OG; `(A1200)|CD32` -> A1200; `(A600)` -> A600; `(A500+)|(A500PLUS)` -> A500PLUS;
`(A500OG)|(512K)|(512KB)` -> A500OG; `(A500)` -> A500; `AGA|AmigaCD` -> A1200 si no es ya AGA; `ECS` -> A600 si no es ya A500PLUS; `OCS` -> A500 si no es ya A500*.
Sin tag y contenido HD/WHDLoad -> `opt_model_hd`. CD (core.c:7047-7060): `(CD32FR)|FastRAM` -> CD32FR; `(CD32)|(CD32NF)` -> CD32; `CDTV` -> CDTV; si no `opt_model_cd`.
Region (solo si `puae_video_standard` = auto, core.c:5494-5511): `NTSC|(USA)` -> NTSC; `PAL|(Europe)|(France)|(Germany)|(Italy)|(Spain)|(Finland)|(Denmark)|(Sweden)` -> PAL.
Otros: `(CE)` fuerza ciclo-exacto (7327); `(MD)` MultiDrive (7001); `_Arcadia` activa pad Arcadia (5354); `CD32` o preset CD32 activa CD32 pad (5350-5351).
PUAE6: FALTA (todo) -> P29.

Kickstart (opcion `puae_kickstart`, core.c:853; tabla `uae_kickstarts[]` en core.h): 4 nombres por ROM (normal/Amiga Forever/TOSEC-mod/TOSEC), incluye KS1.2, 1.3, 2.04, 2.05 A600, 3.1 A600,
3.0/3.1 A1200, 3.0/3.1 A4000, CDTV ext, CD32 + ext; "AROS" integrado como fallback (aros.rom.c, 3.4 MB; `retro_config_kickstart` core.c:5608-5766 hace la busqueda por nombres).
PUAE6: HECHO solo KS1.3 (A500) y 2.05 (A600) por nombres (firmware.h) y T24e añadira 3.1 A1200 por nombres. FALTA: selector, KS1.2/2.04/3.0/3.1-A500/A2000/A4000,
CD32/CDTV, `rom.key` (cifradas), AROS (retirado a proposito en T06b2ba) -> P03.

### 1b. CPU, FPU, MMU, JIT

| Capacidad (PUAE5) | Cita | PUAE6 |
|---|---|---|
| `puae_cpu_model` auto/68000/68010/68020/68030/68040/68060 (+ fuerza `mmu_model` y 32-bit si >68020) | core.c:953, 3718-3745 | 68000: HECHO (T04..T08). 68020: PLANIFICADO T24a (CE, in_progress). 68010: probable con `cpuemu_13/14` (T24a genera 13/14/21): FALTA expo -> P04. 030/040/060: FALTA, T44 FUTURE -> P06 |
| `puae_fpu_model` auto/0/68881/68882/cpu | core.c:972, 3748-3772 | FALTA; T44 FUTURE -> P05 (solo softfloat; fpp_native usa long double host: prohibido en deterministic por DET04) |
| MMU (preset A4030/A4040) | core.c:6240-6265 | FALTA; T44 FUTURE -> P07 (opcional, ultimo) |
| `puae_cpu_compatibility` normal/compatible/memory/exact | core.c:1041, 3774-3836 | PARCIAL: solo `exact` (CE). Los demas niveles requieren tablas no-CE (`cpuemu_0/11/20/22`); NO es hueco funcional sino de rendimiento. Decision: ver C5 |
| `puae_cpu_throttle` (-90%..+1000%) | core.c:989, 3840 | NO APLICA: PUAE5 lo ignora con CE (descripcion l.995) y PUAE6 es CE; `options.json` "unsupported cpu_speed" sigue valido |
| `puae_cpu_multiplier` (reloj CE 3.5..56 MHz) | core.c:1021, 3852 | FALTA -> P04. Es determinista como opcion de reinicio (`cpu_clock_multiplier` ya esta en el perfil: config/profiles/a600-ecs.json "cpu_clock_multiplier=512") |
| Velocidad de CPU / turbo | idem | idem |
| JIT | `retrodep/sysconfig.h:12` desactivado | **No hay hueco**. T42 sigue FUTURE |

### 1c. Memoria

| Opcion | Valores | Cita | PUAE6 |
|---|---|---|---|
| `puae_chipmem_size` | auto, 0.5/1/1.5/2M | core.c:876, 3656 | FALTA (fijo por maquina, options.json "unsupported chipmem_size") -> P02 |
| `puae_bogomem_size` | auto, 0, 0.5/1/1.5/1.8M | 893, 3671 | FALTA -> P02 |
| `puae_fastmem_size` (Z2) | auto, 0,1,2,4,8M | 911, 3686 | FALTA -> P02 (A1200/A600/A4000) |
| `puae_z3mem_size` | auto, 0..512M | 929, 3701 (fuerza `cpu_24bit_addressing=false`) | FALTA -> P06 (solo A4000/32 bit; tope por perfil, ver riesgo R1) |

### 1d. Medios

| Capacidad | Cita PUAE5 | PUAE6 |
|---|---|---|
| ADF | `valid_extensions` core.c:5390; tipos libretro-dc.c:819-854 | HECHO (T12, T17) solo DD 901120 |
| ADZ, DMS, ZIP (1 disco) | idem | HECHO (T17d) en memoria |
| FDI, `raw` (CT RAW), IPF | idem (FDI2RAW, CAPS dlopen) | RECHAZADO con mensaje (T17d, container.h). FALTA -> P09 (FDI), P10 (IPF) |
| 7z, rp9, ZIP multi-disco -> M3U automatico en `retro_temp_directory`, `zip#miembro` | core.c:6340-6460 | FALTA (PUAE6: ZIP de exactamente un disco, sin temp) -> P11 |
| M3U con `|etiqueta`, `#COMMAND:`, `#SAVEDISK:`, HD y CD en playlist, DC_MAX_SIZE 20 | libretro-dc.h:25-45; dc.c:733-760 | HECHO parcial (T17a): `|etiqueta`, relativas; `#SAVEDISK` se ignora con aviso; max 16 (`P6_MAX_MEDIA`); sin `#COMMAND`; sin HD/CD -> P12/P15 |
| Disk Control (EXT interface), add/replace imagen | core.c:5245-5256 | HECHO (T17a, T17e) |
| Escritura de discos / "save disks" (`#SAVEDISK`, tecla vkbd SVDS, `dc_save_disk_toggle`, gz de save disk en unload) | dc.c:380-560; core.c:8660-8670 | PLANIFICADO T27/T28 (overlay, oblig.); FALTA la UX libretro -> P12 |
| `puae_floppy_write_protection` | core.c:1109, 3996-4026 | PARCIAL: todo RO siempre (`floppy_read_only=true`, bridge/config.cpp:184). Opcion llega con T27/P12 |
| `puae_floppy_write_redirect` (disco sustituto en `saves`) | core.c:1123, 4029-4065 | CONFLICTO C3 -> se resuelve con overlay T27 + exportacion explicita P12 |
| `puae_floppy_multidrive` + tag `(MD)` (DF0-DF3) | core.c:1095, 3988, 7001-7020 | PARCIAL: ABI tiene DF0 y DF1 (T12b); `options.json` "reserved floppy_drives" -> P13 |
| `puae_floppy_speed` 1x/2x/4x/8x/turbo | core.c:1078, 3976 | FALTA; `options.json` "unsupported floppy_speed" lo rechaza por "cambia el timing" -> reclasificar como opcion de reinicio -> P14 (ver C6) |
| HDF/HDZ (RDB autodetectado; geometria 32 sect/1-16 surfaces), controlador `uae0` | core.c:5805-5845; dc.c:837-840 | PLANIFICADO T29 (oblig.). Matiz: usar el backend uaehf/filesys (como PUAE5), Gayle IDE opcional (P08). FALTA libretro HDF/HDZ -> P15 |
| Directorio como disco duro, LHA como HD de solo lectura (`filesystem2=ro/rw`), `.slave`/`.info` (WHDLoad) | core.c:5780-5805; dc.c:843-846 | **CONFLICTO C1**: filesystem del host. Camino: P16 (importador a imagen) |
| Global Boot HD (`puae_use_boot_hd`: Files / HDF 20-512 MB, `make_hdf`) | core.c:1238, 5517-5600 | HDF vacio: P15 (creado en memoria, overlay). "Files": CONFLICTO C1 |
| WHDLoad (`puae_use_whdload` off/files/hdfs, theme, prefs/splash, nowritecache, buttonwait[puae]) | core.c:1179-1237, 6590-6930 | PLANIFICADO parcial T30 (flujo offline) -> P17 para el UX libretro completo |
| CD: CUE/CCD/NRG/MDS/ISO/CHD, CD audio, `puae_cd_speed`, `puae_cd_startup_delayed_insert`, `puae_shared_nvram` | core.c:1137,1151,1165; dc.c:828-834 | FALTA; T43 FUTURE -> P18 (CD32), P19 (CDTV) |
| Cartuchos (`puae_cart_file`: Action Replay 1/2/3, HRTMon, CD32 FMV cart; escaneo de `system/uae_data` por `*rom`) | core.c:1259, 2674-2700, 3364-3385 | FALTA (opcional) -> P30 |
| `puae_autoloadfastforward` FD/HD/CD | core.c:1061, 340-440 | FALTA; politica del adaptador, no toca la maquina (C8) -> en T31/P27 |
| NVRAM CD32/CDTV (`flash_file=*.nvr`) | core.c:5740-5766 | con P18/P19 |

### 1e. Entrada

| Capacidad | Cita | PUAE6 |
|---|---|---|
| RetroPad -> joystick (B=fuego, A=2o, ...) | mapper.c; README "Default controls" | HECHO (T17c): B/A/X + D-pad + stick izq. (input_map.h:140-150) |
| Teclado fisico completo, pass-through, Keyrah keypad | core.c:2121,2135; mapper.c:911-1003 | HECHO (T17c) teclado posicional por poll; FALTA Keyrah (`puae_keyrah_keypad_mappings`) y la opcion pass-through (PUAE6: el modo "Keyboard only" del puerto) |
| Raton fisico (RETRO_DEVICE_MOUSE) y `puae_mouse_speed` 10..300% | core.c:2064, 4147; mapper.c:1899-2148 | HECHO el raton (T17c, 120 cuentas/eje/unidad). FALTA velocidad global -> P22 |
| `puae_physicalmouse` disabled/enabled/double (2 ratones) | core.c:2106; mapper.c:1899-1907,2135-2148 | FALTA el segundo (la ABI solo tiene raton en ports[0]) -> P21 |
| Analog Stick Mouse (izq/dcha/ambos), deadzone, velocidades, D-Pad Mouse speed | core.c:1975-2034 | FALTA, es del adaptador (sin ABI) -> P22 |
| Cambio joystick/raton del D-pad (`puae_joyport`, hotkey RCtrl, tecla "J/M" del vkbd) | core.c:2549; vkbd.c:72 | PARCIAL: tipos "Joystick only/Mouse only/Joystick+Mouse" por puerto (input_map.h:206-215). Falta el toggle en caliente y la hotkey -> P22 |
| `puae_joyport_order` (1234/2143/3412/4321) | core.c:2563; mapper.c:624,829 | FALTA, necesita 4 puertos -> P21/P22 |
| Puertos libretro 0..5: Automatic/RetroPad/CD32 Pad/Analog Joystick/Arcadia/Lightgun(Trojan Phazer)/Lightpen/Joystick/Keyboard/None; puertos 2-3 = adaptador paralelo 4 jugadores (`parport_controllers`) | core.c:2982-3027, mapper.h:12-19 | PARCIAL: Joystick+Mouse/Joystick only/Mouse only/Keyboard/None en 2 puertos. FALTA CD32 pad, analogico, Arcadia, lightgun, lightpen, puertos paralelos -> P21 |
| `puae_retropad_options` / `puae_cd32pad_options` (rotar botones, 2o fuego = arriba) | core.c:2579,2595 | FALTA -> P22 |
| Turbo fire (`puae_turbo_fire`, `_button`, `_pulse`, hotkey) | core.c:2497-2531 | FALTA -> P22 (autofire = transformacion del adaptador sobre el poll; estado en InputMemory) |
| Mapper: cada boton/eje del RetroPad -> tecla/accion (24 entradas), hotkeys VKBD, STATUSBAR, MOUSE_TOGGLE, TURBO, SAVE_DISK, ASPECT, CROP, RESET | core.c:2149-2487; mapper.h:34-60 | FALTA; T31 lo nombra ("mapping") pero sin detalle -> P22 |
| Teclado virtual (vkbd): 10 temas, transparencia, dimming, teclas J/M, SVDS, stickiness, CapsLock, quick map | core.c:1672-1727; vkbd.c (1489 lineas) | PLANIFICADO T31 (oblig.), sin implementar |
| Light pen / gun + crosshair color (`puae_joyport_pointer_color`) | core.c:1776, 4431-4443; core.c:5357-5370 | FALTA -> P21 (+ crosshair en T31) |
| Disk-load fast-forward con Select mantenido (`retro_fastforwarding`) | core.c:340-440 | FALTA -> T31 |

### 1f. Video

| Capacidad | Cita | PUAE6 |
|---|---|---|
| `puae_video_standard` PAL/NTSC + "auto por tags" | core.c:1300, 3387 | HECHO PAL/NTSC (T08); "auto" -> P29 |
| `puae_video_allow_hz_change` disabled/enabled/locked | core.c:1285, 3441 | HECHO y mejor: cadencia desde BEAMCON0 y SET_SYSTEM_AV_INFO solo si cambia >1% (T23f; docs/02-contracts.md:317) |
| `puae_video_aspect` auto/PAL/NTSC (+hotkey) | core.c:1316 | HECHO (T17b `video_aspect`: pixel / 4:3). PAL/NTSC forzado -> P23 |
| `puae_video_resolution` auto/lores/hires/superhires; `puae_video_vresolution` auto/single/double | core.c:1331,1347,3463-3570 | FALTA. Raster canonico fijo Hires+doble linea. El motor ya genera SHRES en ECS (T23c) pero se reduce -> P24 (opciones de reinicio, otra variante de raster canonico, AV01) |
| `puae_crop` (disabled + 7 niveles + auto), `crop_delay`, `crop_mode` (both/h/v/16:9/16:10/4:3/5:4), `vertical_pos`, `horizontal_pos` (auto o ±), `zoom_mode*` (deprecado) | core.c:1362-1570, 4296-4365 | FALTA; presentacion pura del adaptador -> P23 (T31 lo menciona) |
| `puae_immediate_blits` false/immediate/waiting | core.c:1571, 4163 | CONFLICTO C14 (perfil fija 0/0; T24a rechaza `waiting_blits`) |
| `puae_collision_level` none/sprites/playfields/full | core.c:1586, 4203 | PERFIL FIJO full (3). Opcion trivial de reinicio -> P31 |
| `puae_gfx_flickerfixer` (quitar artefactos de interlace) | core.c:1602, 4276 | FALTA (post-proceso de adaptador) -> P23 |
| `puae_gfx_framerate` frameskip 1/2 | core.c:1616 | CONFLICTO C7 (no saltar componentes; ademas PUAE5 dice "no compatible con CE"). Fuera |
| `puae_gfx_gamma` -0.5..+0.5 | core.c:1631 | FALTA -> P23 (presentacion) |
| `puae_gfx_colors` 16/24 bit | core.c:1654 | PARCIAL: solo XRGB8888. 16-bit RGB565 -> P23 (conversion en adaptador, opcional) |

### 1g. Audio

| Capacidad | Cita | PUAE6 |
|---|---|---|
| Stereo separation 0..100% | core.c:1810, 3864 | Fijo 7/10 (bridge/config.cpp:150). FALTA -> P25 |
| Interpolacion none/anti/sinc/rh/crux | core.c:1833, 3880 | Fijo `anti` (perfil `sound_interpol=1`). FALTA -> P25 |
| Filtro emulated/off/on y tipo auto/A500/A1200 | core.c:1850,1865 | A500 fijo (`sound_filter=1`, `type=0`). Filtro A1200: PLANIFICADO T24d. Selector -> P25 |
| Sonido de unidad de disquete: volumen, "mute si vacia", tipo interno/A500/LOUD | core.c:1880-1941, 4067-4140; `DRIVESOUND` | FALTA -> P26. `driveclick.cpp` esta en el snapshot de upstream sin `rand` ni reloj (grep) y es funcion del estado de la unidad |
| Volumen CD audio | core.c:1942, 3934 | con P18 |
| Frecuencia de audio | (fija 44.1/48 kHz en PUAE5) | HECHO (`audio_rate_hz` 44100/48000) |

### 1h. Varios

| Capacidad | Cita | PUAE6 |
|---|---|---|
| RTC | heredado del preset (A500 con trapdoor; A1200 no) | HECHO: A500 MSM6242B y A600 sin RTC; epoca en config (options.json `rtc_epoch_seconds`) |
| Savestates (usa el serializador `.uss` en memoria; quirk INCOMPLETE) | core.c:5266-5270, 8688-8815 | HECHO y superior: T15/T18, rewind/runahead probados (LR02) |
| Rewind / runahead / netplay | quirk INCOMPLETE, sin netplay | HECHO (T18); netplay sigue "sin habilitar hasta su prueba" (T18 paso 4) |
| `RETRO_MEMORY_SYSTEM_RAM` = Chip, memory map CHIP/SLOW/FAST[0], `SET_SUPPORT_ACHIEVEMENTS` | core.c:8631-8644, 8808-8820, 5289 | FALTA en libretro (la API `p6_memory_regions` existe desde T20a; BizHawk si expone dominios) -> P28 |
| Cheats | `retro_cheat_set` vacio (core.c:8822-8829) | **PUAE5 no tiene cheats**; nada que igualar. PUAE6 tambien vacio (libretro_core.cpp:1048) |
| LED interface (Power, Drives, HD/CD/MD, DF0-3, HD, CD/MD) | core.c:262-338; README | FALTA; la luz de unidad existe en la ABI (p6.h:170, T20a) -> T31 / P27 |
| Statusbar (Bottom/Top, Full/Basic/Minimal, startup, mensajes) y OSD | core.c:1728-1775; glue.c:315+ | PLANIFICADO T31 ("indicadores"); sin implementar |
| Core info / mensajes `SET_MESSAGE` | core.c:8502 | HECHO (SET_MESSAGE/EXT en libretro_core.cpp) |
| Perifericos no expuestos como opcion en PUAE5: serie/paralelo, uaeserial, A2091/NCR, GFXBOARD, A2065 | sysconfig.h | Sin hueco: PUAE5 no los configura desde el core (solo el 4 jugadores paralelo y `scsi=true` en CD+BootHD, core.c:7085) |

## 2. Conflictos con AGENTS.md / perfil deterministic y como encajarlos

- C1 Directorios del host, LHA como HD de solo lectura, WHDLoad modo "Files", Boot HD "Files" (`filesystem2=`; core.c:5780-5805, 6742-6800, 5585-5600).
  Choca con: AGENTS.md "No FS del host en el perfil deterministic"; docs/02-contracts.md:360 "HDF: no exponer filesystem del host a TAS"; T29 ("rechazar explicitamente montajes de directorios host en deterministic");
  docs/01-architecture.md:160 (no se toman timestamps de archivos). filesys.cpp ademas usa hilos (`UAE_FILESYS_THREADS`; el fork MacHawk lo parcheo con `sched_yield` en Waterbox: references/machawk-uae filesys.c diff) y guarda arboles de inodos en el estado.
  Encaje: NO montar el directorio vivo. P16 convierte directorio / LHA / ZIP / `.slave|.info` en una IMAGEN de bloques de solo lectura construida en memoria (orden lexicografico, sellos de tiempo fijos), cuyo content ID es el SHA-256 de la imagen; entra al manifiesto como cualquier HDF (T29) y las escrituras van al overlay (T27). El resultado es determinista una vez importado (la importacion pasa antes del `p6_load` y no es parte del estado). El "perfil portable" de docs/01-architecture.md:132 podria alojar el montaje vivo, pero lo desaconsejo: duplica el motor de archivos, rompe rewind/runahead (tema `a_inode`) y no figura como requisito de PUAE5 que no se pueda cubrir con la imagen. Si el usuario lo exige despues: tarea aparte, build/perfil separado, NO anunciado como TAS y sin rewind.
- C2 IPF por `capsimg` dlopen (`caps/uae_dlopen.c`, `HAVE_DLOPEN`; README "IPF support"). Choca con: licencia SPS (references/capsimg/LICENCE.txt: uso/redistribucion NO comercial, cualquier derivado modificado debe incluir fuentes, "cualquier otro formato no debe habilitarse") incompatible con GPLv2 del motor si se enlaza (restriccion extra), sin dlopen en el guest Waterbox (docs/01-architecture.md:87 "plugins dinamicos no se enlazan"; contracts "no carga plugins de forma implicita") y el estado interno de CAPSImg (su emulador FDC) no esta en el snapshot (DET02/STATE01). T17d ya lo rechazo. Encaje: P10 (ADR + lector BSD de MAME o conversor offline). No es asesoria legal: verificar la licencia real de `ipf_dsk` de MAME antes de importar (docs/07-machawk.md:210-235 lo describe como BSD-3-Clause segun la issue frangarcj/BizHawk#1, sin verificar).
- C3 Escritura a ficheros del host (`floppy_write_redirect`, save disks `*.save*.adf`, `.gz` al descargar, WHDSaves, nvram `.nvr`). Choca con "flush explicito y atomico, nunca como efecto oculto" (docs/02-contracts.md:355-358, T27). Encaje: overlay dentro del estado (T27/T28) + exportacion explicita y acotada por el adaptador libretro (VFS al descargar o tecla explicita, nunca durante runahead/rewind) y/o `RETRO_MEMORY_SAVE_RAM` (decision de P12; T28 dice "no mapear todo HDF como SaveRAM automaticamente" -> exportar solo el overlay acotado).
- C4 Fichero `.uae` arbitrario como contenido (core.c:7125-7260) y overrides `puae_libretro_*.uae`. Choca con `options.json` "unsupported uae_config_file" y T31 (allowlist). Encaje: solo claves del esquema + rutas de disquete resueltas por VFS; claves peligrosas (filesystem2, hardfile2 con ruta, cdimage, kickstart_rom_file) se rechazan con error claro.
- C5 Niveles de compatibilidad de CPU no ciclo-exactos (`normal/compatible/memory`) y `puae_cpu_throttle`. No chocan con determinismo (el interprete prefetch tambien lo es) pero SI con la prioridad "correccion antes que rendimiento" y con la arquitectura de T24a (solo CE generado). Decision propuesta: ADR "PUAE6 es solo ciclo-exacto"; reabrir tras T33 si la baseline muestra que 68020/030 CE no alcanza tiempo real en ARM64 Waterbox. Para 030/040 no existe variante CE en el motor (cpuemu_22..25 son prefetch): son un caso aparte de P06.
- C6 Floppy turbo (`floppy_speed`) y `cpu_multiplier`: `options.json` los rechaza por "cambia el timing". No es conflicto de determinismo mientras entren en `config_hash` (opcion de reinicio). Reclasificar: la razon de `unsupported` solo vale para velocidad dictada por el host (`cpu_speed`/throttle).
- C7 Frameskip (`puae_gfx_framerate`): choca con "no saltarse componentes de hardware" (T34 acc) y `bridge/config.cpp:159`. Fuera de alcance; el equivalente legitimo es "render/audio off" por la frontera (DET05, ya probado).
- C8 `autoloadfastforward`: solo cambia el ritmo con que el frontend llama a `retro_run` (SET_FASTFORWARDING_OVERRIDE). No altera la maquina; lee la luz de unidad que ya entrega `p6_run_frame`. Permitido, sin estado en el hash.
- C9 Flush de WHDLoad al descargar (`whdload_quitkey`, core.c:6015+, ejecuta frames extra tras pedir quit con `QuitKey=$4a`): permitido solo como secuencia EXPLICITA en `retro_unload_game` de la instancia principal (inyecta la tecla y corre N unidades deterministas, luego exporta el overlay); jamas en la instancia de runahead.
- C14 `immediate_blits`/`waiting_blits=automatic` (por defecto "waiting" en PUAE5): son atajos de compatibilidad, no hardware. T24a decidio declarar `waiting_blits=0` en el A1200 (upstream lo pone a 1 con 020, cfgfile.cpp:9753). Si se quiere paridad: opcion de reinicio "compat. hack" default OFF y marcada como no exacta.
- Sin conflicto por ausencia en PUAE5: JIT, red/bsdsocket, PPC, RTG/Picasso96, cartas de red.
- Riesgos tecnicos (no conflictos de politica):
  R1 Memoria Waterbox: `manifests/waterbox-memory.json` (mmap 16 MiB, sealed 16 MiB, sbrk 4 MiB; medido 5.3 MiB). 8 MiB Fast + 2 MiB Chip entra, 64+ MiB Z3 o 512 MiB no sin nuevo presupuesto/medicion y el coste de dirty pages del snapshot (docs/03-waterbox.md:127-135). No usar `alloc_invisible` para esquivarlo (AGENTS.md). Tope por perfil y medido (T33/T36).
  R2 Punteros de RAM: docs/02-contracts.md:371 avisa de que `memory_reset` libera y reasigna RAM; libretro (RetroAchievements) necesita punteros estables -> P28 debe reanunciar tras reset o asegurar asignacion estable con parche pequeño.
  R3 CD de 650 MB no cabe en el heap sellado (16 MiB): requiere el backend de "lecturas puras por content ID+offset" que docs/02-contracts.md:360-364 ya prevé; ABI nueva y callback documentado como lectura pura (P18).
  R4 FPU: `fpp_native.cpp` usa `long double` del host; ARM64 no es x87 -> solo softfloat (`fpp_softfloat.cpp`, `softfloat/`), DET04 entre ISA.
  R5 Cambiar la identidad del A600/A1200 despues (p.ej. añadir Gayle) cambia `config_hash` y descarta estados/peliculas: decidirlo ANTES de cerrar T24 (ver P08).

## 3. Tareas nuevas propuestas

Convenciones: dependencias = tareas existentes. "Cubrible por" = si basta ampliar una ficha existente. Rutas = zonas `touch` probables. Tamaño: S (<400 lineas logicas/1-5 archivos), M, L (dividir al ejecutar).

### Maquinas, memoria, CPU

- **P01 Presets sin hardware nuevo: A500OG, A500+, A2000OG, A2000** (obligatoria, M2). Objetivo: `puae6_machine` acepta A500OG (512 KiB Chip, sin Slow), A500PLUS (ECS, 1 MiB, KS2.04, tabla `cs_*` de `built_in_prefs`), A2000OG y A2000, cada uno campo a campo contra `built_in_prefs` (patron de `config/profiles/a600-ecs.json`), con FirmwareID y nombres de Kickstart propios. Deps: T23 (puede ir antes de T24; mejor tras T24b para compartir el patron de enumerar maquinas). Rutas: `bridge/include/p6.h` (P6_MACHINE_* aditivos), `bridge/config.cpp`, `config/profiles/*.json`, `config/schema/options.json`, `adapters/libretro/firmware.h`, `integration/bizhawk/**`, `tests/host|scenarios|state`. Checks: AV01, DET02, STATE01, LR01, WB03, FUZZ01. Cubrible por: no (T23 esta cerrada); copia la receta T23a-e. Vertical inicial: A500OG + A500PLUS; A2000 despues.
- **P02 Memoria configurable (Chip, Slow, Fast Z2)** (obligatoria, M2). Objetivo: Chip {0.5,1,1.5,2}M, Slow {0,0.5,1,1.5,1.8}M, Z2 Fast {0,1,2,4,8}M como opciones de reinicio (`p6_config.chip_kib/slow_kib/fast_kib`, `config_hash`, `libretro puae6_chip_size|slow|fast`, sync settings de BizHawk), validadas por maquina (OCS no acepta >1M? comprobar contra `fixup_prefs`), enumeracion AutoConfig de la placa Fast (expansion ya compila: T06b2bq), `p6_memory_regions` informa Fast, cota de estado por configuracion, presupuesto Waterbox medido (R1). Deps: T24b (A1200), T23. Rutas: `bridge/config.cpp|state*.cpp`, `bridge/include/p6.h`, `config/schema/**`, `adapters/libretro/**`, `integration/bizhawk/**`, `manifests/waterbox-memory.json`, `tests/**`. Checks: STATE01, DET02, WB03, AV01, FUZZ01, LR01. Incluye explicitamente "Fast RAM A1200/A600/A4000" (el A4000 queda con P06). Cubrible por: no.
- **P03 Selector y tabla de Kickstart** (obligatoria, M2). Objetivo: opcion `puae6_kickstart` (auto, 1.2, 1.3, 2.04, 2.05, 3.0, 3.1 por familia A500/A600/A1200/A4000/CD32/CDTV) con la tabla completa de nombres de PUAE5 (`uae_kickstarts[]` en core.h: normal, Amiga Forever, TOSEC-mod, TOSEC), matriz maquina x Kickstart aceptada y MEDIDA (hoy "NO probado" en docs/02-contracts.md:309), extension CDTV/CD32 (.ext) cuando existan, y soporte de `rom.key` (Amiga Forever cifrada): leer `rom.key` del directorio de sistema y pasarla a `p6_load` como bytes identificados (nueva entrada ABI aditiva; los bytes de clave no entran en el estado ni se registran). Deps: T24e. Rutas: `adapters/libretro/firmware.h|libretro_core.cpp`, `bridge/include/p6.h`, `bridge/rom*.cpp`, `tests/libretro-host`, `tests/fuzz`. Checks: LR01, FUZZ01, MED01. Opcional P03b: AROS embebido como firmware con content ID (aros.rom.c 3.4 MB; T06b2ba lo retiro a proposito; revisar licencia APL y presupuesto sealed). Cubrible por: T24e parcialmente (nombres 3.1 A1200); el resto no.
- **P04 CPU elegible: 68010 y reloj CE (cpu_multiplier)** (obligatoria, M2). Objetivo: `puae6_cpu_model` {68000, 68010, 68020}, `puae6_cpu_multiplier` {1,2,4,8,10,12,16}x como opciones de reinicio ya dentro de `config_hash`; 68010 con tablas `cpuemu_13/14` ya generadas; el multiplicador sobre 68000 y 68EC020 CE comprobado con AV02 (audio/ticks) y STATE01. Deps: T24a, T24b. Rutas: `bridge/config.cpp`, `config/schema/**`, `adapters/libretro/**`, `integration/bizhawk/**`, `tests/host|step`. Checks: DET02, AV01, AV02, STATE01. Cubrible por: ampliar T24b/T24e seria posible, pero conviene separado (cambia identidad de estado).
- **P05 FPU 68881/68882/interna con softfloat** (obligatoria para paridad, M2/M3; hoy T44 FUTURE). Objetivo: `fpu_model` {0, 68881, 68882, cpu} en 68020/030/040; solo `fpp.cpp` + `fpp_softfloat.cpp` + `softfloat/*` (nunca `fpp_native` ni long double host); estado de FPU en el chunk de CPU; corpus de instrucciones FPU con resultados deducidos (redondeo, NaN, extendido) y misma salida en x86_64 y ARM64. Deps: T24c (estado 020), T25 (matriz ISA). Rutas: `manifests/*`, `patches/winuae/**`, `bridge/state_cpu.cpp`, `tests/fpu/**`. Checks: GEN01, DET02, DET04, STATE01, SAFE01. Cubrible por: T44 (promover de FUTURE a obligatoria y dividirla en P05/P06/P07).
- **P06 68030/68040(/68060) interpretados + A4030/A4040 + Zorro III** (obligatoria para paridad, L; hoy T44 FUTURE). Objetivo: cpuemu_22/23/24/25 generados y seleccionados, caches de datos/instrucciones 030/040 en el estado, 32-bit addressing, Z3 RAM (tope por perfil medido), A4000 (Ramsey/Fat Gary/RTC A4000 segun `cs_*`), KS3.0/3.1 A4000, presets `A4030`/`A4040`. Deps: P05, P02, T24 (cierre), T29 (IDE A4000 si aplica), T25. Vertical inicial: 68030 sin MMU + FPU 68882 + 8M Fast (A4030 "lite"), luego 68040. Checks: GEN01, AV01, DET02, STATE01, WB03, DET04. Cubrible por: T44.
- **P07 MMU 030/040** (opcional/ultima, M3+). `cpummu30.cpp`, `cpummu.cpp`, CPUEMU_31-35, ATC/TLB en el estado, corpus de fallos de pagina. PUAE5 activa `mmu_model` en A4030/A4040 (core.c:6244,6257) pero ningun contenido tipico lo necesita: decidir con el usuario si "paridad estricta" lo incluye. Deps: P06. Checks: DET02, STATE01, DET04, FUZZ01. Cubrible por: T44.
- **P08 Gayle + IDE (A600/A1200) con HDF** (recomendada; amplía T29). Objetivo: `gayle.cpp`, `ide.cpp`/`idecontrollers.cpp` (sin PCMCIA), `cs_ide`/`cs_compatible` reales en A600/A1200 (como PUAE5), estado nuevo `GAYL/IDE` en el inventario. Nota: PUAE5 NO lo necesita para HDF (usa `uae0`); es para fidelidad de hardware (registros Gayle, CIA overlay real) y para instalar AmigaOS en un IDE. Cambia la identidad de las maquinas A600 (T23) y A1200 (T24): decidir ANTES de cerrar T24b. Deps: T24b, T29. Rutas: `bridge/state_devices.cpp`, `manifests/state-inventory.json`, `patches/winuae/**`, `config/profiles/*`. Checks: DET02, STATE01, MED03, FUZZ01. Cubrible por: ampliar T29 (paso "controlador de estado alrededor de save/load") con IDE, o tarea hija T29b.

### Medios

- **P09 Disquete extendido (UAE-1ADF/pistas MFM) y FDI** (obligatoria, M3). Objetivo: la ABI acepta ademas del ADF de 901120 bytes un medio de pistas MFM (ADF extendido `UAE-1ADF`/`UAE--ADF` de WinUAE, longitud variable por pista), identificado por SHA-256; `fdi2raw.cpp` (FDI1/2) compilado y usado SOLO para convertir a ese formato en memoria dentro del adaptador (patron T17d); cota de memoria (≈2 MiB/disco ⇒ revisar `sealed` 16 MiB y `P6_MAX_MEDIA`); estado de pista MFM ya cubierto por T15d/T15k. Deps: T12, T17d. Rutas: `bridge/include/p6.h`, `bridge/media.cpp`, `adapters/libretro/container.h`, `patches/winuae/**`, `docs/adr/0008`, `tests/media*`. Checks: MED01, FUZZ01, DET02, STATE01. Cubrible por: no (ADR 0008 lo excluye explicitamente; hay que enmendarlo).
- **P10 IPF (y CT RAW/KF stream): decision de licencia y backend** (obligatoria si "IPF" entra en paridad, L, riesgo alto). Fase a: ADR de licencias (capsimg SPS = no comercial/incompatible con GPLv2; MAME `ipf_dsk.cpp` descrito BSD-3 en docs/07-machawk.md:210, a verificar con commit fijado). Fase b (preferida): lector portable IPF->pistas MFM con temporizacion (peso: gaps, bits debiles, revision de encoder) que produce el medio de P09; vive en el guest y el estado causal va al snapshot. Fase c (alternativa de coste minimo): conversor OFFLINE (herramienta del usuario) IPF->ADF extendido; el core nunca ve IPF. Nunca dlopen. Deps: P09, T27 (los IPF son RO). Rutas: `manifests/licenses.json`, `ports/portable/**`, `docs/adr/**`, `tests/ipf/**`. Checks: MED01, FUZZ01, SRC01, DET02, WB03. Cubrible por: no (docs/07-machawk.md:227 ya dice "IPF necesita una tarea propia").
- **P11 Contenedores de contenido en libretro sin temp** (obligatoria, S/M). Objetivo: ZIP con varios discos -> playlist en memoria (orden alfabetico como `qsort` de PUAE5, core.c:6420), `fichero.zip#miembro.adf`, `.rp9` (zip), `.7z` (LZMA SDK de dominio publico, como `deps/7zip`), `.zip` de contenido HD/CD cuando existan P15/P16; todo con cotas y sin directorio temporal. Deps: T17d. Rutas: `adapters/libretro/container.h`, `content.h`, `tests/libretro-host`, `tests/fuzz`. Checks: LR01, MED01, FUZZ01. Cubrible por: ampliar T17d es imposible (cerrada); tarea nueva pequeña.
- **P12 Discos de guardado y persistencia en libretro** (obligatoria, M3). Objetivo: `#SAVEDISK:<etiqueta>` en M3U, tecla "SVDS"/hotkey "Toggle Save Disk", creacion de un ADF en blanco (formato OFS/FFS con etiqueta, funcion pura de la etiqueta) como base inmutable + overlay (T27), opciones `puae6_floppy_write_protection` y "write redirect" = overlay con exportacion explicita (VFS en unload/tecla, o `SAVE_RAM` acotado; nunca en runahead), re-importacion al cargar. Equivalente de `floppy_open_redirect`, `.gz` de save disk. Deps: T27, T28, T17a. Rutas: `adapters/libretro/**`, `bridge/media.cpp` (generador de disco en blanco), `tests/media-cow`, `tests/libretro-host`. Checks: MED02, LR02, DET02, STATE01. Cubrible por: ampliar T27 (paso nuevo "UX libretro de escritura") o tarea hija T27c.
- **P13 Cuatro unidades (DF0-DF3), MultiDrive y tag (MD)** (obligatoria, S/M). Objetivo: la ABI/estado/pelicula soportan 4 unidades (hoy DF0/DF1); opcion de reinicio `puae6_floppy_multidrive` y tag `(MD)`; wrapper BizHawk `FloppyDrives`. Deps: T12b, P29 para el tag. Rutas: `bridge/media.cpp|state*`, `config/schema` (sale de `reserved: floppy_drives`), `adapters/**`, `integration/bizhawk/**`. Checks: MED01, IN01, DET02, STATE01, WB03. Cubrible por: no.
- **P14 Velocidad de disquete** (obligatoria, S). `puae6_floppy_speed` {100, 200, 400, 800, turbo} como opcion de reinicio (config_hash); retira `floppy_speed` de `unsupported`; DET02 con carga de disco a cada velocidad; ojo con el estado de pista bufferizada en turbo (T15k). Deps: T15k. Checks: DET02, AV02, MED01, STATE01. Cubrible por: no.
- **P15 HDF/HDZ, RDB y Boot HD en libretro** (obligatoria, M3). Objetivo: `.hdf/.hdz` (HDZ=gzip, descompresion acotada en memoria), deteccion de RDB (`"RDSK"`, core.c:5815) y geometria por defecto (32 sect/1-16 surf., core.c:5821-5835), M3U de HDs (todos montados a la vez), opcion `puae6_boot_hd` = HDF vacio de 20-512 MB creado en memoria (equivalente de `make_hdf`; el contenido lo da el overlay), tipo de controlador `uae0` (uaehf + placa AutoConfig de arranque) con KS>=2.x; Disk Control no lista HDs. Cuenta de memoria: 512 MB no cabe en el heap sellado -> backend de lecturas puras por offset (R3) o limite documentado por perfil. Deps: T29, T27, T28, P03. Rutas: `adapters/libretro/**`, `ports/portable/block/**`, `tests/hdf`. Checks: MED03, MED02, WB10, LR01, FUZZ01. Cubrible por: parte de T29 (nucleo del dispositivo) ya existe; esta es la capa libretro y el modelo del HDF vacio.
- **P16 Importador determinista directorio/LHA/ZIP -> imagen de bloques** (obligatoria para "WHDLoad completo", L, riesgo alto; resuelve C1). Objetivo: dado un directorio del host (solo en el momento de cargar), un `.lha` (WHDLoad Retroplay, `archivers/lha` ya en WinUAE) o un ZIP, construir en memoria una imagen FFS de solo lectura (orden y sellos de tiempo fijos) con content ID; `.slave`/`.info` apunta al directorio padre (core.c:5787-5803). Deps: T29, T30 (comparte reglas de imagen), P15. Rutas: `ports/portable/block/**` o `tools/` + `adapters/libretro/**`, `tests/hdf`, `manifests/licenses.json`. Checks: MED03, FUZZ01, DET06 (rutas/hora distintas dan la misma imagen), SRC01. Cubrible por: T30 cubre la version offline (`tools/prepare_whdload.py`); P16 añade la version en el core. Vertical inicial: LHA -> imagen (sin escritura).
- **P17 WHDLoad en libretro con la UX de PUAE5** (obligatoria para paridad, L). Objetivo: opciones `puae6_use_whdload` {off, hdf}, `theme` (default/native: dos variantes de `system-configuration` dentro de la imagen auxiliar), `prefs` (config/splash/both: ConfigDelay/SplashDelay en WHDLoad.prefs, core.c:6590-6660; parche en memoria de la imagen => su content ID cambia con la opcion), `nowritecache` + `QuitKey=$4a` + flush al descargar (C9), `buttonwait` (solo en references/puae), WHDSaves como overlay HDF (core.c:6905-6925), copia de Kickstarts y de `WHDLoad.key/rom.key/WHDLoad.prefs` del usuario a la imagen (nunca se redistribuyen), `custom`/`load`/`MkCustom`, instalaciones Retroplay y Arcadia (esta con P21/P20-arcadia). Activos auxiliares (`WHDLoad.hdf`, `WHDSaves.hdf`, `WHDLoad_files.zip` en references/machawk-uae/whdload y retrodep/*.gz.c): manifiesto de licencias antes de embeberlos. Deps: T30, P15, P16, P12, P03, P29. Rutas: `config/whdload/**`, `adapters/libretro/**`, `tools/prepare_whdload.py`, `docs/whdload.md`, `manifests/licenses.json`, `tests/whdload`. Checks: MED03, DET02, WB03, SRC01, LR02. Cubrible por: ampliar T30 (hoy solo "flujo reproducible") o dividir en T30b libretro.
- **P18 CD32 + backend de CD** (obligatoria para paridad, L; hoy T43 FUTURE). Objetivo (T43 ya pide dividir): CD32 = 68EC020 AGA, Akiko (C2P + CD + NVRAM), ROM combinada o KS+ext, NVRAM `.nvr` como estado exportable, pad CD32 (P21), audio CD (cda_play, mp2) mezclado y `puae6_cd_volume`, `puae6_cd_speed`, `puae6_cd_startup_delayed_insert`, `puae6_shared_nvram`; backend de imagenes: ISO, CUE/BIN, CCD, NRG, MDS, CHD (libchdr BSD-3 + LZMA + FLAC dr_libs: auditar licencias) con TOC e identidad (SHA-256 de TOC + pistas, o hash interno de CHD verificado), lecturas puras por content ID+offset sin cursor mutable fuera del estado (R3). Deps: T24 (cierre), T27, T29, P21 (CD32 pad), T43->requerida. Subtareas sugeridas (al ejecutar): P18a backend/TOC/identidad, P18b Akiko+CD32 preset (sin CD audio), P18c CD audio, P18d NVRAM/estado, P18e escenarios y libretro/BizHawk. Checks: MED03, AV01, AV02, DET02, STATE01, LR01, WB03, FUZZ01. Cubrible por: T43 (promover a obligatoria).
- **P19 CDTV** (obligatoria para paridad, L). `cdtv.cpp`/`cdtvcr.cpp`, ROM ext CDTV, DMAC/SCSI del CDTV, NVRAM, mando/IR (tag `CDTV`), `scsi=true` para BootHD+CD (core.c:7085). Deps: P18. Checks igual que P18. Cubrible por: T43.
- **P20 CD32 FMV (MPEG-2) y cartuchos CD32** (opcional). `cd32_fmv.cpp` + libmpeg2 (GPL, ya en deps de PUAE5: `HAVE_MPEG2`), `cd32fmv=true` si el cartucho contiene "CD32" (core.c:3382). Deps: P18, P30. Cubrible por: T43.
- **P30 Cartuchos (Action Replay 1/2/3, HRTMon) y tecla Freeze** (opcional). `ar.cpp`, ROM del usuario identificada por contenido, entrada "freeze" como evento de entrada normal (EMU_FREEZE en core.h), opcion `puae6_cartridge`. Deps: T24. Checks: IN01, DET02, STATE01.

### Entrada

- **P21 ABI de puertos ampliada** (obligatoria para paridad, L; dividir por dispositivo). Dispositivos: CD32 pad (7 botones + protocolo de registro de desplazamiento por POT/CIA), joystick analogico (POT), Arcadia (`arcadia.cpp`), lightpen y lightgun Trojan Phazer (posicion a coordenada de haz desde el raster; mapeo con la geometria de P23/P24), segundo raton en el puerto 2 (`physicalmouse=double`), puertos paralelos 3-4 (adaptador de 4 jugadores; `puae6_joyport_order`). Aditivo en `p6.h` (`P6_PORT_*`, `P6_ABI` version minor), `bridge/input.cpp`, chunk `P6IN`. Deps: T10, T17c, T24c (ABI estable) ; el CD32 pad es util ya en A500/A1200. Checks: IN01, DET02, STATE01, FUZZ01, LR01, WB03. Vertical inicial: CD32 pad; luego lightpen/gun; luego puertos paralelos. Cubrible por: no.
- **P22 Entrada del adaptador libretro/BizHawk sin ABI** (obligatoria para paridad con PUAE5, M; mejor como T31c). Objetivo: Analog Stick Mouse (izq/dcha/ambos, deadzone, velocidades), D-Pad Mouse, `puae6_mouse_speed`, opciones de botones (rotate/jump para RetroPad y CD32), toggle joystick/raton (opcion, tecla RCtrl, tecla J/M del vkbd), turbo fire (boton/pulso/hotkey; contador en `InputMemory`), Keyrah, mapper de botones del RetroPad a tecla/accion (24 entradas, core.c:2149-2487), hotkeys {vkbd, statusbar, mouse toggle, turbo, save disk, aspect, crop, reset}. Todo es funcion de lo que el frontend informa en cada `retro_run` y de `InputMemory` dentro del estado del adaptador (input_map.h:13-17), de modo que rewind/runahead lo reproduzcan. Deps: T17c, T18, P21 (CD32 pad), T31. Checks: IN01, LR01, LR02, FUZZ01, DET05. Cubrible por: T31 (dividirla; hoy mezcla UX, importador .uae y todo mapping).

### Video, audio, UX

- **P23 Presentacion de video: crop, posicion, aspecto, gamma, interlace, RGB565** (obligatoria para paridad, M; ⊂ T31). Objetivo: `crop` {disabled, 7 niveles, auto}, `crop_delay`, `crop_mode`, `vertical_pos`/`horizontal_pos`, `video_aspect` PAL/NTSC/auto/4:3, `gamma`, `remove_interlace_artifacts`, `color_depth` 16/24 como POST-PROCESO sobre el raster canonico (hash AV01 no cambia; DET05). Geometria con SET_GEOMETRY ya existente. Deps: T08, T23f. Rutas: `adapters/libretro/ui/**` (T31). Checks: AV01, LR01, DET05. Cubrible por: T31 (ampliar pasos con la lista exacta de opciones).
- **P24 Resolucion y modo de linea como opciones de reinicio** (obligatoria para paridad, M). Objetivo: `puae6_video_resolution` {auto, lores 360, hires 720, superhires 1440} y `puae6_video_vresolution` {auto, single, double} cambian `gfx_resolution/vresolution` y por tanto el raster canonico (otra variante documentada en ADR 0006: ancho 378/756/1512, alto 287/574); "auto" decide por `lof`/BPLCON0 al arrancar o en una politica determinista (nunca por tiempo de pared). AV01 con nuevas dimensiones y cota `P6_FRAME_NO_VIDEO` (hoy 760x576, docs/02-contracts.md:318) a revisar. Deps: T23g, T24d (AGA SHRES). Checks: AV01, STATE01, DET02, LR01, WB03. Cubrible por: no (cambia ADR 0006).
- **P25 Opciones de audio de Paula como opciones de reinicio** (obligatoria, S/M). `stereo_separation` {0..100%}, `interpolation` {none, anti, sinc, rh, crux}, `filter` {emulated, off, on}, `filter_type` {auto, A500, A1200} (auto = por maquina: A500/A600 -> A500, A1200 -> A1200 tras T24d), y volumen de salida como presentacion. Entran en `config_hash` porque cambian el PCM (AV02). Deps: T24d, T11. Checks: AV02, DET02, STATE01, LR01. Cubrible por: ampliar T24d/T24e solo el filtro A1200; las demas, tarea nueva.
- **P26 Sonido de unidad de disquete** (obligatoria para paridad, M). Objetivo: `driveclick.cpp` (+ muestras internas: `drive_click.wav` de WinUAE y las de retrodep/driveclick_wav.c; "A500"/"LOUD" externas desde `system/uae_data` se leen por VFS al cargar y se identifican por hash) mezclado con Paula; `puae6_floppy_sound` (0..100%), `mute_ejected`, `type`; el estado de reproduccion de clicks entra en el estado nativo/Waterbox (no hay `rand`; funcion de pasos y motor de la unidad). Interaccion con `autoloadfastforward` (que silencia el click). Deps: T15d/T15k, P25. Checks: AV02, DET02, STATE01, DET05. Cubrible por: no.
- **P27 OSD: statusbar, LEDs, vkbd y mensajes** = **T31** (ya oblig.). No tarea nueva; ampliar pasos: statusbar {bottom/top × full/basic/minimal, startup, messages}, tema de vkbd (10), transparencia, dimming, color de crosshair, teclas J/M y SVDS del vkbd (dependen de P22/P12), interfaz `RETRO_ENVIRONMENT_GET_LED_INTERFACE` en el orden de PUAE5 (Power, Drives, HD/CD/MD, DF0..DF3, HD, CD/MD) alimentado por la luz de unidad de `p6_run_frame` (p6.h:170) y el estado de HD/CD cuando existan; autoload fast-forward. Dividir T31 en T31a (opciones y esquema), T31b (vkbd/OSD), T31c (=P22), T31d (importador `.uae`).
- **P28 Memory maps y RetroAchievements** (obligatoria para paridad, S/M). Objetivo: `SET_MEMORY_MAPS` con CHIP, SLOW, FAST, `RETRO_MEMORY_SYSTEM_RAM` y `SET_SUPPORT_ACHIEVEMENTS` a partir de `p6_memory_regions` (T20a), re-anunciado tras cada reset/carga o con asignacion estable (R2); endianness big-endian y direcciones del bus; sin escritura desde el host durante runahead. No verifique aqui si existe un set de logros Amiga en RetroAchievements: la paridad es exponer el mapa (PUAE5 lo hace en core.c:8631-8644). Deps: T20a, T18, P02. Checks: LR01, LR02, DET05. Cubrible por: no.
- **P29 Modelo automatico, tags de nombre y config por modelo/contenido** (obligatoria para paridad, M). Objetivo: `puae6_model` {auto + los de P01/T24/P06/P18}, `puae6_model_fd/hd/cd`, tags de ruta de la seccion 1a (misma precedencia), region por tag, `(CE)` (en PUAE6 CE ya es siempre: se acepta y se ignora con aviso), `(MD)`, `_Arcadia`; ficheros `puae6_<modelo>.uae`, `puae6_global.uae`, `<contenido>.uae` via el importador allowlist de T31d. La ruta solo ELIGE el `p6_config` al cargar; no es identidad ni entra al estado (el `config_hash` manda), y BizHawk no la usa (elige en el sync setting). Deps: P01, T24e, P15, P18, T31. Checks: LR01, FUZZ01 (rutas hostiles), DET06 (rutas distintas, misma maquina). Cubrible por: T31d parcialmente.
- **P31 Opciones de compatibilidad del chipset** (S, opcional). `collision_level` {none, sprites, playfields, full} como opcion de reinicio (hoy fijo 3), y -con aviso "no exacto"- `waiting_blits` (C14) default OFF. Checks: AV01, DET02, STATE01.

### Ampliaciones a tareas existentes (sin tarea nueva)

- T24b/T24e/T24f: añadir en la tabla de capacidades que el A1200 de PUAE6 = A1200OG de PUAE5 menos Gayle (P08) y sin Fast (P02).
- T29: incluir `uae0`/uaehf como controlador principal, HDZ y detalle de geometria; Gayle IDE queda como P08; sin directorios del host (C1).
- T30: tras el flujo offline, referencia a P16/P17 para el uso desde libretro.
- T31: dividir (T31a-d) y absorber P22, P23, P27, y parte de P29.
- T43: promover de FUTURE a obligatoria (M3/M4) y reemplazar por P18-P20; T44: idem por P05-P07.
- T42 (JIT), T45 (RTG), T46, T47: siguen FUTURE; PUAE5 no los tiene (JIT/RTG desactivados en sysconfig.h).
- `config/schema/options.json` "unsupported": retirar cpu_model, chipmem_size, bogomem_size, fastmem_size, floppy_speed cuando entren sus tareas, y reescribir el motivo de `cpu_speed`.

## 4. Orden sugerido

1. Ya en curso: T24a..f (A1200 68EC020 AGA). Decidir en este punto si P08 (Gayle) se hace antes de T24b para no cambiar la identidad dos veces (R5).
2. Rapidas e independientes (pueden ir en paralelo con T24): P11, P14, P01 (tras T24b), P25 (filtro), P28, P13.
3. Memoria y CPU: P02 -> P04 -> P05 -> P06 (-> P07).
4. Escritura y HD (T27, T28, T29) -> P12, P15, P08 -> P16 -> P17 (WHDLoad completo).
5. Entrada y UX: P21 (CD32 pad primero), P22/T31, P23, P24, P26, P27.
6. CD: P18 -> P19 -> P20/P30 (requiere T27/T29 por la persistencia de NVRAM).
7. Formatos: P09 -> P10 (IPF) en paralelo con 4-5.
8. P29 (modelo auto y tags) al final de cada tanda, cuando existan los modelos que nombra.
Puertas de cierre: T25, T32 (corpus) y T41 deberian incluir una "matriz de paridad PUAE5" generada de este inventario; una linea FALTA sin tarea bloquea la etiqueta de paridad.

## 5. Preguntas abiertas para el usuario

1. IPF: ¿basta un conversor offline (menor riesgo) o se quiere lector integrado (P10b)? ¿Se acepta que la fidelidad de bits debiles/temporizacion dependa del lector?
2. Directorios del host: ¿se acepta la solucion "importar a imagen" (P16) como unica via en el core determinista, o se quiere ademas una build "portable" no determinista?
3. ¿Se acepta que PUAE6 sea siempre ciclo-exacto (C5) y que los niveles `normal/compatible/memory` queden fuera?
4. ¿MMU 030/040 (P07) entra en "al menos todo" o se difiere a pedido?
5. Tope de RAM Z3 y Fast por perfil (R1): ¿8 MiB Z2 + hasta 64 MiB Z3 hasta medir?
6. Gayle (P08): ¿se hace antes de cerrar T24b?
