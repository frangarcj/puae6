/*
* UAE - The Un*x Amiga Emulator
*
* Call Amiga Exec functions outside the main UAE thread.
*
* Copyright 1999 Patrick Ohly
*
* Uses the EXTER interrupt that is setup in filesys.c
* and needs thread support.
*/

#include "sysconfig.h"
#include "sysdeps.h"

#include "threaddep/thread.h"
#include "options.h"
#include "memory.h"
#include "autoconf.h"
#include "traps.h"
#include "native2amiga.h"

smp_comm_pipe native2amiga_pending;
static uae_sem_t n2asem;

/*
* to be called when setting up the hardware
*/

void native2amiga_install (void)
{
	init_comm_pipe (&native2amiga_pending, 300, 2);
	uae_sem_init (&n2asem, 0, 1);
}

#ifdef P6_NATIVE2AMIGA
// The queue is allocated by native2amiga_install, once per load of an image: unloading gives it back.
void native2amiga_free (void)
{
	destroy_comm_pipe (&native2amiga_pending);
}
#endif

void native2amiga_reset (void)
{
	smp_comm_pipe *p = &native2amiga_pending;
	p->rdp = p->wrp = 0;
	p->reader_waiting = 0;
	p->writer_waiting = 0;
}

/*
* to be called when the Amiga boots, i.e. by filesys_diagentry()
*/
void native2amiga_startup (void)
{
}

int native2amiga_isfree (void)
{
	return comm_pipe_has_data (&native2amiga_pending) == 0;
}

#if defined(SUPPORT_THREADS) || defined(P6_NATIVE2AMIGA) // P6: the pending-work queue is kept, with one thread and no waiting

void uae_nativesem_wait(void)
{
	uae_sem_wait(&n2asem);
}
void uae_nativesem_post(void)
{
	uae_sem_post(&n2asem);
}

void uae_Cause (uaecptr interrupt)
{
	uae_nativesem_wait();
	write_comm_pipe_int (&native2amiga_pending, 3, 0);
	write_comm_pipe_u32 (&native2amiga_pending, interrupt, 1);
	do_uae_int_requested ();
	uae_nativesem_post();
}

void uae_ReplyMsg (uaecptr msg)
{
	uae_nativesem_wait();
	write_comm_pipe_int (&native2amiga_pending, 2, 0);
	write_comm_pipe_u32 (&native2amiga_pending, msg, 1);
	do_uae_int_requested ();
	uae_nativesem_post();
}

void uae_PutMsg (uaecptr port, uaecptr msg)
{
	uae_nativesem_wait();
	write_comm_pipe_int (&native2amiga_pending, 1, 0);
	write_comm_pipe_u32 (&native2amiga_pending, port, 0);
	write_comm_pipe_u32 (&native2amiga_pending, msg, 1);
	do_uae_int_requested ();
	uae_nativesem_post();
}

void uae_Signal (uaecptr task, uae_u32 mask)
{
	uae_nativesem_wait();
	write_comm_pipe_int (&native2amiga_pending, 0, 0);
	write_comm_pipe_u32 (&native2amiga_pending, task, 0);
	write_comm_pipe_int (&native2amiga_pending, mask, 1);
	do_uae_int_requested ();
	uae_nativesem_post();
}

void uae_Signal_with_Func(uaecptr task, uae_u32 mask, UAE_PROCESSED state)
{
	uae_nativesem_wait();
	write_comm_pipe_int(&native2amiga_pending, 0 | 0x80, 0);
	write_comm_pipe_pvoid(&native2amiga_pending, (void*)state, 0);
	write_comm_pipe_u32(&native2amiga_pending, task, 0);
	write_comm_pipe_int(&native2amiga_pending, mask, 1);
	do_uae_int_requested();
	uae_nativesem_post();
}


void uae_NotificationHack (uaecptr port, uaecptr nr)
{
	uae_nativesem_wait();
	write_comm_pipe_int (&native2amiga_pending, 4, 0);
	write_comm_pipe_int (&native2amiga_pending, port, 0);
	write_comm_pipe_int (&native2amiga_pending, nr, 1);
	do_uae_int_requested ();
	uae_nativesem_post();
}

