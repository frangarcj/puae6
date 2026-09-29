#pragma once

#include <stdint.h>

// In-memory hardfile backend for the BizHawk Waterbox build.
//
// Hard disk images live in guest memory, so Waterbox savestates only carry the
// pages that changed. A per-chunk dirty bitmap (also in guest memory, so it is
// part of the state) records which chunks differ from the original image and is
// what the host uses to build a sparse SaveRAM diff.

#define BIZHAWK_HD_MAX 4
#define BIZHAWK_HD_CHUNK 4096
#define BIZHAWK_HD_NAME "HardDrive"

int bizhawk_hd_alloc(int index, int64_t size);
int64_t bizhawk_hd_size(int index);
uint8_t *bizhawk_hd_data(int index);
