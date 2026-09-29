#include "sysdeps.h"
#include "gui.h"
#include "diagnostics.h"
#include <stdarg.h>

/* Actual upstream layout, not a replacement UI struct. CIA owns powerled and
 * brightness; Paula reads powerled. Keep all fields in ordinary guest storage. */
struct gui_info gui_data = {};
unsigned int gui_ledstate;
bool no_gui = true, quit_to_gui = false;
static uint32_t button_state[4];
static int32_t axis_state[4][4];
static int activity_countdown[4];

static uae_s8 *activity_state(int index)
{
 uae_s8 *fields[] = { &gui_data.hd, &gui_data.cd, &gui_data.md, &gui_data.net };
 return fields[index];
}

int32_t p6_gui_read(p6_gui_observation *out)
{
 if (!out) return -1;
 out->led_mask = gui_ledstate;
 memcpy(out->buttons, button_state, sizeof button_state);
 memcpy(out->axes, axis_state, sizeof axis_state);
 for (int i = 0; i < 4; ++i) out->activity[i] = *activity_state(i);
 return 0;
}

void p6_gui_reset(void)
{
 memset(button_state, 0, sizeof button_state);
 memset(axis_state, 0, sizeof axis_state);
 memset(activity_countdown, 0, sizeof activity_countdown);
 gui_ledstate &= 1u << LED_POWER;
 for (int i = 0; i < 4; ++i) if (*activity_state(i) >= 0) *activity_state(i) = 0;
}

static void invalid_indicator(const char *kind, int index)
{
 p6_diag_write(P6_DIAG_ERROR, index, P6_DIAG_UNSUPPORTED, "Invalid %s indicator: %d", kind, index);
}

void gui_filename(int drive, const TCHAR *name)
{
 if (drive < 0 || drive >= 4) { invalid_indicator("drive", drive); return; }
 p6_diag_write(P6_DIAG_FILENAME, drive, 0, "%s", name ? name : "");
}

void gui_disk_image_change(int drive, const TCHAR *name, bool writeprotected)
{
 if (drive < 0 || drive >= 4) { invalid_indicator("drive", drive); return; }
 p6_diag_write(P6_DIAG_DISK_CHANGE, drive, writeprotected ? P6_DIAG_WRITE_PROTECTED : 0,
               "%s", name ? name : "");
}

void gui_gameport_button_change(int port, int button, int onoff)
{
 if (port < 0 || port >= 4) { invalid_indicator("port", port); return; }
 if (button < 0 || button > 10) { invalid_indicator("button", button); return; }
 uint32_t bit = 1u << button;
 if (onoff) button_state[port] |= bit;
 else button_state[port] &= ~bit;
}

void gui_gameport_axis_change(int port, int axis, int state, int maximum)
{
 if (port < 0 || port >= 4) { invalid_indicator("port", port); return; }
 if (axis < 0 || axis >= 4) { invalid_indicator("axis", axis); return; }
 int32_t *value = &axis_state[port][axis];
 if (maximum < 0) { if (*value > 0) --*value; }
 else if (maximum == 0) *value = state ? 100 : 0;
 else {
  if (state > maximum) state = maximum;
  if (state < 0) state = 0;
  *value = (int32_t)((int64_t)state * 127 / maximum);
 }
}

/* Same call-count transition as win32gui.cpp::gui_flicker_led2, without UI or
 * RETROPLATFORM callbacks. The selected profile has neither HD/CD nor network;
 * drawing still ticks this presentation state once per vertical boundary. */
static void flicker(int index, int status)
{
 const int leds[] = { LED_HD, LED_CD, LED_MD, LED_NET };
 int led = leds[index];
 uae_s8 *value = activity_state(index);
 int old = *value;
 if (status < 0) { gui_led(led, old < 0 ? -1 : 0, -1); return; }
 if (status > 127) { invalid_indicator("activity", status); return; }
 if (status == 0 && old < 0) {
  *value = 0; activity_countdown[index] = 0; gui_led(led, 0, -1); return;
 }
 if (status == 0 && --activity_countdown[index] > 0) return;
 *value = (uae_s8)status;
 activity_countdown[index] = 15;
 if (old != *value) gui_led(led, *value, -1);
}

void gui_flicker_led(int led, int unit, int status)
{
 (void)unit; /* No host device activity callbacks. */
 if (led < 0) {
  flicker(0, 0); flicker(1, 0);
  if (gui_data.net >= 0) flicker(3, 0);
  if (gui_data.md >= 0) flicker(2, 0);
 } else if (led == LED_HD) flicker(0, status);
 else if (led == LED_CD) flicker(1, status);
 else if (led == LED_MD) flicker(2, status);
 else if (led == LED_NET) flicker(3, status);
}

void gui_led(int led, int on, int brightness)
{
 (void)brightness;
 if (led < 0 || led >= LED_MAX) return;
 unsigned bit = 1u << led;
 if (on > 0) gui_ledstate |= bit;
 else if (on == 0) gui_ledstate &= ~bit;
 /* Presentation only: never overwrite CIA's causal state or call the host. */
}

void gui_fps(int fps, int lines, bool lace, int idle, int color)
{
 gui_data.fps = fps; gui_data.lines = lines; gui_data.lace = lace;
 gui_data.idle = idle; gui_data.fps_color = color;
}

void notify_user(int message)
{
 p6_diag_write(P6_DIAG_NOTIFY, message, 0, "WinUAE notification %d", message);
}

void notify_user_parms(int message, const TCHAR *parms, ...)
{
 (void)parms; /* Upstream uses a resource format, NOT this fixed argument. */
 va_list args; va_start(args, parms);
 if (message == NUMSG_ROMNEED) {
  /* The sole caller in the selected 6030 sources supplies one TCHAR*. */
  const TCHAR *rom = va_arg(args, const TCHAR *);
  p6_diag_write(P6_DIAG_NOTIFY, message, 0, "Required ROM: %s", rom ? rom : "");
 } else {
  p6_diag_write(P6_DIAG_NOTIFY, message, P6_DIAG_UNSUPPORTED,
                "Unsupported notification parameters: %d", message);
 }
 va_end(args);
}
