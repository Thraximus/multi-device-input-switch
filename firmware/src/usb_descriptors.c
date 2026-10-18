#include <string.h>

#include "pico/unique_id.h"
#include "tusb.h"
#include "usb_descriptors.h"
#include "xinput.h"

// USB identity from firmware/settings.cmake (the repo's .env). Default: the pid.codes test VID/PID, for
// private use.
#ifndef USB_VID
#define USB_VID 0x1209
#endif
#ifndef USB_PID
#define USB_PID 0x0001
#endif
#ifndef USB_MANUFACTURER
#define USB_MANUFACTURER "pi-switcher-hub"
#endif
#ifndef USB_PRODUCT
#define USB_PRODUCT "Switcher Hub"
#endif

static tusb_desc_device_t const desc_device = {
    .bLength = sizeof(tusb_desc_device_t),
    .bDescriptorType = TUSB_DESC_DEVICE,
    .bcdUSB = 0x0200,
    .bDeviceClass = 0x00,
    .bDeviceSubClass = 0x00,
    .bDeviceProtocol = 0x00,
    .bMaxPacketSize0 = CFG_TUD_ENDPOINT0_SIZE,
    .idVendor = USB_VID,
    .idProduct = USB_PID,
    // Windows asks for the MS OS descriptor only the first time it sees a VID/PID/version; bumped when
    // the XInput interface was added so it asks again.
    .bcdDevice = 0x0200,
    .iManufacturer = 1,
    .iProduct = 2,
    .iSerialNumber = 3,
    .bNumConfigurations = 1,
};

uint8_t const *tud_descriptor_device_cb(void) {
    return (uint8_t const *)&desc_device;
}

//--------------------------------------------------------------------+
// HID report descriptors
//--------------------------------------------------------------------+

static uint8_t const desc_hid_kbd[] = {TUD_HID_REPORT_DESC_KEYBOARD()};

// 5 buttons, 16-bit relative X/Y (no chunking of fast movement), wheel, horizontal pan.
static uint8_t const desc_hid_mouse[] = {
    HID_USAGE_PAGE(HID_USAGE_PAGE_DESKTOP),
    HID_USAGE(HID_USAGE_DESKTOP_MOUSE),
    HID_COLLECTION(HID_COLLECTION_APPLICATION),
      HID_USAGE(HID_USAGE_DESKTOP_POINTER),
      HID_COLLECTION(HID_COLLECTION_PHYSICAL),
        HID_USAGE_PAGE(HID_USAGE_PAGE_BUTTON),
        HID_USAGE_MIN(1), HID_USAGE_MAX(5),
        HID_LOGICAL_MIN(0), HID_LOGICAL_MAX(1),
        HID_REPORT_COUNT(5), HID_REPORT_SIZE(1),
        HID_INPUT(HID_DATA | HID_VARIABLE | HID_ABSOLUTE),
        HID_REPORT_COUNT(1), HID_REPORT_SIZE(3),
        HID_INPUT(HID_CONSTANT),

        HID_USAGE_PAGE(HID_USAGE_PAGE_DESKTOP),
        HID_USAGE(HID_USAGE_DESKTOP_X), HID_USAGE(HID_USAGE_DESKTOP_Y),
        HID_LOGICAL_MIN_N(-32767, 2), HID_LOGICAL_MAX_N(32767, 2),
        HID_REPORT_COUNT(2), HID_REPORT_SIZE(16),
        HID_INPUT(HID_DATA | HID_VARIABLE | HID_RELATIVE),

        HID_USAGE(HID_USAGE_DESKTOP_WHEEL),
        HID_LOGICAL_MIN(0x81), HID_LOGICAL_MAX(0x7f),
        HID_REPORT_COUNT(1), HID_REPORT_SIZE(8),
        HID_INPUT(HID_DATA | HID_VARIABLE | HID_RELATIVE),

        HID_USAGE_PAGE(HID_USAGE_PAGE_CONSUMER),
        HID_USAGE_N(HID_USAGE_CONSUMER_AC_PAN, 2),
        HID_LOGICAL_MIN(0x81), HID_LOGICAL_MAX(0x7f),
        HID_REPORT_COUNT(1), HID_REPORT_SIZE(8),
        HID_INPUT(HID_DATA | HID_VARIABLE | HID_RELATIVE),
      HID_COLLECTION_END,
    HID_COLLECTION_END,
};

static uint8_t const desc_hid_consumer[] = {TUD_HID_REPORT_DESC_CONSUMER()};

uint8_t const *tud_hid_descriptor_report_cb(uint8_t instance) {
    switch (instance) {
    case HID_KBD: return desc_hid_kbd;
    case HID_MOUSE: return desc_hid_mouse;
    default: return desc_hid_consumer;
    }
}

//--------------------------------------------------------------------+
// Configuration descriptor
//--------------------------------------------------------------------+

enum { ITF_KBD = 0, ITF_MOUSE, ITF_CONSUMER, ITF_XINPUT, ITF_NUM_TOTAL };

