// Switcher Hub ESP32 router.
//
// UART0 (the DevKit's USB-serial chip) talks to the Linux hub. Frames on channel 0 are forwarded
// unchanged to the Pico on UART2 (and the Pico's replies back); frames on channel 0x40 drive this
// chip's own Bluetooth LE keyboard/mouse/consumer-control device.
//
// Several Bluetooth hosts (phone, tablet, ...) can be connected at once; the hub selects which one
// receives input, so switching between them is instant.
#include <string.h>

#include "driver/uart.h"
#include "esp_bt.h"
#include "esp_bt_main.h"
#include "esp_gap_ble_api.h"
#include "esp_gatts_api.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "freertos/task.h"
#include "tinycrypt/aes.h"
#include "nvs_flash.h"

#include "oled.h"
#include "protocol.h"

#define HUB_UART     UART_NUM_0
// UART0 must be routed explicitly: with the console disabled nothing else configures its pins.
#define HUB_TX_PIN   1
#define HUB_RX_PIN   3
#define PICO_UART    UART_NUM_2
// Wiring: GPIO16 (labelled RX2) -> Pico GP1, GPIO17 (TX2) <- Pico GP0. The DevKit labels don't matter;
// the UART matrix can put TX/RX on either pin.
// Pins and speed come from firmware/settings.cmake (the repo's .env); these are only the defaults.
#ifndef ESP32_PICO_TX_PIN
#define ESP32_PICO_TX_PIN 16
#endif
#ifndef ESP32_PICO_RX_PIN
#define ESP32_PICO_RX_PIN 17
#endif
#ifndef LINK_BAUD
#define LINK_BAUD 921600
#endif
#ifndef BLE_DEVICE_NAME
#define BLE_DEVICE_NAME "Switcher Hub"
#endif
#ifndef USB_VID
#define USB_VID 0x1209
#endif
#ifndef USB_PID
#define USB_PID 0x0001
#endif
#define PICO_TX_PIN  ESP32_PICO_TX_PIN
#define PICO_RX_PIN  ESP32_PICO_RX_PIN
#define UART_BUF     2048

#define MAX_HOSTS    4
// A connection that hasn't been encrypted (bonded) after this long is dropped, so strangers can't
// hold connection slots.
#define UNPAIRED_TIMEOUT_US (10 * 1000000LL)
// While pairing is off nobody should be bonding: a bonded host re-encrypts within a moment, so anything that
// hasn't been encrypted this soon is a stranger.
#define STRANGER_TIMEOUT_US (3 * 1000000LL)
// A new host's pairing code must be approved on the hub within this time, and the phone's own prompt gets
// another 30 s after that.
#define CONFIRM_TIMEOUT_US  (30 * 1000000LL)

#define RID_KEYBOARD 1
#define RID_MOUSE    2
#define RID_CONSUMER 3

// Mouse motion is coalesced while one report is in flight and sent the moment the stack confirms the
// previous one (ESP_GATTS_CONF_EVT), like Espressif's esp_hidd does. That fills every connection event
// with as many reports as the radio takes, each covering the real time since the last, without ever
// queueing reports inside the stack (which shows up as input lag).
#define MOUSE_TICK_MS 1
// Safety net if a confirmation never arrives (e.g. the link dropped mid-send).
#define IN_FLIGHT_TIMEOUT_US 50000

static const char DEVICE_NAME[] = BLE_DEVICE_NAME;

//--------------------------------------------------------------------+
// HID report map: keyboard (ID 1, with LED output), mouse (ID 2, 16-bit X/Y like the Pico),
// consumer control (ID 3).
//--------------------------------------------------------------------+

static const uint8_t report_map[] = {
    0x05, 0x01, 0x09, 0x06, 0xA1, 0x01, 0x85, RID_KEYBOARD,
    0x05, 0x07, 0x19, 0xE0, 0x29, 0xE7, 0x15, 0x00, 0x25, 0x01, 0x75, 0x01, 0x95, 0x08, 0x81, 0x02,
    0x95, 0x01, 0x75, 0x08, 0x81, 0x01,
    0x05, 0x08, 0x19, 0x01, 0x29, 0x05, 0x95, 0x05, 0x75, 0x01, 0x91, 0x02,
    0x95, 0x01, 0x75, 0x03, 0x91, 0x01,
    0x05, 0x07, 0x19, 0x00, 0x2A, 0xFF, 0x00, 0x15, 0x00, 0x26, 0xFF, 0x00, 0x95, 0x06, 0x75, 0x08, 0x81, 0x00,
    0xC0,

    0x05, 0x01, 0x09, 0x02, 0xA1, 0x01, 0x85, RID_MOUSE, 0x09, 0x01, 0xA1, 0x00,
    0x05, 0x09, 0x19, 0x01, 0x29, 0x05, 0x15, 0x00, 0x25, 0x01, 0x95, 0x05, 0x75, 0x01, 0x81, 0x02,
    0x95, 0x01, 0x75, 0x03, 0x81, 0x01,
    0x05, 0x01, 0x09, 0x30, 0x09, 0x31, 0x16, 0x01, 0x80, 0x26, 0xFF, 0x7F, 0x75, 0x10, 0x95, 0x02, 0x81, 0x06,
    0x09, 0x38, 0x15, 0x81, 0x25, 0x7F, 0x75, 0x08, 0x95, 0x01, 0x81, 0x06,
    0x05, 0x0C, 0x0A, 0x38, 0x02, 0x15, 0x81, 0x25, 0x7F, 0x75, 0x08, 0x95, 0x01, 0x81, 0x06,
    0xC0, 0xC0,

    0x05, 0x0C, 0x09, 0x01, 0xA1, 0x01, 0x85, RID_CONSUMER,
    0x19, 0x00, 0x2A, 0xFF, 0x03, 0x15, 0x00, 0x26, 0xFF, 0x03, 0x75, 0x10, 0x95, 0x01, 0x81, 0x00,
    0xC0,
};

//--------------------------------------------------------------------+
// GATT database: HID service (HOGP), battery, device information.
// Attribute values for input reports are irrelevant: reports are sent as notifications to one
// connection at a time.
//--------------------------------------------------------------------+

