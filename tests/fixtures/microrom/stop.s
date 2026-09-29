; Original PUAE6 micro-ROM, licensed under MIT; see LICENSE.
; Scenario S7 (STOP): the vertical-blank setup of vsync.s, but the main loop sleeps in STOP until the VERTB
; interrupt. The handler counts interrupts in D1 and logs D0 (which stays 0 here) through A0 from $1000.
entry:
    MOVE.W #$2700,SR
    LEA $400.W,A7
    MOVE.B #3,$BFE201.L
    MOVE.B #2,$BFE001.L
    MOVE.L #handler,$6C.L       ; level-3 autovector (vector 27)
    LEA $1000.W,A0              ; log pointer
    MOVE.W #$C020,$DFF09A.L     ; INTENA: SET|INTEN|VERTB
    MOVE.W #$2000,SR            ; interrupts on
loop:
    STOP #$2000                 ; sleep until an interrupt; the handler returns to the BRA.S
    BRA.S loop
handler:
    MOVE.W #$0020,$DFF09C.L     ; INTREQ: clear VERTB
    ADDQ.L #1,D1
    MOVE.L D0,(A0)+
    RTE
