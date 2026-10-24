; Original PUAE6 micro-ROM, licensed under MIT; see LICENSE.
; Kickstart-shaped 256 KiB image whose reset vector points at ROM offset 0xD2. It masks
; interrupts, sets a stack, switches the ROM overlay off through CIA-A and counts in D0.
entry:
    MOVE.W #$2700,SR
    LEA $400.W,A7
    MOVE.B #3,$BFE201.L
    MOVE.B #2,$BFE001.L
loop:
    ADDQ.L #1,D0
    BRA.S loop
