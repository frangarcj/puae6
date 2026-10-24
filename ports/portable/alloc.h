#ifndef P6_PORTABLE_ALLOC_H
#define P6_PORTABLE_ALLOC_H

#include <errno.h>
#include <stdint.h>
#include <stdlib.h>

/* libc owns the storage: ordinary malloc alignment, free with free/xfree.
 * Overflow is ENOMEM; realloc failure with a nonzero size retains the old block.
 * Zero sizes deliberately retain the target libc's malloc/calloc/realloc policy,
 * including realloc(p, 0), which may free p. No callbacks or hidden heap state.
 */
static inline void *p6_malloc_array(size_t size, size_t count)
{
	if (size && count > SIZE_MAX / size) {
		errno = ENOMEM;
		return NULL;
	}
	return malloc(size * count);
}

static inline void *p6_calloc_array(size_t size, size_t count)
{
	if (size && count > SIZE_MAX / size) {
		errno = ENOMEM;
		return NULL;
	}
	return calloc(count, size);
}

static inline void *p6_realloc_array(void *ptr, size_t size, size_t count)
{
	if (size && count > SIZE_MAX / size) {
		errno = ENOMEM;
		return NULL;
	}
	return realloc(ptr, size * count);
}

#ifdef __cplusplus
#define P6_ALLOC_CAST(T, expr) static_cast<T *>(expr)
#else
#define P6_ALLOC_CAST(T, expr) (expr)
#endif
#define xmalloc(T, N) P6_ALLOC_CAST(T, p6_malloc_array(sizeof(T), (N)))
#define xcalloc(T, N) P6_ALLOC_CAST(T, p6_calloc_array(sizeof(T), (N)))
#define xrealloc(T, P, N) P6_ALLOC_CAST(T, p6_realloc_array((P), sizeof(T), (N)))
#define xfree(P) free(P)

#endif
