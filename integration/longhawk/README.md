# PUAE61 beta21 para LongHawk

Esta integración añade `PUAE61` como tercera opción Amiga sin sustituir `UAE`
ni `PUAE6`. El parche parte exactamente del commit LongHawk
`8de36990487237db3c50bf446e6af69671a89a9e`.

Los dos artefactos no textuales se aceptan únicamente con estas identidades:

- guest Windows x64: `a244f88642163ab6163d92d3ba3ed004a5e02c5eb2f94b99f41f89484c80a2b2`;
- fuentes correspondientes: `dfaf5c5ec4fc81cc95ccc59f4ce5e88fb2c74e11d1784bdb1fa817e15aff0c0b`.

Aplicación sobre un checkout limpio:

```sh
python3 integration/longhawk/apply_puae61.py \
  /ruta/a/LongHawk \
  build/waterbox-x64/release/puae6.wbx \
  /ruta/a/source.tar.gz
```

La receta verifica base, limpieza y hashes antes de modificar el checkout. El
resultado conserva `UAE` como predeterminado, fija cada variante a un nombre,
guest y pin distintos, y añade la compilación, el inventario y el smoke real de
Windows al workflow existente. No publica ni crea una release.
