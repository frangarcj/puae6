# T06cc — proveedores portables de texto y de la bandera de interrupcion

Cuatro simbolos que el motor necesita de verdad y que definia la capa de Windows:

- `au(const char*)`: texto de Amiga/legacy (ISO-8859-1) a TCHAR (UTF-8). ASCII queda
  igual y 0x80-0xff pasan a su forma de dos bytes; probado con los 255 valores de
  byte, resultado UTF-8 bien formado que decodifica de vuelta, y 100000 bytes.
- `my_strdup`, `uaetcslen`: copia y longitud en unidades TCHAR (bytes; el codigo
  legacy indexa y hace memmove por elementos, no cuenta puntos de codigo).
- `uae_int_requested`: palabra de bits de interrupcion pedidos desde fuera de la CPU
  (native2amiga, tarjeta UAE, aceleradoras). En el perfil no existe ningun productor,
  asi que vale siempre 0 para custom, devices y newcpu (ADR 0002). Es estado
  ordinario del motor, no un servicio; el contrato atomico se prueba sobre ella.

Errores de asignacion devuelven NULL con `errno = ENOMEM` (gancho de `malloc` en la
prueba) y la funcion se recupera. Sin locale, iconv ni reloj (comprobado con nm).

## No incluido

`ua`, `uutf8`, `utf8u`, `au_copy` y `uaestrlen` solo los llamaba la maquinaria .uss de
savestate.cpp, que T06cb excluye; no se implementan sin usuarios. Si algun usuario
aparece, la puerta de imports lo mostrara.

## Validacion

`tests.host.test_text_services`: sonda C++ en O0/O2 con ASan/UBSan y objeto con solo
los simbolos prometidos. La puerta de imports baja de 159 a 155.
