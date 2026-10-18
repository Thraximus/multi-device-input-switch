#!/usr/bin/env bash
# Build the Pico firmware and flash it over the serial link (no BOOTSEL button needed). The Pico must be
# plugged into this PC for this; when it's in the laptop, use `python3 -m hub.ctl flash-pico laptop`.
# Usage: tools/flash.sh [SERIAL_PORT]   (default: SWITCHER_SERIAL_PORT from .env)
set -euo pipefail
cd "$(dirname "$0")/.."
. tools/env.sh
. tools/firmware-args.sh
port="${1:-$SWITCHER_SERIAL_PORT}"
# -U: settings removed from .env go back to their defaults instead of staying cached
cmake -S firmware -B firmware/build -G Ninja -U "SWITCHER_*" "${firmware_args[@]}" >/dev/null
ninja -C firmware/build
python3 -c "
import sys; sys.path.insert(0, '.')
from hub.link import Link; from hub import protocol as p
Link(sys.argv[1]).send(p.frame(p.MSG_BOOTLOADER))" "$port"
for _ in $(seq 1 20); do
    dev=$(lsblk -lno NAME,LABEL | awk '$2=="RPI-RP2"{print $1}')
    [ -n "$dev" ] && break
    sleep 0.5
done
[ -n "${dev:-}" ] || { echo "Pico did not enter the bootloader" >&2; exit 1; }
mnt=$(findmnt -lno TARGET "/dev/$dev" || true)
[ -n "$mnt" ] || mnt=$(udisksctl mount -b "/dev/$dev" | sed -n 's/.* at //p')
cp firmware/build/switcher_hub.uf2 "$mnt/"
sync
echo "flashed"
