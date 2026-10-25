# T42: JIT nativo separado del perfil determinista

## Decision

Posponer la implementacion. El interpretador sigue siendo la unica ruta publica.
No hay un caso prioritario con hotspot de CPU medido que justifique el costo de
un recompilador. Este estudio y `plan/extensions/jit.json` no habilitan JIT ni
prometen determinismo TAS. JIT-A..F son propuestas, no fichas ni comandos nuevos.

La baseline T33 descrita en `docs/04-validation.md` separo motor y adaptadores:
el caso que excedia el objetivo orientativo era A1200 con runtime Waterbox Debug
y expulsion forzada (p95 20,5 ms frente a unidad de 20,03 ms). El coste de entrada
era aproximadamente 2,8 ms en Debug y 0,0002 ms en Release. T34a ya selecciono
Release. Ese cuello de botella del runtime no demuestra necesidad de traducir
CPU Amiga. `plan/performance-budgets.json` sigue ADVISORY: cinco repeticiones,
dispersion y runner dedicado antes de aprobar limites; no certifica todo hardware.

La campana posterior alojada [40114263286](https://github.com/frangarcj/puae6/actions/runs/40114263286)
termino correctamente sobre `c5b3b99fae85296b6eda0e234d81966aac2e5795`:
12 selectores, 43 metodos, 360 ventanas CPU/flags/frontend. Es procedencia de
medidas ya ejecutadas, no una nueva medida del HEAD de este estudio ni un perfil
del JIT. No se repite una bateria para producir este ADR. Antes de cambiar la
decision, elegir una carga reproducible realmente prioritaria, identificar su
hotspot y comparar mejoras del interprete/capas sin cambiar precision ni ticks.

## Auditoria del pin

Autoridad: WinUAE6.0.3/tag6030, commit
`857f48fcb09399d4d95527e29a2fdeaedb41f827`, snapshot verificado por
`manifests/upstream-files.json`. No se incorpora un backend de otro repositorio.

| Superficie real bajo upstream/winuae/jit | Resultado de lectura |
| --- | --- |
| compemu_support.cpp, compemu.h | Bloques, checksums, invalidacion y cache; CPU_i386/CPU_x86_64/CPU_arm. ARAnyM/UAE/Basilisk II; cabecera GPLv2 o posterior. |
| codegen_x86.cpp/.h, compemu_midfunc_x86.cpp/.h, gencomp.cpp | Emisor IA32/AMD64 y generacion; cabecera del emisor GPLv2 o posterior. Existir en snapshot no demuestra que enlace en nuestro host. |
| codegen_arm.cpp/.h, compemu_midfunc_arm*.cpp/.h, gencomp_arm.c | ARM de 32 bits: r0..r15, SP=r13/LR=r14/PC=r15, CPSR y palabras A32. No es un backend AArch64. Cabeceras GPLv2 o posterior, conservar atribuciones ARAnyM/Bernd Meyer y derivacion CCG/Ian Piumarta. |
| compemu_fpp.cpp | Camino FPU propio del JIT; no acredita equivalencia con SoftFloat entero ni resuelve CU06. |
| exception_handler.cpp | NATMEM y recuperacion por excepciones Win32/SEH o sigaction SIGSEGV/SIGBUS; no es una dependencia portable ya satisfecha. |

`alloc_code` exige cache por debajo de 4 GiB y `alloc_cache` solicita RWX en
`compemu_support.cpp`. No copiar esa politica como backend moderno. El emisor
ARM32 tampoco se convierte en ARM64 cambiando un macro. Soportar Darwin ARM64
requeriria backend/ABI nuevos y revision de licencia/procedencia, no un flag.
`ports/portable/sysconfig.h` rechaza JIT/NATMEM_OFFSET, el manifiesto los excluye,
y `config/schema/options.json` declara jit no soportado. Se conservan esas puertas.

## Contrato exigido antes de implementar

Una futura vertical empezaria solo nativa, con perfil accelerated explicito e
identidad propia de build/config/estado. No cambiar el perfil deterministic,
Waterbox, los niveles Exact/Compatible ni sus goldens. Establecer primero CPU y
precision admitidas, MMU0, politica FPU y fallback por opcode; llamar Normal a una
CPU no autoriza omitir eventos de DMA, IRQ o presupuesto de ciclos.

Un bloque debe retornar a la misma continuacion de `p6_run_frame` antes del
proximo evento pertinente. Nada de hilos host, segundo scheduler, longjmp a traves
de C++, callbacks host por instruccion/acceso o reloj de pared dentro del motor.

Invalidacion exige pruebas de escritura CPU y DMA sobre codigo, alias de bancos,
remapeo/reset/carga de estado y bloques encadenados con saltos directos. Incluir
SMC del siguiente opcode, cruce de pagina y cambio mientras hay bloque enlazado.
Inventariar `invalidate_block`, `calc_checksum` y `flush_icache_*`: un checksum
ocasional no demuestra que todas las escrituras invaliden antes del siguiente
fetch observable. Fallos/IO deben volver al interprete sin duplicar efectos.

Memoria de codigo con cota explicita, RW durante emision y RX durante ejecucion,
sin paginas simultaneamente RWX. Revisar permisos reales, invalidacion de icache,
alineacion de pagina host, politica de firma/entitlements y errores de asignacion
por host. Thunks MAP_JIT de WaterboxHost no autorizan JIT Amiga ni otra memoria
ejecutable en el guest. No instalar manejadores globales de senales por arrastre.

Guardar estado semantico, nunca bytes ejecutables, direcciones host, enlaces de
bloques o caches de traduccion. Tras validar identidad y estado, descartar cache
y reconstruir desde RAM restaurada; demostrar mismo futuro con cache fria,
caliente y envenenada. Separar caches JIT derivadas de caches de CPU emuladas que
puedan ser observables. Rechazar estados del perfil interprete/otro build antes
de mutar. No prometer compatibilidad de estados/peliculas entre builds.

## Costo y puerta de salida

Adoptar el x86 upstream evitaria escribir todos los emisores, pero exige portar
allocador/ABI/excepciones, invalidacion, retorno por frame y generadores. ARM64
anade un backend, mas superficie caliente por actualizar y una segunda matriz
de paridad. Rechazar JIT para siempre tampoco esta justificado sin una carga
prioritaria: por eso se pospone y se fija un disparador medible en JIT-A.

Antes de exponer una opcion: paridad CPU/RAM/eventos/ticks/A-V frente al interprete
con mismos parametros; probes independientes de instrucciones/fallos/SMC;
restore mismo/nuevo proceso y caches frias; bounds y W^X negativos; matriz de
hosts admitidos y ensayo UPD01 de generadores/parches. Un aumento de velocidad
con otra precision no es una mejora del mismo perfil. El JSON ordena esas puertas.
ADR01 valida solo esta decision y su subbacklog; no ejecuta ni acredita JIT.
