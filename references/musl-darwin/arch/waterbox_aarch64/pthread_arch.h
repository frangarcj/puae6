static inline struct pthread *__pthread_self()
{
    long *context;
    __asm__ ("mov %0,x28" : "=r"(context));
    return (void *)((char *)context[0] - sizeof(struct pthread));
}

#define TLS_ABOVE_TP
#define GAP_ABOVE_TP 16
#define TP_ADJ(p) ((char *)(p) + sizeof(struct pthread))

#define MC_PC pc
