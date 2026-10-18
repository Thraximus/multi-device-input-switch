#pragma once

#ifndef CFG_TUSB_MCU
#error CFG_TUSB_MCU must be defined
#endif

#define CFG_TUSB_RHPORT0_MODE  OPT_MODE_DEVICE
#define CFG_TUD_ENABLED        1
#define CFG_TUD_ENDPOINT0_SIZE 64

// Keyboard, mouse, consumer control
#define CFG_TUD_HID            3
#define CFG_TUD_CDC            0
#define CFG_TUD_MSC            0
#define CFG_TUD_MIDI           0
#define CFG_TUD_VENDOR         0

#define CFG_TUD_HID_EP_BUFSIZE 16
