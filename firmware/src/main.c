// Switcher Hub Pico firmware: receives input frames from the Linux hub over UART
// and replays them as a USB keyboard, mouse and consumer-control device.
#include <string.h>

#include "hardware/irq.h"
#include "hardware/uart.h"
#include "pico/bootrom.h"
#include "pico/stdlib.h"
#include "tusb.h"

#include "protocol.h"
#include "usb_descriptors.h"
#include "xinput.h"

// Link settings come from firmware/settings.cmake (the repo's .env); these are only the defaults.
#ifndef LINK_BAUD
#define LINK_BAUD 921600
#endif
#ifndef PICO_LINK_TX_PIN
#define PICO_LINK_TX_PIN 0
#endif
#ifndef PICO_LINK_RX_PIN
#define PICO_LINK_RX_PIN 1
#endif
#define LINK_UART     uart0
#define LINK_TX_PIN   PICO_LINK_TX_PIN
#define LINK_RX_PIN   PICO_LINK_RX_PIN
#define LED_PIN       PICO_DEFAULT_LED_PIN

// Debug counters, reported by MSG_DEBUG.
static struct {
    uint16_t frames_rx, crc_errors, queued[3], sent[3], send_rejected[3], completed[3], failed[3], set_reports;
} dbg;

//--------------------------------------------------------------------+
// UART receive ring buffer (filled from the RX interrupt)
//--------------------------------------------------------------------+

#define RX_BUF_SIZE 1024  // power of two
static volatile uint8_t rx_buf[RX_BUF_SIZE];
static volatile uint32_t rx_head, rx_tail;

static void on_uart_rx(void) {
    while (uart_is_readable(LINK_UART)) {
        uint8_t b = (uint8_t)uart_getc(LINK_UART);
        uint32_t next = (rx_head + 1) & (RX_BUF_SIZE - 1);
        if (next != rx_tail) {  // drop on overflow; framing resyncs
            rx_buf[rx_head] = b;
            rx_head = next;
        }
    }
}

static bool rx_pop(uint8_t *b) {
    if (rx_tail == rx_head) return false;
    *b = rx_buf[rx_tail];
    rx_tail = (rx_tail + 1) & (RX_BUF_SIZE - 1);
    return true;
}

static void send_frame(uint8_t type, uint8_t const *payload, uint8_t len) {
    uint8_t crc = crc8_update(crc8_update(0, type), len);
    uart_putc_raw(LINK_UART, FRAME_SOF);
    uart_putc_raw(LINK_UART, type);
    uart_putc_raw(LINK_UART, len);
    for (uint8_t i = 0; i < len; i++) {
        uart_putc_raw(LINK_UART, payload[i]);
        crc = crc8_update(crc, payload[i]);
    }
    uart_putc_raw(LINK_UART, crc);
}

//--------------------------------------------------------------------+
// Report queues. Every state change is queued so a press + release that arrive
// within one USB poll interval still reach the host as two reports.
//--------------------------------------------------------------------+

#define Q_SIZE 32  // power of two

typedef struct {
    uint8_t data[Q_SIZE][8];
    uint32_t head, tail;
} report_queue_t;

static void q_push(report_queue_t *q, void const *report, size_t len) {
    uint32_t next = (q->head + 1) & (Q_SIZE - 1);
    if (next == q->tail) q->tail = (q->tail + 1) & (Q_SIZE - 1);  // full: drop oldest
    memcpy(q->data[q->head], report, len);
    q->head = next;
}

static bool q_empty(report_queue_t const *q) { return q->head == q->tail; }

static void const *q_front(report_queue_t const *q) { return q->data[q->tail]; }

static void q_pop(report_queue_t *q) { q->tail = (q->tail + 1) & (Q_SIZE - 1); }

static report_queue_t kbd_q, consumer_q, mouse_q;

static uint8_t kbd_last[8];       // last keyboard state queued
static uint16_t consumer_last;    // last consumer usage queued
static mouse_report_t mouse_acc;  // movement accumulated since last report
static uint8_t mouse_committed;   // button state of the last queued/sent mouse report
static uint8_t kbd_leds;

static bool mouse_dirty(void) {
    return mouse_acc.x || mouse_acc.y || mouse_acc.wheel || mouse_acc.pan ||
           mouse_acc.buttons != mouse_committed;
}

static void mouse_commit(void) {
    q_push(&mouse_q, &mouse_acc, sizeof(mouse_acc));
    dbg.queued[HID_MOUSE]++;
    mouse_committed = mouse_acc.buttons;
    mouse_acc.x = mouse_acc.y = 0;
    mouse_acc.wheel = mouse_acc.pan = 0;
}

static int32_t clamp(int32_t v, int32_t lo, int32_t hi) { return v < lo ? lo : v > hi ? hi : v; }