static const uint16_t UUID_PRIMARY = ESP_GATT_UUID_PRI_SERVICE;
static const uint16_t UUID_CHAR_DECL = ESP_GATT_UUID_CHAR_DECLARE;
static const uint16_t UUID_CCCD = ESP_GATT_UUID_CHAR_CLIENT_CONFIG;
static const uint16_t UUID_REPORT_REF = 0x2908;
static const uint16_t UUID_HID_SVC = 0x1812, UUID_HID_INFO = 0x2A4A, UUID_REPORT_MAP = 0x2A4B,
                      UUID_HID_CTRL = 0x2A4C, UUID_REPORT = 0x2A4D, UUID_PROTO_MODE = 0x2A4E;
static const uint16_t UUID_BAT_SVC = 0x180F, UUID_BAT_LEVEL = 0x2A19;
static const uint16_t UUID_DIS_SVC = 0x180A, UUID_PNP_ID = 0x2A50, UUID_MANUFACTURER = 0x2A29;

static const uint8_t PROP_READ = ESP_GATT_CHAR_PROP_BIT_READ;
static const uint8_t PROP_READ_NOTIFY = ESP_GATT_CHAR_PROP_BIT_READ | ESP_GATT_CHAR_PROP_BIT_NOTIFY;
static const uint8_t PROP_WRITE_NR = ESP_GATT_CHAR_PROP_BIT_WRITE_NR;
static const uint8_t PROP_READ_WRITE_NR = ESP_GATT_CHAR_PROP_BIT_READ | ESP_GATT_CHAR_PROP_BIT_WRITE_NR;
static const uint8_t PROP_OUTPUT =
    ESP_GATT_CHAR_PROP_BIT_READ | ESP_GATT_CHAR_PROP_BIT_WRITE | ESP_GATT_CHAR_PROP_BIT_WRITE_NR;

#define PERM_R   ESP_GATT_PERM_READ_ENCRYPTED
#define PERM_RW  (ESP_GATT_PERM_READ_ENCRYPTED | ESP_GATT_PERM_WRITE_ENCRYPTED)

static const uint8_t hid_info[4] = {0x11, 0x01, 0x00, 0x02};  // HID 1.11, no country, normally connectable
static uint8_t hid_ctrl, proto_mode = 0x01;                    // report protocol
static uint8_t kbd_in_val[8], kbd_out_val[1], mouse_in_val[7], consumer_in_val[2];
static uint8_t cccd_kbd[2], cccd_mouse[2], cccd_consumer[2], cccd_bat[2];
static const uint8_t ref_kbd_in[2] = {RID_KEYBOARD, 1}, ref_kbd_out[2] = {RID_KEYBOARD, 2},
                     ref_mouse_in[2] = {RID_MOUSE, 1}, ref_consumer_in[2] = {RID_CONSUMER, 1};
static uint8_t battery_level = 100;
// PnP ID: vendor ID source 2 (USB), VID, PID, version 1.0 — the same USB identity as the Pico.
static const uint8_t pnp_id[7] = {0x02, USB_VID & 0xFF, USB_VID >> 8, USB_PID & 0xFF, USB_PID >> 8, 0x00, 0x01};
static const char manufacturer[] = "pi-switcher-hub";

#define DECL(prop) {{ESP_GATT_AUTO_RSP}, {ESP_UUID_LEN_16, (uint8_t *)&UUID_CHAR_DECL, ESP_GATT_PERM_READ, 1, 1, (uint8_t *)&(prop)}}
#define ATTR(uuid, perm, val) {{ESP_GATT_AUTO_RSP}, {ESP_UUID_LEN_16, (uint8_t *)&(uuid), perm, sizeof(val), sizeof(val), (uint8_t *)(val)}}

enum {
    HID_SVC, HID_INFO_D, HID_INFO_V, HID_MAP_D, HID_MAP_V, HID_CTRL_D, HID_CTRL_V, HID_PROTO_D, HID_PROTO_V,
    HID_KBD_IN_D, HID_KBD_IN_V, HID_KBD_IN_CCCD, HID_KBD_IN_REF,
    HID_KBD_OUT_D, HID_KBD_OUT_V, HID_KBD_OUT_REF,
    HID_MOUSE_D, HID_MOUSE_V, HID_MOUSE_CCCD, HID_MOUSE_REF,
    HID_CONS_D, HID_CONS_V, HID_CONS_CCCD, HID_CONS_REF,
    HID_NB,
};

static const esp_gatts_attr_db_t hid_db[HID_NB] = {
    [HID_SVC] = {{ESP_GATT_AUTO_RSP}, {ESP_UUID_LEN_16, (uint8_t *)&UUID_PRIMARY, ESP_GATT_PERM_READ, 2, 2, (uint8_t *)&UUID_HID_SVC}},
    [HID_INFO_D] = DECL(PROP_READ),
    [HID_INFO_V] = ATTR(UUID_HID_INFO, PERM_R, hid_info),
    [HID_MAP_D] = DECL(PROP_READ),
    [HID_MAP_V] = ATTR(UUID_REPORT_MAP, PERM_R, report_map),
    [HID_CTRL_D] = DECL(PROP_WRITE_NR),
    [HID_CTRL_V] = {{ESP_GATT_AUTO_RSP}, {ESP_UUID_LEN_16, (uint8_t *)&UUID_HID_CTRL, PERM_RW, 1, 1, &hid_ctrl}},
    [HID_PROTO_D] = DECL(PROP_READ_WRITE_NR),
    [HID_PROTO_V] = {{ESP_GATT_AUTO_RSP}, {ESP_UUID_LEN_16, (uint8_t *)&UUID_PROTO_MODE, PERM_RW, 1, 1, &proto_mode}},
    [HID_KBD_IN_D] = DECL(PROP_READ_NOTIFY),
    [HID_KBD_IN_V] = ATTR(UUID_REPORT, PERM_R, kbd_in_val),
    [HID_KBD_IN_CCCD] = ATTR(UUID_CCCD, PERM_RW, cccd_kbd),
    [HID_KBD_IN_REF] = ATTR(UUID_REPORT_REF, PERM_R, ref_kbd_in),
    [HID_KBD_OUT_D] = DECL(PROP_OUTPUT),
    [HID_KBD_OUT_V] = ATTR(UUID_REPORT, PERM_RW, kbd_out_val),
    [HID_KBD_OUT_REF] = ATTR(UUID_REPORT_REF, PERM_R, ref_kbd_out),
    [HID_MOUSE_D] = DECL(PROP_READ_NOTIFY),
    [HID_MOUSE_V] = ATTR(UUID_REPORT, PERM_R, mouse_in_val),
    [HID_MOUSE_CCCD] = ATTR(UUID_CCCD, PERM_RW, cccd_mouse),
    [HID_MOUSE_REF] = ATTR(UUID_REPORT_REF, PERM_R, ref_mouse_in),
    [HID_CONS_D] = DECL(PROP_READ_NOTIFY),
    [HID_CONS_V] = ATTR(UUID_REPORT, PERM_R, consumer_in_val),
    [HID_CONS_CCCD] = ATTR(UUID_CCCD, PERM_RW, cccd_consumer),
    [HID_CONS_REF] = ATTR(UUID_REPORT_REF, PERM_R, ref_consumer_in),
};

