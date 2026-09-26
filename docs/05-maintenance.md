# 05. Mantenimiento y actualizaciones

## Fuente de verdad

Una sola importacion limpia de WinUAE 6.0.3. plan/upstream.lock.json fija los SHA;
T01 completa las dependencias aun null. Los pins deben ser commits completos y los
archivos externos deben tener SHA-256 y licencia. No confundir SHA de tag/blob/tree
con commit. Todas las fuentes publicas consultadas se enumeran en 00-evidence.md.

Para runtime e integracion, el usuario selecciono ../MacHawk. docs/07-machawk.md y
plan/machawk.reference.json registran el checkout y sus cambios locales; T01 debe
capturar inputs completos de la receta elegida. HEAD por si solo NO identifica
un submodulo sucio. No modificar ni limpiar MacHawk para obtener reproducibilidad.

Estrategia decidida: snapshot fiel/subtree de upstream bajo upstream/winuae, sin
editar. El lock y manifest permiten verificarlo sin depender de un submodulo
editable. No mantener dos copias divergentes. Crear una copia derivada en build
para aplicar la cola y generar fuentes. Cachear esta copia por contenido; nunca
tratar un build previo modificado a mano como input de release.

El libretro antiguo, el port Unix y el port UAE de BizHawk son referencias fijadas.
El libretro antiguo no es solo UX: su cambio m68k_go/frame_end y el uso que hace
MacHawk de esa primitive son evidencia para el parche de frame-control. Eso NO
autoriza copiar libretro-core.c como arquitectura nueva. Si se reutiliza codigo o
una idea implementada, registrar repositorio/commit/ruta/licencia/cambios y
clasificarla como engine patch, host support o frontend-only. No atribuir todo a
WinUAE ni borrar copyright de fragmentos importados. La revision de licencias del
conjunto y distribucion de fuentes es una tarea, no una garantia legal emitida
por este documento. ROMs y otros assets se tratan separadamente.

## Inventarios que debe producir el bootstrap

- upstream-files.json: lista/ruta/hash de cada archivo importado y commit.
- source-manifest.json: fuentes por feature/profile y motivo de exclusion.
- host-surface.json: simbolo/llamadores, semantica, backend y test de frontera.
- state-inventory.json: estado causal, propietario, reset/save/restore y test.
- licenses.json: fuente de licencia y obligaciones por componente/asset.

Un perfil se compila con una allowlist de fuentes y features. La CI compara el
manifiesto con el arbol nuevo y avisa de archivos relevantes añadidos/quitados.
No dejar que un glob compile un nuevo dispositivo Windows despues de un update.

## Cola de parches

patches/winuae/series.json incluye por parche: ID, titulo, motivo, orden,
archivos/simbolos tocados, prueba que justifica el cambio, si es candidato upstream
y version de eliminacion si corresponde. Categorias separadas: build/portability,
host hooks, frame control y state corrections. Los stubs de features excluidas
pertenecen al port, no a modificaciones de algoritmos CPU/chipset.

Mantener nombres/formato upstream en su codigo. No hacer clang-format masivo.
Cada parche resuelve una cosa. Los cambios a generadores modifican el generador;
sus outputs se regeneran, nunca se editan manualmente. Un typo local no justifica
copiar una version antigua completa de custom.cpp.

Presupuesto de mantenimiento propuesto tras el inventario inicial: no mas de 15
archivos upstream ni 1500 lineas manuales de delta para llegar a M1. Es alarma
de revision, NO excusa para ocultar codigo en macros o trucos de linker. Sobrepasar
el presupuesto requiere un ADR que muestre el costo y alternativas. Registrar
cantidad de parches, archivos calientes tocados y tiempo/conflictos del update.

Compilar sin parches de presentacion y sin dependencias de adaptadores debe ser
posible para cada backend host habilitado. El motor y bridge neutral no incluyen
libretro.h ni C#; p6_run_frame existe aunque ningun frontend este enlazado.