static void handle_mouse(uint8_t const *p) {
    uint8_t buttons = p[0];
    int16_t dx = (int16_t)(p[1] | p[2] << 8);
    int16_t dy = (int16_t)(p[3] | p[4] << 8);
    int8_t wheel = (int8_t)p[5];
    int8_t pan = (int8_t)p[6];

    // Don't merge a button change into movement that happened before it.
    if (buttons != mouse_acc.buttons && mouse_dirty()) mouse_commit();
    mouse_acc.buttons = buttons;

    int32_t x = mouse_acc.x + dx, y = mouse_acc.y + dy;
    int32_t w = mouse_acc.wheel + wheel, h = mouse_acc.pan + pan;
    if (x < -32767 || x > 32767 || y < -32767 || y > 32767 || w < -127 || w > 127 || h < -127 || h > 127) {
        mouse_commit();
        x = dx, y = dy, w = wheel, h = pan;
    }
    mouse_acc.x = (int16_t)clamp(x, -32767, 32767);
    mouse_acc.y = (int16_t)clamp(y, -32767, 32767);
    mouse_acc.wheel = (int8_t)clamp(w, -127, 127);
    mouse_acc.pan = (int8_t)clamp(h, -127, 127);
}

static void handle_keyboard(uint8_t const *p) {
    if (memcmp(p, kbd_last, 8) == 0) return;
    memcpy(kbd_last, p, 8);
    q_push(&kbd_q, p, 8);
    dbg.queued[HID_KBD]++;
}

static void handle_consumer(uint16_t usage) {
    if (usage == consumer_last) return;
    consumer_last = usage;
    q_push(&consumer_q, &usage, 2);
    dbg.queued[HID_CONSUMER]++;
}

static void handle_gamepad(uint8_t const *p) {
    xinput_report_t r = {0};
    r.buttons = (uint16_t)(p[0] | p[1] << 8);
    r.lt = p[2];
    r.rt = p[3];
    r.lx = (int16_t)(p[4] | p[5] << 8);
    r.ly = (int16_t)(p[6] | p[7] << 8);
    r.rx = (int16_t)(p[8] | p[9] << 8);
    r.ry = (int16_t)(p[10] | p[11] << 8);
    xinput_set_state(&r);
}

void xinput_rumble_cb(uint8_t strong, uint8_t weak) {
    uint8_t payload[2] = {strong, weak};
    send_frame(MSG_RUMBLE, payload, 2);
}

static void release_all(void) {
    static uint8_t const zero[8];
    static uint8_t const neutral_pad[12];
    handle_gamepad(neutral_pad);
    handle_keyboard(zero);
    handle_consumer(0);
    uint8_t mouse_up[7] = {0};
    handle_mouse(mouse_up);
}

static void send_debug(void) {
    uint8_t payload[1 + sizeof(dbg)];
    payload[0] = (tud_hid_n_ready(HID_KBD) ? 1 : 0) | (tud_hid_n_ready(HID_MOUSE) ? 2 : 0) |
                 (tud_hid_n_ready(HID_CONSUMER) ? 4 : 0) | (tud_ready() ? 0x80 : 0);
    memcpy(payload + 1, &dbg, sizeof(dbg));
    send_frame(MSG_DEBUG, payload, sizeof(payload));
}

static void send_status(void) {
    uint8_t flags = (tud_mounted() ? STATUS_MOUNTED : 0) | (tud_suspended() ? STATUS_SUSPENDED : 0) |
                    (xinput_ready() ? STATUS_XINPUT : 0);
    uint8_t payload[2] = {flags, kbd_leds};
    send_frame(MSG_STATUS, payload, 2);
}

//--------------------------------------------------------------------+
// Frame parser
//--------------------------------------------------------------------+

static uint32_t last_rx_ms;
static bool released_on_timeout = true;

static void handle_frame(uint8_t type, uint8_t const *p, uint8_t len) {
    last_rx_ms = to_ms_since_boot(get_absolute_time());
    released_on_timeout = false;
    dbg.frames_rx++;

    switch (type) {
    case MSG_MOUSE:
        if (len == 7) handle_mouse(p);
        break;
    case MSG_KEYBOARD:
        if (len == 8) handle_keyboard(p);
        break;
    case MSG_CONSUMER:
        if (len == 2) handle_consumer((uint16_t)(p[0] | p[1] << 8));
        break;
    case MSG_GAMEPAD:
        if (len == 12) handle_gamepad(p);
        break;
    case MSG_RELEASE_ALL:
        release_all();
        break;
    case MSG_PING:
        send_status();
        break;
    case MSG_DEBUG_REQ:
        send_debug();
        break;
    case MSG_BOOTLOADER:
        reset_usb_boot(0, 0);
        break;
    }
}

