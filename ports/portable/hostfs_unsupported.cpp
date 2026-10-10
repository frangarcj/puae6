/* Host services that filesys.cpp and hardfile.cpp name and the profile does not have (T29a).
 * SPDX-License-Identifier: GPL-2.0-or-later
 *
 * filesys.cpp is built whole: next to the hard file code (units, RDB, the UAEFS board) it holds the volumes that map a host directory, an archive or a CD
 * onto AmigaDOS. A session has no host file system, no archive volume, no CD or tape and no clipboard, so every service below FAILS the way a missing
 * file or a missing device fails (NULL, -1, false, 0); none of them reports success for something that is not there. Nothing reaches them: a
 * configuration with a directory, archive, CD or tape unit is refused before the machine exists (bridge/media.cpp only builds hard file units), and the
 * unit table the filesystem opens holds nothing else. T64 replaces the volume half with an in-memory file system behind the same names.
 */
#include "sysconfig.h"
#include "sysdeps.h"
#include "options.h"
#include "uae.h"
#include "zfile.h"
#include "fsdb.h"
#include "fsusage.h"
#include "isofs_api.h"
#include "scsi.h"
#include "scsidev.h"
#include "blkdev.h"
#include "gayle.h"
#include "autoconf.h"
#include "traps.h"
#include "clipboard.h"
#include "consolehook.h"
#include "inputdevice.h"

#define AMIGADOS_ERROR_NOT_IMPLEMENTED 236

/* --- host files and directories (fsdb.h) ------------------------------------------------------------------------------------------------------ */
void my_close(struct my_openfile_s *) {}
void my_closedir(struct my_opendir_s *) {}
bool my_createshortcut(const TCHAR *, const TCHAR *, const TCHAR *) { return false; }
int my_existsdir(const TCHAR *) { return 0; }
int my_existsfile(const TCHAR *) { return 0; }
uae_s64 my_fsize(struct my_openfile_s *) { return -1; }
int my_getvolumeinfo(const TCHAR *) { return -1; }
int my_issamevolume(const TCHAR *, const TCHAR *, TCHAR *) { return 0; }
uae_s64 my_lseek(struct my_openfile_s *, uae_s64, int) { return -1; }
int my_mkdir(const TCHAR *) { return -1; }
struct my_openfile_s *my_open(const TCHAR *, int) { return NULL; }
struct my_opendir_s *my_opendir(const TCHAR *) { return NULL; }
unsigned int my_read(struct my_openfile_s *, void *, unsigned int) { return 0; }
int my_readdir(struct my_opendir_s *, TCHAR *) { return 0; }
int my_rename(const TCHAR *, const TCHAR *) { return -1; }
bool my_resolvesoftlink(TCHAR *, int, bool) { return false; }
int my_rmdir(const TCHAR *) { return -1; }
bool my_stat(const TCHAR *, struct mystat *) { return false; }
int my_truncate(const TCHAR *, uae_u64) { return -1; }
int my_unlink(const TCHAR *, bool) { return -1; }
bool my_utime(const TCHAR *, struct mytimeval *) { return false; }
unsigned int my_write(struct my_openfile_s *, void *, unsigned int) { return 0; }
int get_fs_usage(const TCHAR *, const TCHAR *, struct fs_usage *) { return -1; }
int dos_errno(void) { return AMIGADOS_ERROR_NOT_IMPLEMENTED; }
uae_u32 getlocaltime(void) { return 0; }   /* no time zone: the clock of the machine is UTC (rtc_time.cpp) */
void cfgfile_resolve_path_load(TCHAR *, int, int) {}                                       /* a name is never a path here: it stays as it is */
void cfgfile_resolve_path_out_load(const TCHAR *path, TCHAR *out, int size, int)
{
    if (size > 0) {
        strncpy(out, path, size_t(size) - 1);
        out[size - 1] = 0;
    }
}
uae_u8 *zfile_load_file(const TCHAR *, int *outlen) { *outlen = 0; return NULL; }

/* The file-name database of a host directory (the .uaefsdb of upstream) and the names it keeps. */
void fsdb_clean_dir(a_inode *) {}
TCHAR *fsdb_create_unique_nname(a_inode *, const TCHAR *) { return NULL; }
void fsdb_dir_writeback(a_inode *) {}
int fsdb_exists(const TCHAR *) { return 0; }
int fsdb_fill_file_attrs(a_inode *, a_inode *) { return 0; }
a_inode *fsdb_lookup_aino_aname(a_inode *, const TCHAR *) { return NULL; }
a_inode *fsdb_lookup_aino_nname(a_inode *, const TCHAR *) { return NULL; }
int fsdb_mode_supported(const a_inode *) { return 0; }
int fsdb_name_invalid(a_inode *, const TCHAR *) { return 1; }
int fsdb_name_invalid_dir(a_inode *, const TCHAR *) { return 1; }
TCHAR *fsdb_search_dir(const TCHAR *, TCHAR *, TCHAR **) { return NULL; }
int fsdb_set_file_attrs(a_inode *) { return AMIGADOS_ERROR_NOT_IMPLEMENTED; }
int fsdb_used_as_nname(a_inode *, const TCHAR *) { return 0; }
int same_aname(const TCHAR *a, const TCHAR *b)   /* AmigaDOS names compare without regard to case */
{
    for (; *a && *b; ++a, ++b) {
        const int x = (*a >= 'A' && *a <= 'Z') ? *a + 32 : *a, y = (*b >= 'A' && *b <= 'Z') ? *b + 32 : *b;
        if (x != y) return 0;
    }
    return *a == *b;
}

