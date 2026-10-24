/*
 * UAE hardfile target backend for BizHawk: every hardfile is a guest memory
 * buffer named "HardDrive<n>" that the host fills before Init.
 */

#include "sysconfig.h"
#include "sysdeps.h"
#include "options.h"
#include "filesys.h"
#include "zfile.h"
#include "uae/types.h"

#include "emulibc.h"
#include "bizhawk-hd.h"

struct bizhawk_hd
{
	uae_u8 *data;
	uae_u64 size;
	uae_u8 *dirty; /* one bit per BIZHAWK_HD_CHUNK, set when the chunk differs from the original image */
	uae_u64 chunks;
	int modified; /* written since the host last flushed SaveRAM */
};

static struct bizhawk_hd hds[BIZHAWK_HD_MAX];

struct hardfilehandle
{
	int index;
};

int harddrive_dangerous, do_rdbdump;

static struct bizhawk_hd *hd_from_hfd(struct hardfiledata *hfd)
{
	if (!hfd->handle || hfd->handle->index < 0 || hfd->handle->index >= BIZHAWK_HD_MAX)
		return NULL;
	return &hds[hfd->handle->index];
}

int bizhawk_hd_alloc(int index, int64_t size)
{
	if (index < 0 || index >= BIZHAWK_HD_MAX || size <= 0)
		return 0;
	struct bizhawk_hd *hd = &hds[index];
	if (hd->data)
		return 0;
	hd->size = size;
	hd->chunks = (size + BIZHAWK_HD_CHUNK - 1) / BIZHAWK_HD_CHUNK;
	hd->data = malloc(size);
	hd->dirty = calloc((hd->chunks + 7) / 8, 1);
	hd->modified = 0;
	if (!hd->data || !hd->dirty)
	{
		free(hd->data);
		free(hd->dirty);
		hd->data = NULL;
		hd->dirty = NULL;
		hd->size = 0;
		return 0;
	}
	return 1;
}

int64_t bizhawk_hd_size(int index)
{
	if (index < 0 || index >= BIZHAWK_HD_MAX)
		return 0;
	return hds[index].size;
}

uint8_t *bizhawk_hd_data(int index)
{
	if (index < 0 || index >= BIZHAWK_HD_MAX)
		return NULL;
	return hds[index].data;
}

static void mark_dirty(struct bizhawk_hd *hd, uae_u64 offset, uae_u64 len)
{
	uae_u64 first = offset / BIZHAWK_HD_CHUNK;
	uae_u64 last = (offset + len - 1) / BIZHAWK_HD_CHUNK;
	for (uae_u64 c = first; c <= last && c < hd->chunks; c++)
		hd->dirty[c >> 3] |= 1 << (c & 7);
	hd->modified = 1;
}

/* host side: fill or patch the image. mark=1 for SaveRAM restores, 0 for the initial load */
ECL_EXPORT int HardDiskAlloc(int index, int64_t size)
{
	return bizhawk_hd_alloc(index, size);
}

ECL_EXPORT int64_t HardDiskSize(int index)
{
	return bizhawk_hd_size(index);
}

ECL_EXPORT int HardDiskWrite(int index, int64_t offset, const uint8_t *src, int64_t srcOffset, int len, int mark)
{
	if (index < 0 || index >= BIZHAWK_HD_MAX || len < 0)
		return 0;
	struct bizhawk_hd *hd = &hds[index];
	if (!hd->data || offset < 0 || (uae_u64)offset + (uae_u64)len > hd->size)
		return 0;
	memcpy(hd->data + offset, src + srcOffset, len);
	if (mark && len > 0)
		mark_dirty(hd, offset, len);
	return 1;
}

ECL_EXPORT int HardDiskRead(int index, int64_t offset, uint8_t *dst, int64_t dstOffset, int len)
{
	if (index < 0 || index >= BIZHAWK_HD_MAX || len < 0)
		return 0;
	struct bizhawk_hd *hd = &hds[index];
	if (!hd->data || offset < 0 || (uae_u64)offset + (uae_u64)len > hd->size)
		return 0;
	memcpy(dst + dstOffset, hd->data + offset, len);
	return 1;
}

ECL_EXPORT int64_t HardDiskChunkCount(int index)
{
	if (index < 0 || index >= BIZHAWK_HD_MAX)
		return 0;
	return hds[index].chunks;
}

