; Original PUAE6 micro-ROM, licensed under MIT; see LICENSE.
; Scenario V1 (long-field bit stuck): a lores screen is drawn, then VPOSW sets LOF and nothing ever clears it, so every unit is a
; 313-line frame without interlace. After three units the renderer adopts the longer frame: 576 rows (PAL) / 486 (NTSC).
entry:
    MOVE.W #$2700,SR
    LEA $400.W,A7
    MOVE.B #3,$BFE201.L
    MOVE.B #2,$BFE001.L
    MOVE.L #$C0000000,$20000.L
    MOVE.L #$0F0F0F0F,$20004.L
    MOVE.L #$0F0F0F0F,$20008.L
    MOVE.L #$0F0F0F0F,$2000C.L
    MOVE.L #$0F0F0F0F,$20010.L
    MOVE.L #$0F0F0F0F,$20014.L
    MOVE.L #$0F0F0F0F,$20018.L
    MOVE.L #$0F0F0F0F,$2001C.L
    MOVE.L #$0F0F0F0F,$20020.L
    MOVE.L #$00000003,$20024.L
    MOVE.W #$0F00,$DFF180.L
    MOVE.W #$00F0,$DFF182.L
    MOVE.W #$2C81,$DFF08E.L
    MOVE.W #$2CC1,$DFF090.L
    MOVE.W #$0038,$DFF092.L
    MOVE.W #$00D0,$DFF094.L
    MOVE.W #$FFD8,$DFF108.L
    MOVE.W #$FFD8,$DFF10A.L
    MOVE.W #$0002,$DFF0E0.L
    MOVE.W #$0000,$DFF0E2.L
    MOVE.W #$1200,$DFF100.L
    MOVE.W #$8300,$DFF096.L
    MOVE.W #$8000,$DFF02A.L
loop:
    ADDQ.L #1,D0
    BRA.S loop