static void parse_rx(void) {
    static enum { S_SOF, S_TYPE, S_LEN, S_PAYLOAD, S_CRC } state = S_SOF;
    static uint8_t type, len, pos, crc;
    static uint8_t payload[FRAME_MAX_PAYLOAD];

    uint8_t b;
    while (rx_pop(&b)) {
        switch (state) {
        case S_SOF:
            if (b == FRAME_SOF) state = S_TYPE;
            break;
        case S_TYPE:
            type = b;
            crc = crc8_update(0, b);
            state = S_LEN;
            break;
        case S_LEN:
            if (b > FRAME_MAX_PAYLOAD) { state = S_SOF; break; }
            len = b;
            pos = 0;
            crc = crc8_update(crc, b);
            state = len ? S_PAYLOAD : S_CRC;
            break;
        case S_PAYLOAD:
            payload[pos++] = b;
            crc = crc8_update(crc, b);
            if (pos == len) state = S_CRC;
            break;
        case S_CRC:
            if (b == crc) handle_frame(type, payload, len);
            else dbg.crc_errors++;
            state = S_SOF;  // on a bad CRC the frame is dropped and we hunt for the next SOF
            break;
        }
    }
}

//--------------------------------------------------------------------+
// USB report pump
//--------------------------------------------------------------------+

static bool has_pending_input(void) {
    return !q_empty(&kbd_q) || !q_empty(&consumer_q) || !q_empty(&mouse_q) || mouse_dirty();
}

static void send_front(uint8_t instance, report_queue_t *q, uint16_t len) {
    if (tud_hid_n_report(instance, 0, q_front(q), len)) {
        q_pop(q);
        dbg.sent[instance]++;
    } else {
        dbg.send_rejected[instance]++;
    }
}

static void pump_reports(void) {
    if (!tud_mounted()) return;

    if (tud_suspended()) {
        if (has_pending_input()) tud_remote_wakeup();
        return;
    }

    if (!q_empty(&kbd_q) && tud_hid_n_ready(HID_KBD)) send_front(HID_KBD, &kbd_q, 8);

    if (tud_hid_n_ready(HID_MOUSE)) {
        if (q_empty(&mouse_q) && mouse_dirty()) mouse_commit();
        if (!q_empty(&mouse_q)) send_front(HID_MOUSE, &mouse_q, sizeof(mouse_report_t));
    }

    if (!q_empty(&consumer_q) && tud_hid_n_ready(HID_CONSUMER)) send_front(HID_CONSUMER, &consumer_q, 2);
}

//--------------------------------------------------------------------+
// TinyUSB HID callbacks
//--------------------------------------------------------------------+

uint16_t tud_hid_get_report_cb(uint8_t instance, uint8_t report_id, hid_report_type_t report_type,
                               uint8_t *buffer, uint16_t reqlen) {
    (void)instance, (void)report_id, (void)report_type, (void)buffer, (void)reqlen;
    return 0;
}

void tud_hid_report_complete_cb(uint8_t instance, uint8_t const *report, uint16_t len) {
    (void)report, (void)len;
    if (instance < 3) dbg.completed[instance]++;
}

void tud_hid_report_failed_cb(uint8_t instance, hid_report_type_t report_type, uint8_t const *report,
                              uint16_t xferred_bytes) {
    (void)report_type, (void)report, (void)xferred_bytes;
    if (instance < 3) dbg.failed[instance]++;
}

void tud_hid_set_report_cb(uint8_t instance, uint8_t report_id, hid_report_type_t report_type,
                           uint8_t const *buffer, uint16_t bufsize) {
    (void)report_id;
    dbg.set_reports++;
    if (instance == HID_KBD && report_type == HID_REPORT_TYPE_OUTPUT && bufsize >= 1) {
        kbd_leds = buffer[0];
        send_frame(MSG_KBD_LEDS, &kbd_leds, 1);
    }
}

void tud_mount_cb(void) { send_status(); }
void tud_umount_cb(void) { send_status(); }
void tud_suspend_cb(bool remote_wakeup_en) { (void)remote_wakeup_en; send_status(); }
void tud_resume_cb(void) { send_status(); }

//--------------------------------------------------------------------+

int main(void) {
    gpio_init(LED_PIN);
    gpio_set_dir(LED_PIN, GPIO_OUT);

    uart_init(LINK_UART, LINK_BAUD);
    gpio_set_function(LINK_TX_PIN, GPIO_FUNC_UART);
    gpio_set_function(LINK_RX_PIN, GPIO_FUNC_UART);
    uart_set_format(LINK_UART, 8, 1, UART_PARITY_NONE);
    uart_set_hw_flow(LINK_UART, false, false);
    uart_set_fifo_enabled(LINK_UART, true);
    int irq = LINK_UART == uart0 ? UART0_IRQ : UART1_IRQ;
    irq_set_exclusive_handler(irq, on_uart_rx);
    irq_set_enabled(irq, true);
    uart_set_irq_enables(LINK_UART, true, false);

    tusb_rhport_init_t dev_init = {.role = TUSB_ROLE_DEVICE, .speed = TUSB_SPEED_AUTO};
    tusb_init(0, &dev_init);

    while (true) {
        tud_task();
        parse_rx();

        uint32_t now = to_ms_since_boot(get_absolute_time());
        if (!released_on_timeout && now - last_rx_ms > HEARTBEAT_TIMEOUT_MS) {
            release_all();  // hub went away: never leave keys or buttons held
            released_on_timeout = true;
        }
        // LED on while the hub link is alive.
        gpio_put(LED_PIN, !released_on_timeout);

        pump_reports();
        xinput_task();
    }
}
