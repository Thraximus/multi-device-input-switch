# Firmware settings shared by the Pico and ESP32 builds. The tools (tools/flash.sh, tools/flash-esp32.sh)
# pass the values from the repo's .env as -DNAME=value; anything not given keeps the default below.
set(SWITCHER_USB_ID "1209:0001" CACHE STRING "Pico USB VID:PID (hex); the hub must use the same (SWITCHER_USB_ID)")
set(SWITCHER_USB_MANUFACTURER "pi-switcher-hub" CACHE STRING "Pico USB manufacturer string")
set(SWITCHER_USB_PRODUCT "Switcher Hub" CACHE STRING "Pico USB product string")
set(SWITCHER_BLE_NAME "Switcher Hub" CACHE STRING "Bluetooth name the ESP32 advertises")
set(SWITCHER_LINK_BAUD 921600 CACHE STRING "Serial speed hub <-> ESP32 <-> Pico (the hub uses SWITCHER_LINK_BAUD too)")
set(SWITCHER_PICO_LINK_TX_PIN 0 CACHE STRING "Pico GPIO that transmits to the ESP32")
set(SWITCHER_PICO_LINK_RX_PIN 1 CACHE STRING "Pico GPIO that receives from the ESP32")
set(SWITCHER_ESP32_PICO_TX_PIN 16 CACHE STRING "ESP32 GPIO that transmits to the Pico")
set(SWITCHER_ESP32_PICO_RX_PIN 17 CACHE STRING "ESP32 GPIO that receives from the Pico")

string(REPLACE ":" ";" _usb_id "${SWITCHER_USB_ID}")
list(LENGTH _usb_id _usb_parts)
if(NOT _usb_parts EQUAL 2)
    message(FATAL_ERROR "SWITCHER_USB_ID must look like 1209:0001, not '${SWITCHER_USB_ID}'")
endif()
list(GET _usb_id 0 _usb_vid)
list(GET _usb_id 1 _usb_pid)

set(SWITCHER_FIRMWARE_DEFINITIONS
    USB_VID=0x${_usb_vid}
    USB_PID=0x${_usb_pid}
    USB_MANUFACTURER="${SWITCHER_USB_MANUFACTURER}"
    USB_PRODUCT="${SWITCHER_USB_PRODUCT}"
    BLE_DEVICE_NAME="${SWITCHER_BLE_NAME}"
    LINK_BAUD=${SWITCHER_LINK_BAUD}
    PICO_LINK_TX_PIN=${SWITCHER_PICO_LINK_TX_PIN}
    PICO_LINK_RX_PIN=${SWITCHER_PICO_LINK_RX_PIN}
    ESP32_PICO_TX_PIN=${SWITCHER_ESP32_PICO_TX_PIN}
    ESP32_PICO_RX_PIN=${SWITCHER_ESP32_PICO_RX_PIN}
)
