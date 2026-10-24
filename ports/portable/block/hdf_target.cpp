/* Block backend of the hard files (T29a): the seam include/filesys.h:149-155 names, over an image of the blob store.
 * SPDX-License-Identifier: GPL-2.0-or-later
 *
 * An image is the immutable base registered by p6_load under its blob id (the SHA-256 of its bytes, bridge/media.cpp). The name that hdf_open receives is
 * that id; a read is p6_blob_pread, a function of the registered bytes and the offset, so no cursor, handle or file of the host takes part and two sessions
 * with the same image answer the same bytes. A write goes to the overlay of the image (4 KiB chunks over the immutable base, p6_blob_hdf_write, T29b): the base is
 * never touched and nothing leaves the process until the caller exports.
 */
#include "sysconfig.h"
#include "sysdeps.h"
#include "options.h"
#include "filesys.h"
#include "blob_io.h"

#include <string.h>

namespace {

enum { kWriteProtected = 28 };

// A hard file handle is only a mark that the unit is open: the data is found by the blob id the unit was configured with (ci.rootdir).
struct Mark {
    int open;
};

}  // namespace

struct hardfilehandle {
    Mark mark;
};

int hdf_init_target(void)
{
    return 1;
}

int hdf_open_target(struct hardfiledata *hfd, const TCHAR *name)
{
    uint64_t bytes = 0;
    uint32_t kind = 0;
    hfd->flags = 0;
    hfd->drive_empty = 0;
    hfd->dangerous = 0;
    hfd->cache = NULL;
    hfd->cache_valid = 0;
    hfd->handle = NULL;
    hfd->handle_valid = 0;
    if (!name || p6_blob_size(name, &bytes, &kind) != P6_BLOB_OK || kind != P6_BLOB_HDF)
        return 0;
    hfd->handle = xcalloc(struct hardfilehandle, 1);
    if (!hfd->handle)
        return 0;
    hfd->handle->mark.open = 1;
    _tcscpy(hfd->vendor_id, _T("UAE"));
    _tcscpy(hfd->product_id, _T("HardDrive"));
    _tcscpy(hfd->product_rev, _T("0.4"));
    hfd->offset = 0;
    hfd->physsize = hfd->virtsize = bytes;
    hfd->handle_valid = 1;
    return 1;
}

int hdf_dup_target(struct hardfiledata *dhfd, const struct hardfiledata *shfd)
{
    if (!shfd->handle_valid)
        return 0;
    xfree(dhfd->handle);
    dhfd->handle = xcalloc(struct hardfilehandle, 1);
    if (!dhfd->handle)
        return 0;
    dhfd->handle->mark.open = 1;
    dhfd->handle_valid = shfd->handle_valid;
    return 1;
}

void hdf_close_target(struct hardfiledata *hfd)
{
    xfree(hfd->handle);
    hfd->handle = NULL;
    hfd->handle_valid = 0;
    hfd->cache = NULL;
    hfd->cache_valid = 0;
    hfd->drive_empty = 0;
    hfd->dangerous = 0;
}

int hdf_read_target(struct hardfiledata *hfd, void *buffer, uae_u64 offset, int len, uae_u32 *error)
{
    size_t got = 0;
    if (len <= 0 || !hfd->handle_valid || offset > hfd->physsize)
        return 0;
    offset += hfd->offset;
    if (p6_blob_hdf_read(hfd->ci.rootdir, offset, buffer, size_t(len), &got) != P6_BLOB_OK) {
        if (error)
            *error = 1;
        return 0;
    }
    return int(got);
}

int hdf_write_target(struct hardfiledata *hfd, void *buffer, uae_u64 offset, int len, uae_u32 *error)
{
    size_t done = 0;
    if (len <= 0 || !hfd->handle_valid || hfd->ci.readonly) {
        if (error)
            *error = kWriteProtected;
        return 0;
    }
    if (p6_blob_hdf_write(hfd->ci.rootdir, offset + hfd->offset, buffer, size_t(len), &done) != P6_BLOB_OK) {
        if (error)
            *error = 1;
        return 0;
    }
    return int(done);
}

int hdf_resize_target(struct hardfiledata *hfd, uae_u64 newsize)
{
    (void)hfd;
    (void)newsize;
    return 0;
}
