# Mini-core Waterbox: compilacion T09a

Desde la raiz del repo:

```sh
python3 tools/import_sources.py verify --all
python3 tools/build_waterbox.py
python3 -m unittest discover -s tests/waterbox-smoke -v
```

La receta verifica las herramientas y el sysroot de `plan/toolchains.json`,
compila `guest.cpp` y emulibc con el common.mak/linkscript capturados y construye
WaterboxHost ARM64. Los outputs, wrappers relocados, cache Cargo y recibo quedan
en `build/waterbox/`. La referencia MacHawk se lee, nunca se compila en ella.
Si faltan dependencias Cargo, `--fetch` permite descargar exclusivamente las del
Cargo.lock capturado a esa cache local; el build posterior sigue siendo offline.

El guest usa constructor global, asignacion C++, TLS y heaps plain/sealed/invisible.
En T09a son entradas compiladas: todavia no se certifica su ejecucion ni snapshot.
Los tests Rust habilitados en ARM64 no incluyen toda la suite de snapshots x86.
T09b ejecutara el mini-core desde .NET y restaurara en una instancia nueva;
T09c cubrira callbacks/registros, protecciones y paginas logicas/fisicas.

El harness de T09b se construye/ejecuta con
`python3 tools/build_waterbox.py --snapshot-smoke`: net8.0, SDK10 fijado y
RollForward=Major. Actualmente falla en el lowering TLS del ELF LTO; ver
`reports/tasks/T09b.md`. No interpretar la existencia del harness como PASS.

El sysroot es una cache binaria local fijada por inventario, no una reconstruccion
de libc/libc++ desde fuentes. Su musl Darwin procede de `22440a4` con siete
overlays capturados en `references/musl-darwin`; no reemplaza la referencia
general `2063abc` en `references/musl`. Falta cerrar la reconstruccion de las
bibliotecas LLVM antes de una release. No distribuir este smoke como core Amiga.
