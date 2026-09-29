# T06b2ce — puerta de cierre de imports del motor A500

Convierte el enlace de prueba de T06b2cd en un check automatico. `tests/codegen/
test_engine_imports.py` construye el motor (directorio propio `build/link-audit`,
incremental, sin sanitizadores), enlaza todos los objetos y `libp6_portable.a` en
una dylib desechable con `-undefined error` y compara los simbolos que quedan con
`manifests/link-imports.json`. Falla si aparece un simbolo sin clasificar o en dos
familias, si un nombre listado ya no esta sin resolver, o si cambia el recuento de
una familia por patron. Asi una dependencia nueva de upstream se ve aqui, y un
simbolo resuelto obliga a salir de la lista. Mutaciones comprobadas: quitar
`akiko_mute`, anadir un nombre inventado y alterar el recuento de `op_`.

## Estado que mide (12084 simbolos, 16 familias, triage de primera pasada)

| Familia | Simbolos | Dueño | Disposicion |
|---|---:|---|---|
| cpu_dispatch_other_models | 11859 | T06c | select |
| state_serializer / state_host_paths | 75 / 4 | T15 | state_gate |
| input_device_backends | 20 | T10 | provide |
| video_presentation | 15 | T08 | provide |
| desktop_actions | 24 | T06c | decide |
| host_paths_files | 12 | T06c | guard_callers |
| text_charset / scheduler_time | 8 / 3 | T06c | provide |
| input_recording | 18 | T06b2cb | guard_callers |
| cpuboard | 14 | T06b2cb | guard_callers |
| rtg_hostfs_traps | 16 | T06b2cb | guard_callers |
| non_a500_banks | 6 | T06b2cb | guard_callers |
| cd32_parallel_cia | 3 | T06b2cb | guard_callers |
| excluded_models_code | 3 | T06b2cb | guard_callers |
| config_defaults_excluded_devices | 4 | T06b2cb | guard_callers |

Sin los 11859 manejadores `op_`, quedan 225 simbolos reales; 64 pertenecen a
T06b2cb (guardar callers de caracteristicas ya excluidas).

## Lo que la puerta ya ha descubierto

- `cia.o` aun referencia CD32 (`akiko_mute`), SCSI paralelo y grabacion de entrada:
  codigo de hardware que un A500 no tiene, sin guardar.
- `memory.o` referencia bancos Gayle/Gary/mbres de A600/A1200/A3000, y
  `newcpu`/`memory`/`expansion` los hooks de cpuboard y de RTG.
- `cpustbl.cpp` (generado, intacto) nombra los manejadores de todos los modelos de
  CPU aunque solo se compila CPUEMU_13. No se edita: T06c debe elegir la tabla
  (opcion del generador con reproducer y GEN01, o seleccion en el port).

## Limites

- La clasificacion es una primera pasada: la disposicion es una intencion, no una
  decision revisada por familia. Cada tarea duena debe confirmarla o cambiarla.
- Solo Mach-O (`-undefined error`). La variante ELF (`--no-undefined`, salida
  distinta) no esta escrita ni probada; el test se salta fuera de macOS y lo dice.
- Mide que falta por resolver, no que el binario funcione ni que un simbolo
  "proporcionado" tenga la semantica correcta. No hay enlace, arranque ni Waterbox.
- Construye el motor sin sanitizadores; la lista no debe depender de ellos.
- Coste: ~40 s la primera vez, unos segundos despues. Vive en tests/codegen, fuera
  del runner paralelo de host.
