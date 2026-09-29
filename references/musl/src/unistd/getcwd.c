#include <unistd.h>
#include <errno.h>
#include <limits.h>
#include <string.h>
#include "syscall.h"

char *getcwd(char *buf, size_t size)
{
	char tmp[buf ? 1 : PATH_MAX];
	if (!buf) {
		buf = tmp;
		size = sizeof tmp;
	} else if (!size) {
		errno = EINVAL;
		return 0;
	}
	long ret = syscall(SYS_getcwd, buf, size);
	if (ret < 0)
		return 0;
	if (ret == 0 || buf[0] != '/') {
		errno = ENOENT;
		return 0;
	}
	// silences an incorrect warning in gcc 10+, see https://gcc.gnu.org/bugzilla/show_bug.cgi?id=93644
	#pragma GCC diagnostic ignored "-Wreturn-local-addr" \
	return buf == tmp ? strdup(buf) : buf;
}
