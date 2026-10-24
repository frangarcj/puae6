; Original PUAE6 micro-ROM, licensed under MIT; see LICENSE.
; Scenario S4 (long instructions), a template: build it with --define PAD=<n> (recorded default: PAD=3).
; @PAD@ NOPs shift the phase of the loop against the video units. The loop is dominated by two long
; instructions (MOVEM of 15 registers, DIVU without overflow), so unit boundaries fall inside them.
; The VERTB handler is the one of vsync.s. Note that the MOVEM restore also restores A0 and D1, so an
; interrupt taken between the save and the restore loses its D1 count and its log slot: deterministic,
; and part of what the scenario exercises.
entry:
    MOVE.W #$2700,SR
    LEA $400.W,A7
    MOVE.B #3,$BFE201.L
    MOVE.B #2,$BFE001.L
    .nops @PAD@
    MOVE.L #handler,$6C.L       ; level-3 autovector (vector 27)
    LEA $1000.W,A0              ; log pointer
    MOVE.W #$C020,$DFF09A.L     ; INTENA: SET|INTEN|VERTB
    MOVE.L #7,D2                ; divisor
    MOVE.L #$00065678,D3        ; dividend: high word < divisor, else DIVU overflows and takes 10 cycles
    MOVE.W #$2000,SR            ; interrupts on
loop:
    MOVEM.L D0-D7/A0-A6,-(SP)
    DIVU.W D2,D3
    MOVEM.L (SP)+,D0-D7/A0-A6
    ADDQ.L #1,D0
    BRA.S loop
handler:
    MOVE.W #$0020,$DFF09C.L     ; INTREQ: clear VERTB
    ADDQ.L #1,D1
    MOVE.L D0,(A0)+
    RTE
