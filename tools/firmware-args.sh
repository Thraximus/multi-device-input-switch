# Sourced by the firmware tools: the firmware settings from .env as CMake -D options (firmware/settings.cmake).
firmware_args=()
for _name in SWITCHER_USB_ID SWITCHER_USB_MANUFACTURER SWITCHER_USB_PRODUCT SWITCHER_BLE_NAME SWITCHER_LINK_BAUD \
             SWITCHER_PICO_LINK_TX_PIN SWITCHER_PICO_LINK_RX_PIN SWITCHER_ESP32_PICO_TX_PIN SWITCHER_ESP32_PICO_RX_PIN; do
    [ -n "${!_name:-}" ] && firmware_args+=("-D$_name=${!_name}")
done
unset _name