enum { BAT_SVC, BAT_LEVEL_D, BAT_LEVEL_V, BAT_LEVEL_CCCD, BAT_NB };
static const esp_gatts_attr_db_t bat_db[BAT_NB] = {
    [BAT_SVC] = {{ESP_GATT_AUTO_RSP}, {ESP_UUID_LEN_16, (uint8_t *)&UUID_PRIMARY, ESP_GATT_PERM_READ, 2, 2, (uint8_t *)&UUID_BAT_SVC}},
    [BAT_LEVEL_D] = DECL(PROP_READ_NOTIFY),
    [BAT_LEVEL_V] = {{ESP_GATT_AUTO_RSP}, {ESP_UUID_LEN_16, (uint8_t *)&UUID_BAT_LEVEL, PERM_R, 1, 1, &battery_level}},
    [BAT_LEVEL_CCCD] = ATTR(UUID_CCCD, PERM_RW, cccd_bat),
};

enum { DIS_SVC, DIS_PNP_D, DIS_PNP_V, DIS_MFR_D, DIS_MFR_V, DIS_NB };
static const esp_gatts_attr_db_t dis_db[DIS_NB] = {
    [DIS_SVC] = {{ESP_GATT_AUTO_RSP}, {ESP_UUID_LEN_16, (uint8_t *)&UUID_PRIMARY, ESP_GATT_PERM_READ, 2, 2, (uint8_t *)&UUID_DIS_SVC}},
    [DIS_PNP_D] = DECL(PROP_READ),
    [DIS_PNP_V] = ATTR(UUID_PNP_ID, PERM_R, pnp_id),
    [DIS_MFR_D] = DECL(PROP_READ),
    [DIS_MFR_V] = {{ESP_GATT_AUTO_RSP}, {ESP_UUID_LEN_16, (uint8_t *)&UUID_MANUFACTURER, PERM_R,
                                         sizeof(manufacturer) - 1, sizeof(manufacturer) - 1, (uint8_t *)manufacturer}},
};

static uint16_t hid_handles[HID_NB];
// Diagnostics per service table (0 DIS, 1 battery, 2 HID): create status, handle count, start status.
static uint8_t diag_create[3] = {0xFF, 0xFF, 0xFF}, diag_handles[3], diag_start[3] = {0xFF, 0xFF, 0xFF};
// Last pairing/encryption result: attempts, successes, last success flag, last fail reason, auth mode.
static uint8_t diag_auth[5];
static esp_gatt_if_t gatts_if_hid = ESP_GATT_IF_NONE;

//--------------------------------------------------------------------+
// Connections
//--------------------------------------------------------------------+

typedef struct {
    bool in_use;
    bool encrypted;          // bonded host, link encrypted: may receive input
    uint16_t conn_id;
    esp_bd_addr_t bda;       // address of this connection (may be a rotating private address)
    esp_bd_addr_t id_addr;   // stable identity address, reported to the hub
    uint16_t interval;       // 1.25 ms units
    bool congested;          // stack's send queue for this link is full
    bool in_flight;          // a report was handed to the stack and not yet confirmed
    int64_t sent_at_us;
    int64_t connected_at_us;
} host_t;

static host_t hosts[MAX_HOSTS];
static esp_bd_addr_t selected;   // host that receives input; all zero = none

// Bluetooth "ah" function: does this resolvable private address belong to the device with this IRK?
// Addresses are most-significant byte first; the stack stores keys least-significant first, so both
// key orders are tried (a false match is a 1 in 2^24 chance).
static bool rpa_matches(const uint8_t *bda, const uint8_t *irk) {
    if ((bda[0] & 0xC0) != 0x40) return false;  // not a resolvable private address
    for (int order = 0; order < 2; order++) {
        uint8_t key[16], in[16] = {0}, out[16];
        for (int i = 0; i < 16; i++) key[i] = order ? irk[i] : irk[15 - i];
        memcpy(in + 13, bda, 3);  // prand
        struct tc_aes_key_sched_struct sched;
        tc_aes128_set_encrypt_key(&sched, key);
        tc_aes_encrypt(out, in, &sched);
        if (!memcmp(out + 13, bda + 3, 3)) return true;  // hash
    }
    return false;
}

// Find the bond for a connection address (stored address, or an RPA resolved with the bond's IRK).
// Returns false if the address isn't bonded. With a bond, *identity is its stable identity address.
static bool bond_lookup(const uint8_t *bda, uint8_t *identity, uint8_t *bond_addr) {
    int n = esp_ble_get_bond_device_num();
    if (n <= 0) return false;
    esp_ble_bond_dev_t *list = calloc(n, sizeof(*list));
    bool found = false;
    if (list && esp_ble_get_bond_device_list(&n, list) == ESP_OK) {
        for (int i = 0; i < n && !found; i++) {
            bool has_id = list[i].bond_key.key_mask & ESP_LE_KEY_PID;
            if (!memcmp(list[i].bd_addr, bda, 6) || (has_id && (!memcmp(list[i].bond_key.pid_key.static_addr, bda, 6) ||
                                                                 rpa_matches(bda, list[i].bond_key.pid_key.irk)))) {
                memcpy(identity, has_id ? list[i].bond_key.pid_key.static_addr : list[i].bd_addr, 6);
                if (bond_addr) memcpy(bond_addr, list[i].bd_addr, 6);
                found = true;
            }
        }
    }
    free(list);
    return found;
}