## Generacion reproducible

Inventariar build68k/gencpu y demas generadores realmente usados por el commit
fijado; no copiar comandos de otra version. Separar host tools de target tools.
Guardar inputs, flags, hash del generador y outputs. Dos builds limpios en misma
config deben producir outputs identicos; comparar tambien entre hosts antes de
usar su cache indistintamente. Estabilizar locale, orden de archivos y timestamps.

CMake no descargara master durante configure. Dependencias se resuelven en una
fase fetch explicita a pins y con opcion offline. El Makefile Waterbox recibe
las mismas salidas/manifiesto; no se mantiene una segunda seleccion a mano.

## Procedimiento de actualizacion upstream

1. Abrir una tarea UPDATE con OLD_SHA, NEW_SHA concreto y release notes. No mezclar
   el update con cambio de perfil o optimizacion.
2. Crear un area de trabajo descartable del proyecto, no borrar el arbol del usuario.
3. Importar NEW_SHA sin modificar y generar diff de inventarios/licencias/features.
4. Ejecutar la cola sin fuzz. Conflicto = fallo explicito y reporte, no aceptar todo
   'ours/theirs'. Resolver un parche por vez leyendo ambos lados y sus tests.
5. Regenerar herramientas/tablas desde el upstream nuevo; nunca portar el output
   viejo encima. Revisar cambios en scheduler, CPU, CIA, custom, disk y savestate.
6. Ejecutar integridad, builds limpios, microtests, corpus, native restore, Waterbox
   restore en sandbox nuevo, movie seek y perf por perfil.
7. Clasificar cambios de hashes: bug upstream documentado, regresion del port o
   desconocido. Un desconocido bloquea. Actualizar golden solo con evidencia.
8. Actualizar core_build_id, compatibilidad de estados/peliculas y documentacion.
   Conservar el build anterior para peliculas; no garantizar que la version nueva
   pueda cargar su snapshot.
9. Publicar diff de parches, conflictos resueltos, performance y lista de escenarios.
   Release solo tras revision; rollback es volver al conjunto de pins anterior.

UPD01 prueba tambien errores: parche que no aplica, generador que cambio y archivo
nuevo relevante deben detectarse. Antes de M4 se ensaya una actualizacion real a
un candidato fijado y evaluado; si no hay candidato apropiado, se registra el
bloqueo y no se afirma haber demostrado mantenimiento real solo con un mock.
No es necesario adoptar ese candidato como version final del producto.

## CI y distribucion

Artefactos: shared core libretro por plataforma, WBX+integracion C# por runtime,
headless runner de pruebas, manifest de build/perfiles, fuentes exactas/parches,
codegen/recipe, licencias y notas de incompatibilidad. No incluir fixtures privados.

No incrustar __DATE__/paths absolutos/orden inestable en build IDs. Fijar toolchain,
dependencias y environment reproducible; demostrar dos builds limpios antes de
anunciar reproducibilidad binaria (fuentes reproducibles no basta).

Minimo release: todas las tareas core de M4 con sus checks, todos los perfiles
anunciados probados, cero pins obligatorios pendientes, estado/medios rechazados
correctamente al cambiar identidad, presupuestos revisados y guia de instalacion.
No enviar PR upstream ni reemplazar archivos de BizHawk instalado automaticamente.

## Deprecaciones y bugs

Config tiene version; nombres de opciones no cambian arbitrariamente. Importador
de configs antiguas genera advertencias explicitas. Estados/peliculas se identifican
por core build y perfil, no por nombre de archivo. Bugs nuevos incluyen escenario
minimo, hash de firmware/medio, trace corto y primer frame divergente.

Asignar cada bug a motor upstream, port host, bridge, adapter o runtime. Evitar un
arreglo WinUAE para un fallo de input C#. Un bugfix que toca semantica necesita
regresion y cambia la identidad compatible; una optimizacion debe conservarla
semanticamente aunque el hash binario cambie.
