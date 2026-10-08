; Original PUAE6 micro-ROM, licensed under MIT; see tests/fixtures/microrom/LICENSE.
; 68020 instruction probe (T24a). Straight-line program for the 68EC020 cycle-exact core (cpuemu_21).
; tests/host/test_cpu_020.py derives every expected value from the architecture (M68020UM) with Python
; integer arithmetic; nothing here is a copy of what the engine printed. The assembler of the fixtures
; only knows the 68000 subset, so every 68020 instruction is a `.word` line whose comment is the
; disassembly of those words; the test compares it with Capstone (an independent disassembler) when
; it is installed. A comment starting with `~` is not compared: Capstone leaves out the scale of a
; brief extension word, so its fields are spelled out instead.
;
; Memory map (Chip RAM, the micro-ROM turns the overlay off):
;   $0800   supervisor stack top           $2000   results, one long per store tagged with a semicolon and an equals sign, in order
;   $2800   exception frames (see below)   $3000   data (bit field, indexes, CAS, bounds)
;   $4000   vector table (VBR)             @RADDR@ routine that the cache test patches
; A0 = results pointer, A5 = frame log pointer, A1 = $3000, D6 = CCR scratch (always zero but its low byte).
; Every flag capture clears CCR first (MOVE #0,CCR) so X is zero, runs the instruction and reads CCR with
; MOVE.W CCR,D6 (68010+).

entry:
    MOVE.W #$2700,SR
    LEA $800.W,A7
    MOVE.B #3,$BFE201.L
    MOVE.B #2,$BFE001.L
    MOVEA.L #$2000,A0
    MOVEA.L #$2800,A5
    MOVEA.L #$3000,A1
    MOVEQ #0,D6

; ---- MOVEC: VBR (the vector table moves before any exception), CACR, CAAR, SFC, DFC, MSP, ISP, USP
    MOVE.L #$4000,D0
    .word $4E7B,$0801            ; movec d0, vbr
    .word $4E7A,$1801            ; movec vbr, d1
    MOVE.L D1,(A0)+              ;=vbr
    MOVE.L #h_fmt0,$4080.L       ; TRAP #0
    MOVE.L #h_addr,$400C.L       ; address error
    MOVE.L #h_skip2,$4010.L      ; illegal instruction
    MOVE.L #h_fmt2,$4014.L       ; zero divide
    MOVE.L #h_fmt2,$4018.L       ; CHK, CHK2
    MOVE.L #h_fmt2,$401C.L       ; TRAPcc
    MOVE.L #h_skip2,$4028.L      ; ~ line A (capstone has no name for it)
    MOVE.L #h_skip4,$402C.L      ; line F
    MOVE.L #$0000000B,D0         ; CACR: enable, freeze, clear
    .word $4E7B,$0002            ; movec d0, cacr
    .word $4E7A,$1002            ; movec cacr, d1
    MOVE.L D1,(A0)+              ;=cacr_ei_fi
    MOVEQ #1,D0                  ; enable, no freeze
    .word $4E7B,$0002            ; movec d0, cacr
    .word $4E7A,$1002            ; movec cacr, d1
    MOVE.L D1,(A0)+              ;=cacr_ei
    MOVE.L #$12345678,D0
    .word $4E7B,$0802            ; movec d0, caar
    .word $4E7A,$2802            ; movec caar, d2
    MOVE.L D2,(A0)+              ;=caar
    MOVEQ #5,D0
    .word $4E7B,$0000            ; movec d0, sfc
    .word $4E7A,$3000            ; movec sfc, d3
    MOVE.L D3,(A0)+              ;=sfc
    MOVEQ #13,D0                 ; only three bits exist
    .word $4E7B,$0001            ; movec d0, dfc
    .word $4E7A,$3001            ; movec dfc, d3
    MOVE.L D3,(A0)+              ;=dfc
    MOVE.L #$0900,D0
    .word $4E7B,$0803            ; movec d0, msp
    .word $4E7A,$4803            ; movec msp, d4
    MOVE.L D4,(A0)+              ;=msp
    .word $4E7A,$5804            ; movec isp, d5 | M=0: the ISP is A7
    MOVE.L D5,(A0)+              ;=isp
    MOVE.L #$0A00,D0
    .word $4E7B,$0800            ; movec d0, usp
    .word $4E7A,$4800            ; movec usp, d4
    MOVE.L D4,(A0)+              ;=usp
    MOVE.W #$3700,SR             ; M=1: A7 is the MSP
    .word $2A0F                  ; move.l a7, d5
    MOVE.W #$2700,SR             ; M=0: A7 is the ISP again
    MOVE.L D5,(A0)+              ;=a7_master
    .word $2A0F                  ; move.l a7, d5
    MOVE.L D5,(A0)+              ;=a7_interrupt

; ---- data used below
    MOVE.B #$5A,$2FFF.L
    MOVE.L #0,$3000.L            ; Chip RAM starts as stripes of 0 and $FF, not as zeros
    MOVE.L #$0F1E2D3C,$3008.L
    MOVE.L #$CAFEBABE,$3024.L
    MOVE.L #$00003300,$302C.L
    MOVE.L #$00003200,$3040.L
    MOVE.L #$11112222,$3100.L
    MOVE.L #$00000010,$3110.L
    MOVE.L #$00000020,$3114.L
    MOVE.L #$0BADF00D,$320E.L
    MOVE.L #$13572468,$3304.L

; ---- bit fields
    MOVE.L #$12345678,D1
    .word $E9C1,$220C            ; bfextu d1{8:12}, d2
    MOVE.L D2,(A0)+              ;=bfextu
    MOVE.L #$F0000000,D4
    .word $44FC,$0000            ; move.w #$0, ccr
    .word $EBC4,$5004            ; bfexts d4{0:4}, d5
    .word $42C6                  ; move.w ccr, d6
    MOVE.L D5,(A0)+              ;=bfexts
    MOVE.L D6,(A0)+              ;=bfexts_ccr
    MOVE.L #$00080000,D7
    .word $44FC,$0000            ; move.w #$0, ccr
    .word $EDC7,$3110            ; bfffo d7{4:16}, d3
    .word $42C6                  ; move.w ccr, d6
    MOVE.L D3,(A0)+              ;=bfffo
    MOVE.L D6,(A0)+              ;=bfffo_ccr
    MOVEQ #0,D7
    .word $44FC,$0000            ; move.w #$0, ccr
    .word $EDC7,$3110            ; bfffo d7{4:16}, d3
    .word $42C6                  ; move.w ccr, d6
    MOVE.L D3,(A0)+              ;=bfffo_zero
    MOVE.L D6,(A0)+              ;=bfffo_zero_ccr
    MOVEQ #0,D1
    MOVE.L #$000000AB,D0
    .word $44FC,$0000            ; move.w #$0, ccr
    .word $EFC1,$0108            ; bfins d0, d1{4:8}
    .word $42C6                  ; move.w ccr, d6
    MOVE.L D1,(A0)+              ;=bfins
    MOVE.L D6,(A0)+              ;=bfins_ccr
    MOVE.L #$00000ABC,D0
    MOVEQ #20,D2
    .word $EFD1,$088C            ; ~ bfins d0, (a1){d2:12}: Do=1, offset in D2, width 12
    .word $2811                  ; move.l (a1), d4
    MOVE.L D4,(A0)+              ;=bfins_memory
    MOVE.L #$FFFFFFF8,D4
    .word $E9D1,$5908            ; ~ bfextu (a1){d4:8}, d5: Do=1, offset in D4 (-8), width 8
    MOVE.L D5,(A0)+              ;=bfextu_negative_offset
    MOVE.L #$12345678,D1
    .word $EAC1,$0108            ; bfchg d1{4:8}
    MOVE.L D1,(A0)+              ;=bfchg
    .word $ECC1,$0004            ; bfclr d1{0:4}
    MOVE.L D1,(A0)+              ;=bfclr
    .word $EEC1,$0704            ; bfset d1{28:4}
    MOVE.L D1,(A0)+              ;=bfset
    .word $44FC,$0000            ; move.w #$0, ccr
    .word $E8C1,$0208            ; bftst d1{8:8}
    .word $42C6                  ; move.w ccr, d6
    MOVE.L D6,(A0)+              ;=bftst_ccr

; ---- 32x32 -> 64 and 32 bit multiply
    MOVE.L #$89ABCDEF,D3
    MOVE.L #$FEDCBA98,D1
    .word $44FC,$0000            ; move.w #$0, ccr
    .word $4C01,$3402            ; mulu.l d1, d2:d3
    .word $42C6                  ; move.w ccr, d6
    MOVE.L D2,(A0)+              ;=mulu64_hi
    MOVE.L D3,(A0)+              ;=mulu64_lo
    MOVE.L D6,(A0)+              ;=mulu64_ccr
    MOVE.L #$FEDCBA98,D3
    MOVE.L #$12345678,D1
    .word $44FC,$0000            ; move.w #$0, ccr
    .word $4C01,$3C02            ; muls.l d1, d2:d3
    .word $42C6                  ; move.w ccr, d6
    MOVE.L D2,(A0)+              ;=muls64_hi
    MOVE.L D3,(A0)+              ;=muls64_lo
    MOVE.L D6,(A0)+              ;=muls64_ccr
    MOVE.L #$00010000,D3
    MOVE.L #$00010000,D1
    .word $44FC,$0000            ; move.w #$0, ccr
    .word $4C01,$3000            ; mulu.l d1, d3
    .word $42C6                  ; move.w ccr, d6
    MOVE.L D3,(A0)+              ;=mulu32_overflow
    MOVE.L D6,(A0)+              ;=mulu32_overflow_ccr
    MOVEQ #-3,D3
    MOVEQ #7,D1
    .word $44FC,$0000            ; move.w #$0, ccr
    .word $4C01,$3800            ; muls.l d1, d3
    .word $42C6                  ; move.w ccr, d6
    MOVE.L D3,(A0)+              ;=muls32
    MOVE.L D6,(A0)+              ;=muls32_ccr

; ---- 64/32 and 32/32 divide
    MOVEQ #1,D2
    MOVE.L #$23456789,D3
    MOVE.L #$00001000,D1
    .word $44FC,$0000            ; move.w #$0, ccr
    .word $4C41,$3402            ; divu.l d1, d2:d3
    .word $42C6                  ; move.w ccr, d6
    MOVE.L D3,(A0)+              ;=divu64_quotient
    MOVE.L D2,(A0)+              ;=divu64_remainder
    MOVE.L D6,(A0)+              ;=divu64_ccr
    MOVE.L #$FFFFFF17,D2
    MOVE.L #$2B5AF000,D3
    MOVE.L #$00003039,D1
    .word $44FC,$0000            ; move.w #$0, ccr
    .word $4C41,$3C02            ; divs.l d1, d2:d3
    .word $42C6                  ; move.w ccr, d6
    MOVE.L D3,(A0)+              ;=divs64_quotient
    MOVE.L D2,(A0)+              ;=divs64_remainder
    MOVE.L D6,(A0)+              ;=divs64_ccr
    MOVE.L #1000003,D3
    MOVE.L #1000,D1
    .word $4C41,$3002            ; ~ divul.l d1, d2:d3: 32 bit dividend D3, quotient D3, remainder D2
    MOVE.L D3,(A0)+              ;=divul32_quotient
    MOVE.L D2,(A0)+              ;=divul32_remainder
    MOVEQ #1,D2
    MOVEQ #0,D3
    MOVEQ #1,D1
    .word $44FC,$0000            ; move.w #$0, ccr
    .word $4C41,$3402            ; divu.l d1, d2:d3 | the quotient needs 33 bits: overflow, operands kept
    .word $42C6                  ; move.w ccr, d6
    MOVE.L D2,(A0)+              ;=divu_overflow_d2
    MOVE.L D3,(A0)+              ;=divu_overflow_d3
    MOVE.L D6,(A0)+              ;=divu_overflow_ccr
    MOVEQ #0,D7
    MOVEQ #1,D2
    MOVE.L #l_zdiv,D0
    MOVE.L D0,(A0)+              ;=at_zdiv
    MOVE.L #l_zdiv_next,D0
    MOVE.L D0,(A0)+              ;=at_zdiv_next
    .word $44FC,$0000            ; move.w #$0, ccr
l_zdiv:
    .word $4C47,$3402            ; divu.l d7, d2:d3 | divide by zero: vector 5, format 2 frame
l_zdiv_next:

; ---- CAS
    MOVEA.L #$3100,A2
    MOVE.L #$11112222,D2
    MOVE.L #$AAAABBBB,D3
    .word $44FC,$0000            ; move.w #$0, ccr
    .word $0ED2,$00C2            ; cas.l d2, d3, (a2)
    .word $42C6                  ; move.w ccr, d6
    .word $2812                  ; move.l (a2), d4
    MOVE.L D4,(A0)+              ;=cas_match_memory
    MOVE.L D6,(A0)+              ;=cas_match_ccr
    MOVE.L #$12345678,D4
    .word $44FC,$0000            ; move.w #$0, ccr
    .word $0ED2,$00C4            ; cas.l d4, d3, (a2)
    .word $42C6                  ; move.w ccr, d6
    MOVE.L D4,(A0)+              ;=cas_mismatch_dc
    .word $2A12                  ; move.l (a2), d5
    MOVE.L D5,(A0)+              ;=cas_mismatch_memory
    MOVE.L D6,(A0)+              ;=cas_mismatch_ccr

; ---- CMP2 / CHK2
    MOVEA.L #$3110,A3
    MOVEQ #$18,D5
    .word $44FC,$0000            ; move.w #$0, ccr
    .word $04D3,$5000            ; cmp2.l (a3), d5
    .word $42C6                  ; move.w ccr, d6
    MOVE.L D6,(A0)+              ;=cmp2_inside
    MOVEQ #$10,D5
    .word $44FC,$0000            ; move.w #$0, ccr
    .word $04D3,$5000            ; cmp2.l (a3), d5
    .word $42C6                  ; move.w ccr, d6
    MOVE.L D6,(A0)+              ;=cmp2_lower_bound
    MOVEQ #$20,D5
    .word $44FC,$0000            ; move.w #$0, ccr
    .word $04D3,$5000            ; cmp2.l (a3), d5
    .word $42C6                  ; move.w ccr, d6
    MOVE.L D6,(A0)+              ;=cmp2_upper_bound
    MOVEQ #$30,D5
    .word $44FC,$0000            ; move.w #$0, ccr
    .word $04D3,$5000            ; cmp2.l (a3), d5
    .word $42C6                  ; move.w ccr, d6
    MOVE.L D6,(A0)+              ;=cmp2_above
    MOVEQ #5,D5
    .word $44FC,$0000            ; move.w #$0, ccr
    .word $04D3,$5000            ; cmp2.l (a3), d5
    .word $42C6                  ; move.w ccr, d6
    MOVE.L D6,(A0)+              ;=cmp2_below
    MOVEQ #$18,D5
    .word $44FC,$0000            ; move.w #$0, ccr
    .word $04D3,$5800            ; chk2.l (a3), d5 | inside: no trap
    .word $42C6                  ; move.w ccr, d6
    MOVE.L D6,(A0)+              ;=chk2_inside
    MOVEQ #$30,D5
    MOVE.L #l_chk2,D0
    MOVE.L D0,(A0)+              ;=at_chk2
    MOVE.L #l_chk2_next,D0
    MOVE.L D0,(A0)+              ;=at_chk2_next
    .word $44FC,$0000            ; move.w #$0, ccr
l_chk2:
    .word $04D3,$5800            ; chk2.l (a3), d5 | outside: vector 6, format 2 frame
l_chk2_next:

; ---- TRAPcc and CHK.L
    MOVE.L #l_trapne,D0
    MOVE.L D0,(A0)+              ;=at_trapne
    MOVE.L #l_trapne_next,D0
    MOVE.L D0,(A0)+              ;=at_trapne_next
    .word $44FC,$0000            ; move.w #$0, ccr
    .word $57FC                  ; trapeq | Z is clear: no trap
l_trapne:
    .word $56FC                  ; trapne | vector 7, format 2 frame
l_trapne_next:
    MOVEQ #10,D4
    MOVEQ #-1,D2
    MOVE.L #l_chkl,D0
    MOVE.L D0,(A0)+              ;=at_chkl
    MOVE.L #l_chkl_next,D0
    MOVE.L D0,(A0)+              ;=at_chkl_next
l_chkl:
    .word $4504                  ; chk.l d4, d2 | D2 < 0: vector 6, format 2 frame
l_chkl_next:
    MOVE.L #l_trap0_next,D0
    MOVE.L D0,(A0)+              ;=at_trap0_next
    .word $44FC,$0000            ; move.w #$0, ccr
    TRAP #0
l_trap0_next:

; ---- EXTB.L, LINK.L / UNLK, JSR / RTD
    MOVE.L #$000000F0,D4
    .word $44FC,$0000            ; move.w #$0, ccr
    .word $49C4                  ; extb.l d4
    .word $42C6                  ; move.w ccr, d6
    MOVE.L D4,(A0)+              ;=extb
    MOVE.L D6,(A0)+              ;=extb_ccr
    MOVEA.L #$12345678,A6
    .word $480E,$FFFF,$FFE0      ; link.l a6, #$ffffffe0
    .word $2A0E                  ; move.l a6, d5
    MOVE.L D5,(A0)+              ;=link_a6
    .word $2A0F                  ; move.l a7, d5
    MOVE.L D5,(A0)+              ;=link_sp
    .word $4E5E                  ; unlk a6
    .word $2A0F                  ; move.l a7, d5
    MOVE.L D5,(A0)+              ;=unlk_sp
    .word $2A0E                  ; move.l a6, d5
    MOVE.L D5,(A0)+              ;=unlk_a6
    .word $280F                  ; move.l a7, d4
    .word $518F                  ; subq.l #$8, a7
    .word $4EB9                  ; ~ jsr (xxx).l s_rtd
    .long s_rtd
    .word $2A0F                  ; move.l a7, d5
    MOVE.L D4,(A0)+              ;=rtd_sp_before
    MOVE.L D5,(A0)+              ;=rtd_sp_after

; ---- PACK / UNPK
    MOVE.L #$00000812,D0
    MOVE.L #$AABBCC00,D1
    .word $8340,$0101            ; pack d0, d1, #$101
    MOVE.L D1,(A0)+              ;=pack
    MOVE.L #$AB000035,D2
    MOVE.L #$11110000,D3
    .word $8782,$3030            ; unpk d2, d3, #$3030
    MOVE.L D3,(A0)+              ;=unpk

; ---- 68020 effective addresses
    MOVEQ #5,D2
    .word $2631,$2C10            ; ~ move.l ($10,a1,d2.l*4), d3: ext = D2 long, scale 4, d8 = $10
    MOVE.L D3,(A0)+              ;=ea_brief_long_index
    MOVE.L #$0000FFFE,D7
    .word $2831,$7410            ; ~ move.l ($10,a1,d7.w*4), d4: ext = D7 word (-2), scale 4, d8 = $10
    MOVE.L D4,(A0)+              ;=ea_brief_word_index
    MOVEQ #3,D2
    .word $2831,$2B26,$0040,$0008 ; move.l ([$40,a1],d2.l*2,$8), d4
    MOVE.L D4,(A0)+              ;=ea_memory_post_indexed
    .word $2A31,$2D22,$0020,$0004 ; move.l ([$20,a1,d2.l*4],$4), d5
    MOVE.L D5,(A0)+              ;=ea_memory_pre_indexed

; ---- 32 bit branch displacements
    MOVE.L #$11111111,D4
b_bra:
    .word $60FF                  ; ~ bra.l
    .long b_bra_to - b_bra - 2
    MOVE.L #$BAD0BAD0,D4
b_bra_to:
    MOVE.L D4,(A0)+              ;=bra_long
    MOVEQ #1,D4
b_beq:
    .word $67FF                  ; ~ beq.l: Z is clear, not taken
    .long b_beq_to - b_beq - 2
    MOVE.L #$22222222,D4
b_beq_to:
    MOVE.L D4,(A0)+              ;=beq_long_not_taken
    MOVEQ #1,D4
b_bne:
    .word $66FF                  ; ~ bne.l: taken
    .long b_bne_to - b_bne - 2
    MOVE.L #$BAD1BAD1,D4
b_bne_to:
    MOVE.L D4,(A0)+              ;=bne_long_taken

; ---- illegal instruction, line A, line F (no FPU, no MMU)
    MOVE.L #l_ill,D0
    MOVE.L D0,(A0)+              ;=at_illegal
    MOVE.L #l_aline,D0
    MOVE.L D0,(A0)+              ;=at_aline
    MOVE.L #l_fline,D0
    MOVE.L D0,(A0)+              ;=at_fline
l_ill:
    .word $4AFC                  ; illegal #$4afc
l_aline:
    .word $A000                  ; ~ line A (capstone has no name for it)
l_fline:
    .word $F280,$0000            ; fnop | line F: this machine has no FPU

; ---- address error: JMP to an odd address, frame format B
    MOVE.L #addr_resume,D0
    MOVE.L D0,(A0)+              ;=at_addr_resume
    MOVEA.L #$3001,A2
    .word $44FC,$0000            ; move.w #$0, ccr
    .word $4ED2                  ; jmp (a2)
    MOVE.L #$BADBAD00,D0         ; never executed
addr_resume:

; ---- instruction cache: a patched instruction is not seen while its line is cached
    MOVEA.L #@RADDR@,A4
    MOVE.W #$203C,@RADDR@.L
    MOVE.L #$11111111,(@RADDR@+2).L
    MOVE.W #$4E75,(@RADDR@+6).L
    MOVE.L #$00000009,D0         ; enable and clear
    .word $4E7B,$0002            ; movec d0, cacr
    MOVE.L #$57A15711,D7
    .word $4E94                  ; jsr (a4)
    MOVE.L D0,(A0)+              ;=icache_first
    MOVE.L #$22222222,(@RADDR@+2).L
    .word $4E94                  ; jsr (a4)
    MOVE.L D0,(A0)+              ;=icache_stale
    MOVE.L #$57A15712,D7
    MOVE.L #$00000009,D0         ; clear the cache, keep it enabled
    .word $4E7B,$0002            ; movec d0, cacr
    .word $4E94                  ; jsr (a4)
    MOVE.L D0,(A0)+              ;=icache_fresh
    .word $2A0F                  ; move.l a7, d5
    MOVE.L D5,(A0)+              ;=sp_end
    .word $2A0D                  ; move.l a5, d5
    MOVE.L D5,(A0)+              ;=frames_end
    .word $2A08                  ; move.l a0, d5
    MOVE.L D5,(A0)+              ;=results_end
    MOVE.L #$600DF00D,$1FFC.L
done:
    BRA.S done

s_rtd:
    .word $4E74,$0008            ; rtd #$8

h_fmt2:
    .word $2AD7                  ; move.l (a7), (a5)+
    .word $2AEF,$0004            ; move.l $4(a7), (a5)+
    .word $2AEF,$0008            ; move.l $8(a7), (a5)+
    RTE
h_fmt0:
    .word $2AD7                  ; move.l (a7), (a5)+
    .word $2AEF,$0004            ; move.l $4(a7), (a5)+
    RTE
h_skip2:
    .word $2AD7                  ; move.l (a7), (a5)+
    .word $2AEF,$0004            ; move.l $4(a7), (a5)+
    .word $54AF,$0002            ; addq.l #$2, $2(a7)
    RTE
h_skip4:
    .word $2AD7                  ; move.l (a7), (a5)+
    .word $2AEF,$0004            ; move.l $4(a7), (a5)+
    .word $58AF,$0002            ; addq.l #$4, $2(a7)
    RTE
h_addr:
    .word $2AD7                  ; move.l (a7), (a5)+
    .word $2AEF,$0004            ; move.l $4(a7), (a5)+
    .word $2ACF                  ; move.l a7, (a5)+
    .word $2F7C                  ; ~ move.l #addr_resume, 2(a7)
    .long addr_resume
    .word $0002
    RTE