static host_t *host_by_conn(uint16_t conn_id) {
    for (int i = 0; i < MAX_HOSTS; i++)
        if (hosts[i].in_use && hosts[i].conn_id == conn_id) return &hosts[i];
    return NULL;
}

static host_t *host_by_bda(const uint8_t *bda) {
    for (int i = 0; i < MAX_HOSTS; i++)
        if (hosts[i].in_use && !memcmp(hosts[i].bda, bda, 6)) return &hosts[i];
    return NULL;
}

static int host_count(void) {
    int n = 0;
    for (int i = 0; i < MAX_HOSTS; i++) n += hosts[i].in_use;
    return n;
}

static host_t *host_by_identity(const uint8_t *id) {
    for (int i = 0; i < MAX_HOSTS; i++)
        if (hosts[i].in_use && hosts[i].encrypted && !memcmp(hosts[i].id_addr, id, 6)) return &hosts[i];
    return NULL;
}

static host_t *active_host(void) {
    host_t *h = host_by_identity(selected);
    return h && h->encrypted ? h : NULL;
}

//--------------------------------------------------------------------+
// State
//--------------------------------------------------------------------+

static SemaphoreHandle_t hub_tx_lock;  // whole frames only, from any task
static SemaphoreHandle_t ble_lock;     // host table and HID state below

static volatile int64_t pair_until_us; // pairing window end (0 = closed)
static esp_bd_addr_t confirm_bda;      // host whose pairing code waits for the hub's approval
static int64_t confirm_until_us;       // 0 = nothing is waiting
static int64_t pairing_grace_until_us; // unencrypted hosts aren't dropped before this (a pairing is under way)
static bool adv_discoverable;          // advertising as discoverable (only while pairing is open)
static void configure_adv(bool discoverable);
static int64_t last_hub_us;            // last BLE-channel frame from the hub
static bool released_on_timeout = true;

typedef struct __attribute__((packed)) {
    uint8_t buttons;
    int16_t x, y;
    int8_t wheel, pan;
} mouse_report_t;

static mouse_report_t mouse_acc;
static uint8_t mouse_sent_buttons;
static uint8_t kbd_held[8];
static uint16_t consumer_held;

static bool pairing_open(void) { return pair_until_us && esp_timer_get_time() < pair_until_us; }

//--------------------------------------------------------------------+
// Frames to the hub
//--------------------------------------------------------------------+

static void hub_write_frame(const uint8_t *frame, size_t len) {
    xSemaphoreTake(hub_tx_lock, portMAX_DELAY);
    uart_write_bytes(HUB_UART, frame, len);
    xSemaphoreGive(hub_tx_lock);
}

static void hub_send(uint8_t type, const uint8_t *payload, uint8_t len) {
    uint8_t frame[4 + FRAME_MAX_PAYLOAD];
    frame[0] = FRAME_SOF;
    frame[1] = type;
    frame[2] = len;
    uint8_t crc = crc8_update(crc8_update(0, type), len);
    for (uint8_t i = 0; i < len; i++) {
        frame[3 + i] = payload[i];
        crc = crc8_update(crc, payload[i]);
    }
    frame[3 + len] = crc;
    hub_write_frame(frame, 4 + len);
}

// Call with ble_lock held.
static void ble_send_state(void) {
    bool any = false;
    uint8_t list[MAX_HOSTS * 8];
    uint8_t n = 0;
    for (int i = 0; i < MAX_HOSTS; i++) {
        if (!hosts[i].in_use || !hosts[i].encrypted) continue;
        any = true;
        memcpy(list + n, hosts[i].id_addr, 6);
        list[n + 6] = (uint8_t)hosts[i].interval;
        list[n + 7] = (uint8_t)(hosts[i].interval >> 8);
        n += 8;
    }
    uint8_t status[2] = {(uint8_t)((any ? STATUS_MOUNTED : 0) | (pairing_open() ? STATUS_PAIRING : 0)), 0};
    hub_send(MSG_STATUS | CHANNEL_BLE, status, 2);
    hub_send(MSG_BLE_HOSTS | CHANNEL_BLE, list, n);
}

//--------------------------------------------------------------------+
// Frame parser (one per direction)
//--------------------------------------------------------------------+

typedef struct {
    enum { S_SOF, S_TYPE, S_LEN, S_PAYLOAD, S_CRC } state;
    uint8_t type, len, pos, crc;
    uint8_t buf[4 + FRAME_MAX_PAYLOAD];  // raw frame, for forwarding unchanged
} parser_t;

// Returns true when buf holds a complete, CRC-valid frame.
static bool parser_feed(parser_t *p, uint8_t b) {
    switch (p->state) {
    case S_SOF:
        if (b == FRAME_SOF) {
            p->buf[0] = b;
            p->state = S_TYPE;
        }
        break;
    case S_TYPE:
        p->type = b;
        p->buf[1] = b;
        p->crc = crc8_update(0, b);
        p->state = S_LEN;
        break;
    case S_LEN:
        if (b > FRAME_MAX_PAYLOAD) {
            p->state = S_SOF;
            break;
        }
        p->len = b;
        p->buf[2] = b;
        p->pos = 0;
        p->crc = crc8_update(p->crc, b);
        p->state = b ? S_PAYLOAD : S_CRC;
        break;
    case S_PAYLOAD:
        p->buf[3 + p->pos++] = b;
        p->crc = crc8_update(p->crc, b);
        if (p->pos == p->len) p->state = S_CRC;
        break;
    case S_CRC:
        p->state = S_SOF;
        p->buf[3 + p->len] = b;
        return b == p->crc;
    }
    return false;
}

//--------------------------------------------------------------------+
// Bluetooth HID reports (call with ble_lock held)
//--------------------------------------------------------------------+

static bool ble_input(int value_idx, void *data, size_t len) {
    host_t *h = active_host();
    if (!h || gatts_if_hid == ESP_GATT_IF_NONE) return true;  // nobody to send to: nothing to keep
    if (esp_ble_gatts_send_indicate(gatts_if_hid, h->conn_id, hid_handles[value_idx], len, data, false) != ESP_OK)
        return false;
    h->in_flight = true;
    h->sent_at_us = esp_timer_get_time();
    return true;
}

