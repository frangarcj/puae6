/*
 * minizip access to an archive UAE already holds open.
 *
 * The Waterbox filesystem allows a single open handle per mounted file, so the
 * libretro path of reopening the zip by name (unzOpen(z->name)) fails with
 * EACCES while the zfile is open. Serve minizip from a private copy of the
 * archive read through the existing zfile instead.
 */

#include "sysconfig.h"
#include "sysdeps.h"
#include "zfile.h"
#include "deps/libz/zlib.h"
#include "deps/libz/ioapi.h"
#include "deps/libz/unzip.h"

#include "bizhawk-unzip.h"

struct membuf
{
	uae_u8 *data;
	uae_s64 size;
	uae_s64 pos;
};

static voidpf ZCALLBACK mem_open(voidpf opaque, const char *filename, int mode)
{
	return opaque;
}

static uLong ZCALLBACK mem_read(voidpf opaque, voidpf stream, void *buf, uLong size)
{
	struct membuf *m = (struct membuf *)stream;
	if (m->pos >= m->size)
		return 0;
	if ((uae_s64)size > m->size - m->pos)
		size = (uLong)(m->size - m->pos);
	memcpy(buf, m->data + m->pos, size);
	m->pos += size;
	return size;
}

static uLong ZCALLBACK mem_write(voidpf opaque, voidpf stream, const void *buf, uLong size)
{
	return 0;
}

static long ZCALLBACK mem_tell(voidpf opaque, voidpf stream)
{
	return (long)((struct membuf *)stream)->pos;
}

static long ZCALLBACK mem_seek(voidpf opaque, voidpf stream, uLong offset, int origin)
{
	struct membuf *m = (struct membuf *)stream;
	uae_s64 npos;
	switch (origin)
	{
	case ZLIB_FILEFUNC_SEEK_SET: npos = offset; break;
	case ZLIB_FILEFUNC_SEEK_CUR: npos = m->pos + (uae_s64)offset; break;
	case ZLIB_FILEFUNC_SEEK_END: npos = m->size + (uae_s64)offset; break;
	default: return -1;
	}
	if (npos < 0 || npos > m->size)
		return -1;
	m->pos = npos;
	return 0;
}

static int ZCALLBACK mem_close(voidpf opaque, voidpf stream)
{
	struct membuf *m = (struct membuf *)stream;
	xfree(m->data);
	xfree(m);
	return 0;
}

static int ZCALLBACK mem_error(voidpf opaque, voidpf stream)
{
	return 0;
}

unzFile bizhawk_unzopen_zfile(struct zfile *z)
{
	if (!z)
		return NULL;
	uae_s64 old = zfile_ftell(z);
	uae_s64 size = zfile_size(z);
	if (size <= 0)
	{
		/* z->size is only tracked for memory-backed zfiles; measure FILE-backed ones */
		zfile_fseek(z, 0, SEEK_END);
		size = zfile_ftell(z);
	}
	if (size <= 0)
	{
		write_log(_T("bizhawk_unzopen_zfile: cannot determine archive size\n"));
		zfile_fseek(z, old, SEEK_SET);
		return NULL;
	}
	struct membuf *m = xcalloc(struct membuf, 1);
	m->data = xmalloc(uae_u8, (size_t)size);
	m->size = size;
	zfile_fseek(z, 0, SEEK_SET);
	size_t got = zfile_fread(m->data, 1, (size_t)size, z);
	zfile_fseek(z, old, SEEK_SET);
	if ((uae_s64)got != size)
	{
		write_log(_T("bizhawk_unzopen_zfile: short read (%lld of %lld)\n"), (long long)got, (long long)size);
		xfree(m->data);
		xfree(m);
		return NULL;
	}
	zlib_filefunc_def funcs;
	funcs.zopen_file = mem_open;
	funcs.zread_file = mem_read;
	funcs.zwrite_file = mem_write;
	funcs.ztell_file = mem_tell;
	funcs.zseek_file = mem_seek;
	funcs.zclose_file = mem_close;
	funcs.zerror_file = mem_error;
	funcs.opaque = m;
	unzFile uz = unzOpen2("", &funcs);
	if (!uz)
	{
		write_log(_T("bizhawk_unzopen_zfile: unzOpen2 failed (%lld bytes)\n"), (long long)size);
		xfree(m->data);
		xfree(m);
		return NULL;
	}
	unz_global_info gi;
	if (unzGetGlobalInfo(uz, &gi) == UNZ_OK)
		write_log(_T("bizhawk_unzopen_zfile: %lld bytes, %lu entries\n"), (long long)size, gi.number_entry);
	return uz;
}
