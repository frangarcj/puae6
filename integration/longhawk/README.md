# PUAE61 beta21 para LongHawk

Esta integración añade `PUAE61` como tercera opción Amiga sin sustituir `UAE`
ni `PUAE6`. El parche parte exactamente del commit LongHawk
`8de36990487237db3c50bf446e6af69671a89a9e`.

Los tres artefactos no textuales se aceptan únicamente con estas identidades:

- guest PUAE6 Windows x64: `c3aaf5889c7002b6a905a0b8af517d9627e772de255238d4b1c319216a958be2`;
- guest PUAE61 Windows x64: `6893d33541cf28cf27a244aec2950f6f4f3bde5f3d3036a5acdbdeb1a83c8f46`;
- fuentes correspondientes: `791a9fdf28bd0f710b5d52d834d407a9e401ceb4598cfeed9bbec3ded4d2b171`.

Aplicación sobre un checkout limpio:

```sh
python3 integration/longhawk/apply_puae61.py \
  /ruta/a/LongHawk \
  /ruta/a/puae6.wbx \
  /ruta/a/puae61.wbx \
  /ruta/a/source.tar.gz
```

La receta verifica base, limpieza y hashes antes de modificar el checkout. El
resultado conserva `UAE` como predeterminado, fija cada variante a un nombre,
guest y pin distintos, y añade la compilación, el inventario y el smoke real de
Windows al workflow existente. No publica ni crea una release.
