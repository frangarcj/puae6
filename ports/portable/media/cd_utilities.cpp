/* UAE CD format utilities, ported verbatim from WinUAE 6.0.3
 * 857f48fcb09399d4d95527e29a2fdeaedb41f827: blkdev.cpp:61-101 and
 * blkdev_cdimage.cpp:280-306. Copyright 2009-2013 Toni Wilen.
 * SPDX-License-Identifier: GPL-2.0-or-later
 * The full host/device dispatchers remain excluded. */
#include "sysconfig.h"
#include "tchar.h"
struct TrapContext;
#include "blkdev.h"

int msf2lsn (int msf)
{
	int sector = (((msf >> 16) & 0xff) * 60 * 75 + ((msf >> 8) & 0xff) * 75 + ((msf >> 0) & 0xff));
	sector -= 150;
	return sector;
}
int lsn2msf (int sectors)
{
	int msf;
	sectors += 150;
	msf = (sectors / (75 * 60)) << 16;
	msf |= ((sectors / 75) % 60) << 8;
	msf |= (sectors % 75) << 0;
	return msf;
}
uae_u8 frombcd (uae_u8 v)
{
	return (v >> 4) * 10 + (v & 15);
}
uae_u8 tobcd (uae_u8 v)
{
	return ((v / 10) << 4) | (v % 10);
}
int fromlongbcd (uae_u8 *p)
{
	return (frombcd (p[0]) << 16) | (frombcd (p[1]) << 8) | (frombcd (p[2])  << 0);
}
void tolongbcd (uae_u8 *p, int v)
{
	p[0] = tobcd ((v >> 16) & 0xff);
	p[1] = tobcd ((v >> 8) & 0xff);
	p[2] = tobcd ((v >> 0) & 0xff);
}
void sub_to_interleaved (const uae_u8 *s, uae_u8 *d)
{
	for (int i = 0; i < 8 * SUB_ENTRY_SIZE; i ++) {
		int dmask = 0x80;
		int smask = 1 << (7 - (i & 7));
		(*d) = 0;
		for (int j = 0; j < 8; j++) {
			(*d) |= (s[(i / 8) + j * SUB_ENTRY_SIZE] & smask) ? dmask : 0;
			dmask >>= 1;
		}
		d++;
	}
}
void sub_to_deinterleaved (const uae_u8 *s, uae_u8 *d)
{
	for (int i = 0; i < 8 * SUB_ENTRY_SIZE; i ++) {
		int dmask = 0x80;
		int smask = 1 << (7 - (i / SUB_ENTRY_SIZE));
		(*d) = 0;
		for (int j = 0; j < 8; j++) {
			(*d) |= (s[(i % SUB_ENTRY_SIZE) * 8 + j] & smask) ? dmask : 0;
			dmask >>= 1;
		}
		d++;
	}
}
