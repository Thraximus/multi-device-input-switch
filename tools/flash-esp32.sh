#!/usr/bin/env bash
# Build and flash the ESP32 router firmware. The hub service is paused while flashing.
# Needs ESP-IDF: IDF_PATH in .env (or the environment). Usage: tools/flash-esp32.sh [SERIAL_PORT]
set -euo pipefail
cd "$(dirname "$0")/.."
. tools/env.sh
. tools/firmware-args.sh
port="${1:-$SWITCHER_SERIAL_PORT}"
[ -n "${IDF_PATH:-}" ] && [ -f "$IDF_PATH/export.sh" ] || { echo "set IDF_PATH (ESP-IDF checkout) in .env" >&2; exit 1; }
. "$IDF_PATH/export.sh" >/dev/null
cd firmware/esp32
was_active=$(systemctl --user is-active pi-switcher-hub.service || true)
[ "$was_active" = active ] && systemctl --user stop pi-switcher-hub.service
trap '[ "$was_active" = active ] && systemctl --user start pi-switcher-hub.service' EXIT
idf.py -p "$port" -b "${SWITCHER_FLASH_BAUD:-921600}" "${firmware_args[@]}" build flash
