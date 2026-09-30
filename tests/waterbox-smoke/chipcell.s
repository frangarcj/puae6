; PUAE6 test fixture (T19c2, MIT like the other micro-ROMs): stores a marker and a colour word in Chip RAM, then copies the colour word into
; COLOR00 forever, so a host that overwrites that word changes the next picture. The marker lets a host find Chip RAM in the sandbox.
entry:
    MOVE.W #$2700,SR
    LEA $400.W,A7
    MOVE.B #3,$BFE201.L
    MOVE.B #2,$BFE001.L
    MOVE.L #$50364D57,$10000.L
    MOVE.W #$0000,$30000.L
loop:
    MOVEA.L #$DFF180,A0
    MOVE.W $30000.L,(A0)+
    ADDQ.L #1,D0
    BRA.S loop
