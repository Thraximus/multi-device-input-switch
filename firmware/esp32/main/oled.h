// Optional SSD1306 OLED (128x64, I2C): shows the picture the hub sends (MSG_DISPLAY), or "HUB OFFLINE" when
// the pictures stop. Without a display connected everything here is a no-op.
#pragma once

#include <stdint.h>

// Find the display and start its task. Safe to call without a display attached.
void oled_start(void);

// One chunk of the picture (SSD1306 page layout, DISPLAY_CHUNK bytes each); the last chunk shows the picture.
void oled_chunk(uint8_t index, const uint8_t *data, uint8_t len);