ECL_EXPORT void HardDiskGetDirtyMap(int index, uint8_t *dst)
{
	if (index < 0 || index >= BIZHAWK_HD_MAX || !hds[index].dirty)
		return;
	memcpy(dst, hds[index].dirty, (hds[index].chunks + 7) / 8);
}

ECL_EXPORT int HardDiskModified(int index, int clear)
{
	if (index < 0 || index >= BIZHAWK_HD_MAX)
		return 0;
	int ret = hds[index].modified;
	if (clear)
		hds[index].modified = 0;
	return ret;
}

/* UAE target interface */

int hdf_open_target(struct hardfiledata *hfd, const TCHAR *pname)
{
	const TCHAR *name = pname;
	const TCHAR *slash = _tcsrchr(pname, '/');
	if (slash)
		name = slash + 1;

	hfd->flags = 0;
	hfd->drive_empty = 0;
	hdf_close(hfd);
	hfd->cache = NULL;
	hfd->cache_valid = 0;
	hfd->virtual_size = 0;
	hfd->virtual_rdb = NULL;

	int index = -1;
	if (_tcsncmp(name, _T(BIZHAWK_HD_NAME), _tcslen(_T(BIZHAWK_HD_NAME))) == 0)
		index = _tstoi(name + _tcslen(_T(BIZHAWK_HD_NAME)));
	if (index < 0 || index >= BIZHAWK_HD_MAX || !hds[index].data)
	{
		write_log(_T("HDF '%s' is not a mounted BizHawk hard disk\n"), pname);
		return 0;
	}

	hfd->handle = xcalloc(struct hardfilehandle, 1);
	hfd->handle->index = index;
	hfd->physsize = hfd->virtsize = hds[index].size & ~((uae_u64)hfd->ci.blocksize - 1);
	if (hfd->physsize < hfd->ci.blocksize || hfd->physsize == 0)
	{
		write_log(_T("HDF '%s' is too small\n"), pname);
		hdf_close_target(hfd);
		return 0;
	}
	_tcscpy(hfd->vendor_id, _T("UAE"));
	_tcsncpy(hfd->product_id, name, 15);
	hfd->product_id[15] = 0;
	hfd->product_rev[0] = 0;
	_tcscpy(hfd->product_rev, _T("0.3"));
	hfd->handle_valid = 1;
	hfd->emptyname = my_strdup(name);
	write_log(_T("HDF '%s' opened, size=%lluK\n"), pname, hfd->physsize / 1024);
	return 1;
}

void hdf_close_target(struct hardfiledata *hfd)
{
	xfree(hfd->handle);
	xfree(hfd->emptyname);
	xfree(hfd->virtual_rdb);
	hfd->handle = NULL;
	hfd->emptyname = NULL;
	hfd->virtual_rdb = NULL;
	hfd->virtual_size = 0;
	hfd->handle_valid = 0;
	hfd->cache = NULL;
	hfd->cache_valid = 0;
	hfd->drive_empty = 0;
	hfd->dangerous = 0;
}

int hdf_dup_target(struct hardfiledata *dhfd, const struct hardfiledata *shfd)
{
	return 0;
}

int hdf_read_target(struct hardfiledata *hfd, void *buffer, uae_u64 offset, int len)
{
	struct bizhawk_hd *hd = hd_from_hfd(hfd);
	if (!hd || hfd->drive_empty || len <= 0)
		return 0;
	offset += hfd->offset;
	if (offset >= hd->size)
		return 0;
	if (offset + len > hd->size)
		len = (int)(hd->size - offset);
	memcpy(buffer, hd->data + offset, len);
	return len;
}

int hdf_write_target(struct hardfiledata *hfd, void *buffer, uae_u64 offset, int len)
{
	struct bizhawk_hd *hd = hd_from_hfd(hfd);
	if (!hd || hfd->drive_empty || hfd->ci.readonly || len <= 0)
		return 0;
	offset += hfd->offset;
	if (offset >= hd->size)
		return 0;
	if (offset + len > hd->size)
		len = (int)(hd->size - offset);
	if (memcmp(hd->data + offset, buffer, len) != 0)
	{
		memcpy(hd->data + offset, buffer, len);
		mark_dirty(hd, offset, len);
	}
	return len;
}

int hdf_resize_target(struct hardfiledata *hfd, uae_u64 newsize)
{
	return 0;
}

int hdf_init_target(void)
{
	return 0;
}

int hdf_getnumharddrives(void)
{
	return 0;
}

TCHAR *hdf_getnameharddrive(int index, int flags, int *sectorsize, int *dangerousdrive, uae_u32 *outflags)
{
	return NULL;
}
