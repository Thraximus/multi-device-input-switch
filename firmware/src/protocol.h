// Serial link protocol shared with hub/protocol.py.
//
// Frame: 0xA5, type, len, payload[len], crc8(type, len, payload)
// crc8: polynomial 0x07, init 0x00.
#pragma once

#include <stdint.h>

#define FRAME_SOF        0xA5
#define FRAME_MAX_PAYLOAD 64

// Hub -> Pico
#define MSG_MOUSE        0x01  // u8 buttons, i16 dx, i16 dy, i8 wheel, i8 pan (little endian)
#define MSG_KEYBOARD     0x02  // u8 modifiers, u8 reserved, u8 keys[6]  (HID boot report)
#define MSG_CONSUMER     0x03  // u16 usage (0 = released)
#define MSG_RELEASE_ALL  0x04  // no payload
#define MSG_PING         0x05  // no payload; also serves as heartbeat
#define MSG_BOOTLOADER   0x06  // no payload; reboot into the USB (BOOTSEL) bootloader for flashing
#define MSG_DEBUG_REQ    0x07  // no payload; Pico answers with MSG_DEBUG
#define MSG_GAMEPAD      0x0B  // u16 buttons (XInput layout), u8 lt, u8 rt, i16 lx, ly, rx, ry (XInput: Y up)

// Pico -> Hub
#define MSG_KBD_LEDS     0x81  // u8 HID LED bitmap
#define MSG_STATUS       0x82  // u8 flags (STATUS_*), u8 LED bitmap
#define MSG_DEBUG        0x83  // u8 hid ready bits, then u16 counters (see send_debug in main.c)
#define MSG_RUMBLE       0x86  // u8 strong (large motor), u8 weak (small motor), 0-255
#define STATUS_XINPUT    0x04  // Pico status flag: the host has opened the XInput interface

#define STATUS_MOUNTED   0x01
#define STATUS_SUSPENDED 0x02

// Channels (see hub/protocol.py): the ESP32 router forwards channel 0 to the Pico and handles
// channel 0x40 (same message types + 0x40) itself as a Bluetooth LE keyboard/mouse.
#define CHANNEL_MASK     0x40
#define CHANNEL_BLE      0x40

// Bluetooth channel only
#define MSG_BLE_PAIR     0x08  // u8 seconds: accept a new bond for this long (0 = stop)
#define MSG_BLE_FORGET   0x09  // 6-byte address: forget that host; empty: forget all
#define MSG_BLE_SELECT   0x0A  // 6-byte address of the host that receives input (all zero = none)
#define MSG_BLE_PASSKEY  0x84  // u32 passkey shown on the phone during pairing (reply)
#define MSG_BLE_HOSTS    0x85  // reply: n x (6-byte address, u16 connection interval in 1.25 ms units)
// On the Bluetooth channel MSG_KBD_LEDS carries the host address first: 6-byte address, u8 LEDs.
#define STATUS_PAIRING   0x04  // Bluetooth: the pairing window is open

// Release everything if the hub has been silent this long. The hub pings every 100 ms.
#define HEARTBEAT_TIMEOUT_MS 500

static inline uint8_t crc8_update(uint8_t crc, uint8_t byte) {
    crc ^= byte;
    for (int i = 0; i < 8; i++)
        crc = (crc & 0x80) ? (uint8_t)((crc << 1) ^ 0x07) : (uint8_t)(crc << 1);
    return crc;
}
