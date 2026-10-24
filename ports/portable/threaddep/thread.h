/* Single-thread replacement of WinUAE's threaddep/thread.h for the filesystem and hardfile units (T29a).
 * SPDX-License-Identifier: GPL-2.0-or-later
 *
 * The profile has one thread. A "semaphore" is a counter that only one thread touches: post adds, trywait takes when it can, and a wait that
 * would block has no one to wake it, so it is a fatal error with a reason instead of a hang. Threads cannot be started: uae_start_thread*,
 * uae_end_thread and uae_set_thread_priority are declared nowhere, so a unit that still needs one fails to compile or to link, which is the guard.
 * commpipe.h (queues over these semaphores) works unchanged: its writers and readers never wait while the queue is neither full nor empty.
 */
#ifndef P6_THREADDEP_THREAD_H
#define P6_THREADDEP_THREAD_H

#include <stdlib.h>
#include <string.h>
#include "diagnostics.h"

typedef struct { volatile int p6_count; } uae_sem_t;
typedef int uae_thread_id;      /* a type for the structures that still name one; no thread is ever created */

STATIC_INLINE void uae_sem_init(uae_sem_t *sem, int manual_reset, int initial_state)
{
	(void)manual_reset;
	sem->p6_count = initial_state ? 1 : 0;
}
STATIC_INLINE void uae_sem_destroy(uae_sem_t *sem) { sem->p6_count = 0; }
STATIC_INLINE void uae_sem_post(uae_sem_t *sem) { sem->p6_count++; }
STATIC_INLINE void uae_sem_unpost(uae_sem_t *sem) { if (sem->p6_count > 0) sem->p6_count--; }
STATIC_INLINE int uae_sem_trywait(uae_sem_t *sem)
{
	if (sem->p6_count <= 0)
		return -1;
	sem->p6_count--;
	return 0;
}
STATIC_INLINE int uae_sem_trywait_delay(uae_sem_t *sem, int millis)
{
	(void)millis;           /* nobody could post during the delay */
	return uae_sem_trywait(sem);
}
STATIC_INLINE void uae_sem_wait(uae_sem_t *sem)
{
	if (uae_sem_trywait(sem)) {
		p6_diag_write(P6_DIAG_ERROR, 0, 0, "a semaphore wait would block and the profile has no other thread to post it");
		abort();
	}
}

#include "commpipe.h"

#endif /* P6_THREADDEP_THREAD_H */