void uae_ShellExecute(TCHAR *command)
{
	TCHAR *cmd = my_strdup(command);
	uae_nativesem_wait();
	write_comm_pipe_int(&native2amiga_pending, 5, 0);
	write_comm_pipe_pvoid(&native2amiga_pending, cmd, 1);
	do_uae_int_requested();
	uae_nativesem_post();
}

void uae_ShellExecute2(uae_u32 id)
{
	uae_nativesem_wait();
	write_comm_pipe_int(&native2amiga_pending, 6, 0);
	write_comm_pipe_int(&native2amiga_pending, id, 1);
	do_uae_int_requested();
	uae_nativesem_post();
}

void uae_ClockSync(void)
{
	uae_nativesem_wait();
	write_comm_pipe_int(&native2amiga_pending, 7, 0);
	write_comm_pipe_int(&native2amiga_pending, 0, 1);
	do_uae_int_requested();
	uae_nativesem_post();
}

#endif

uaecptr uae_AllocMem (TrapContext *ctx, uae_u32 size, uae_u32 flags, uaecptr sysbase)
{
	trap_set_dreg(ctx, 0, size);
	trap_set_dreg(ctx, 1, flags);
	return CallLib(ctx, sysbase, -198); /* AllocMem */
}

void uae_FreeMem (TrapContext *ctx, uaecptr memory, uae_u32 size, uaecptr sysbase)
{
	trap_set_dreg(ctx, 0, size);
	trap_set_areg(ctx, 1, memory);
	CallLib(ctx, sysbase, -0xD2); /* FreeMem */
}

#ifdef P6_NATIVE2AMIGA
/* Native state (bridge/state_upstream.cpp, the hard-file section of P6DK, patch 0180): the messages the host has queued for the 68k and that the EXTER interrupt has
 * not delivered yet. The queue is a ring of at most size - 1 entries (rdp, wrp, the position in the ring is not behaviour); the state is the entries in order, each
 * message as the producers wrote it: Signal (0, task, mask), PutMsg (1, port, message), ReplyMsg (2, message), Cause (3, interrupt), NotificationHack (4, port, number)
 * and ShellExecute2 (6, id). A message that carries a pointer of the host (the callback of Signal_with_Func, 0x80 | 0, and the command of ShellExecute, 5) cannot
 * be saved, and nothing in the profile produces one: the walk below says so and the save is refused. */
static unsigned p6_n2a_length(uae_u32 cmd)
{
	switch (cmd) {
	case 0: case 1: case 4: return 3;
	case 2: case 3: case 6: return 2;
	default: return 0;
	}
}

static unsigned p6_n2a_walk(const uae_u32 *values, unsigned count)
{
	for (unsigned at = 0; at < count;) {
		const unsigned length = p6_n2a_length(values[at]);
		if (!length || length > count - at)
			return 0;
		at += length;
	}
	return count;
}

bool p6_n2a_state_get(uint32_t *values, unsigned capacity, unsigned *count)
{
	const smp_comm_pipe *p = &native2amiga_pending;
	if (!values || !count || !p->data || p->size < 2)
		return false;
	const unsigned n = (unsigned)((p->wrp - p->rdp + p->size) % p->size);
	if (n > capacity || n > (unsigned)p->size - 1)
		return false;
	for (unsigned i = 0; i < n; ++i)
		values[i] = p->data[(p->rdp + (int)i) % p->size].u32;
	if (n && !p6_n2a_walk(values, n))
		return false;
	*count = n;
	return true;
}

/* The inverse, for an image that has installed its queue: false (and nothing touched) for values the producers could not have written. */
bool p6_n2a_state_set(const uint32_t *values, unsigned count)
{
	smp_comm_pipe *p = &native2amiga_pending;
	if ((count && !values) || !p->data || p->size < 2 || count > (unsigned)p->size - 1)
		return false;
	if (count && !p6_n2a_walk(values, count))
		return false;
	for (unsigned i = 0; i < count; ++i) {
		uae_pt v;
		memset(&v, 0, sizeof v);
		v.u32 = values[i];
		p->data[i] = v;
	}
	p->rdp = 0;
	p->wrp = (int)count;
	p->reader_waiting = 0;
	p->writer_waiting = 0;
	return true;
}
#endif
