; Original PUAE6 micro-ROM, licensed under MIT; see LICENSE.
; Scenario S2 (vertical blank): the level-3 handler counts VERTB interrupts in D1 and logs the main-loop
; counter D0 through A0 (a long per interrupt from $1000) while the main loop counts in D0.
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
    ADDQ.L #1,D0
    BRA.S loop
handler:
    MOVE.W #$0020,$DFF09C.L     ; INTREQ: clear VERTB
    ADDQ.L #1,D1
    MOVE.L D0,(A0)+
    RTE