#define EPNUM_KBD      0x81
#define EPNUM_MOUSE    0x82
#define EPNUM_CONSUMER 0x83
#define EPNUM_XINPUT_IN  0x84
#define EPNUM_XINPUT_OUT 0x04

#define CONFIG_TOTAL_LEN (TUD_CONFIG_DESC_LEN + 3 * TUD_HID_DESC_LEN + XINPUT_DESC_LEN)

// Poll interval 1 ms = 1000 Hz.
static uint8_t const desc_configuration[] = {
    TUD_CONFIG_DESCRIPTOR(1, ITF_NUM_TOTAL, 0, CONFIG_TOTAL_LEN, TUSB_DESC_CONFIG_ATT_REMOTE_WAKEUP, 100),
    TUD_HID_DESCRIPTOR(ITF_KBD, 4, HID_ITF_PROTOCOL_KEYBOARD, sizeof(desc_hid_kbd), EPNUM_KBD, CFG_TUD_HID_EP_BUFSIZE, 1),
    TUD_HID_DESCRIPTOR(ITF_MOUSE, 5, HID_ITF_PROTOCOL_NONE, sizeof(desc_hid_mouse), EPNUM_MOUSE, CFG_TUD_HID_EP_BUFSIZE, 1),
    TUD_HID_DESCRIPTOR(ITF_CONSUMER, 6, HID_ITF_PROTOCOL_NONE, sizeof(desc_hid_consumer), EPNUM_CONSUMER, CFG_TUD_HID_EP_BUFSIZE, 1),
    XINPUT_DESCRIPTOR(ITF_XINPUT, EPNUM_XINPUT_IN, EPNUM_XINPUT_OUT),
};

//--------------------------------------------------------------------+
// Microsoft OS 1.0 descriptors: tell Windows to load its Xbox 360 driver (xusb22) for the XInput
// interface via the "XUSB10" compatible ID, so no driver install is needed.
//--------------------------------------------------------------------+

#define MS_OS_VENDOR_CODE 0x20

static uint8_t const ms_compat_id[] = {
    40, 0, 0, 0,             // dwLength = 16 + 24
    0x00, 0x01,              // bcdVersion 1.0
    0x04, 0x00,              // wIndex: extended compat ID
    1,                       // bCount
    0, 0, 0, 0, 0, 0, 0,     // reserved
    ITF_XINPUT, 0x01,        // interface, reserved
    'X', 'U', 'S', 'B', '1', '0', 0, 0,  // compatibleID
    0, 0, 0, 0, 0, 0, 0, 0,  // subCompatibleID
    0, 0, 0, 0, 0, 0,        // reserved
};

bool tud_vendor_control_xfer_cb(uint8_t rhport, uint8_t stage, tusb_control_request_t const *request) {
    if (stage != CONTROL_STAGE_SETUP) return true;
    if (request->bRequest == MS_OS_VENDOR_CODE && request->wIndex == 0x0004)
        return tud_control_xfer(rhport, request, (void *)(uintptr_t)ms_compat_id,
                                tu_min16(request->wLength, sizeof(ms_compat_id)));
    return false;  // stall anything else
}

uint8_t const *tud_descriptor_configuration_cb(uint8_t index) {
    (void)index;
    return desc_configuration;
}

//--------------------------------------------------------------------+
// Strings
//--------------------------------------------------------------------+

static char const *string_desc_arr[] = {
    NULL,               // 0: language (handled below)
    USB_MANUFACTURER,   // 1: manufacturer
    USB_PRODUCT,        // 2: product
    NULL,               // 3: serial (board unique id)
    "Switcher Keyboard",
    "Switcher Mouse",
    "Switcher Media Keys",
};
// (index 0xEE is the MS OS string, handled below)

static uint16_t desc_str[32 + 1];

uint16_t const *tud_descriptor_string_cb(uint8_t index, uint16_t langid) {
    (void)langid;
    size_t chr_count;

    if (index == 0) {
        desc_str[1] = 0x0409;  // English (US)
        chr_count = 1;
    } else if (index == 0xEE) {
        // MS OS string descriptor: "MSFT100" + vendor code (low byte) + pad.
        static char const sig[] = "MSFT100";
        for (int i = 0; i < 7; i++) desc_str[1 + i] = (uint16_t)sig[i];
        desc_str[8] = MS_OS_VENDOR_CODE;
        chr_count = 8;
    } else {
        char serial[2 * PICO_UNIQUE_BOARD_ID_SIZE_BYTES + 1];
        char const *str;
        if (index == 3) {
            pico_get_unique_board_id_string(serial, sizeof(serial));
            str = serial;
        } else if (index < sizeof(string_desc_arr) / sizeof(string_desc_arr[0])) {
            str = string_desc_arr[index];
        } else {
            return NULL;
        }
        chr_count = strlen(str);
        if (chr_count > 32) chr_count = 32;
        for (size_t i = 0; i < chr_count; i++) desc_str[1 + i] = str[i];
    }

    desc_str[0] = (uint16_t)((TUSB_DESC_STRING << 8) | (2 * chr_count + 2));
    return desc_str;
}
