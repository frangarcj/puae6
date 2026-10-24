// __clone(func, stack, flags, arg, ptid, tls, ctid)
//         x0,   x1,    w2,    x3,  x4,   x5,  x6

.global __clone
.hidden __clone
.type   __clone,%function
__clone:
    // align child stack and store entry point and argument
    and x1, x1, #-16
    sub x1, x1, #16
    stp x3, x0, [x1, #-16]!  // x3 is arg, x0 is func

    // prepare syscall arguments
    mov x0, x5          // tls
    // x1 is already child stack
    adr x2, child_thread_start  // child rip
    mov x3, x6          // child_tid
    // x4 is already parent_tid (ptid)
    mov x8, #2000       // NR_WBX_CLONE

    // custom clone syscall pointer
    ldr x10, =0x35f00000080
    stp x30, xzr, [sp, #-16]!
    blr x10
	ldp x30, xzr, [sp], #16

    // parent returns
    ret

child_thread_start:
    ldp x0, x1, [sp], #16   // x0 = arg, x1 = func
    blr x1                  // call thread function

    // exit syscall
    mov x8, #93         // SYS_exit
    ldr x10, =0x35f00000080
    blr x10
    brk #0
