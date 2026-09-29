; Original PUAE6 CPU microprogram, licensed under MIT; see LICENSE.
; Raw program image loaded at 0x1000 with entry PC 0x1000.
entry:
    MOVEQ #0,D0
loop:
    ADDQ.L #1,D0
    BRA.S loop