static bool mouse_dirty(void) {
    return mouse_acc.x || mouse_acc.y || mouse_acc.wheel || mouse_acc.pan || mouse_acc.buttons != mouse_sent_buttons;
}

static void mouse_flush(void) {
    if (!mouse_dirty()) return;
    host_t *h = active_host();
    // Keep accumulating while the previous report is in flight or the link is congested.
    if (h && (h->congested || (h->in_flight && esp_timer_get_time() - h->sent_at_us < IN_FLIGHT_TIMEOUT_US))) return;
    if (!ble_input(HID_MOUSE_V, &mouse_acc, sizeof(mouse_acc))) return;  // not accepted: retry next tick
    mouse_sent_buttons = mouse_acc.buttons;
    mouse_acc.x = mouse_acc.y = 0;
    mouse_acc.wheel = mouse_acc.pan = 0;
}

// Every host gets a fast interval with a high peripheral latency: with nothing to send the ESP32 may
// skip up to 15 connection events, so idle hosts barely use the shared radio. Switching needs no
// parameter update, so it's instant. The controller does NOT wake early for queued data, though (a
// key could wait up to ~180 ms, measured with tools/ble_latency.py), so the active host is kept awake
// by keep_awake() below.
static void request_conn_params(host_t *h) {
    esp_ble_conn_update_params_t conn = {.min_int = 6, .max_int = 9, .latency = 15, .timeout = 600};
    memcpy(conn.bda, h->bda, sizeof(esp_bd_addr_t));
    esp_ble_gap_update_conn_params(&conn);
}

// Keep the active host's link at full rate: when nothing else is going out, resend the unchanged keyboard
// state once per connection event. A report equal to the current state produces no input on the host,
// but it stops the link from dozing through peripheral latency, so the next real key goes out at the
// very next event instead of up to 16 events later. Idle (non-selected) hosts keep dozing.
static void keep_awake(void) {
    host_t *h = active_host();
    if (!h || !h->encrypted || h->congested || h->in_flight || released_on_timeout) return;
    int64_t period_us = (int64_t)(h->interval ? h->interval : 9) * 1250 * 7 / 10;
    if (esp_timer_get_time() - h->sent_at_us < period_us) return;
    ble_input(HID_KBD_IN_V, kbd_held, 8);
}

static int32_t clamp(int32_t v, int32_t lo, int32_t hi) { return v < lo ? lo : v > hi ? hi : v; }

static void ble_mouse(const uint8_t *p) {
    uint8_t buttons = p[0];
    int16_t dx = (int16_t)(p[1] | p[2] << 8), dy = (int16_t)(p[3] | p[4] << 8);
    if (buttons != mouse_acc.buttons) {
        mouse_flush();  // movement before the click goes out with the old button state
        mouse_acc.buttons = buttons;
    }
    mouse_acc.x = (int16_t)clamp(mouse_acc.x + dx, -32767, 32767);
    mouse_acc.y = (int16_t)clamp(mouse_acc.y + dy, -32767, 32767);
    mouse_acc.wheel = (int8_t)clamp(mouse_acc.wheel + (int8_t)p[5], -127, 127);
    mouse_acc.pan = (int8_t)clamp(mouse_acc.pan + (int8_t)p[6], -127, 127);
    if (buttons != mouse_sent_buttons) mouse_flush();  // clicks go out immediately
}

static void ble_keyboard(const uint8_t *p) {
    memcpy(kbd_held, p, 8);
    ble_input(HID_KBD_IN_V, kbd_held, 8);
}

static void ble_consumer(uint16_t usage) {
    consumer_held = usage;
    uint8_t r[2] = {(uint8_t)usage, (uint8_t)(usage >> 8)};
    ble_input(HID_CONS_V, r, 2);
}

static void ble_release_all(void) {
    static const uint8_t zero[8];
    if (memcmp(kbd_held, zero, 8)) ble_keyboard(zero);
    if (consumer_held) ble_consumer(0);
    mouse_acc.x = mouse_acc.y = 0;
    mouse_acc.wheel = mouse_acc.pan = 0;
    mouse_acc.buttons = 0;
    mouse_flush();
}

static void ble_select(const uint8_t *bda) {
    if (!memcmp(selected, bda, 6)) return;
    ble_release_all();  // nothing stays held on the host we leave
    memcpy(selected, bda, 6);
    mouse_sent_buttons = 0;
}

static void ble_forget(const uint8_t *bda) {
    if (bda) {  // identity address from the hub
        host_t *h = host_by_identity(bda);
        if (h) esp_ble_gap_disconnect(h->bda);
        uint8_t identity[6], bond_addr[6];
        if (bond_lookup(bda, identity, bond_addr)) esp_ble_remove_bond_device(bond_addr);
        return;
    }
    int n = esp_ble_get_bond_device_num();
    if (n <= 0) return;
    esp_ble_bond_dev_t *list = calloc(n, sizeof(*list));
    if (list && esp_ble_get_bond_device_list(&n, list) == ESP_OK) {
        for (int i = 0; i < n; i++) esp_ble_remove_bond_device(list[i].bd_addr);
    }
    free(list);
    for (int i = 0; i < MAX_HOSTS; i++)
        if (hosts[i].in_use) esp_ble_gap_disconnect(hosts[i].bda);
}

// Diagnostics: 9 bytes of service-table state (create status x3, handle count x3, start status x3),
// 5 bytes of pairing state (see diag_auth), then n x (bond address, identity (static) address, identity address type, has identity key).
static void ble_send_bonds(void) {
    int n = esp_ble_get_bond_device_num();
    uint8_t out[FRAME_MAX_PAYLOAD];
    memcpy(out, diag_create, 3);
    memcpy(out + 3, diag_handles, 3);
    memcpy(out + 6, diag_start, 3);
    memcpy(out + 9, diag_auth, 5);
    uint8_t len = 14;
    if (n > 0) {
        esp_ble_bond_dev_t *list = calloc(n, sizeof(*list));
        if (list && esp_ble_get_bond_device_list(&n, list) == ESP_OK) {
            for (int i = 0; i < n && len + 14 <= sizeof(out); i++) {
                memcpy(out + len, list[i].bd_addr, 6);
                memcpy(out + len + 6, list[i].bond_key.pid_key.static_addr, 6);
                out[len + 12] = list[i].bond_key.pid_key.addr_type;
                out[len + 13] = (list[i].bond_key.key_mask & ESP_LE_KEY_PID) ? 1 : 0;
                len += 14;
            }
        }
        free(list);
    }
    hub_send(MSG_DEBUG | CHANNEL_BLE, out, len);
}

