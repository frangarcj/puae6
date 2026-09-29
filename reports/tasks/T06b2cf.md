# T06b2cf — CIA A500 sin CD32, SCSI paralelo, grabacion de entrada ni ficheros de reloj

Primera reduccion guiada por la puerta de imports (T06b2ce). El parche 0049 guarda
en cia.cpp cinco sitios; `cia.o` dejaba de importar antes de este cambio, y
comprobado con `nm -u`, nueve simbolos: `akiko_mute`, `parallel_port_scsi`,
`parallel_port_scsi_read`, `cfgfile_resolve_path_out_load`,
`inprec_recorddebug_cia`, `inprec_playdebug_cia`, `input_play`, `input_record` e
`inputrecord_debug`. La puerta baja de 12084 a 12078 simbolos sin resolver (los
tres del CIA que eran exclusivos desaparecen del todo; `input_play/record` siguen
referenciados desde newcpu e inputdevice).

## Decisiones

- Las ramas SCSI de puerto paralelo se sustituyen por el bloque que ya existia en
  la rama `else` (joystick por puerto paralelo y dongle); no se define la bandera
  a false ni se da un lector que aborte (ADR 0004).
- La RAM del reloj no se persiste en fichero en M1. `write_battclock` conserva su
  salida temprana y la lectura al reiniciar el RTC no busca fichero. Consecuencia:
  con `rtcfile` no vacio la opcion se ignora en silencio, asi que la config
  publica debe rechazarla (T13). El estado del RTC va en el estado nativo y en
  Waterbox, no en un fichero.
- `PARALLEL_PORT` sigue sin definirse en el perfil: las escrituras de puerto
  paralelo siguen compiladas fuera y `test_cia` conserva su prueba sintactica de
  esa rama.

## Validacion

`tests.host.test_cia` (nuevo test de objeto: pristine contiene los nueve simbolos,
`cia.o` en O0/O2 no importa ninguno; sin el parche los importaba todos), los
modulos vecinos test_cia_keyboard/test_custom_cia/test_rtc/test_devices/test_events
y la puerta `tests.codegen.test_engine_imports`, que obligo a quitar del manifiesto
la familia cd32_parallel_cia, `cfgfile_resolve_path_out_load` y dos `inprec_*`.

## Limites

No prueba el RTC completo ni un arranque. Los otros propietarios T06b2cb siguen:
input_recording (newcpu, disk, savestate, inputdevice), cpuboard, rtg_hostfs_traps,
non_a500_banks, excluded_models_code y config_defaults_excluded_devices.
