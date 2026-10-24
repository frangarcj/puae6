#ifndef BIZHAWK_CD_H
#define BIZHAWK_CD_H

#include <stdint.h>

#define BIZHAWK_CD_MAX_TRACKS 100

typedef struct
{
	int32_t start;
	int32_t end;
	int32_t mode;
	int32_t adr;
	int32_t control;
} BizhawkCDTrack;

typedef struct
{
	int32_t first_track;
	int32_t last_track;
	int32_t leadout;
	BizhawkCDTrack tracks[BIZHAWK_CD_MAX_TRACKS];
} BizhawkCDTOC;

typedef void (*BizhawkCDTOCCallback)(int32_t disk, BizhawkCDTOC *toc);
typedef int32_t (*BizhawkCDSectorCallback)(int32_t disk, int32_t lba, uint8_t *dest, int32_t sector_size);

void bizhawk_cd_set_callbacks(BizhawkCDTOCCallback toc_callback, BizhawkCDSectorCallback sector_callback);
int bizhawk_cd_get_toc(int32_t disk, BizhawkCDTOC *toc);
int bizhawk_cd_read_sector(int32_t disk, int32_t lba, uint8_t *dest, int32_t sector_size);

#endif