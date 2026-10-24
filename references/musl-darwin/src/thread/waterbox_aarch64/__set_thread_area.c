#include "pthread_impl.h"

int __set_thread_area(void *p)
{
    long *context;
    __asm__ ("mov %0, x28" : "=r" (context));
    context[0] = (long)p;
    return 0;
}
