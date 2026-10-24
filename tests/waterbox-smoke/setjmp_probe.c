/* SPDX-License-Identifier: MIT. Only live C frames participate in longjmp. */
#include <errno.h>
#include <pthread.h>
#include <setjmp.h>
#include <stdint.h>
#include "emulibc.h"
typedef uint64_t (*callback_t)(uint64_t,uint64_t,uint64_t,uint64_t,uint64_t,uint64_t);
extern int smoke_abi_probe(callback_t callback, uint64_t *out, size_t capacity);
static jmp_buf saved;
static pthread_key_t key;
static volatile int requested, last_value, status;
static uint64_t written;
ECL_EXPORT int sj_init(void) { return pthread_key_create(&key, 0) || pthread_setspecific(key, (void *)0x4567); }
static __attribute__((noinline)) uint64_t jump_callback(uint64_t a,uint64_t b,uint64_t c,uint64_t d,uint64_t e,uint64_t f)
{
    volatile uintptr_t context, tls = (uintptr_t)pthread_getspecific(key);
    volatile int error = errno;
    __asm__ volatile("mov %0,x28" : "=r"(context));
    switch (setjmp(saved)) {
    case 0:
        written = saved[0].__jb[9];
        if (written) { status = 28; return 0xBAD28; } /* original: no dangerous jump */
        saved[0].__jb[9] = UINT64_C(0xBAD028BAD028BAD0);
        longjmp(saved, requested); /* same C activation, no C++ frame crossed */
    case 1: last_value = 1; break;
    case 7: last_value = 7; break;
    default: status = 2; return 0xBAD02;
    }
    uintptr_t current; __asm__ volatile("mov %0,x28" : "=r"(current));
    status = last_value != (requested ? requested : 1) || current != context
        || (uintptr_t)pthread_getspecific(key) != tls || errno != error;
    return status ? 0xBAD00 : 0xC0FFEE00 + a+b+c+d+e+f;
}
ECL_EXPORT int sj_run(int value, uint64_t *out, size_t capacity)
{
    if (value != 0 && value != 7) return -1;
    requested = value; last_value = 0; status = 0;
    return smoke_abi_probe(jump_callback, out, capacity);
}
ECL_EXPORT uint64_t sj_status(void) { return (uint64_t)status; }
ECL_EXPORT uint64_t sj_nonzero_slot(void) { return written != 0; }
ECL_EXPORT uint64_t sj_return(void) { return (uint64_t)last_value; }
