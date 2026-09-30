; Original PUAE6 micro-ROM, licensed under MIT; see LICENSE.
; Scenario I1 (input): every VERTB logs a tag and the input registers the guest can read; every keyboard byte the CIA
; delivers is logged with the interrupt-control byte and acknowledged with the serial handshake (CRA bit 6 up, then down).
; VERTB record (7 words, from $1000): $AA55, JOY0DAT, JOY1DAT, POT0DAT, POT1DAT, POTGOR, CIA-A PRA (fire buttons).
; Keyboard record (3 words): $BB00, CIA-A ICR, CIA-A SDR (the keyboard byte, complemented and rotated by the CIA).
entry:
    MOVE.W #$2700,SR
    LEA $400.W,A7
    MOVE.B #3,$BFE201.L         ; DDRA: OVL and power LED are outputs, bits 6-7 (fire buttons) inputs
    MOVE.B #2,$BFE001.L         ; OVL off
    MOVE.L #kbd,$68.L           ; level 2: CIA-A (PORTS)
    MOVE.L #vb,$6C.L            ; level 3: VERTB
    LEA $1000.W,A0              ; log pointer
    MOVE.B #$88,$BFED01.L       ; CIA-A ICR mask: SET | SP (serial port)
    MOVE.W #$C028,$DFF09A.L     ; INTENA: SET | INTEN | PORTS | VERTB
    MOVE.W #$2000,SR
loop:
    ADDQ.L #1,D0
    BRA.S loop
vb:
    MOVE.W #$0020,$DFF09C.L     ; INTREQ: clear VERTB
    MOVE.W #$AA55,(A0)+
    MOVE.W $DFF00A.L,(A0)+      ; JOY0DAT
    MOVE.W $DFF00C.L,(A0)+      ; JOY1DAT
    MOVE.W $DFF012.L,(A0)+      ; POT0DAT
    MOVE.W $DFF014.L,(A0)+      ; POT1DAT
    MOVE.W $DFF016.L,(A0)+      ; POTGOR
    MOVEQ #0,D2
    MOVE.B $BFE001.L,D2         ; PRA
    MOVE.W D2,(A0)+
    RTE
kbd:
    MOVE.W #$2300,SR            ; keep VERTB out while a record is written
    MOVEQ #0,D3
    MOVE.B $BFED01.L,D3         ; ICR (reading it clears it)
    MOVEQ #0,D4
    MOVE.B $BFEC01.L,D4         ; SDR
    MOVE.W #$BB00,(A0)+
    MOVE.W D3,(A0)+
    MOVE.W D4,(A0)+
    MOVE.B #$40,$BFEE01.L       ; handshake: CRA SPMODE = output ...
    MOVE.B #$00,$BFEE01.L       ; ... and back
    MOVE.W #$0008,$DFF09C.L     ; INTREQ: clear PORTS
    RTE