static void handle_ble_frame(uint8_t type, const uint8_t *p, uint8_t len) {
    last_hub_us = esp_timer_get_time();
    xSemaphoreTake(ble_lock, portMAX_DELAY);
    released_on_timeout = false;
    switch (type & ~CHANNEL_MASK) {
    case MSG_MOUSE:
        if (len == 7) ble_mouse(p);
        break;
    case MSG_KEYBOARD:
        if (len == 8) ble_keyboard(p);
        break;
    case MSG_CONSUMER:
        if (len == 2) ble_consumer((uint16_t)(p[0] | p[1] << 8));
        break;
    case MSG_RELEASE_ALL:
        ble_release_all();
        break;
    case MSG_BLE_SELECT:
        if (len == 6) ble_select(p);
        break;
    case MSG_BLE_PAIR:
        if (len == 1) pair_until_us = p[0] ? esp_timer_get_time() + (int64_t)p[0] * 1000000 : 0;
        ble_send_state();
        break;
    case MSG_BLE_FORGET:
        ble_forget(len == 6 ? p : NULL);
        break;
    case MSG_BLE_CONFIRM:
        if (len == 1 && confirm_until_us) {
            confirm_until_us = 0;
            esp_ble_confirm_reply(confirm_bda, p[0] && pairing_open());
        }
        break;
    case MSG_DISPLAY:
        if (len >= 1) oled_chunk(p[0], p + 1, len - 1);
        break;
    case MSG_PING:
        ble_send_state();
        break;
    case MSG_DEBUG_REQ:
        ble_send_bonds();
        break;
    }
    xSemaphoreGive(ble_lock);
}

//--------------------------------------------------------------------+
// UART tasks
//--------------------------------------------------------------------+

// Block for the first byte, then take whatever else is already buffered. (uart_read_bytes with a
// length waits until that many bytes arrive or the timeout passes, which would batch 1000 Hz mouse
// frames into timeout-sized chunks.)
static int uart_read_available(uart_port_t port, uint8_t *buf, size_t size) {
    int n = uart_read_bytes(port, buf, 1, portMAX_DELAY);
    if (n <= 0) return n;
    size_t avail = 0;
    uart_get_buffered_data_len(port, &avail);
    if (avail > size - 1) avail = size - 1;
    if (avail) n += uart_read_bytes(port, buf + 1, avail, 0);
    return n;
}

static void hub_rx_task(void *arg) {
    static parser_t parser;
    uint8_t buf[256];
    while (true) {
        int n = uart_read_available(HUB_UART, buf, sizeof(buf));
        for (int i = 0; i < n; i++) {
            if (!parser_feed(&parser, buf[i])) continue;
            if (parser.type & CHANNEL_MASK)
                handle_ble_frame(parser.type, parser.buf + 3, parser.len);
            else
                uart_write_bytes(PICO_UART, parser.buf, 4 + parser.len);  // Pico frames pass through
        }
    }
}

static void pico_rx_task(void *arg) {
    static parser_t parser;
    uint8_t buf[256];
    while (true) {
        int n = uart_read_available(PICO_UART, buf, sizeof(buf));
        for (int i = 0; i < n; i++) {
            // Forward whole frames only, so they never interleave with our own replies.
            if (parser_feed(&parser, buf[i])) hub_write_frame(parser.buf, 4 + parser.len);
        }
    }
}

static void ble_timer_task(void *arg) {
    while (true) {
        vTaskDelay(pdMS_TO_TICKS(MOUSE_TICK_MS));
        xSemaphoreTake(ble_lock, portMAX_DELAY);
        int64_t now = esp_timer_get_time();
        mouse_flush();
        keep_awake();
        // Hub went quiet: never leave keys or buttons held on a host.
        if (!released_on_timeout && now - last_hub_us > HEARTBEAT_TIMEOUT_MS * 1000LL) {
            ble_release_all();
            released_on_timeout = true;
        }
        if (pair_until_us && now >= pair_until_us) pair_until_us = 0;
        // Nobody approved the code in time, or pairing was switched off meanwhile: refuse it.
        if (confirm_until_us && (now >= confirm_until_us || !pairing_open())) {
            confirm_until_us = 0;
            esp_ble_confirm_reply(confirm_bda, false);
        }
        // Only show up in Bluetooth scans while pairing is open; bonded hosts still reconnect.
        if (pairing_open() != adv_discoverable) {
            adv_discoverable = pairing_open();
            esp_ble_gap_stop_advertising();
            configure_adv(adv_discoverable);
        }
        int64_t unpaired_limit = pairing_open() ? UNPAIRED_TIMEOUT_US : STRANGER_TIMEOUT_US;
        for (int i = 0; i < MAX_HOSTS; i++) {
            if (hosts[i].in_use && !hosts[i].encrypted && now > pairing_grace_until_us &&
                now - hosts[i].connected_at_us > unpaired_limit) {
                esp_ble_gap_disconnect(hosts[i].bda);
                hosts[i].connected_at_us = now;  // don't repeat every tick while the disconnect completes
            }
        }
        xSemaphoreGive(ble_lock);
    }
}

static void uart_setup(uart_port_t port, int tx, int rx) {
    uart_config_t cfg = {
        .baud_rate = LINK_BAUD,
        .data_bits = UART_DATA_8_BITS,
        .parity = UART_PARITY_DISABLE,
        .stop_bits = UART_STOP_BITS_1,
        .flow_ctrl = UART_HW_FLOWCTRL_DISABLE,
        .source_clk = UART_SCLK_DEFAULT,
    };
    ESP_ERROR_CHECK(uart_driver_install(port, UART_BUF, UART_BUF, 0, NULL, 0));
    ESP_ERROR_CHECK(uart_param_config(port, &cfg));
    ESP_ERROR_CHECK(uart_set_pin(port, tx, rx, UART_PIN_NO_CHANGE, UART_PIN_NO_CHANGE));
}

//--------------------------------------------------------------------+
// Bluetooth GAP: advertising and pairing policy
//--------------------------------------------------------------------+

