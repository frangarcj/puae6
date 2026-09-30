; Original PUAE6 micro-ROM, licensed under MIT; see LICENSE.
; Scenario I2 (input lag): every VERTB reads a different input resource, or none, and logs a tag saying which:
; $A1 CIA-A PRA, $A2 POTGOR, $A3 POT0DAT, $A4 nothing, $A5 JOY0DAT, $A6 JOY1DAT, $A7 POT1DAT, then it starts again.
; The VERTB vector is rewritten by each handler to point at the next one, so no branch is needed.
; The keyboard interface (level 2) logs $BB00, ICR and SDR and acknowledges, as in input_all.s.
; Records: tag word, then the value read (one word) for the register reads.
entry:
    MOVE.W #$2700,SR
    LEA $400.W,A7
    MOVE.B #3,$BFE201.L
    MOVE.B #2,$BFE001.L
    MOVE.L #kbd,$68.L
    MOVE.L #h1,$6C.L
    LEA $1000.W,A0
    MOVE.B #$88,$BFED01.L
    MOVE.W #$C028,$DFF09A.L
    MOVE.W #$2000,SR
loop:
    ADDQ.L #1,D0
    BRA.S loop
h1:
    MOVE.W #$0020,$DFF09C.L
    MOVE.W #$00A1,(A0)+
    MOVEQ #0,D2
    MOVE.B $BFE001.L,D2
    MOVE.W D2,(A0)+
    MOVE.L #h2,$6C.L
    RTE
h2:
    MOVE.W #$0020,$DFF09C.L
    MOVE.W #$00A2,(A0)+
    MOVE.W $DFF016.L,(A0)+
    MOVE.L #h3,$6C.L
    RTE
h3:
    MOVE.W #$0020,$DFF09C.L
    MOVE.W #$00A3,(A0)+
    MOVE.W $DFF012.L,(A0)+
    MOVE.L #h4,$6C.L
    RTE
h4:
    MOVE.W #$0020,$DFF09C.L
    MOVE.W #$00A4,(A0)+
    MOVE.L #h5,$6C.L
    RTE
h5:
    MOVE.W #$0020,$DFF09C.L
    MOVE.W #$00A5,(A0)+
    MOVE.W $DFF00A.L,(A0)+
    MOVE.L #h6,$6C.L
    RTE
h6:
    MOVE.W #$0020,$DFF09C.L
    MOVE.W #$00A6,(A0)+
    MOVE.W $DFF00C.L,(A0)+
    MOVE.L #h7,$6C.L
    RTE
h7:
    MOVE.W #$0020,$DFF09C.L
    MOVE.W #$00A7,(A0)+
    MOVE.W $DFF014.L,(A0)+
    MOVE.L #h1,$6C.L
    RTE
kbd:
    MOVE.W #$2300,SR
    MOVEQ #0,D3
    MOVE.B $BFED01.L,D3
    MOVEQ #0,D4
    MOVE.B $BFEC01.L,D4
    MOVE.W #$BB00,(A0)+
    MOVE.W D3,(A0)+
    MOVE.W D4,(A0)+
    MOVE.B #$40,$BFEE01.L
    MOVE.B #$00,$BFEE01.L
    MOVE.W #$0008,$DFF09C.L
    RTE
