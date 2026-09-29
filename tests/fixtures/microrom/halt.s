; Original PUAE6 micro-ROM, licensed under MIT; see LICENSE.
; Scenario S5 (halt): a TRAP with an odd supervisor stack pointer faults while stacking its frame, and the
; address error frame faults again, so the 68000 halts (double fault) and D1 stays 0. The trap vector points
; at a counting loop that is reached only if the CPU fails to halt.
entry:
    MOVE.W #$2700,SR
    LEA $400.W,A7
    MOVE.B #3,$BFE201.L
    MOVE.B #2,$BFE001.L
    MOVE.L #trap0,$80.L         ; vector 32 (TRAP #0)
    MOVEA.L #$1001,A7           ; odd stack pointer
    TRAP #0
trap0:
    ADDQ.L #1,D1
    BRA.S trap0
