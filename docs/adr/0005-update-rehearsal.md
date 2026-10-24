# 0005. Actualizaciones upstream en una copia aislada

## Contexto

El producto conserva WinUAE 6.0.3, commit
`857f48fcb09399d4d95527e29a2fdeaedb41f827`. El ensayo T38 uso como candidato
WinUAE 6.1.0 beta 17, commit `8132fd219c808d950028de2b50f76e142460da50`.
El usuario decidio mantener el pin del producto.

## Decision

Evaluar cada candidato por SHA completo en un directorio temporal bajo build/.
Usar `tools/update_upstream.py` para clasificar la cola; resolver cada conflicto
leyendo ambos lados. Una fusion de tres vias puede ayudar a preparar la cola,
pero la serie final debe aplicar exactamente, sin offset ni fuzz, mediante
`tools/materialize_upstream.py`. Regenerar las tablas desde el candidato.

No adoptar un candidato hasta clasificar las diferencias de CPU/chipset y superar
las pruebas de estado nativo y Waterbox. Mantener independientes el lock, la cola
y los generados del candidato y del producto. Eliminar el area temporal al acabar;
no conservar copias de la cola ni informes historicos en el repositorio.

## Alternativas descartadas

- Mover el pin porque el candidato compila: no demuestra fidelidad ni restore.
- Ablar los accesores de estado para aprobar la actualizacion: ocultaria una
  capacidad soportada.
- Dar por buena una fusion automatica: puede introducir errores semanticos
  aunque Git no produzca marcadores de conflicto.

## Hallazgos utiles del ensayo

El candidato ejecuto el corpus, pero el estado nativo fallo con
`COPR: the strobe of the Copper is out of range`; WB03/WB04 quedaron bloqueadas.
Las diferencias de fase de CPU seguian sin clasificar. Los accesores de estado
que leen internos de Copper, beam, DMAL, scheduler y blitter necesitan adaptacion;
los fixtures ligados a 6.0.3 deben revisarse por comportamiento, sin cambiar
los golden para ocultar diferencias. No se promete compatibilidad de estados
entre los dos builds.

## Costes y riesgos

La dependencia mas costosa son los accesores de estado que leen variables
internas de upstream. Contar lineas de parche no demuestra mantenimiento.
Una cola que aplica limpiamente puede seguir rompiendo el reinicio del contador
de ciclos o los limites de una unidad. Revisar scheduler, arbitraje de bus,
blanking y serializacion con sus tests de frontera.

## Como se valida

- `python3 -m unittest discover -s tests/upstream-update`: prueba la herramienta
  con un upstream minimo, sus conflictos y el snapshot fijado.
- Materializar y regenerar el candidato en su propia copia; ejecutar REF01,
  DET01, DET02, WB03/WB04 y las medidas de rendimiento sobre el corpus publico.
- Comparar hardware y A/V por separado y explicar cada divergencia. Una prueba
  de la herramienta no sustituye el ensayo del motor actualizado.
