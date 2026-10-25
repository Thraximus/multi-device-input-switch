#include "oled.h"

#include <string.h>

#include "driver/i2c_master.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "freertos/task.h"

#include "font5x7.h"
#include "protocol.h"

// Wiring: SDA and SCL of the module on these GPIOs. Pins and the sleep timeout come from
// firmware/settings.cmake (the repo's .env); these are only the defaults.
#ifndef ESP32_OLED_SDA_PIN
#define ESP32_OLED_SDA_PIN 21
#endif
#ifndef ESP32_OLED_SCL_PIN
#define ESP32_OLED_SCL_PIN 22
#endif
#ifndef OLED_SLEEP_SECONDS
#define OLED_SLEEP_SECONDS 600
#endif

#define OLED_ADDRESS     0x3C
#define OLED_WIDTH       128
#define OLED_PAGES       8
#define OLED_CONTRAST    0x40  // blue pixels wear faster than white: don't run them at full brightness
#define OLED_TICK_MS     100
// The hub resends the picture every second; this long without one means the hub is gone.
#define HUB_TIMEOUT_US   (3 * 1000000LL)
#define CELL_WIDTH       6

static const char *TAG = "oled";

static i2c_master_dev_handle_t dev;
static TaskHandle_t oled_handle;

// From the hub (guarded by picture_lock)
static SemaphoreHandle_t picture_lock;
static uint8_t incoming[OLED_PAGES * OLED_WIDTH];  // chunks arrive here
static uint8_t picture[OLED_PAGES * OLED_WIDTH];   // the last complete picture
static int64_t last_picture_us;                    // 0 = the hub never sent anything

// Only the OLED task
static uint8_t shown[OLED_PAGES * OLED_WIDTH];     // what the panel shows
static uint8_t pending[OLED_PAGES * OLED_WIDTH];   // what it should show
static uint8_t tx[1 + sizeof(shown)];              // I2C buffer: control byte + a picture

static esp_err_t command(const uint8_t *bytes, size_t n) {
    uint8_t buf[16];
    if (n > sizeof(buf) - 1) return ESP_ERR_INVALID_SIZE;
    buf[0] = 0x00;  // control byte: the rest are commands
    memcpy(buf + 1, bytes, n);
    return i2c_master_transmit(dev, buf, n + 1, 100);
}

static esp_err_t power(bool on) {
    uint8_t cmd[] = {on ? 0xAF : 0xAE};
    return command(cmd, sizeof(cmd));
}

static esp_err_t init_panel(void) {
    // Each entry: number of bytes, then the command and its parameter.
    static const uint8_t setup[][3] = {
        {1, 0xAE},                 // display off
        {2, 0xD5, 0x80},           // clock divide / oscillator
        {2, 0xA8, 0x3F},           // 64 rows
        {2, 0xD3, 0x00},           // no vertical offset
        {1, 0x40},                 // start line 0
        {2, 0x8D, 0x14},           // charge pump on
        {2, 0x20, 0x00},           // horizontal addressing
        {1, 0xA1},                 // not mirrored
        {1, 0xC8},                 // not upside down
        {2, 0xDA, 0x12},           // COM pins
        {2, 0x81, OLED_CONTRAST},
        {2, 0xD9, 0xF1},           // pre-charge
        {2, 0xDB, 0x40},           // VCOM level
        {1, 0xA4},                 // follow RAM
        {1, 0xA6},                 // not inverted
        {1, 0xAF},                 // display on
    };
    for (size_t i = 0; i < sizeof(setup) / sizeof(setup[0]); i++) {
        esp_err_t err = command(setup[i] + 1, setup[i][0]);
        if (err != ESP_OK) return err;
    }
    return ESP_OK;
}

static esp_err_t push(const uint8_t *frame) {
    static const uint8_t window[] = {0x21, 0x00, OLED_WIDTH - 1, 0x22, 0x00, OLED_PAGES - 1};
    esp_err_t err = command(window, 3);
    if (err == ESP_OK) err = command(window + 3, 3);
    if (err != ESP_OK) return err;
    tx[0] = 0x40;  // control byte: the rest is display data
    memcpy(tx + 1, frame, sizeof(shown));
    return i2c_master_transmit(dev, tx, sizeof(tx), 200);
}

