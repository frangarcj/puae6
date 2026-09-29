#define __SYSCALL_LL_E(x) (x)
#define __SYSCALL_LL_O(x) (x)

static inline long __syscall0(long n)
{
	register long x8 __asm__("x8") = n;
	register long x0 __asm__("x0");
	register long x16 __asm__("x16") = 0x35f00000080UL;

	__asm__ __volatile__ (
		"stp x30, xzr, [sp, #-16]!\n\t"
		"blr x16\n\t"
		"ldp x30, xzr, [sp], #16"
		: "=r"(x0)
		: "r"(x8), "r"(x16)
		: "x1", "x2", "x3", "x4", "x5", "x6", "x7",
		  "x9", "x10", "x11", "x12", "x13", "x14", "x15",
		  "x17", "x18",
		  "v0", "v1", "v2", "v3", "v4", "v5", "v6", "v7",
		  "memory", "cc"
	);
	
	return x0;
}

static inline long __syscall1(long n, long a)
{
	register long x8 __asm__("x8") = n;
	register long x0 __asm__("x0") = a;
	register long x16 __asm__("x16") = 0x35f00000080UL;

	__asm__ __volatile__ (
		"stp x30, xzr, [sp, #-16]!\n\t"
		"blr x16\n\t"
		"ldp x30, xzr, [sp], #16"
		: "+r"(x0)
		: "r"(x8), "r"(x16)
		: "x1", "x2", "x3", "x4", "x5", "x6", "x7",
		  "x9", "x10", "x11", "x12", "x13", "x14", "x15",
		  "x17", "x18",
		  "v0", "v1", "v2", "v3", "v4", "v5", "v6", "v7",
		  "memory", "cc"
	);

	return x0;
}

static inline long __syscall2(long n, long a, long b)
{
	register long x8  __asm__("x8")  = n;
	register long x0  __asm__("x0")  = a;
	register long x1  __asm__("x1")  = b;
	register long x16 __asm__("x16") = 0x35f00000080UL;

	__asm__ __volatile__ (
		"stp x30, xzr, [sp, #-16]!\n\t"
		"blr x16\n\t"
		"ldp x30, xzr, [sp], #16"
		: "+r"(x0)
		: "r"(x8), "r"(x1), "r"(x16)
		: "x2", "x3", "x4", "x5", "x6", "x7",
		  "x9", "x10", "x11", "x12", "x13", "x14", "x15",
		  "x17", "x18",
		  "v0", "v1", "v2", "v3", "v4", "v5", "v6", "v7",
		  "memory", "cc"
	);

	return x0;
}

static inline long __syscall3(long n, long a, long b, long c)
{
	register long x8  __asm__("x8")  = n;
	register long x0  __asm__("x0")  = a;
	register long x1  __asm__("x1")  = b;
	register long x2  __asm__("x2")  = c;
	register long x16 __asm__("x16") = 0x35f00000080UL;

	__asm__ __volatile__ (
		"stp x30, xzr, [sp, #-16]!\n\t"
		"blr x16\n\t"
		"ldp x30, xzr, [sp], #16"
		: "+r"(x0)
		: "r"(x8), "r"(x1), "r"(x2), "r"(x16)
		: "x3", "x4", "x5", "x6", "x7",
		  "x9", "x10", "x11", "x12", "x13", "x14", "x15",
		  "x17", "x18",
		  "v0", "v1", "v2", "v3", "v4", "v5", "v6", "v7",
		  "memory", "cc"
	);

	return x0;
}

static inline long __syscall4(long n, long a, long b, long c, long d)
{
	register long _x8  __asm__("x8")  = n;
	register long _x0  __asm__("x0")  = a;
	register long _x1  __asm__("x1")  = b;
	register long _x2  __asm__("x2")  = c;
	register long _x3  __asm__("x3")  = d;
	register long _x16 __asm__("x16") = 0x35f00000080;

	__asm__ __volatile__ (
		"stp x30, xzr, [sp, #-16]!\n\t"
		"blr x16\n\t"
		"ldp x30, xzr, [sp], #16"
		: "+r"(_x0)
		: "r"(_x8), "r"(_x1), "r"(_x2), "r"(_x3), "r"(_x16)
		: "x4", "x5", "x6", "x7",
		  "x9", "x10", "x11", "x12", "x13", "x14", "x15",
		  "x17", "x18",
		  "v0", "v1", "v2", "v3", "v4", "v5", "v6", "v7",
		  "memory", "cc"
	);

	return _x0;
}


static inline long __syscall5(long n, long a, long b, long c, long d, long e)
{
	register long _x8  __asm__("x8")  = n;
	register long _x0  __asm__("x0")  = a;
	register long _x1  __asm__("x1")  = b;
	register long _x2  __asm__("x2")  = c;
	register long _x3  __asm__("x3")  = d;
	register long _x4  __asm__("x4")  = e;
	register long _x16 __asm__("x16") = 0x35f00000080;

	__asm__ __volatile__ (
		"stp x30, xzr, [sp, #-16]!\n\t"
		"blr x16\n\t"
		"ldp x30, xzr, [sp], #16"
		: "+r"(_x0)
		: "r"(_x8), "r"(_x1), "r"(_x2), "r"(_x3), "r"(_x4), "r"(_x16)
		: "x5", "x6", "x7",
		  "x9", "x10", "x11", "x12", "x13", "x14", "x15",
		  "x17", "x18",
		  "v0", "v1", "v2", "v3", "v4", "v5", "v6", "v7",
		  "memory", "cc"
	);

	return _x0;
}

static inline long __syscall6(long n, long a, long b, long c, long d, long e, long f)
{
	register long _x8  __asm__("x8")  = n;
	register long _x0  __asm__("x0")  = a;
	register long _x1  __asm__("x1")  = b;
	register long _x2  __asm__("x2")  = c;
	register long _x3  __asm__("x3")  = d;
	register long _x4  __asm__("x4")  = e;
	register long _x5  __asm__("x5")  = f;
	register long _x16 __asm__("x16") = 0x35f00000080;

	__asm__ __volatile__ (
		"stp x30, xzr, [sp, #-16]!\n\t"
		"blr x16\n\t"
		"ldp x30, xzr, [sp], #16"
		: "+r"(_x0)
		: "r"(_x8), "r"(_x1), "r"(_x2), "r"(_x3), "r"(_x4), "r"(_x5), "r"(_x16)
		: "x6", "x7",
		  "x9", "x10", "x11", "x12", "x13", "x14", "x15",
		  "x17", "x18",
		  "v0", "v1", "v2", "v3", "v4", "v5", "v6", "v7",
		  "memory", "cc"
	);

	return _x0;
}

#define VDSO_USEFUL
#define VDSO_CGT_SYM "__kernel_clock_gettime"
#define VDSO_CGT_VER "LINUX_2.6.39"

#define IPC_64 0
