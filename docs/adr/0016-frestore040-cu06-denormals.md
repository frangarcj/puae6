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
