// MIT. Native caller canaries and a minimal guest entry, built separately.
#ifdef HOST_SHIM
.text
.globl _entry_host_call
_entry_host_call:
    stp x29, x30, [sp, #-16]!
    mov x29, sp
    stp x19, x20, [sp, #-16]!
    stp x21, x22, [sp, #-16]!
    stp x23, x24, [sp, #-16]!
    stp x25, x26, [sp, #-16]!
    stp x27, x28, [sp, #-16]!
    stp x1, xzr, [sp, #-16]!
    mov x9, x0
    mov x19, x1
    add x20, x1, #1
    add x21, x1, #2
    add x22, x1, #3
    add x23, x1, #4
    add x24, x1, #5
    add x25, x1, #6
    add x26, x1, #7
    add x27, x1, #8
    mov x0, x2
    blr x9
    mov x10, #0
    mov x11, #0x1122
    cmp x0, x11
    cset x10, ne
    ldr x9, [sp]
    .macro check reg
    cmp \reg, x9
    cset x11, ne
    orr x10, x10, x11
    add x9, x9, #1
    .endm
    check x19
    check x20
    check x21
    check x22
    check x23
    check x24
    check x25
    check x26
    check x27
    add x9, sp, #96
    cmp x29, x9
    cset x11, ne
    orr x0, x10, x11
    add sp, sp, #16
    ldp x27, x28, [sp], #16
    ldp x25, x26, [sp], #16
    ldp x23, x24, [sp], #16
    ldp x21, x22, [sp], #16
    ldp x19, x20, [sp], #16
    ldp x29, x30, [sp], #16
    ret
#else
.text
.globl entry_capture
.type entry_capture, %function
entry_capture:
    adrp x9, entry_values
    add x9, x9, :lo12:entry_values
    stp x19, x20, [x9]
    stp x21, x22, [x9, #16]
    stp x23, x24, [x9, #32]
    stp x25, x26, [x9, #48]
    stp x27, x29, [x9, #64]
    str x0, [x9, #80]
    ldr x10, [x9, #88]
    add x10, x10, x0
    str x10, [x9, #88]
    mov x0, #0x1122
    ret
.globl entry_read
.type entry_read, %function
entry_read:
    adrp x9, entry_values
    add x9, x9, :lo12:entry_values
    .macro copy offset
    ldp x2, x3, [x9, #\offset]
    stp x2, x3, [x0, #\offset]
    .endm
    copy 0
    copy 16
    copy 32
    copy 48
    copy 64
    copy 80
    ret
.bss
.balign 16
.type entry_values, %object
.size entry_values, 96
entry_values: .zero 96
#endif
