#include "sysdeps.h"
#include "machdep/maccess.h"
#include "gui.h"
#include "zfile.h"
#include "crc32.h"
#include "blob_io.h"
#include "civil_time.h"
#include "diagnostics.h"
#include <errno.h>

int main(int argc, char **)
{
 if (argc > 1) {
  uae_u8 bytes[8193];
  size_t length = fread(bytes, 1, sizeof bytes, stdin);
  assert(getc(stdin) == EOF && !ferror(stdin));
  uae_u8 digest[SHA1_SIZE]; get_sha1(bytes, (int)length, digest);
  const char *text = get_sha1_txt(bytes, (int)length);
  static const char hex[] = "0123456789ABCDEF";
  assert(strlen(text) == SHA1_SIZE * 2);
  for (int i = 0; i < SHA1_SIZE; ++i) {
   assert(text[i * 2] == hex[digest[i] >> 4]);
   assert(text[i * 2 + 1] == hex[digest[i] & 15]);
  }
  printf("%08X %04X %s\n", get_crc32(bytes, (int)length), get_crc16(bytes, (int)length), text);
  return 0;
 }
 const uae_u8 raw[] = {0x12,0x34,0x56,0x78,0xaa,0xbb,0xcc,0xdd};
 p6_diag_reset(); p6_blob_clear();
 assert(!p6_blob_register("fixture.rom", P6_BLOB_ROM, raw, sizeof raw));
 assert(!zfile_fopen("fixture.rom", "r+b") && errno == EROFS);
 p6_diag_write(P6_DIAG_ERROR, EROFS, 0, "Read-only: %s", "fixture.rom");
 zfile *asset = zfile_fopen("fixture.rom", "rb"); assert(asset);
 uae_u8 *ram = xcalloc(uae_u8, 16); assert(ram);
 assert(zfile_fread(ram + 1, 1, sizeof raw, asset) == sizeof raw);
 assert(do_get_mem_long(ram + 1) == 0x12345678 && do_get_mem_long(ram + 5) == 0xaabbccdd);
 do_put_mem_word(ram + 2, 0x9876);
 assert(ram[2] == 0x98 && ram[3] == 0x76 && !ram[0] && !ram[9]);
 zfile *temp = zfile_fopen_empty(asset, "temp.rom"); assert(temp);
 assert(zfile_fwrite(ram + 1, 1, sizeof raw, temp) == sizeof raw);
 xfree(ram);
 assert(zfile_crc32(asset) != zfile_crc32(temp));
 assert(zfile_ftell(asset) == sizeof raw && zfile_ftell(temp) == sizeof raw);
 p6_clock_state clock; p6_civil_time civil;
 assert(!p6_clock_init(&clock, P6_CIVIL_DEFAULT_EPOCH, 1000, 0));
 assert(!p6_clock_advance(&clock, 1999) && !p6_clock_read(&clock, 0, &civil));
 assert(civil.second == 1 && clock.subsecond == 999 && clock.seed == 1);
 gui_data.powerled = true; gui_data.powerled_brightness = 80;
 gui_disk_image_change(0, "fixture.rom", true);
 write_log("Civil second %d", civil.second);
 p6_gui_reset(); assert(gui_data.powerled && gui_data.powerled_brightness == 80);
 p6_diagnostic events[3];
 assert(p6_diag_read(events, 3) == 3 && events[0].code == EROFS);
 assert(events[1].kind == P6_DIAG_DISK_CHANGE && (events[1].flags & P6_DIAG_WRITE_PROTECTED));
 assert(events[2].kind == P6_DIAG_LOG && !strcmp(events[2].text, "Civil second 1"));
 assert(zfile_crc32(asset) == get_crc32((void *)raw, sizeof raw));
 zfile_fclose(asset); zfile_fclose(temp); zfile_exit(); p6_blob_clear(); p6_diag_reset();
 assert(!p6_diag_read(events, 3));
 return 0;
}