/* --- archive volumes (zfile.h) ----------------------------------------------------------------------------------------------------------------- */
void zfile_close_archive(struct zfile *) {}
void zfile_closedir_archive(struct zdirectory *) {}
int zfile_exists_archive(const TCHAR *, const TCHAR *) { return 0; }
void zfile_fclose_archive(struct zvolume *) {}
int zfile_fill_file_attrs_archive(const TCHAR *, int *, int *, TCHAR **) { return 0; }
struct zvolume *zfile_fopen_archive(const TCHAR *) { return NULL; }
int zfile_fs_usage_archive(const TCHAR *, const TCHAR *, struct fs_usage *) { return -1; }
uae_s64 zfile_fsize_archive(struct zfile *) { return -1; }
uae_s64 zfile_lseek_archive(struct zfile *, uae_s64, int) { return -1; }
struct zfile *zfile_open_archive(const TCHAR *, int) { return NULL; }
struct zdirectory *zfile_opendir_archive(const TCHAR *) { return NULL; }
int zfile_read_archive(struct zfile *, void *, unsigned int) { return 0; }
int zfile_readdir_archive(struct zdirectory *, TCHAR *) { return 0; }
int zfile_stat_archive(const TCHAR *, struct mystat *) { return 0; }

/* --- CD file systems (isofs_api.h) ------------------------------------------------------------------------------------------------------------- */
void isofs_closedir(struct cd_opendir_s *) {}
void isofs_closefile(struct cd_openfile_s *) {}
void isofs_dispose_inode(void *, uae_u64) {}
bool isofs_exists(void *, uae_u64, const TCHAR *, uae_u64 *) { return false; }
uae_s64 isofs_fsize(struct cd_openfile_s *) { return -1; }
uae_s64 isofs_lseek(struct cd_openfile_s *, uae_s64, int) { return -1; }
bool isofs_mediainfo(void *, struct isofs_info *) { return false; }
void *isofs_mount(int, uae_u64 *) { return NULL; }
struct cd_opendir_s *isofs_opendir(void *, uae_u64) { return NULL; }
struct cd_openfile_s *isofs_openfile(void *, uae_u64, int) { return NULL; }
uae_s64 isofs_read(struct cd_openfile_s *, void *, unsigned int) { return -1; }
bool isofs_readdir(struct cd_opendir_s *, TCHAR *, uae_u64 *) { return false; }
bool isofs_stat(void *, uae_u64, struct mystat *) { return false; }
void isofs_unmount(void *) {}
void isofss_fill_file_attrs(void *, uae_u64, int *, int *, TCHAR **, uae_u64) {}

/* --- CD drives, tapes, Gayle and PCMCIA: none exists (a hard file is on the UAE controller, not on a bus) ------------------------------------------ */
struct scsi_data_tape *tape_alloc(int, const TCHAR *, bool) { return NULL; }
void tape_free(struct scsi_data_tape *) {}
bool tape_get_info(int, struct device_info *) { return false; }
int scsi_add_tape(struct uaedev_config_info *) { return -1; }
uae_u32 scsi_get_cd_drive_mask(void) { return 0; }
uae_u32 scsi_get_cd_drive_media_mask(void) { return 0; }
int sys_command_open(int) { return 0; }
void sys_command_close(int) {}
bool blkdev_get_info(struct uae_prefs *, int, struct device_info *) { return false; }
void gayle_free_units(void) {}
void pcmcia_reinsert(struct uae_prefs *) {}
bool pcmcia_disk_reinsert(struct uae_prefs *, struct uaedev_config_info *, bool) { return false; }
int target_get_volume_name(struct uaedev_mount_info *, struct uaedev_config_info *, bool, bool, int) { return 0; }
void filesys_addexternals(void) {}   /* upstream adds the drives and disc images the host has; there is no host */

/* --- guest-visible host integration: console hook, clipboard, mouse hack ------------------------------------------------------------------------- */
int consolehook_activate(void) { return 0; }
uaecptr consolehook_beginio(TrapContext *, uaecptr) { return 0; }
void consolehook_ret(TrapContext *, uaecptr, uaecptr) {}
void amiga_clipboard_die(TrapContext *) {}
void amiga_clipboard_got_data(TrapContext *, uaecptr, uae_u32, uae_u32) {}
void amiga_clipboard_init(TrapContext *) {}
uaecptr amiga_clipboard_proc_start(TrapContext *) { return 0; }
void amiga_clipboard_task_start(TrapContext *, uaecptr) {}
int amiga_clipboard_want_data(TrapContext *) { return 0; }
int input_mousehack_status(TrapContext *, int, uaecptr, uaecptr, uaecptr, uae_u32) { return 0; }
