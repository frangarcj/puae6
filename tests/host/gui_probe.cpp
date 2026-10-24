#include "sysdeps.h"
#include "gui.h"
#include "diagnostics.h"
#include <limits.h>

// P6_FLICKER_REFERENCE

static p6_gui_observation observe(void)
{
 p6_gui_observation view; assert(p6_gui_read(&view) == 0); return view;
}

int main(void)
{
 logging_init(); p6_gui_reset(); assert(p6_gui_read(NULL) == -1);
 gui_data.powerled = true; gui_data.powerled_brightness = 129;
 gui_data.drives[0].drive_track = 43; gui_data.drives[0].floppy_inserted = true;
 strcpy(gui_data.drives[0].df, "engine-owned");
 unsigned char causal[sizeof gui_data]; memcpy(causal, &gui_data, sizeof causal);
 char name[] = "ADF 100% %n"; gui_filename(0, name); name[0] = 'X';
 gui_disk_image_change(3, "disk.adf", true);
 gui_disk_image_change(3, NULL, false);
 char long_name[257]; memset(long_name, 'a', 256); long_name[256] = 0;
 gui_filename(1, long_name);
 p6_diagnostic events[4]; assert(p6_diag_read(events, 4) == 4);
 assert(events[0].kind == P6_DIAG_FILENAME && events[0].code == 0);
 assert(strcmp(events[0].text, "ADF 100% %n") == 0);
 assert(events[1].kind == P6_DIAG_DISK_CHANGE && events[1].code == 3);
 assert(events[1].flags == P6_DIAG_WRITE_PROTECTED);
 assert(events[2].flags == 0 && events[2].length == 0);
 assert(events[3].length == 255 && events[3].flags == P6_DIAG_TRUNCATED);
 assert(memcmp(causal, &gui_data, sizeof causal) == 0);

 for (int port = 0; port < 4; ++port) {
  for (int button = 0; button <= 10; ++button) gui_gameport_button_change(port, button, 1);
  assert(observe().buttons[port] == 2047);
  gui_gameport_button_change(port, 10, 0); assert(observe().buttons[port] == 1023);
  for (int axis = 0; axis < 4; ++axis) {
   gui_gameport_axis_change(port, axis, 16, 32); assert(observe().axes[port][axis] == 63);
   gui_gameport_axis_change(port, axis, INT_MAX, INT_MAX); assert(observe().axes[port][axis] == 127);
   gui_gameport_axis_change(port, axis, INT_MIN, 5); assert(observe().axes[port][axis] == 0);
   gui_gameport_axis_change(port, axis, -1, 0); assert(observe().axes[port][axis] == 100);
   gui_gameport_axis_change(port, axis, 0, -1); assert(observe().axes[port][axis] == 99);
   for (int n = 0; n < 150; ++n) gui_gameport_axis_change(port, axis, INT_MAX, -1);
   assert(observe().axes[port][axis] == 0);
  }
 }
 p6_gui_observation before = observe();
 gui_gameport_button_change(-1, 0, 1); gui_gameport_button_change(4, 0, 1);
 gui_gameport_button_change(0, INT_MAX, 1); gui_gameport_axis_change(0, 4, 1, 0);
 p6_gui_observation after = observe(); assert(memcmp(&before, &after, sizeof before) == 0);
 assert(p6_diag_read(events, 4) == 4);
 for (const auto &event : events) assert(event.flags == P6_DIAG_UNSUPPORTED);
 assert(events[2].code == INT_MAX && events[3].code == 4);
 gui_filename(-1, name); gui_disk_image_change(4, name, false);
 assert(p6_diag_read(events, 4) == 2);
 assert(memcmp(causal, &gui_data, sizeof causal) == 0);

 /* Compile the actual pinned upstream transition as an independent oracle. */
 p6_gui_reset();
 const int leds[] = { -1, LED_HD, LED_CD, LED_MD, LED_NET };
 const int statuses[] = { -1, 0, 0, 0, 1, 2, 4, 7 };
 const unsigned mask = (1u << LED_HD) | (1u << LED_CD) | (1u << LED_MD) | (1u << LED_NET);
 uint32_t rng = 17;
 for (int n = 0; n < 2000; ++n) {
  rng = rng * 1664525u + 1013904223u;
  int led = leds[(rng >> 16) % 5], status = statuses[(rng >> 8) % 8];
  gui_flicker_led(led, 0, status); reference::gui_flicker_led(led, 0, status);
  assert(gui_data.hd == reference::gui_data.hd && gui_data.cd == reference::gui_data.cd);
  assert(gui_data.md == reference::gui_data.md && gui_data.net == reference::gui_data.net);
  assert((observe().led_mask & mask) == reference::led_mask);
 }
 p6_gui_reset(); gui_flicker_led(LED_HD, 0, 2);
 for (int i = 0; i < 14; ++i) { gui_flicker_led(-1, 0, 0); assert(gui_data.hd == 2); }
 gui_flicker_led(-1, 0, 0); assert(gui_data.hd == 0);
 gui_data.md = -1; gui_data.net = -1; gui_flicker_led(-1, 0, 0);
 assert(gui_data.md == -1 && gui_data.net == -1);
 gui_flicker_led(LED_MD, 0, 0); assert(gui_data.md == 0);
 gui_flicker_led(LED_HD, 0, INT_MAX); assert(gui_data.hd == 0);
 assert(p6_diag_read(events, 4) == 1 && events[0].flags == P6_DIAG_UNSUPPORTED);
 gui_data.md = -1;
 gui_gameport_axis_change(3, 3, 1, 0); gui_led(LED_POWER, 1, 129);
 gui_filename(0, "pending"); p6_gui_reset();
 after = observe(); assert(after.led_mask == (1u << LED_POWER));
 for (int i = 0; i < 4; ++i) {
  assert(after.buttons[i] == 0);
  for (int j = 0; j < 4; ++j) assert(after.axes[i][j] == 0);
 }
 assert(after.activity[0] == 0 && after.activity[2] == -1 && after.activity[3] == -1);
 assert(gui_data.powerled && gui_data.powerled_brightness == 129);
 assert(gui_data.drives[0].drive_track == 43 && gui_data.drives[0].floppy_inserted);
 assert(strcmp(gui_data.drives[0].df, "engine-owned") == 0);
 assert(p6_diag_read(events, 4) == 1 && strcmp(events[0].text, "pending") == 0);
 return 0;
}
