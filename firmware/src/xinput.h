// Wired Xbox 360 controller (XInput) interface for the composite device.
#pragma once

#include <stdbool.h>
#include <stdint.h>

// 20-byte input report of a wired Xbox 360 controller.
typedef struct __attribute__((packed)) {
    uint8_t report_id;    // 0x00
    uint8_t report_size;  // 0x14
    uint16_t buttons;     // XInput wButtons bit layout (dpad, start, back, thumbs, shoulders, guide, A/B/X/Y)
    uint8_t lt, rt;
    int16_t lx, ly, rx, ry;  // XInput orientation: Y up-positive
    uint8_t reserved[6];
} xinput_report_t;

// Interface descriptor bytes for the configuration descriptor (interface + vendor-specific descriptor
// + IN/OUT interrupt endpoints), as on a real controller.
#define XINPUT_DESC_LEN (9 + 17 + 7 + 7)
#define XINPUT_DESCRIPTOR(itf, ep_in, ep_out) \
    9, TUSB_DESC_INTERFACE, itf, 0, 2, 0xFF, 0x5D, 0x01, 0, \
    17, 0x21, 0x00, 0x01, 0x01, 0x25, ep_in, 0x14, 0x00, 0x00, 0x00, 0x00, 0x13, ep_out, 0x08, 0x00, 0x00, \
    7, TUSB_DESC_ENDPOINT, ep_in, TUSB_XFER_INTERRUPT, 0x20, 0x00, 1, \
    7, TUSB_DESC_ENDPOINT, ep_out, TUSB_XFER_INTERRUPT, 0x20, 0x00, 8

// Queue a new controller state; sent when the IN endpoint is free. Returns false if the host hasn't
// opened the interface (no Xbox driver bound).
bool xinput_set_state(const xinput_report_t *report);
bool xinput_ready(void);
void xinput_task(void);

// Called by the driver when the host sends rumble (motor speeds 0-255).
void xinput_rumble_cb(uint8_t strong, uint8_t weak);