static esp_ble_adv_params_t adv_params = {
    .adv_int_min = 0x20,
    .adv_int_max = 0x30,
    .adv_type = ADV_TYPE_IND,
    .own_addr_type = BLE_ADDR_TYPE_PUBLIC,
    .channel_map = ADV_CHNL_ALL,
    .adv_filter_policy = ADV_FILTER_ALLOW_SCAN_ANY_CON_ANY,
};

static uint8_t hid_service_uuid128[] = {
    0xfb, 0x34, 0x9b, 0x5f, 0x80, 0x00, 0x00, 0x80, 0x00, 0x10, 0x00, 0x00, 0x12, 0x18, 0x00, 0x00,
};

static void configure_adv(bool discoverable) {
    esp_ble_adv_data_t adv_data = {
        .set_scan_rsp = false,
        .include_name = true,
        .include_txpower = false,
        .min_interval = 0x0006,
        .max_interval = 0x0010,
        .appearance = 0x03C1,  // keyboard
        .service_uuid_len = sizeof(hid_service_uuid128),
        .p_service_uuid = hid_service_uuid128,
        .flag = (discoverable ? ESP_BLE_ADV_FLAG_GEN_DISC : 0) | ESP_BLE_ADV_FLAG_BREDR_NOT_SPT,
    };
    ESP_ERROR_CHECK(esp_ble_gap_config_adv_data(&adv_data));
}

// Keep advertising while there are free connection slots, so other hosts can (re)connect.
static void advertise_if_room(void) {
    if (host_count() < MAX_HOSTS) esp_ble_gap_start_advertising(&adv_params);
}

static void gap_cb(esp_gap_ble_cb_event_t event, esp_ble_gap_cb_param_t *param) {
    switch (event) {
    case ESP_GAP_BLE_ADV_DATA_SET_COMPLETE_EVT:
        advertise_if_room();
        break;
    case ESP_GAP_BLE_SEC_REQ_EVT:
        esp_ble_gap_security_rsp(param->ble_security.ble_req.bd_addr, true);
        break;
    case ESP_GAP_BLE_NC_REQ_EVT: {
        // A new bond: only while the pairing window is open, and only after the hub's user has compared the
        // code with the one on the phone and approved it (MSG_BLE_CONFIRM). One at a time.
        xSemaphoreTake(ble_lock, portMAX_DELAY);
        if (pairing_open() && !confirm_until_us) {
            int64_t now = esp_timer_get_time();
            uint32_t pk = param->ble_security.key_notif.passkey;
            uint8_t payload[4] = {(uint8_t)pk, (uint8_t)(pk >> 8), (uint8_t)(pk >> 16), (uint8_t)(pk >> 24)};
            memcpy(confirm_bda, param->ble_security.key_notif.bd_addr, 6);
            confirm_until_us = now + CONFIRM_TIMEOUT_US;
            pairing_grace_until_us = confirm_until_us + 30 * 1000000LL;
            hub_send(MSG_BLE_PASSKEY | CHANNEL_BLE, payload, 4);
        } else {
            esp_ble_confirm_reply(param->ble_security.key_notif.bd_addr, false);
        }
        xSemaphoreGive(ble_lock);
        break;
    }
    case ESP_GAP_BLE_AUTH_CMPL_EVT: {
        xSemaphoreTake(ble_lock, portMAX_DELAY);
        uint8_t *bda = param->ble_security.auth_cmpl.bd_addr;
        confirm_until_us = 0;
        diag_auth[0]++;
        diag_auth[1] += param->ble_security.auth_cmpl.success;
        diag_auth[2] = param->ble_security.auth_cmpl.success;
        diag_auth[3] = param->ble_security.auth_cmpl.fail_reason;
        diag_auth[4] = param->ble_security.auth_cmpl.auth_mode;
        host_t *h = host_by_bda(bda);
        if (h == NULL) {
            // The completion may name the host's identity address rather than the (private) address it
            // connected with; then it belongs to the only connection still waiting for encryption.
            int waiting = 0;
            for (int i = 0; i < MAX_HOSTS; i++)
                if (hosts[i].in_use && !hosts[i].encrypted && waiting++ == 0) h = &hosts[i];
            if (waiting != 1) h = NULL;
        }
        if (h && param->ble_security.auth_cmpl.success) {
            h->encrypted = true;
            if (!bond_lookup(h->bda, h->id_addr, NULL) && !bond_lookup(bda, h->id_addr, NULL))
                memcpy(h->id_addr, bda, 6);
            pair_until_us = 0;       // one bond per pairing window
            request_conn_params(h);
        }
        ble_send_state();
        xSemaphoreGive(ble_lock);
        break;
    }
    case ESP_GAP_BLE_UPDATE_CONN_PARAMS_EVT:
        if (param->update_conn_params.status == ESP_BT_STATUS_SUCCESS) {
            xSemaphoreTake(ble_lock, portMAX_DELAY);
            host_t *h = host_by_bda(param->update_conn_params.bda);
            if (!h) h = host_by_identity(param->update_conn_params.bda);
            if (h) h->interval = param->update_conn_params.conn_int;
            ble_send_state();
            xSemaphoreGive(ble_lock);
        }
        break;
    default:
        break;
    }
}

