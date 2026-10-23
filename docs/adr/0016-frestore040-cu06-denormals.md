# ADR 0016: denormales y procedencia en CU06 del 68040

## Decision

T55c12 queda bloqueada. No identificar un SINGLE reconstruido por su fraccion
de 23 bits ni interpretar el minimo de formato como exponente de un denormal.
El decoder propuesto 0397 se ha retirado; sus pruebas partian del mismo supuesto
y no aportaban un oraculo de la representacion del frame.

La restauracion general de BUSY040 sigue pendiente. Los casos normales,
ceros/infinidades y NaN ya admitidos no demuestran el caso denormal.

## Reproducer de la ambiguedad

El candidato seleccionaba rev40, CU_SAVEPC06, CMD1=5400, STAG5,
ETEMP=(3c000000,00000100,00000000), sin campos que indiquen procedencia.
Proponia recuperar SINGLE con fraccion1 y obtener 2^-149.

Una fraccion DOUBLE igual a 1<<29 produce la misma mantisa interna:
`(1<<29)<<11 == 1<<40`. Su valor es 2^-1045. Limitar los bits bajos de la
mantisa no distingue esos operandos: sus exponentes normalizados son 3f6a y
3bea, respectivamente. Esto es una colision de representaciones propuestas,
no una afirmacion de que ambos frames sean legales en ese estadio del hardware.

## Fuentes que impiden aceptar la inferencia

- [Motorola FPSP bugfix.S fijado](https://raw.githubusercontent.com/torvalds/linux/1da177e4c3f41524e886b7f1b8a0c1fc7321cac2/arch/m68k/fpsp040/bugfix.S):
  op2_xi conserva STAG, ETE15 y ETEMP; op2_com cambia el formato a DOUBLE sin
  añadir un marcador de procedencia. Remapea exactamente 3f80 a 3c00.
- [Motorola FPSP get_op.S fijado](https://raw.githubusercontent.com/torvalds/linux/1da177e4c3f41524e886b7f1b8a0c1fc7321cac2/arch/m68k/fpsp040/get_op.S):
  src_sd_dnrm elige el sesgo 3f81 o 3c01 por el formato del comando. Con 5400
  elige DOUBLE. El sesgo no se deduce del ancho aparente de la fraccion.
- [MC68040UM, apartado9.6.2 y figura9-9](https://www.nxp.com/docs/en/reference-manual/MC68040UM.pdf):
  los operandos denormales SINGLE y DOUBLE de esa figura tienen ETE cero.
  La figura no prueba todos los estadios CU06 anteriores a E1.
- [FPSP res_func.S fijado](https://raw.githubusercontent.com/torvalds/linux/1da177e4c3f41524e886b7f1b8a0c1fc7321cac2/arch/m68k/fpsp040/res_func.S):
  atiende el vector55. `nrm_src` normaliza y `fix_stk` prepara CU=FE con E1
  desactivado; `cu_dnrm` completa ciertas operaciones en software sin FRESTORE.
  Es una fase distinta y no prueba la representacion previa en CU06.
- [Errata oficial MC68040DE](https://www.nxp.com/docs/en/errata/MC68040DE_D.txt)
  para mascaras 2E42K/2E71M y [addendum Motorola de 1998](https://www.nxp.com/docs/en/reference-manual/MC68040UMAD.pdf):
  no especifican esa representacion. La correccion FP del addendum trata
  BSUN/FPIAR; no permite resolver la procedencia de ETEMP ni aceptar el decoder.

## Condicion para continuar

Demostrar mediante otra fuente primaria o un programa ejecutado en hardware la
representacion legal del operando en CU06 y su relacion con el workaround1238.
Dos hipotesis siguen abiertas: un estadio anterior a E1 aporta una distincion
que falta en el modelo, o el frame reconstruido tiene una restriccion/errata
para denormales. No inferir la instruccion desde memoria ni elegir una de las
dos interpretaciones porque sus pruebas aisladas pasan.

## Correccion independiente: operandos normalizados en FE (T55c13)

La ruta FE documentada por FPSP no presenta la perdida de procedencia de CU06:
`get_op:src_sd_dnrm` aun conoce el formato SINGLE y normaliza su fraccion F
como F×2^-149. Para F=1 escribe exponente3f6a, mantisa8000000000000000,
STAG normal y ETE15=1. `mk_norm` pone E15 para exponentes <=3fff;
eso incluye valores normales pequenos, no solamente exponentes sesgados negativos.

Para FADD/FMUL/FDIV/FCMP, `res_func:normal/fix_stk` escribe CU=FE y limpia E1
sin modificar estos campos. [gen_except.S fijado](https://raw.githubusercontent.com/torvalds/linux/1da177e4c3f41524e886b7f1b8a0c1fc7321cac2/arch/m68k/fpsp040/gen_except.S)
conserva el frame hasta FRESTORE por `do_clean`. FMOVE/FTST se completan en
software y no se usan como demostracion de ese recorrido FE.

El restaurador aplicaba la desnormalizacion por E15 aislado: para3f6a desplazaba
mas de63 bits y entregaba cero. La correccion0414 comprueba tambien bit14 del
exponente almacenado, tanto para ETEMP como FPTEMP. Ese criterio procede de
[round.S:denorm fijado](https://raw.githubusercontent.com/torvalds/linux/1da177e4c3f41524e886b7f1b8a0c1fc7321cac2/arch/m68k/fpsp040/round.S),
que extiende el signo del rango4000..7fff. Conserva3f6a y sigue reconstruyendo
los extended denormales normalizados con exponentes envueltos7fc1/7fc2.
No se atribuye esta interpretacion a una definicion explicita del manual.

El reproducer ejecutable esta en `busy_fe_single_denorm` del probe FRESTORE040:
frames independientes para ambos signos, las23 potencias de fraccion SINGLE,
fraccion maxima y3, FP0..7, aritmetica exacta y FCMP sin escritura; adicionalmente
1±2^-149 en las precisiones24/53/64 y cuatro modos de redondeo, INEX enmascarada,
habilitada y E3 anterior prioritario. Comprueba bus y estado privado tras
reinicializar el backend. El primer FADD falla sin0414. Las revisiones40/41
se prueban por separado, junto con la regresion FE de extended denormales.
Esta correccion no prueba ni desbloquea el frame CU06 de T55c12.
