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

El guest usa constructor global, asignacion C++, datos por hilo con pthread_key
(el TLS de musl, que llega al hilo del sandbox por x28) y heaps
plain/sealed/invisible. No usa TLS del compilador (`thread_local`): XNU
sobrescribe TPIDR_EL0 en cada cambio de contexto, ver
`docs/adr/waterbox-compiler-tls.md`.
Los tests Rust habilitados en ARM64 no incluyen toda la suite de snapshots x86.
T09b1 ejecuta el mini-core desde .NET y restaura en una instancia nueva; T09c
cubre callbacks/registros (WB13), paginas logicas/fisicas (WB14) e identidad del guest
y la dylib con restore en otro proceso (WB15).

La puerta completa se construye/ejecuta con `python3 tools/build_waterbox.py --smoke`
(`--snapshot-smoke` es un alias): net8.0, SDK10 fijado y RollForward=Major. Escribe
`build/waterbox/smoke-result.json` con el guest y la dylib realmente cargados y FALLA si
el harness no cubre todas las aserciones de WB01, WB13, WB14 y WB15 o si `validate_elf`
acepta una de las imagenes dañadas que el harness escribe en `build/waterbox/damaged`.
Es evidencia SMOKE_RUNTIME: prueba el runtime Waterbox, no Amiga. Lo que el runtime
acepta o aborta ante cargas dañadas esta en `reports/tasks/T09c.md`.
`tls-negative/tpidr_probe.c` es una sonda nativa manual, no parte de ningun check.

El sysroot es una cache binaria local fijada por inventario, no una reconstruccion
de libc/libc++ desde fuentes. Su musl Darwin procede de `22440a4` con siete
overlays capturados en `references/musl-darwin`; no reemplaza la referencia
general `2063abc` en `references/musl`. Falta cerrar la reconstruccion de las
bibliotecas LLVM antes de una release. No distribuir este smoke como core Amiga.