static void gatts_cb(esp_gatts_cb_event_t event, esp_gatt_if_t gatts_if, esp_ble_gatts_cb_param_t *param) {
    switch (event) {
    case ESP_GATTS_REG_EVT:
        gatts_if_hid = gatts_if;
        esp_ble_gatts_create_attr_tab(dis_db, gatts_if, DIS_NB, 0);
        esp_ble_gatts_create_attr_tab(bat_db, gatts_if, BAT_NB, 1);
        esp_ble_gatts_create_attr_tab(hid_db, gatts_if, HID_NB, 2);
        break;
    case ESP_GATTS_CREAT_ATTR_TAB_EVT:
        if (param->add_attr_tab.svc_inst_id < 3) {
            diag_create[param->add_attr_tab.svc_inst_id] = param->add_attr_tab.status;
            diag_handles[param->add_attr_tab.svc_inst_id] = param->add_attr_tab.num_handle;
        }
        if (param->add_attr_tab.status != ESP_GATT_OK) break;
        if (param->add_attr_tab.num_handle == HID_NB && param->add_attr_tab.svc_inst_id == 2)
            memcpy(hid_handles, param->add_attr_tab.handles, sizeof(hid_handles));
        esp_ble_gatts_start_service(param->add_attr_tab.handles[0]);
        break;
    case ESP_GATTS_START_EVT: {
        static int started;  // services start in creation order: DIS, battery, HID
        if (started < 3) diag_start[started++] = param->start.status;
        break;
    }
    case ESP_GATTS_CONNECT_EVT: {
        xSemaphoreTake(ble_lock, portMAX_DELAY);
        for (int i = 0; i < MAX_HOSTS; i++) {
            if (!hosts[i].in_use) {
                hosts[i] = (host_t){.in_use = true, .conn_id = param->connect.conn_id,
                                    .interval = param->connect.conn_params.interval,
                                    .connected_at_us = esp_timer_get_time()};
                memcpy(hosts[i].bda, param->connect.remote_bda, 6);
                break;
            }
        }
        xSemaphoreGive(ble_lock);
        // Bonded hosts re-encrypt with their stored keys; new ones must pair (window-gated).
        esp_ble_set_encryption(param->connect.remote_bda, ESP_BLE_SEC_ENCRYPT_MITM);
        advertise_if_room();
        break;
    }
    case ESP_GATTS_DISCONNECT_EVT: {
        xSemaphoreTake(ble_lock, portMAX_DELAY);
        host_t *h = host_by_conn(param->disconnect.conn_id);
        if (h) {
            if (h->encrypted && !memcmp(h->id_addr, selected, 6)) {
                mouse_acc = (mouse_report_t){0};
                mouse_sent_buttons = 0;
                memset(kbd_held, 0, sizeof(kbd_held));
                consumer_held = 0;
            }
            h->in_use = false;
        }
        ble_send_state();
        xSemaphoreGive(ble_lock);
        advertise_if_room();
        break;
    }
    case ESP_GATTS_CONF_EVT: {
        // Previous report handed to the radio: send what accumulated meanwhile right away.
        xSemaphoreTake(ble_lock, portMAX_DELAY);
        host_t *h = host_by_conn(param->conf.conn_id);
        if (h) h->in_flight = false;
        mouse_flush();
        keep_awake();
        xSemaphoreGive(ble_lock);
        break;
    }
    case ESP_GATTS_CONGEST_EVT: {
        xSemaphoreTake(ble_lock, portMAX_DELAY);
        host_t *h = host_by_conn(param->congest.conn_id);
        if (h) h->congested = param->congest.congested;
        xSemaphoreGive(ble_lock);
        break;
    }
    case ESP_GATTS_WRITE_EVT: {
        if (!param->write.is_prep && param->write.handle == hid_handles[HID_KBD_OUT_V] && param->write.len >= 1) {
            xSemaphoreTake(ble_lock, portMAX_DELAY);
            host_t *h = host_by_conn(param->write.conn_id);
            if (h) {
                uint8_t payload[7];
                memcpy(payload, h->id_addr, 6);
                payload[6] = param->write.value[0];
                hub_send(MSG_KBD_LEDS | CHANNEL_BLE, payload, 7);
            }
            xSemaphoreGive(ble_lock);
        }
        break;
    }
    default:
        break;
    }
}

static void ble_setup(void) {
    ESP_ERROR_CHECK(esp_bt_controller_mem_release(ESP_BT_MODE_CLASSIC_BT));
    esp_bt_controller_config_t bt_cfg = BT_CONTROLLER_INIT_CONFIG_DEFAULT();
    ESP_ERROR_CHECK(esp_bt_controller_init(&bt_cfg));
    ESP_ERROR_CHECK(esp_bt_controller_enable(ESP_BT_MODE_BLE));
    ESP_ERROR_CHECK(esp_bluedroid_init());
    ESP_ERROR_CHECK(esp_bluedroid_enable());
    ESP_ERROR_CHECK(esp_ble_gap_register_callback(gap_cb));
    ESP_ERROR_CHECK(esp_ble_gatts_register_callback(gatts_cb));

    // Numeric comparison with bonding: a new host must be confirmed while the pairing window is open.
    esp_ble_auth_req_t auth_req = ESP_LE_AUTH_REQ_SC_MITM_BOND;
    esp_ble_io_cap_t iocap = ESP_IO_CAP_IO;
    uint8_t key_size = 16;
    uint8_t keys = ESP_BLE_ENC_KEY_MASK | ESP_BLE_ID_KEY_MASK;
    esp_ble_gap_set_security_param(ESP_BLE_SM_AUTHEN_REQ_MODE, &auth_req, 1);
    esp_ble_gap_set_security_param(ESP_BLE_SM_IOCAP_MODE, &iocap, 1);
    esp_ble_gap_set_security_param(ESP_BLE_SM_MAX_KEY_SIZE, &key_size, 1);
    esp_ble_gap_set_security_param(ESP_BLE_SM_SET_INIT_KEY, &keys, 1);
    esp_ble_gap_set_security_param(ESP_BLE_SM_SET_RSP_KEY, &keys, 1);

    esp_ble_gap_set_device_name(DEVICE_NAME);
    configure_adv(false);  // pairing starts closed, so not discoverable
    ESP_ERROR_CHECK(esp_ble_gatts_app_register(0));
}

void app_main(void) {
    esp_err_t ret = nvs_flash_init();
    if (ret == ESP_ERR_NVS_NO_FREE_PAGES || ret == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        ESP_ERROR_CHECK(nvs_flash_erase());
        ret = nvs_flash_init();
    }
    ESP_ERROR_CHECK(ret);

    hub_tx_lock = xSemaphoreCreateMutex();
    ble_lock = xSemaphoreCreateMutex();
    uart_setup(HUB_UART, HUB_TX_PIN, HUB_RX_PIN);
    uart_setup(PICO_UART, PICO_TX_PIN, PICO_RX_PIN);

    // The Pico path doesn't depend on Bluetooth, so start routing first.
    xTaskCreate(hub_rx_task, "hub_rx", 4096, NULL, 10, NULL);
    xTaskCreate(pico_rx_task, "pico_rx", 4096, NULL, 10, NULL);

    oled_start();
    ble_setup();
    xTaskCreate(ble_timer_task, "ble_timer", 4096, NULL, 5, NULL);
}
