// Minimal TinyUSB class driver for the Xbox 360 controller interface (class 0xFF/0x5D/0x01).
// Windows binds its built-in xusb22 driver through the MS OS "XUSB10" compatible ID (usb_descriptors.c).
#include <string.h>

#include "device/usbd_pvt.h"
#include "tusb.h"
#include "xinput.h"

static uint8_t ep_in, ep_out;
static bool opened;
static bool dirty;
static xinput_report_t current = {.report_id = 0x00, .report_size = 0x14};
CFG_TUSB_MEM_ALIGN static uint8_t in_buf[sizeof(xinput_report_t)];
CFG_TUSB_MEM_ALIGN static uint8_t out_buf[32];

bool xinput_ready(void) { return opened; }

bool xinput_set_state(const xinput_report_t *report) {
    if (memcmp(&current, report, sizeof(current)) != 0) {
        current = *report;
        current.report_id = 0x00;
        current.report_size = 0x14;
        dirty = true;
    }
    return opened;
}

void xinput_task(void) {
    if (!opened || !dirty || !tud_ready() || usbd_edpt_busy(0, ep_in)) return;
    if (!usbd_edpt_claim(0, ep_in)) return;
    memcpy(in_buf, &current, sizeof(current));
    dirty = false;
    usbd_edpt_xfer(0, ep_in, in_buf, sizeof(in_buf));
}

static void xinput_init(void) {}

static bool xinput_deinit(void) { return true; }

static void xinput_reset(uint8_t rhport) {
    (void)rhport;
    opened = false;
    dirty = true;  // resend the current state after re-enumeration
}

static uint16_t xinput_open(uint8_t rhport, tusb_desc_interface_t const *itf, uint16_t max_len) {
    if (itf->bInterfaceClass != 0xFF || itf->bInterfaceSubClass != 0x5D || itf->bInterfaceProtocol != 0x01) return 0;
    uint16_t len = (uint16_t)(sizeof(tusb_desc_interface_t));
    uint8_t const *p = tu_desc_next(itf);
    // Skip the vendor-specific 0x21 descriptor, then open both interrupt endpoints.
    while (len < max_len && tu_desc_type(p) != TUSB_DESC_ENDPOINT) {
        len += tu_desc_len(p);
        p = tu_desc_next(p);
    }
    for (int i = 0; i < 2 && len < max_len; i++) {
        tusb_desc_endpoint_t const *ep = (tusb_desc_endpoint_t const *)p;
        TU_ASSERT(usbd_edpt_open(rhport, ep), 0);
        if (tu_edpt_dir(ep->bEndpointAddress) == TUSB_DIR_IN) ep_in = ep->bEndpointAddress;
        else ep_out = ep->bEndpointAddress;
        len += tu_desc_len(p);
        p = tu_desc_next(p);
    }
    opened = true;
    dirty = true;
    usbd_edpt_xfer(rhport, ep_out, out_buf, sizeof(out_buf));
    return len;
}

static bool xinput_control_xfer_cb(uint8_t rhport, uint8_t stage, tusb_control_request_t const *request) {
    (void)rhport, (void)stage, (void)request;
    return false;  // no class requests; stall
}

static bool xinput_xfer_cb(uint8_t rhport, uint8_t ep_addr, xfer_result_t result, uint32_t len) {
    if (ep_addr == ep_out) {
        // Rumble: 00 08 00 <large motor> <small motor> 00 00 00. (01 03 xx sets the ring LED.)
        if (result == XFER_RESULT_SUCCESS && len >= 5 && out_buf[0] == 0x00 && out_buf[1] == 0x08)
            xinput_rumble_cb(out_buf[3], out_buf[4]);
        usbd_edpt_xfer(rhport, ep_out, out_buf, sizeof(out_buf));
    }
    return true;
}

static usbd_class_driver_t const xinput_driver = {
    .name = "XINPUT",
    .init = xinput_init,
    .deinit = xinput_deinit,
    .reset = xinput_reset,
    .open = xinput_open,
    .control_xfer_cb = xinput_control_xfer_cb,
    .xfer_cb = xinput_xfer_cb,
    .sof = NULL,
};

usbd_class_driver_t const *usbd_app_driver_get_cb(uint8_t *driver_count) {
    *driver_count = 1;
    return &xinput_driver;
}
