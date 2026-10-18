#pragma once

#include <stdint.h>

// HID instance numbers (= interface order)
enum { HID_KBD = 0, HID_MOUSE, HID_CONSUMER };

typedef struct __attribute__((packed)) {
    uint8_t buttons;
    int16_t x;
    int16_t y;
    int8_t wheel;
    int8_t pan;
} mouse_report_t;
