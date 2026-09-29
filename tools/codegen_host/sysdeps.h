#pragma once
// Minimal libc surface used by HOST generators; not an emulator host backend.
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>
#include <ctype.h>
#include <stdarg.h>
#include <assert.h>
#include "uae/types.h"
#define ENUMDECL typedef enum
#define ENUMNAME(x) x
#define _tcsncmp strncmp
#define _tcscmp strcmp
#define _vsnprintf vsnprintf
#define strnicmp strncasecmp
#define STATIC_INLINE static inline
#define CPU_EMU_SIZE 0
#define _istspace isspace
#define xmalloc(T, N) static_cast<T*>(malloc(sizeof(T) * (N)))
#define xfree free
#define NORETURN __attribute__((noreturn))
#define NOWARN_UNUSED(x) x
static inline char *ua(const TCHAR *s) { return s ? strdup(s) : nullptr; }