static void draw_text(uint8_t *page, const char *text, size_t n) {
    for (size_t i = 0; i < n; i++) memcpy(page + i * CELL_WIDTH, font5x7_glyph((uint8_t)text[i]), 5);
}

static void render_offline(void) {
    static const char msg[] = "HUB OFFLINE";
    memset(pending, 0, sizeof(pending));
    draw_text(pending + 3 * OLED_WIDTH + (OLED_WIDTH - (sizeof(msg) - 1) * CELL_WIDTH) / 2, msg, sizeof(msg) - 1);
}

static void oled_task(void *arg) {
    bool awake = true;
    int64_t changed_us = esp_timer_get_time();
    // Start from something that differs from any real screen, so the first render is always sent.
    memset(shown, 0xAA, sizeof(shown));
    while (true) {
        // Woken by every new picture, so animations run at the speed the I2C bus allows; the timeout covers the rest.
        ulTaskNotifyTake(pdTRUE, pdMS_TO_TICKS(OLED_TICK_MS));
        int64_t now = esp_timer_get_time();
        xSemaphoreTake(picture_lock, portMAX_DELAY);
        bool live = last_picture_us && now - last_picture_us < HUB_TIMEOUT_US;
        if (live) memcpy(pending, picture, sizeof(pending));
        xSemaphoreGive(picture_lock);
        if (!live) render_offline();

        if (memcmp(pending, shown, sizeof(shown)) != 0) {
            // Anything new on the screen wakes it; a static screen eventually sleeps (burn-in).
            changed_us = now;
            if (push(pending) != ESP_OK) continue;  // try again next tick
            memcpy(shown, pending, sizeof(shown));
            if (!awake && power(true) == ESP_OK) awake = true;
        }
        if (OLED_SLEEP_SECONDS && awake && now - changed_us > OLED_SLEEP_SECONDS * 1000000LL && power(false) == ESP_OK)
            awake = false;
    }
}

void oled_chunk(uint8_t index, const uint8_t *data, uint8_t len) {
    if (!picture_lock || index >= DISPLAY_CHUNKS || len != DISPLAY_CHUNK) return;
    memcpy(incoming + index * DISPLAY_CHUNK, data, DISPLAY_CHUNK);
    if (index != DISPLAY_CHUNKS - 1) return;
    xSemaphoreTake(picture_lock, portMAX_DELAY);
    memcpy(picture, incoming, sizeof(picture));
    last_picture_us = esp_timer_get_time();
    xSemaphoreGive(picture_lock);
    if (oled_handle) xTaskNotifyGive(oled_handle);
}

void oled_start(void) {
    i2c_master_bus_config_t bus_cfg = {
        .i2c_port = I2C_NUM_0,
        .sda_io_num = ESP32_OLED_SDA_PIN,
        .scl_io_num = ESP32_OLED_SCL_PIN,
        .clk_source = I2C_CLK_SRC_DEFAULT,
        .glitch_ignore_cnt = 7,
        .flags.enable_internal_pullup = true,  // the module has its own; these only help a bare one
    };
    i2c_master_bus_handle_t bus;
    if (i2c_new_master_bus(&bus_cfg, &bus) != ESP_OK) return;
    if (i2c_master_probe(bus, OLED_ADDRESS, 100) != ESP_OK) {
        ESP_LOGI(TAG, "no display at 0x%02X (SDA %d, SCL %d)", OLED_ADDRESS, ESP32_OLED_SDA_PIN, ESP32_OLED_SCL_PIN);
        i2c_del_master_bus(bus);
        return;
    }
    i2c_device_config_t dev_cfg = {
        .dev_addr_length = I2C_ADDR_BIT_LEN_7,
        .device_address = OLED_ADDRESS,
        .scl_speed_hz = 400000,
    };
    if (i2c_master_bus_add_device(bus, &dev_cfg, &dev) != ESP_OK || init_panel() != ESP_OK) return;
    picture_lock = xSemaphoreCreateMutex();
    xTaskCreate(oled_task, "oled", 4096, NULL, 3, &oled_handle);
}
