.global __unmapself
.type   __unmapself,%function
__unmapself:
	sub sp, sp, #16

	mov x8,#215 // SYS_munmap
	ldr x16,=0x35f00000080
	blr x16

	mov x0,#0
	mov x8,#93 // SYS_exit
	ldr x16,=0x35f00000080
	blr x16

	brk #0
