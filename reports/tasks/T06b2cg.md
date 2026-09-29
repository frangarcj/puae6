# T06b2cg — simbolos excluidos que quedaban en 10 unidades

Reduccion guiada por la puerta de imports, repartida en cuatro agentes en paralelo
(un grupo de archivos cada uno, sobre copias en el scratchpad, sin tocar el repo)
e integrada aqui: parches 0050 (newcpu, newcpu_common), 0051 (memory, custom),
0052 (inputdevice, disk, savestate, rommgr) y 0053 (main, expansion, cfgfile). Se
revisaron los diffs y cada informe; se verifico a mano `savestate_capture` (solo
actua con `input_record`, no es el rewind de BizHawk) y la reestructuracion de
`m68k_reset2`. La puerta pasa de 12078 a 12018 simbolos y las familias con dueño
T06b2cb (input_recording, cpuboard, rtg_hostfs_traps, non_a500_banks,
excluded_models_code, config_defaults_excluded_devices) desaparecen del manifiesto.

## Sustituciones que no son solo guardas

- `get_mem_bank_real(x)` -> `&get_mem_bank(x)`: solo difiere con el memwatch del
  depurador, que no existe (newcpu, memory, custom).
- `need_uae_boot_rom`: sin area de traps (`rtarea_base == 0`) devolvia 0 y tipo 0;
  ahora lo hace directamente.
- `expamem_z3hack` sin la comprobacion de memoria de aceleradora.
- `m68k_reset2`: `} else {` pasa a `} else` / `#endif` / `{`, cuerpo intacto.

## Efectos a vigilar

- `savestate_capture` queda excluida junto con sus llamadores; `AKS_STATECAPTURE`
  es un no-op y el bridge no debe exponerlo.
- Una imagen de cartucho con descrambling (Nordic Power, X-Power) falla ahora su CRC
  en lugar de descifrarse; la config publica debe rechazar `ROMTYPE_ALL_CART`.
- Sin `target_default_options`, algunos defaults de host cambian
  (`win32_samplersoundcard` -1 -> 0, `gfx_api` 2 -> 0); el nucleo no los lee.
- `cs_ide`: upstream lo pone a -1 en configs genericas y entonces mapea Gayle. Sin
  el mapeo las lecturas caen en `dummy_bank`; hay que normalizarlo a 0.

## Presupuesto

53 parches, 28 archivos, 1647 lineas (+280): supera la alarma de 1500. ADR 0004 con
coste y alternativas. Ver tambien `manifests/config-invariants.json`.

## Pendiente fuera de esta tarea (agentes lo señalaron)

`consolehook_shutdown` (trivial, desktop_actions), `uaeboard_bank` desde
`mousehack_wakeup` (su unico llamador no esta seleccionado) y las llamadas
`protect_roms(true)` que los parches 0032/0035 añadieron en ramas P6 de memory.cpp:
el agente de memory las neutralizo; la puerta ya no las ve.

## Validacion

Build completo (0 errores), puerta de imports, suite de host, codegen y plan; ver el
commit. La puerta seguia sin clasificar 0 simbolos nuevos antes de podarla.

## Tests que tuvieron que cambiar (contrato, no comportamiento del A500)

Siete modulos de host fallaron al integrar y se actualizaron porque asertaban justo
lo que los parches eliminan: `test_cpu_common` (los helpers cache040 ya no se
importan), `test_cpu_rom_guard` (la limpieza tras un fallo de ROM ya no llama a
`protect_roms` ni `mman_set_barriers`; el probe necesita `mem_banks`),
`test_memory_rom_status` y `test_rom_load_chain` (cero llamadas de proteccion y sin
ciclo de vida de cpuboard), `test_disk_source` (16 regiones), y
`test_cfgfile_defaults`/`test_cfgfile_preset` (la variante parcheada ya no llama a
`blkdev_default_prefs` ni `target_default_options`; la de referencia si). En todos
los casos se retiran los stubs de simbolos que el parche ya no referencia, de modo
que un retroceso rompe el enlace.

Resultado: host 55 modulos/88 tests en 136 s, codegen 18, plan y contratos 74; la
puerta de imports en 12018 simbolos.
