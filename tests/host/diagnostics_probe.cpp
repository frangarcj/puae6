#include "sysdeps.h"
#include "gui.h"
#include "diagnostics.h"
#include <errno.h>
#include <limits.h>

static p6_diagnostic take(void)
{
 p6_diagnostic out;
 assert(p6_diag_read(&out, 1) == 1);
 assert(out.length < sizeof out.text && out.text[out.length] == 0);
 return out;
}

int main(void)
{
 static_assert(sizeof(p6_diagnostic) == 272, "diagnostic layout");
 logging_init();
 assert(p6_diag_read(NULL, 0) == 0 && p6_diag_read(NULL, 1) == -1);
 for (int i = 0; i < P6_DIAG_CAPACITY + 3; ++i) write_log("message %d", i);
 assert(p6_diag_dropped() == 3);
 assert(p6_diag_read(NULL, 1) == -1); /* Invalid reads consume nothing. */
 struct { p6_diagnostic out[4]; uint64_t guard; } batch = {};
 batch.guard = UINT64_C(0xabcdef0123456789);
 assert(p6_diag_read(batch.out, 4) == 4);
 assert(batch.guard == UINT64_C(0xabcdef0123456789));
 for (int i = 0; i < 4; ++i) {
  char expected[32]; snprintf(expected, sizeof expected, "message %d", i);
  assert(strcmp(batch.out[i].text, expected) == 0);
  assert(batch.out[i].kind == P6_DIAG_LOG && batch.out[i].flags == 0);
  write_log("message %d", i + P6_DIAG_CAPACITY);
 }
 p6_diagnostic all[P6_DIAG_CAPACITY];
 assert(p6_diag_read(all, SIZE_MAX) == P6_DIAG_CAPACITY);
 for (int i = 0; i < P6_DIAG_CAPACITY; ++i) {
  char expected[32]; snprintf(expected, sizeof expected, "message %d", i + 4);
  assert(strcmp(all[i].text, expected) == 0);
 }
 assert(p6_diag_read(all, 1) == 0 && p6_diag_dropped() == 3);
 p6_diag_reset(); assert(p6_diag_dropped() == 0);

 char text[P6_DIAG_TEXT_CAPACITY + 1]; memset(text, 'x', sizeof text);
 text[255] = 0; console_out(text);
 p6_diagnostic r = take(); assert(r.length == 255 && r.flags == 0);
 text[255] = 'x'; text[256] = 0; console_out(text);
 r = take(); assert(r.length == 255 && r.flags == P6_DIAG_TRUNCATED);
 console_out("100% %n %s"); /* Content is not a format. */
 assert(strcmp(take().text, "100% %n %s") == 0);
 p6_diag_write(P6_DIAG_ERROR, -7, 0, NULL);
 r = take(); assert(r.flags == P6_DIAG_FORMAT_ERROR && r.length == 0 && r.code == -7);
 char owned[] = "copied"; write_log("%s", owned); owned[0] = 'X';
 assert(strcmp(take().text, "copied") == 0);
 errno = ERANGE; write_logx("%d", 12); assert(errno == ERANGE);
 assert(strcmp(take().text, "12") == 0);
 write_dlog("debug %d", 3); assert(strcmp(take().text, "debug 3") == 0);
 console_out_f("console %d", 4); assert(strcmp(take().text, "console 4") == 0);
 gui_message("error %d", 5); assert(take().kind == P6_DIAG_ERROR);
 assert(gui_message_multibutton(1, "confirm %s", "exit") == -1);
 r = take(); assert(r.flags == P6_DIAG_UNSUPPORTED && r.code == 1);
 notify_user(NUMSG_NOMEMORY); r = take();
 assert(r.kind == P6_DIAG_NOTIFY && r.code == NUMSG_NOMEMORY);
 notify_user_parms(NUMSG_ROMNEED, "%n invalid fixed argument", "A500 100% ROM");
 r = take(); assert(strcmp(r.text, "Required ROM: A500 100% ROM") == 0);
 notify_user_parms(INT_MAX, "unsupported");
 r = take(); assert(r.code == INT_MAX && r.flags == P6_DIAG_UNSUPPORTED);

 /* Model the actual CIA calling order: write causal fields, then notify UI.
  * Queue overflow, reading, init/reset and presentation never mutate them. */
 gui_data.powerled = true; gui_data.powerled_brightness = 123;
 gui_data.drives[0].drive_track = 39;
 unsigned char before[sizeof gui_data]; memcpy(before, &gui_data, sizeof before);
 for (int i = 0; i < 100; ++i) { gui_led(LED_POWER, i & 1, i); write_log("full"); }
 gui_led(-1, 1, 255); gui_led(INT_MAX, 1, 255);
 gui_led(LED_DF3, 1, 255); assert(gui_ledstate & (1u << LED_DF3));
 gui_led(LED_DF3, -1, -1); assert(gui_ledstate & (1u << LED_DF3));
 gui_led(LED_DF3, 0, -1); assert(!(gui_ledstate & (1u << LED_DF3)));
 assert(p6_diag_read(all, P6_DIAG_CAPACITY) == P6_DIAG_CAPACITY);
 logging_init(); assert(p6_diag_dropped() == 0 && p6_diag_read(all, 1) == 0);
 assert(memcmp(before, &gui_data, sizeof before) == 0);
 gui_fps(500, 312, true, 90, 2);
 assert(gui_data.fps == 500 && gui_data.lines == 312 && gui_data.lace == 1);
 assert(gui_data.idle == 90 && gui_data.fps_color == 2 && gui_data.powerled);
 return 0;
}
