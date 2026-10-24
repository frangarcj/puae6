; Original PUAE6 micro-ROM, licensed under MIT; see tests/fixtures/microrom/LICENSE.
; 68010 instruction probe (T52). Straight-line program for the 68010 cycle-exact core (cpuemu_13 row of cputbls).
; tests/host/test_cpu_010.py derives every expected value from the architecture (MC68010 User's Manual) with Python
; integer arithmetic; nothing here is a copy of what the engine printed. What tells a 68010 from a 68000 is on
; display: MOVEC and its control registers (VBR, SFC, DFC, USP; the CACR of a 68020 does not exist), the vector table
; at VBR, the four-word exception frame with the format/vector-offset word (the 68000 pushes three words), the format 8
; frame of an address error, RTD, MOVES, MOVE from CCR, MOVE from SR as a privileged instruction, and the loop mode
; that makes a one-instruction DBcc loop faster than its 68000 timing. The assembler of the fixtures only knows the
; 68000 subset, so every other instruction is a `.word` line whose comment is the disassembly of those words (the
; test compares it with Capstone when it is installed; a comment starting with `~` is not compared).
;
; Memory map (Chip RAM, the micro-ROM turns the overlay off):
;   $0800   supervisor stack top           $2000   results, one long per store tagged with a semicolon and an equals sign, in order
;   $2800   exception frames (see below)   $3000   data
;   $4000   vector table (VBR)
; A0 = results pointer, A5 = frame log pointer. Handlers log, per exception: SR:PC(high), PC(low):format/vector, and A7 on entry.

entry:
    MOVE.W #$2700,SR
    LEA $800.W,A7
    MOVE.B #3,$BFE201.L
    MOVE.B #2,$BFE001.L
    MOVEA.L #$2000,A0
    MOVEA.L #$2800,A5
    MOVEQ #0,D6

; ---- MOVEC: the vector base register (the vector table moves before any exception), SFC, DFC, USP
    .word $4E7A,$0801            ; movec vbr, d0
    MOVE.L D0,(A0)+              ;=vbr_reset
    MOVE.L #$4000,D0
    .word $4E7B,$0801            ; movec d0, vbr
    .word $4E7A,$1801            ; movec vbr, d1
    MOVE.L D1,(A0)+              ;=vbr
    MOVE.L #h_fmt0,$4080.L       ; TRAP #0
    MOVE.L #h_trap1,$4084.L      ; TRAP #1
    MOVE.L #h_addr,$400C.L       ; address error
    MOVE.L #h_skip4,$4010.L      ; illegal instruction
    MOVE.L #h_skip2,$4020.L      ; privilege violation
    MOVEQ #5,D0
    .word $4E7B,$0000            ; movec d0, sfc
    .word $4E7A,$3000            ; movec sfc, d3
    MOVE.L D3,(A0)+              ;=sfc
    MOVEQ #13,D0                 ; only three bits exist
    .word $4E7B,$0001            ; movec d0, dfc
    .word $4E7A,$3001            ; movec dfc, d3
    MOVE.L D3,(A0)+              ;=dfc
    MOVE.L #$0A00,D0
    .word $4E7B,$0800            ; movec d0, usp
    .word $4E7A,$4800            ; movec usp, d4
    MOVE.L D4,(A0)+              ;=usp

; ---- the CACR of the 68020 is not a control register of the 68010: MOVEC takes the illegal instruction exception
    MOVE.L #$CAFE0001,D1
    MOVE.L #l_movec_cacr,D0
    MOVE.L D0,(A0)+              ;=at_movec_cacr
l_movec_cacr:
    .word $4E7A,$1002            ; movec cacr, d1
    MOVE.L D1,(A0)+              ;=movec_cacr_d1

; ---- TRAP #0: the four word frame, format 0, the vector offset in it
    MOVE.L #l_trap0_next,D0
    MOVE.L D0,(A0)+              ;=at_trap0_next
    TRAP #0
l_trap0_next:

; ---- MOVE from CCR (new in the 68010), RTD, MOVES
    .word $44FC,$001F            ; move.w #$1f, ccr
    .word $42C6                  ; move.w ccr, d6
    MOVE.L D6,(A0)+              ;=move_ccr
    MOVEQ #0,D6
    .word $280F                  ; move.l a7, d4
    .word $518F                  ; subq.l #$8, a7
    .word $4EB9                  ; ~ jsr (xxx).l s_rtd
    .long s_rtd
    .word $2A0F                  ; move.l a7, d5
    MOVE.L D4,(A0)+              ;=rtd_sp_before
    MOVE.L D5,(A0)+              ;=rtd_sp_after
    MOVEA.L #$3000,A1
    MOVE.L #$A5A55A5A,D0
    .word $0E91,$0800            ; moves.l d0, (a1)
    .word $2411                  ; move.l (a1), d2
    MOVE.L D2,(A0)+              ;=moves_store
    .word $0E91,$3000            ; moves.l (a1), d3 | the extension word names the register: D3, memory to register
    MOVE.L D3,(A0)+              ;=moves_load

; ---- user mode: MOVE from SR is privileged on a 68010 (it is not on a 68000); MOVE from CCR is not
    MOVE.L #l_move_sr,D0
    MOVE.L D0,(A0)+              ;=at_move_sr
    MOVE.L #$DEADBEEF,D3
    MOVE.W #$0000,SR             ; user mode: A7 is the USP, exceptions use the ISP
l_move_sr:
    .word $40C3                  ; move.w sr, d3
    .word $44FC,$0015            ; move.w #$15, ccr
    .word $42C4                  ; move.w ccr, d4
    TRAP #1                      ; back to supervisor mode: the handler rewrites the SR it stacked
    MOVE.L D3,(A0)+              ;=move_sr_d3
    MOVE.L D4,(A0)+              ;=move_ccr_user

; ---- address error: JMP to an odd address, frame format 8 (29 words) which the RTE of the handler pops
    MOVE.L #addr_resume,D0
    MOVE.L D0,(A0)+              ;=at_addr_resume
    MOVEA.L #$3001,A2
    .word $44FC,$0000            ; move.w #$0, ccr
    .word $4ED2                  ; jmp (a2)
    MOVE.L #$BADBAD00,D0         ; never executed
addr_resume:
    .word $2A0F                  ; move.l a7, d5
    MOVE.L D5,(A0)+              ;=sp_after_address_error

; ---- loop mode: a one-instruction DBRA loop. The 68000 spends 8 + 10 cycles per pass (the last one 8 + 14 in all); the 68010 keeps
; the two instructions in its queue and does not fetch again. CIA-A timer A (one E clock = ten CPU clocks) times the 2000 passes.
    MOVEA.L #$F80000,A2          ; the loop reads ROM: no chip bus cycle can delay it
    MOVE.L #1999,D0
    MOVEQ #0,D2
    MOVEQ #0,D3
    MOVE.B #$FF,$BFE401.L        ; timer A latch, low
    MOVE.B #$FF,$BFE501.L        ; high; the timer is stopped: it loads the counter
    MOVE.B #9,$BFEE01.L          ; CRA: start, one shot
loop:
    .word $4A5A                  ; tst.w (a2)+
    .word $51C8,$FFFC            ; ~ dbra d0, loop (the branch target is spelled out by capstone)
    MOVE.B #0,$BFEE01.L          ; stop
    MOVE.B $BFE401.L,D2
    MOVE.B $BFE501.L,D3
    MOVE.L D2,(A0)+              ;=timer_low
    MOVE.L D3,(A0)+              ;=timer_high
    MOVE.L D0,(A0)+              ;=loop_counter
    .word $2A0F                  ; move.l a7, d5
    MOVE.L D5,(A0)+              ;=sp_end
    .word $2A0D                  ; move.l a5, d5
    MOVE.L D5,(A0)+              ;=frames_end
    MOVE.L #$600DF00D,$1FFC.L
done:
    BRA.S done

s_rtd:
    .word $4E74,$0008            ; rtd #$8

h_fmt0:
    .word $2AD7                  ; move.l (a7), (a5)+
    .word $2AEF,$0004            ; move.l $4(a7), (a5)+
    .word $2ACF                  ; move.l a7, (a5)+
    RTE
h_skip2:
    .word $2AD7                  ; move.l (a7), (a5)+
    .word $2AEF,$0004            ; move.l $4(a7), (a5)+
    .word $2ACF                  ; move.l a7, (a5)+
    .word $54AF,$0002            ; addq.l #$2, $2(a7)
    RTE
h_skip4:
    .word $2AD7                  ; move.l (a7), (a5)+
    .word $2AEF,$0004            ; move.l $4(a7), (a5)+
    .word $2ACF                  ; move.l a7, (a5)+
    .word $58AF,$0002            ; addq.l #$4, $2(a7)
    RTE
h_trap1:
    .word $2AD7                  ; move.l (a7), (a5)+
    .word $2AEF,$0004            ; move.l $4(a7), (a5)+
    .word $2ACF                  ; move.l a7, (a5)+
    .word $3EBC,$2700            ; move.w #$2700, (a7) | return to supervisor mode
    RTE
h_addr:
    .word $2AD7                  ; move.l (a7), (a5)+
    .word $2AEF,$0004            ; move.l $4(a7), (a5)+
    .word $2ACF                  ; move.l a7, (a5)+
    .word $2AEF,$0008            ; move.l $8(a7), (a5)+ | SSW and the high word of the fault address
    .word $2AEF,$000C            ; move.l $c(a7), (a5)+ | the low word of the fault address and the next word
    .word $2F7C                  ; ~ move.l #addr_resume, 2(a7)
    .long addr_resume
    .word $0002
    RTE
